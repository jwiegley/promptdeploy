import os
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml


def current_host() -> str:
    """Return the short, lowercased hostname of the current machine.

    Honours the ``PROMPTDEPLOY_HOST`` environment variable as an override.
    Otherwise derives the name from :func:`socket.gethostname`, stripping
    any trailing domain component (e.g. ``Hera.local`` → ``hera``).
    """
    override = os.environ.get("PROMPTDEPLOY_HOST")
    if override:
        return override.strip().lower()
    name = socket.gethostname()
    # Strip DNS/mDNS suffixes like ".local", ".lan", fully-qualified domains.
    name = name.split(".", 1)[0]
    return name.lower()


@dataclass
class TargetConfig:
    id: str
    type: str  # 'claude', 'codex', 'droid', 'opencode', or 'gptel'
    path: Path
    host: str | None = None
    labels: list[str] = None  # type: ignore[assignment]
    model: str | None = None

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = []


@dataclass
class Config:
    source_root: Path
    targets: dict[str, TargetConfig]
    groups: dict[str, list[str]]


def find_config_file(start_dir: Path | None = None) -> Path:
    if start_dir is None:
        start_dir = Path.cwd()
    current = start_dir.resolve()
    while current != current.parent:
        config_path = current / "deploy.yaml"
        if config_path.exists():
            return config_path
        current = current.parent
    raise FileNotFoundError(
        f"Could not find deploy.yaml in {start_dir} or any parent directory"
    )


def load_config(config_path: Path | None = None) -> Config:
    if config_path is None:
        config_path = find_config_file()
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Top level of {config_path} must be a mapping, got {type(data).__name__}"
        )

    # Expand ``~`` before the absolute-path test so a source_root of
    # ``~/...`` resolves to the home directory instead of being treated
    # as a path relative to deploy.yaml.
    source_root = Path(data.get("source_root", config_path.parent)).expanduser()
    if not source_root.is_absolute():
        source_root = (config_path.parent / source_root).resolve()
    else:
        source_root = source_root.resolve()

    targets = {}
    for target_id, target_data in (data.get("targets") or {}).items():
        host = target_data.get("host")
        path = Path(target_data["path"])
        if host is None:
            path = path.expanduser()
        labels = target_data.get("labels", [])
        model = target_data.get("model")
        targets[target_id] = TargetConfig(
            id=target_id,
            type=target_data["type"],
            path=path,
            host=host,
            labels=labels,
            model=model,
        )

    groups: dict[str, list[str]] = {
        name: list(members or [])
        for name, members in (data.get("groups") or {}).items()
    }

    # Explicit group members must name real targets: groups do not nest,
    # and a typo here would otherwise silently deploy to nothing (or fail
    # later with a bare KeyError).  Label- and host-derived groups are
    # built from the targets themselves, and host groups may be
    # intentionally empty, so only the explicit ``groups:`` mapping is
    # checked.
    for group_name, members in groups.items():
        for member in members:
            if member not in targets:
                raise ValueError(
                    f"Group '{group_name}' in {config_path} references "
                    f"unknown target '{member}' (group members must be "
                    f"target ids)"
                )

    # Auto-generate groups from target labels (merge with explicit groups)
    for target_id, tc in targets.items():
        for label in tc.labels:
            groups.setdefault(label, [])
            if target_id not in groups[label]:
                groups[label].append(target_id)

    # Register every hostname listed under ``hosts:`` as a group, and
    # populate it with every target that deploys to that machine —
    # whether locally (host: matches) or, on the current host, the
    # host-less targets that always deploy to wherever the deploy is
    # invoked from.  This lets models.yaml use ``only: [hera]`` to
    # restrict a model to hera-resident targets regardless of which
    # machine runs the deploy.  ``PROMPTDEPLOY_HOST`` overrides
    # detection of the current host.
    declared_hosts = list(data.get("hosts") or [])
    host_group = current_host()
    for h in declared_hosts:
        existing = groups.setdefault(h, [])
        for tid, tc in targets.items():
            if tc.host == h and tid not in existing:
                existing.append(tid)
    if host_group:
        existing = groups.setdefault(host_group, [])
        for tid, tc in targets.items():
            if tc.host is None and tid not in existing:
                existing.append(tid)

    return Config(source_root=source_root, targets=targets, groups=groups)


def remap_targets_to_root(config: Config, root: Path) -> Config:
    """Return a new Config with all target paths remapped under root.

    Each target's path is replaced with ``root / target_id``, allowing
    deployment to be previewed in a scratch directory without touching real
    configuration files.

    Args:
        config: The original configuration.
        root: The directory under which all targets will be remapped.

    Returns:
        A new :class:`Config` instance with remapped target paths.

    Example::

        new_cfg = remap_targets_to_root(config, Path("/tmp/preview"))
        # config.targets["claude-personal"].path == Path("/tmp/preview/claude-personal")
    """
    new_targets = {}
    for tid, tc in config.targets.items():
        new_targets[tid] = TargetConfig(
            id=tc.id,
            type=tc.type,
            path=root / tid,
            host=None,
            labels=list(tc.labels),
            model=tc.model,
        )
    return Config(
        source_root=config.source_root,
        targets=new_targets,
        groups=config.groups,
    )


def expand_target_arg(targets_arg: list[str] | None, config: Config) -> list[str]:
    if targets_arg is None:
        return list(config.targets.keys())
    result = []
    for t in targets_arg:
        if t in config.groups:
            result.extend(config.groups[t])
        elif t in config.targets:
            result.append(t)
        else:
            raise ValueError(f"Unknown target: {t}")
    # Preserve order while dropping duplicates (e.g. a target named both
    # directly and via a group, or via two overlapping groups).
    return list(dict.fromkeys(result))


def load_anthropic_default_model(models_yaml_path: Path) -> str | None:
    """Read ``providers.anthropic.claude.default_model`` from a models.yaml.

    Returns ``None`` when the file is missing, YAML cannot be parsed, any
    intermediate key is absent, or the final value is not a string. Validation
    of the file's structure is the responsibility of :mod:`promptdeploy.validate`;
    this helper is deliberately permissive so deploy-time can short-circuit
    cleanly when the feature is not configured.
    """
    if not models_yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(models_yaml_path.read_text("utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    anthropic = providers.get("anthropic")
    if not isinstance(anthropic, dict):
        return None
    claude = anthropic.get("claude")
    if not isinstance(claude, dict):
        return None
    default_model = claude.get("default_model")
    if not isinstance(default_model, str):
        return None
    return default_model


def load_anthropic_known_models(models_yaml_path: Path) -> set[str] | None:
    """Return the set of keys under ``providers.anthropic.models`` in a models.yaml.

    Returns ``None`` when the file is missing, cannot be parsed, the top-level
    structure is wrong, or any intermediate key is absent. Returns an empty
    set when ``models:`` is present but empty. Used by
    :mod:`promptdeploy.validate` to surface warnings for unknown model strings;
    treated as the same permissive contract as
    :func:`load_anthropic_default_model`.
    """
    if not models_yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(models_yaml_path.read_text("utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    anthropic = providers.get("anthropic")
    if not isinstance(anthropic, dict):
        return None
    models = anthropic.get("models")
    if not isinstance(models, dict):
        return None
    return set(models.keys())
