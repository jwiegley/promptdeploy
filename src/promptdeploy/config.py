import os
import re
import socket
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .bundles import (
    BundleBindingError,
    BundleConfig,
    load_bundle_bindings_file,
    parse_bundle_declarations,
    parse_bundle_source_overrides,
    resolve_bundle_configs,
)
from .yamlutil import load_unique_yaml

_TARGET_ROOT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


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
    # True when this config was produced by remap_targets_to_root() for a
    # --target-root preview. Preview deploys must never bake expanded
    # secrets into the user-chosen preview directory, so claude targets
    # write ${VAR} references verbatim instead of strict-expanding them.
    preview: bool = False

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = []


def target_is_local(target: TargetConfig, runtime_host: str | None = None) -> bool:
    """Return whether TARGET can be opened without an SSH-backed wrapper."""
    host = current_host() if runtime_host is None else runtime_host
    return target.host is None or target.host == host


def filter_local_target_ids(
    config: "Config",
    target_ids: list[str],
    *,
    runtime_host: str,
) -> list[str]:
    """Keep only targets whose runtime path is local to RUNTIME_HOST."""
    return [
        target_id
        for target_id in target_ids
        if target_is_local(config.targets[target_id], runtime_host)
    ]


@dataclass
class Config:
    source_root: Path
    targets: dict[str, TargetConfig]
    groups: dict[str, list[str]]
    bundles: tuple[BundleConfig, ...] = field(default_factory=tuple)


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


def load_config(
    config_path: Path | None = None,
    *,
    bundle_bindings_file: Path | None = None,
    bundle_source_overrides: Sequence[str] = (),
    require_immutable_bundles: bool = False,
) -> Config:
    if config_path is None:
        config_path = find_config_file()
    with open(config_path, encoding="utf-8") as f:
        try:
            data = load_unique_yaml(f.read()) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc
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

    declarations = parse_bundle_declarations(
        data.get("bundles"),
        config_directory=config_path.parent,
    )
    overrides = parse_bundle_source_overrides(bundle_source_overrides)
    if declarations:
        descriptor_path = bundle_bindings_file
        if descriptor_path is None:
            raw_descriptor_path = os.environ.get("PROMPTDEPLOY_BUNDLE_BINDINGS_FILE")
            if raw_descriptor_path:
                try:
                    descriptor_path = Path(raw_descriptor_path).expanduser()
                except RuntimeError as exc:
                    raise BundleBindingError(
                        "PROMPTDEPLOY_BUNDLE_BINDINGS_FILE has an unknown "
                        "home directory"
                    ) from exc
        descriptor_bindings = (
            load_bundle_bindings_file(descriptor_path)
            if descriptor_path is not None
            else {}
        )
        bundles = resolve_bundle_configs(
            declarations,
            descriptor_bindings=descriptor_bindings,
            source_overrides=overrides,
            require_immutable=require_immutable_bundles,
        )
    else:
        if overrides:
            resolve_bundle_configs(
                (),
                descriptor_bindings={},
                source_overrides=overrides,
            )
        bundles = ()

    return Config(
        source_root=source_root,
        targets=targets,
        groups=groups,
        bundles=bundles,
    )


def remap_targets_to_root(config: Config, root: Path) -> Config:
    """Return a new Config with all target paths remapped under root.

    Each target's path is replaced with ``root / target_id``, allowing
    deployment to be previewed in a scratch directory without touching real
    configuration files. The remapped targets are marked ``preview=True``
    so secret-bearing ``${VAR}`` references are written verbatim rather
    than expanded into the preview directory.

    Args:
        config: The original configuration.
        root: The directory under which all targets will be remapped.

    Returns:
        A new :class:`Config` instance with remapped target paths.

    Example::

        new_cfg = remap_targets_to_root(config, Path("/tmp/preview"))
        # config.targets["claude-personal"].path == Path("/tmp/preview/claude-personal")
    """
    try:
        expanded_root = root.expanduser()
    except RuntimeError as exc:
        raise ValueError("Target root contains an unknown home directory") from exc
    if expanded_root.is_symlink():
        raise ValueError(f"Target root must not be a symlink: {expanded_root}")
    lexical_root = expanded_root.absolute()
    for parent in lexical_root.parents:
        try:
            parent_mode = parent.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(parent_mode):
            raise ValueError(f"Target root parent must not be a symlink: {parent}")
    resolved_root = expanded_root.resolve()
    if resolved_root.exists() and not resolved_root.is_dir():
        raise ValueError(f"Target root is not a directory: {resolved_root}")

    def safe_target_path(target_id: str) -> Path:
        if not isinstance(target_id, str):
            raise ValueError("Target IDs used with --target-root must be strings")
        if _TARGET_ROOT_ID.fullmatch(target_id) is None:
            raise ValueError(
                f"Unsafe target ID for --target-root: {target_id!r}; "
                "expected one lowercase ASCII path component"
            )
        target_path = resolved_root / target_id
        if target_path.is_symlink():
            raise ValueError(f"Unsafe preview target path is a symlink: {target_path}")
        if target_path.exists() and not target_path.is_dir():
            raise ValueError(
                f"Unsafe preview target path is not a directory: {target_path}"
            )
        if target_path.exists():
            pending = [target_path]
            while pending:
                directory = pending.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        entry_stat = entry.stat(follow_symlinks=False)
                        entry_path = Path(entry.path)
                        if stat.S_ISLNK(entry_stat.st_mode):
                            raise ValueError(
                                "Unsafe preview target tree contains a symlink: "
                                f"{entry_path}"
                            )
                        if stat.S_ISDIR(entry_stat.st_mode):
                            pending.append(entry_path)
                        elif stat.S_ISREG(entry_stat.st_mode):
                            if entry_stat.st_nlink != 1:
                                raise ValueError(
                                    "Unsafe preview target tree contains a hard-linked "
                                    f"file: {entry_path}"
                                )
                        else:
                            raise ValueError(
                                "Unsafe preview target tree contains a non-regular "
                                f"file: {entry_path}"
                            )
        return target_path

    new_targets = {}
    for tid, tc in config.targets.items():
        target_path = safe_target_path(tid)
        new_targets[tid] = TargetConfig(
            id=tc.id,
            type=tc.type,
            path=target_path,
            host=None,
            labels=list(tc.labels),
            model=tc.model,
            preview=True,
        )
    return Config(
        source_root=config.source_root,
        targets=new_targets,
        groups=config.groups,
        bundles=config.bundles,
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
