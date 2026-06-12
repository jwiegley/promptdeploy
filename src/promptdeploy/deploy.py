"""Core deploy orchestration for promptdeploy."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional, Set

from .config import Config, load_anthropic_default_model
from .envsubst import _ENV_PATTERN
from .filters import should_deploy_to
from .manifest import (
    Manifest,
    ManifestItem,
    compute_directory_hash,
    compute_file_hash,
    has_changed,
    load_manifest,
    save_manifest,
)
from .settings import render_settings
from .source import SourceDiscovery, SourceItem
from .targets import create_target
from .targets.base import Target

# Maps SourceItem.item_type -> manifest category key
_TYPE_TO_CATEGORY = {
    "agent": "agents",
    "command": "commands",
    "skill": "skills",
    "mcp": "mcp_servers",
    "models": "models",
    "hook": "hooks",
    "marketplace": "marketplaces",
    "prompt": "prompts",
    "settings": "settings",
}

# Maps CLI --only-type values (plural) -> SourceItem.item_type (singular)
_CLI_TYPE_TO_ITEM_TYPE = {
    "agents": "agent",
    "commands": "command",
    "skills": "skill",
    "mcp": "mcp",
    "models": "models",
    "hooks": "hook",
    "marketplaces": "marketplace",
    "prompts": "prompt",
    "settings": "settings",
}


# Every action kind deploy() can emit, mypy-enforced via DeployAction.action.
ActionType = Literal["create", "update", "remove", "skip", "pre-existing"]


@dataclass
class DeployAction:
    """Records a single deploy action taken (or planned)."""

    action: ActionType
    item_type: str
    name: str
    target_id: str
    source_path: Optional[str] = None
    # Warnings produced while computing this action's rendered output (e.g.
    # undefined Jinja variables in a .poet prompt). Empty for items without
    # template rendering.
    warnings: list[str] = field(default_factory=list)


def _apply_provider_overrides(
    prov: dict[str, Any], target_id: str, config: Config
) -> dict[str, Any]:
    """Shallow-merge per-target ``overrides`` onto a provider dict.

    The ``overrides`` field maps a target ID or group name to a partial
    provider config. Any entry whose key matches the target directly,
    or via a group expansion containing the target, is merged on top of
    the provider's defaults; later matches win on conflict. The
    ``models`` and ``overrides`` keys inside an override entry are
    ignored, and the ``overrides`` field itself is stripped from the
    returned dict.
    """
    overrides = prov.get("overrides")
    result = {k: v for k, v in prov.items() if k != "overrides"}
    if not isinstance(overrides, dict):
        return result
    for env_id, override_data in overrides.items():
        if not isinstance(override_data, dict):
            continue
        members = config.groups.get(env_id, [env_id])
        if target_id not in members and env_id != target_id:
            continue
        for k, v in override_data.items():
            if k in ("models", "overrides"):
                continue
            result[k] = v
    return result


def _filter_models_config(
    config_dict: dict[str, Any], target_id: str, config: Config
) -> dict[str, Any]:
    """Filter a models.yaml config dict, keeping only matching providers/models.

    Applies should_deploy_to() at both provider and model level so that
    only/except filtering works for the models item type (which is a single
    SourceItem containing many providers and models). Then applies any
    per-target ``overrides`` for the matching provider.
    """
    filtered_providers: dict[str, Any] = {}
    for prov_key, prov in config_dict.get("providers", {}).items():
        if not should_deploy_to(target_id, prov, config, "models.yaml"):
            continue
        # Filter individual models within the provider
        filtered_models: dict[str, Any] = {}
        for model_id, model in prov.get("models", {}).items():
            if model is None:
                model = {}
            if not should_deploy_to(target_id, model, config, "models.yaml"):
                continue
            filtered_models[model_id] = model
        if filtered_models:
            filtered_prov = _apply_provider_overrides(prov, target_id, config)
            filtered_prov["models"] = filtered_models
            filtered_providers[prov_key] = filtered_prov
    return {"providers": filtered_providers}


def item_selected(
    item: SourceItem, target: Target, target_id: str, config: Config
) -> bool:
    """Decide whether an item applies to a target at all.

    Single source of truth shared by :func:`deploy` and
    :func:`promptdeploy.status.get_status`: an item is selected when it
    passes the environment filters (frontmatter ``only``/``except`` plus
    filename filetags) AND the target would not no-op the deploy via
    :meth:`Target.should_skip`. Keeping both callers on this predicate
    guarantees ``status`` cannot drift from what ``deploy`` would do.
    """
    if not should_deploy_to(
        target_id,
        item.metadata,
        config,
        str(item.path),
        filetags=item.filetags,
    ):
        return False
    return not target.should_skip(
        item.item_type, item.name, item.content, item.metadata
    )


def _expand_env_for_hash(value: object) -> object:
    """Recursively expand ``${VAR}`` references for hashing purposes only.

    Unset variables stay as literal ``${VAR}`` text and no warning is
    printed -- this expansion never produces deployed bytes. It exists so
    the models manifest hash reflects the env values that droid/opencode
    bake into their configs at deploy time: rotating a referenced key
    changes the hash and triggers a redeploy even though models.yaml
    itself is unchanged.
    """
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env_for_hash(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_for_hash(v) for v in value]
    return value


def compute_item_hash(
    item: SourceItem, target: Target, config: Optional[Config] = None
) -> str:
    """Hash that reflects the effective deployed output, not just source bytes.

    The base hash covers the source content (or directory for skills). If the
    target reports a ``content_fingerprint`` -- e.g. an injected model -- it
    is mixed in so that config changes affecting deployed bytes invalidate
    the manifest cache even when source bytes are unchanged. Shared with
    :func:`promptdeploy.status.get_status` so both sides hash identically.

    When ``config`` is provided, ``settings`` and ``models`` items hash
    their effective per-target output instead of raw source bytes: settings
    hash the :func:`promptdeploy.settings.render_settings` result (so a
    deploy.yaml edit such as a removed group label invalidates the cache),
    and models hash the filtered config with current env values folded in
    (so group edits and rotated API keys trigger a redeploy). Without
    ``config`` these items fall back to hashing source bytes.
    """
    if config is not None and item.item_type == "settings":
        rendered = render_settings(item.metadata or {}, target.id, config)
        return compute_file_hash(
            json.dumps(rendered, sort_keys=True, default=str).encode()
        )
    if config is not None and item.item_type == "models":
        filtered = _filter_models_config(item.metadata or {}, target.id, config)
        effective = _expand_env_for_hash(filtered)
        return compute_file_hash(
            json.dumps(effective, sort_keys=True, default=str).encode()
        )
    if item.item_type == "skill":
        base = compute_directory_hash(item.path.parent.resolve())
    else:
        base = compute_file_hash(item.content)
    fingerprint = target.content_fingerprint(item.item_type)
    if fingerprint is None:
        return base
    digest = hashlib.sha256(f"{base}|{fingerprint}".encode()).hexdigest()
    return f"sha256:{digest}"


def _deploy_item(
    target: Target,
    item: SourceItem,
    *,
    filtered_models_config: Optional[dict[str, Any]] = None,
) -> None:
    """Deploy a single source item to a target."""
    if item.item_type == "agent":
        target.deploy_agent(item.name, item.content)
    elif item.item_type == "command":
        target.deploy_command(item.name, item.content)
    elif item.item_type == "skill":
        target.deploy_skill(item.name, item.path.parent)
    elif item.item_type == "mcp":
        target.deploy_mcp_server(item.name, item.metadata or {})
    elif item.item_type == "models":
        target.deploy_models(filtered_models_config or {})
    elif item.item_type == "hook":
        target.deploy_hook(item.name, item.metadata or {})
    elif item.item_type == "marketplace":
        target.deploy_marketplace(item.name, item.metadata or {})
    elif item.item_type == "prompt":
        target.deploy_prompt(item.name, item.content, item.path)
    else:
        # "settings" is dispatched through Target.deploy_settings in
        # deploy(); anything else here is a programming error.
        raise ValueError(f"unsupported item type for deploy: {item.item_type!r}")


def _drain_warnings(target: Target) -> dict[str, list[str]]:
    """Collect target-side warnings into a name -> warnings mapping."""
    drained: dict[str, list[str]] = {}
    for name, warnings in target.consume_warnings():
        drained.setdefault(name, []).extend(warnings)
    return drained


def _disk_matches_source(target: Target, item: SourceItem) -> bool:
    """Return True when the on-disk artifact already equals deploy bytes.

    Only meaningful for single-file artifacts (agents, commands, prompts).
    Returns False whenever either side cannot be materialised -- e.g. for
    skill directories or items that merge into a shared JSON file. The
    caller treats False as "cannot prove identical, keep pre-existing
    behaviour".
    """
    would = target.would_deploy_bytes(
        item.item_type, item.name, item.content, source_path=item.path
    )
    if would is None:
        return False
    on_disk = target.read_deployed_bytes(item.item_type, item.name)
    if on_disk is None:
        return False
    return would == on_disk


def _remove_item(
    target: Target,
    category: str,
    name: str,
    target_path: Optional[Path] = None,
    managed_keys: Optional[list[str]] = None,
) -> None:
    """Remove a single item from a target by manifest category.

    For ``prompts``, ``target_path`` (when present in the manifest) names the
    exact file the previous deploy wrote so the target can unlink only that
    artifact instead of probing extension variants.
    """
    if category == "agents":
        target.remove_agent(name)
    elif category == "commands":
        target.remove_command(name)
    elif category == "skills":
        target.remove_skill(name)
    elif category == "mcp_servers":
        target.remove_mcp_server(name)
    elif category == "models":
        target.remove_models()
    elif category == "hooks":
        target.remove_hook(name)
    elif category == "marketplaces":
        target.remove_marketplace(name)
    elif category == "prompts":
        target.remove_prompt(name, target_path=target_path)
    elif category == "settings":
        target.remove_settings(managed_keys or [])


def deploy(
    config: Config,
    target_ids: Optional[List[str]] = None,
    dry_run: bool = False,
    verbose: bool = False,
    item_types: Optional[List[str]] = None,
    force: bool = False,
) -> List[DeployAction]:
    """Deploy source items to targets.

    Args:
        config: Loaded Config.
        target_ids: Specific targets to deploy to (None = all).
        dry_run: If True, compute actions without writing anything.
        verbose: Print extra detail.
        item_types: CLI --only-type values (plural) to filter by.
        force: If True, deploy all items regardless of checksum or
            pre-existing state.

    Returns:
        List of DeployAction records describing what was done.
    """
    if target_ids is None:
        target_ids = list(config.targets.keys())

    # Resolve --only-type filter to singular item_type values
    allowed_types: Optional[Set[str]] = None
    if item_types:
        allowed_types = {_CLI_TYPE_TO_ITEM_TYPE[t] for t in item_types}

    discovery = SourceDiscovery(config.source_root)
    all_items = list(discovery.discover_all())

    # Resolve the Anthropic default model once from models.yaml; threaded
    # into ClaudeTarget via create_target so that agents/skills receive the
    # injected `model` frontmatter field (overridable per-target).
    global_model = load_anthropic_default_model(config.source_root / "models.yaml")

    actions: List[DeployAction] = []

    for target_id in target_ids:
        target_config = config.targets[target_id]
        target = create_target(target_config, global_model=global_model)
        try:
            target.prepare(verbose=verbose)
            manifest = load_manifest(target.manifest_path())
            new_manifest = Manifest(deployed_at=datetime.now(timezone.utc).isoformat())

            # Track which (category, name) pairs we process for stale detection
            deployed_names: set[tuple[str, str]] = set()

            for item in all_items:
                # Apply --only-type filter
                if allowed_types is not None and item.item_type not in allowed_types:
                    continue

                # Apply environment filters (filetags + only/except) and
                # target-side skip rules via the shared predicate.
                if not item_selected(item, target, target_id, config):
                    continue

                category = _TYPE_TO_CATEGORY[item.item_type]
                current_hash = compute_item_hash(item, target, config)
                deployed_names.add((category, item.name))

                changed = has_changed(manifest, category, item.name, current_hash)

                if item.item_type == "settings":
                    rendered = render_settings(item.metadata or {}, target_id, config)
                    prev = manifest.items.get("settings", {}).get(item.name)
                    previous_keys = (
                        list(prev.managed_keys) if prev and prev.managed_keys else []
                    )
                    is_update = prev is not None
                    if force or changed:
                        if not dry_run:
                            target.deploy_settings(rendered, previous_keys)
                        actions.append(
                            DeployAction(
                                action="update" if is_update else "create",
                                item_type="settings",
                                name=item.name,
                                target_id=target_id,
                                source_path=str(item.path),
                            )
                        )
                        recorded_keys = list(rendered.keys())
                    else:
                        actions.append(
                            DeployAction(
                                action="skip",
                                item_type="settings",
                                name=item.name,
                                target_id=target_id,
                                source_path=str(item.path),
                            )
                        )
                        # Nothing was deployed on this path: carry forward
                        # the keys recorded by the last real deploy so a
                        # later render that drops one of them can still
                        # remove it from the target. Recording the current
                        # render's keys here would orphan any key it no
                        # longer contains.
                        recorded_keys = previous_keys
                    new_manifest.items.setdefault("settings", {})[item.name] = (
                        ManifestItem(
                            source_hash=current_hash,
                            managed_keys=recorded_keys,
                        )
                    )
                    continue

                exists_on_target = target.item_exists(item.item_type, item.name)

                # Drift detection: even when the source hash still matches the
                # manifest, the deployed bytes may no longer match what we
                # would write -- e.g. the transformation logic changed in a
                # newer promptdeploy release, or the artifact was hand-edited
                # at the target.  When both sides can be materialised, compare
                # them and redeploy on mismatch so the target always reflects
                # the current source+transform.
                if not force and not changed and exists_on_target:
                    would = target.would_deploy_bytes(
                        item.item_type,
                        item.name,
                        item.content,
                        source_path=item.path,
                    )
                    on_disk = target.read_deployed_bytes(item.item_type, item.name)
                    if would is not None and on_disk is not None and would != on_disk:
                        changed = True

                if force or changed or not exists_on_target:
                    # Determine if create or update
                    is_update = (
                        category in manifest.items
                        and item.name in manifest.items[category]
                    )

                    # Detect pre-existing: new item but target already has something
                    if not force and not is_update and exists_on_target:
                        # If the on-disk artifact is byte-identical to what
                        # we would deploy, silently adopt it into the
                        # manifest instead of reporting it as pre-existing.
                        # This makes deploys idempotent for files that
                        # arrived at the target outside the manifest (e.g.
                        # via an older promptdeploy run or sideloading) but
                        # are already in the correct state.
                        if _disk_matches_source(target, item):
                            actions.append(
                                DeployAction(
                                    action="skip",
                                    item_type=item.item_type,
                                    name=item.name,
                                    target_id=target_id,
                                    source_path=str(item.path),
                                )
                            )
                            # Fall through to the manifest-recording block
                            # below so the next deploy treats this item as
                            # managed and unchanged.
                        else:
                            actions.append(
                                DeployAction(
                                    action="pre-existing",
                                    item_type=item.item_type,
                                    name=item.name,
                                    target_id=target_id,
                                    source_path=str(item.path),
                                )
                            )
                            continue
                    else:
                        action_type: ActionType = "update" if is_update else "create"

                        if not dry_run:
                            if item.item_type == "models":
                                filtered = _filter_models_config(
                                    item.metadata or {}, target_id, config
                                )
                                _deploy_item(
                                    target, item, filtered_models_config=filtered
                                )
                            else:
                                _deploy_item(target, item)

                        # Drain any warnings the target collected during this
                        # deploy so we can attach them to the matching
                        # DeployAction. We drain immediately after each deploy
                        # so each item's warnings are isolated.
                        drained = _drain_warnings(target)
                        item_warnings = drained.get(item.name, [])

                        actions.append(
                            DeployAction(
                                action=action_type,
                                item_type=item.item_type,
                                name=item.name,
                                target_id=target_id,
                                source_path=str(item.path),
                                warnings=item_warnings,
                            )
                        )
                else:
                    actions.append(
                        DeployAction(
                            action="skip",
                            item_type=item.item_type,
                            name=item.name,
                            target_id=target_id,
                            source_path=str(item.path),
                        )
                    )

                # Record in new manifest regardless of action. Capture the
                # target-side artifact path (when known) so future stale
                # removals can unlink the exact file we wrote.
                tp = target.deployed_artifact_path(item.item_type, item.name)
                prev = manifest.items.get(category, {}).get(item.name)
                prev_path = prev.target_path if prev is not None else None
                if tp is None:
                    # Preserve the previous manifest's target_path if we
                    # don't have a fresh one (e.g. on dry-run/skip the
                    # target didn't actually deploy this run).
                    target_path_str = prev_path
                else:
                    target_path_str = tp.as_posix()
                    # A source extension change (e.g. foo.poet -> foo.md)
                    # moves the deployed artifact. Unlink exactly the file
                    # the previous deploy recorded so it cannot linger as
                    # an orphan -- never stem-siblings, which may be
                    # user-authored files.
                    if (
                        not dry_run
                        and category == "prompts"
                        and prev_path is not None
                        and prev_path != target_path_str
                    ):
                        target.remove_prompt(item.name, target_path=Path(prev_path))
                new_manifest.items.setdefault(category, {})[item.name] = ManifestItem(
                    source_hash=current_hash,
                    target_path=target_path_str,
                )

            # Detect stale items: in old manifest but not in new source
            for category, items_dict in manifest.items.items():
                for name in items_dict:
                    if (category, name) in deployed_names:
                        continue
                    # If --only-type is active, only remove items of matching types
                    if allowed_types is not None:
                        cat_type = {v: k for k, v in _TYPE_TO_CATEGORY.items()}.get(
                            category
                        )
                        if cat_type not in allowed_types:
                            # Preserve unfiltered items in new manifest
                            new_manifest.items.setdefault(category, {})[name] = (
                                items_dict[name]
                            )
                            continue

                    # Look up the deployed artifact path (if any) so the
                    # removal can target the exact file the previous deploy
                    # wrote.
                    prev_item = items_dict.get(name)
                    target_path: Optional[Path] = None
                    if prev_item is not None and prev_item.target_path is not None:
                        target_path = Path(prev_item.target_path)

                    if not dry_run:
                        _remove_item(
                            target,
                            category,
                            name,
                            target_path=target_path,
                            managed_keys=(
                                prev_item.managed_keys if prev_item else None
                            ),
                        )

                    actions.append(
                        DeployAction(
                            action="remove",
                            item_type={v: k for k, v in _TYPE_TO_CATEGORY.items()}.get(
                                category, category
                            ),
                            name=name,
                            target_id=target_id,
                        )
                    )

            # Save updated manifest (atomic write)
            if not dry_run:
                save_manifest(new_manifest, target.manifest_path())
                target.finalize(verbose=verbose)
            else:
                target.cleanup()
        except BaseException:
            target.cleanup()
            raise

    return actions
