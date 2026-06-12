"""Validation for source items and configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

from .config import (
    Config,
    load_anthropic_default_model,
    load_anthropic_known_models,
)
from .envsubst import find_env_refs, read_env_example_keys
from .filetags import parse_filetags
from .frontmatter import FrontmatterError, parse_frontmatter
from .poet import POET_EXTENSIONS, PoetError, parse_poet
from .source import DiscoveryError, SourceDiscovery, SourceItem


@dataclass
class ValidationIssue:
    """A single validation problem found in a source item."""

    level: str  # 'error' or 'warning'
    message: str
    file_path: Path


def validate_all(config: Config) -> List[ValidationIssue]:
    """Validate all discoverable source items against the config."""
    issues: List[ValidationIssue] = []
    discovery = SourceDiscovery(config.source_root)

    # ${VAR} references in MCP env/headers are checked against .env.example
    # so a typo'd or undocumented variable surfaces at validate time. None
    # (no .env.example) disables the check entirely.
    env_keys = read_env_example_keys(config.source_root / ".env.example")

    all_items: List[SourceItem] = []

    # Markdown-backed types collect per-file frontmatter errors leniently so
    # one bad file aborts neither the rest of its directory nor
    # duplicate-name detection.
    for markdown_discover_fn in (
        discovery.discover_agents,
        discovery.discover_commands,
        discovery.discover_skills,
    ):
        discovery_errors: List[DiscoveryError] = []
        for item in markdown_discover_fn(errors=discovery_errors):
            all_items.append(item)
            issues.extend(validate_item(item, config, env_example_keys=env_keys))
        for err in discovery_errors:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=f"Discovery failed: {err.message}",
                    file_path=err.path,
                )
            )

    # YAML-backed types never raise during discovery: parse failures surface
    # as metadata=None and are reported per item by validate_item.
    for discover_fn in (
        discovery.discover_mcp_servers,
        discovery.discover_models,
        discovery.discover_hooks,
        discovery.discover_marketplaces,
        discovery.discover_prompts,
    ):
        for item in discover_fn():
            all_items.append(item)
            issues.extend(validate_item(item, config, env_example_keys=env_keys))

    # A committed skill that is a broken symlink silently vanishes from
    # discovery (and therefore from every target); surface it here.
    for link in discovery.broken_skill_symlinks():
        issues.append(
            ValidationIssue(
                level="warning",
                message=(
                    f"Broken symlink (target {link.readlink()} does not "
                    f"exist); this skill is silently skipped by discovery"
                ),
                file_path=link,
            )
        )

    # Detect duplicate resolved names within each item type
    seen: dict[tuple[str, str], Path] = {}
    for item in all_items:
        key = (item.item_type, item.name)
        if key in seen:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Duplicate {item.item_type} name '{item.name}' "
                        f"(also defined by {seen[key]})"
                    ),
                    file_path=item.path,
                )
            )
        else:
            seen[key] = item.path

    # Commands, skills, and prompts all surface in the same slash-command
    # namespace on claude targets, so a name shared across these types
    # collides in the live `/` menu even though each per-type check passes.
    slash_seen: dict[str, tuple[str, Path]] = {}
    for item in all_items:
        if item.item_type not in ("command", "skill", "prompt"):
            continue
        prev = slash_seen.get(item.name)
        if prev is None:
            slash_seen[item.name] = (item.item_type, item.path)
        elif prev[0] != item.item_type:
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        f"{item.item_type} '{item.name}' shares the "
                        f"slash-command namespace with the {prev[0]} defined "
                        f"by {prev[1]}"
                    ),
                    file_path=item.path,
                )
            )

    # Target-level rules: (a) per-target model: only applies to claude targets;
    # (b) warn when the effective model on a claude target is neither in
    # providers.anthropic.models nor an always-accepted alias.
    models_yaml_path = config.source_root / "models.yaml"
    default_model = load_anthropic_default_model(models_yaml_path)
    known_models = load_anthropic_known_models(models_yaml_path)
    always_accepted_aliases = {"fable", "opus", "sonnet", "haiku", "inherit"}
    allowed_models = always_accepted_aliases | (known_models or set())
    deploy_yaml_path = config.source_root / "deploy.yaml"

    for target in config.targets.values():
        if target.model is not None and target.type != "claude":
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Target '{target.id}' has 'model' set but type is "
                        f"'{target.type}'; model injection only applies to "
                        f"claude targets"
                    ),
                    file_path=deploy_yaml_path,
                )
            )
            continue
        if target.type != "claude":
            continue
        effective = target.model or default_model
        if effective is None:
            continue
        if effective not in allowed_models:
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        f"Target '{target.id}' effective model '{effective}' "
                        f"is not listed in providers.anthropic.models and is "
                        f"not a known alias"
                    ),
                    file_path=deploy_yaml_path,
                )
            )

    issues.extend(validate_settings(config))

    return issues


def validate_settings(config: Config) -> List[ValidationIssue]:
    """Validate settings.yaml structure and override targeting."""
    path = config.source_root / "settings.yaml"
    if not path.exists():
        return []
    issues: List[ValidationIssue] = []
    try:
        doc = yaml.safe_load(path.read_text("utf-8"))
    except yaml.YAMLError as exc:
        return [ValidationIssue("error", f"settings.yaml: {exc}", path)]
    if doc is None:
        return []
    if not isinstance(doc, dict):
        return [
            ValidationIssue("error", "settings.yaml: top level must be a mapping", path)
        ]

    known = set(config.targets) | set(config.groups)

    base = doc.get("base")
    if base is not None and not isinstance(base, dict):
        issues.append(
            ValidationIssue("error", "settings.yaml: 'base' must be a mapping", path)
        )
        base = None

    _managed_by = {
        "hooks": "hooks/",
        "mcpServers": "mcp/",
        "extraKnownMarketplaces": "marketplaces/",
        "enabledPlugins": "marketplaces/",
    }

    def _check_section(section: dict, where: str) -> None:
        for key, source_dir in _managed_by.items():
            if key in section:
                issues.append(
                    ValidationIssue(
                        "warning",
                        f"settings.yaml: '{key}' in {where} is ignored "
                        f"(managed by {source_dir})",
                        path,
                    )
                )

    # JSON-representability: yaml.safe_load yields YAML-only types (e.g.
    # ``datetime.date`` from an unquoted ``2026-06-01``) that json.dump cannot
    # serialize. Reject them at validate time so they surface as a clear error
    # rather than an uncaught TypeError when settings.json is written at deploy.
    _json_scalars = (str, int, float, bool, type(None))

    def _check_json(value: object, where: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                _check_json(v, f"{where}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                _check_json(v, f"{where}[{i}]")
        elif not isinstance(value, _json_scalars):
            issues.append(
                ValidationIssue(
                    "error",
                    f"settings.yaml: {where} has a non-JSON-representable value "
                    f"of type {type(value).__name__} (quote YAML dates/times so "
                    f"they deploy as strings)",
                    path,
                )
            )

    if isinstance(base, dict):
        _check_section(base, "base")
        _check_json(base, "base")
        for k, v in base.items():
            if v is None:
                issues.append(
                    ValidationIssue(
                        "warning",
                        f"settings.yaml: 'base.{k}' is null and will be stripped "
                        f"(null deletes only inside overrides)",
                        path,
                    )
                )

    overrides = doc.get("overrides")
    if overrides is not None:
        if not isinstance(overrides, dict):
            issues.append(
                ValidationIssue(
                    "error", "settings.yaml: 'overrides' must be a mapping", path
                )
            )
        else:
            for ov_key, ov_val in overrides.items():
                if ov_key not in known:
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"settings.yaml: override key '{ov_key}' is not a known "
                            f"target id or group",
                            path,
                        )
                    )
                if ov_val is not None and not isinstance(ov_val, dict):
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"settings.yaml: override '{ov_key}' must be a mapping",
                            path,
                        )
                    )
                elif isinstance(ov_val, dict):
                    _check_section(ov_val, f"overrides.{ov_key}")
                    _check_json(ov_val, f"overrides.{ov_key}")
    return issues


def validate_item(
    item: SourceItem,
    config: Config,
    *,
    env_example_keys: set[str] | None = None,
) -> List[ValidationIssue]:
    """Validate a single source item.

    ``env_example_keys`` is the set of variable names declared in
    ``.env.example``; when provided, MCP ``${VAR}`` references in
    ``env``/``headers`` that are not declared there produce warnings.
    ``None`` skips that check.
    """
    issues: List[ValidationIssue] = []

    _VALID_HOOK_EVENTS = frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "Notification",
            "SubagentStart",
            "SubagentStop",
            "Stop",
            "TeammateIdle",
            "TaskCompleted",
            "SessionStart",
            "SessionEnd",
            "PreCompact",
            "PostCompact",
            "UserPromptSubmit",
            "InstructionsLoaded",
            "ConfigChange",
            "WorktreeCreate",
            "WorktreeRemove",
        }
    )

    # Parse metadata. yaml.safe_load is given raw bytes so a non-UTF-8 file
    # surfaces as a ReaderError (a YAMLError) instead of an uncaught
    # UnicodeDecodeError.
    try:
        if item.item_type in ("mcp", "models", "hook", "marketplace"):
            metadata = yaml.safe_load(item.content)
            if not isinstance(metadata, dict):
                # These item types are deployed from their parsed mapping; a
                # list, bare scalar, or empty file would otherwise validate
                # clean and deploy junk (e.g. an empty mcpServers entry).
                return [
                    ValidationIssue(
                        level="error",
                        message=(
                            f"Top level of a {item.item_type} file must be "
                            f"a YAML mapping"
                        ),
                        file_path=item.path,
                    )
                ]
        elif item.item_type == "prompt":
            metadata = item.metadata
        else:
            metadata, _ = parse_frontmatter(item.content)
    except (yaml.YAMLError, FrontmatterError) as e:
        return [
            ValidationIssue(
                level="error",
                message=f"Invalid YAML: {e}",
                file_path=item.path,
            )
        ]

    # Validate filetag labels
    if item.filetags:
        valid_ids = set(config.targets.keys()) | set(config.groups.keys())
        for tag in item.filetags:
            if tag not in valid_ids:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Invalid filetag label '{tag}'",
                        file_path=item.path,
                    )
                )

    # Prompt-specific validation: parse .poet/.j2/.jinja files to surface
    # template/YAML errors at validate time, and surface any unrendered
    # Jinja variables as warnings. Done before the metadata-None early return
    # because prompts have no YAML frontmatter and routinely have metadata=None.
    if item.item_type == "prompt" and item.path.suffix in POET_EXTENSIONS:
        try:
            doc = parse_poet(item.content, source_path=item.path)
        except PoetError as e:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=f"Poet parse error: {e}",
                    file_path=item.path,
                )
            )
        else:
            for warning in doc.warnings:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        message=warning,
                        file_path=item.path,
                    )
                )

    if metadata is None:
        # A file that opens with '---' but yields no frontmatter (e.g. the
        # closing delimiter is missing) would deploy its raw '---' block to
        # every target with only/except silently ignored.
        if item.item_type in (
            "agent",
            "command",
            "skill",
        ) and item.content.removeprefix(b"\xef\xbb\xbf").startswith(b"---"):
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        "File starts with '---' but no frontmatter was "
                        "parsed (missing closing '---'?)"
                    ),
                    file_path=item.path,
                )
            )
        return issues

    # A non-string name: cannot be used as a manifest/settings key;
    # discovery falls back to the filename-derived name, so surface the
    # malformed value here. (Marketplaces have their own stricter check.)
    if item.item_type != "marketplace":
        name_value = metadata.get("name")
        if name_value is not None and not isinstance(name_value, str):
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"'name' must be a string (got {type(name_value).__name__})"
                    ),
                    file_path=item.path,
                )
            )

    # Check only/except mutual exclusivity
    only = metadata.get("only")
    except_ = metadata.get("except")
    if only is not None and except_ is not None:
        issues.append(
            ValidationIssue(
                level="error",
                message="Cannot specify both 'only' and 'except'",
                file_path=item.path,
            )
        )

    # Validate environment IDs
    valid_ids = set(config.targets.keys()) | set(config.groups.keys())
    for env_list, field_name in [(only, "only"), (except_, "except")]:
        if env_list is not None:
            if not isinstance(env_list, list):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"'{field_name}' must be a list",
                        file_path=item.path,
                    )
                )
                continue
            for env_id in env_list:
                if env_id not in valid_ids:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Invalid environment ID '{env_id}' in '{field_name}'",
                            file_path=item.path,
                        )
                    )

    # MCP-specific validations
    if item.item_type == "mcp":
        if "name" not in metadata:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="MCP server missing 'name' field",
                    file_path=item.path,
                )
            )
        if "command" not in metadata and "url" not in metadata:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="MCP server missing 'command' or 'url'",
                    file_path=item.path,
                )
            )
        enabled = metadata.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            # All targets gate on truthiness, so a truthy non-bool (e.g. the
            # string "false") would silently deploy a server the author
            # meant to disable.
            issues.append(
                ValidationIssue(
                    level="error",
                    message="MCP 'enabled' must be a boolean",
                    file_path=item.path,
                )
            )
        if env_example_keys is not None:
            refs = find_env_refs(metadata.get("env")) | find_env_refs(
                metadata.get("headers")
            )
            for var in sorted(refs - env_example_keys):
                issues.append(
                    ValidationIssue(
                        level="warning",
                        message=(
                            f"MCP references ${{{var}}} which is not declared "
                            f"in .env.example; an unset variable expands to "
                            f"empty at runtime"
                        ),
                        file_path=item.path,
                    )
                )

    # Skill-specific validations (documented Agent Skills limits)
    if item.item_type == "skill":
        if len(item.name) > 64:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Skill name '{item.name}' exceeds 64 characters "
                        f"({len(item.name)})"
                    ),
                    file_path=item.path,
                )
            )
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Skill 'description' is required",
                    file_path=item.path,
                )
            )
        elif len(description) > 1024:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Skill description exceeds 1024 characters "
                        f"({len(description)})"
                    ),
                    file_path=item.path,
                )
            )
        dir_base, _ = parse_filetags(item.path.parent.name)
        if item.name != dir_base:
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        f"Skill name '{item.name}' does not match its "
                        f"directory name '{dir_base}'"
                    ),
                    file_path=item.path,
                )
            )
        line_count = len(item.content.splitlines())
        if line_count > 500:
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        f"SKILL.md is {line_count} lines; consider keeping "
                        f"it under ~500 (move detail into reference files)"
                    ),
                    file_path=item.path,
                )
            )

    # Hook-specific validations
    if item.item_type == "hook":
        if "name" not in metadata:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Hook group missing 'name' field",
                    file_path=item.path,
                )
            )
        hooks_field = metadata.get("hooks")
        if not isinstance(hooks_field, dict):
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Hook group missing or invalid 'hooks' field (must be a dict)",
                    file_path=item.path,
                )
            )
        else:
            for event_type, entries in hooks_field.items():
                if event_type not in _VALID_HOOK_EVENTS:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Invalid hook event type '{event_type}'",
                            file_path=item.path,
                        )
                    )
                if not isinstance(entries, list) or len(entries) == 0:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Hook event '{event_type}' must be a non-empty list",
                            file_path=item.path,
                        )
                    )

    # Marketplace-specific validations
    if item.item_type == "marketplace":
        _known_source_types = {"github", "git", "directory"}
        allowed_keys = {
            "name",
            "description",
            "source",
            "autoUpdate",
            "plugins",
            "enabled",
            "only",
            "except",
        }
        for key in metadata:
            if key not in allowed_keys:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        message=f"Marketplace has unknown key '{key}'",
                        file_path=item.path,
                    )
                )
        name = metadata.get("name", item.name)
        if not isinstance(name, str) or not name:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Marketplace 'name' must be a non-empty string",
                    file_path=item.path,
                )
            )
        elif "@" in name or any(c.isspace() for c in name):
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Marketplace 'name' must not contain '@' or whitespace",
                    file_path=item.path,
                )
            )
        enabled = metadata.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            # A truthy non-bool (e.g. the string "false") would silently
            # deploy a marketplace the author meant to disable.
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Marketplace 'enabled' must be a boolean",
                    file_path=item.path,
                )
            )
        auto_update = metadata.get("autoUpdate")
        if auto_update is not None and not isinstance(auto_update, bool):
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Marketplace 'autoUpdate' must be a boolean",
                    file_path=item.path,
                )
            )
        source = metadata.get("source")
        if source is not None:
            if not isinstance(source, dict):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Marketplace 'source' must be a mapping",
                        file_path=item.path,
                    )
                )
            else:
                source_type = source.get("source")
                if source_type not in _known_source_types:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            message=f"Marketplace 'source.source' value "
                            f"'{source_type}' is not a known type "
                            f"({', '.join(sorted(_known_source_types))})",
                            file_path=item.path,
                        )
                    )
        plugins = metadata.get("plugins")
        if plugins is not None:
            if not isinstance(plugins, dict):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Marketplace 'plugins' must be a mapping",
                        file_path=item.path,
                    )
                )
            else:
                for plugin_name, plugin_enabled in plugins.items():
                    if not isinstance(plugin_name, str) or not plugin_name:
                        issues.append(
                            ValidationIssue(
                                level="error",
                                message="Marketplace plugin name must be a "
                                "non-empty string",
                                file_path=item.path,
                            )
                        )
                    elif "@" in plugin_name:
                        issues.append(
                            ValidationIssue(
                                level="error",
                                message=f"Marketplace plugin name "
                                f"'{plugin_name}' must not contain '@'",
                                file_path=item.path,
                            )
                        )
                    if not isinstance(plugin_enabled, bool):
                        # bool("false") is True, so a non-bool value would
                        # silently invert the author's intent at deploy time.
                        issues.append(
                            ValidationIssue(
                                level="error",
                                message=(
                                    f"Marketplace plugin '{plugin_name}' "
                                    f"value must be a boolean"
                                ),
                                file_path=item.path,
                            )
                        )

    # Models-specific validations
    if item.item_type == "models":
        providers = metadata.get("providers")
        if not isinstance(providers, dict) or not providers:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="models.yaml missing or empty 'providers'",
                    file_path=item.path,
                )
            )
        else:
            for prov_key, prov in providers.items():
                if not isinstance(prov, dict):
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Provider '{prov_key}' must be a mapping",
                            file_path=item.path,
                        )
                    )
                    continue
                # display_name is always required.
                if "display_name" not in prov:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Provider '{prov_key}' missing required field 'display_name'",
                            file_path=item.path,
                        )
                    )
                # base_url and api_key are required only when the provider has
                # a droid: or opencode: subsection — those targets actually
                # dispatch HTTP requests. A claude-only provider carries no
                # credentials because Claude Code does not read them from
                # models.yaml.
                if "droid" in prov or "opencode" in prov:
                    for required in ("base_url", "api_key"):
                        if required not in prov:
                            issues.append(
                                ValidationIssue(
                                    level="error",
                                    message=f"Provider '{prov_key}' missing required field '{required}'",
                                    file_path=item.path,
                                )
                            )
                models = prov.get("models")
                if not isinstance(models, dict) or not models:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Provider '{prov_key}' has no models defined",
                            file_path=item.path,
                        )
                    )
                # Validate provider-level only/except
                p_only = prov.get("only")
                p_except = prov.get("except")
                if p_only is not None and p_except is not None:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Provider '{prov_key}': cannot specify both 'only' and 'except'",
                            file_path=item.path,
                        )
                    )
                for env_list, field_name in [(p_only, "only"), (p_except, "except")]:
                    if env_list is not None:
                        if not isinstance(env_list, list):
                            issues.append(
                                ValidationIssue(
                                    level="error",
                                    message=f"Provider '{prov_key}': '{field_name}' must be a list",
                                    file_path=item.path,
                                )
                            )
                        else:
                            for env_id in env_list:
                                if env_id not in valid_ids:
                                    issues.append(
                                        ValidationIssue(
                                            level="error",
                                            message=f"Provider '{prov_key}': invalid environment ID '{env_id}' in '{field_name}'",
                                            file_path=item.path,
                                        )
                                    )
                # Validate provider-level overrides
                p_overrides = prov.get("overrides")
                if p_overrides is not None:
                    if not isinstance(p_overrides, dict):
                        issues.append(
                            ValidationIssue(
                                level="error",
                                message=f"Provider '{prov_key}': 'overrides' must be a mapping",
                                file_path=item.path,
                            )
                        )
                    else:
                        for env_id, override_data in p_overrides.items():
                            if env_id not in valid_ids:
                                issues.append(
                                    ValidationIssue(
                                        level="error",
                                        message=f"Provider '{prov_key}': invalid environment ID '{env_id}' in 'overrides'",
                                        file_path=item.path,
                                    )
                                )
                            if not isinstance(override_data, dict):
                                issues.append(
                                    ValidationIssue(
                                        level="error",
                                        message=f"Provider '{prov_key}': 'overrides.{env_id}' must be a mapping",
                                        file_path=item.path,
                                    )
                                )
                # Validate model-level only/except
                if isinstance(models, dict):
                    for model_id, model in models.items():
                        if not isinstance(model, dict):
                            continue
                        m_only = model.get("only")
                        m_except = model.get("except")
                        if m_only is not None and m_except is not None:
                            issues.append(
                                ValidationIssue(
                                    level="error",
                                    message=f"Model '{model_id}' in '{prov_key}': cannot specify both 'only' and 'except'",
                                    file_path=item.path,
                                )
                            )
                        for env_list, field_name in [
                            (m_only, "only"),
                            (m_except, "except"),
                        ]:
                            if env_list is not None:
                                if not isinstance(env_list, list):
                                    issues.append(
                                        ValidationIssue(
                                            level="error",
                                            message=f"Model '{model_id}' in '{prov_key}': '{field_name}' must be a list",
                                            file_path=item.path,
                                        )
                                    )
                                else:
                                    for env_id in env_list:
                                        if env_id not in valid_ids:
                                            issues.append(
                                                ValidationIssue(
                                                    level="error",
                                                    message=f"Model '{model_id}' in '{prov_key}': invalid environment ID '{env_id}' in '{field_name}'",
                                                    file_path=item.path,
                                                )
                                            )

    return issues
