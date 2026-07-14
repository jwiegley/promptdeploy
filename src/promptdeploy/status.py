"""Deployment status comparison between source items and manifests."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, load_anthropic_default_model
from .deploy import _TYPE_TO_CATEGORY, compute_item_hash, item_selected
from .manifest import has_changed, load_manifest
from .names import require_canonical_item_name
from .source import SourceDiscovery
from .targets import create_target


@dataclass
class StatusEntry:
    """Status of a single item relative to a target."""

    item_type: str
    name: str
    target_id: str
    state: str  # 'current', 'changed', 'new', 'pending_removal'


_CATEGORY_TO_TYPE = {v: k for k, v in _TYPE_TO_CATEGORY.items()}


def get_status(
    config: Config, target_ids: list[str] | None = None
) -> list[StatusEntry]:
    """Compare source items against deployed manifests for each target.

    Item selection and hashing are shared with :func:`promptdeploy.deploy.deploy`
    (see :func:`promptdeploy.deploy.item_selected` and
    :func:`promptdeploy.deploy.compute_item_hash`) so status reports exactly
    what a deploy would do.
    """
    if target_ids is None:
        target_ids = list(config.targets.keys())

    entries: list[StatusEntry] = []
    discovery = SourceDiscovery(config.source_root)
    items = list(discovery.discover_all())
    for item in items:
        require_canonical_item_name(item.item_type, item.name)

    # Resolve the Anthropic default model exactly as deploy() does so that
    # claude targets report the same content fingerprint (injected model
    # frontmatter) in their hashes.
    global_model = load_anthropic_default_model(config.source_root / "models.yaml")

    for target_id in target_ids:
        target_config = config.targets[target_id]
        target = create_target(target_config, global_model=global_model)
        try:
            target.prepare()
            manifest = load_manifest(target.manifest_path())

            deployed_names: set[tuple[str, str]] = set()
            for item in items:
                if not item_selected(item, target, target_id, config):
                    continue

                category = _TYPE_TO_CATEGORY[item.item_type]
                current_hash = compute_item_hash(item, target, config)
                deployed_names.add((category, item.name))

                if has_changed(manifest, category, item.name, current_hash):
                    if (
                        category in manifest.items
                        and item.name in manifest.items[category]
                    ):
                        state = "changed"
                    else:
                        state = "new"
                else:
                    source_match = target.item_matches_source(
                        item.item_type,
                        item.name,
                        item.content,
                        item.metadata,
                        source_path=item.path,
                    )
                    state = "changed" if source_match is False else "current"

                entries.append(StatusEntry(item.item_type, item.name, target_id, state))

            # Check for items in manifest but no longer in source
            for category, items_dict in manifest.items.items():
                for name in items_dict:
                    if (category, name) not in deployed_names:
                        item_type = _CATEGORY_TO_TYPE.get(category, category)
                        entries.append(
                            StatusEntry(item_type, name, target_id, "pending_removal")
                        )
        finally:
            target.cleanup()

    return entries
