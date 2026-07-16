"""Fail-closed declarations and source bindings for external bundles."""

from __future__ import annotations

import json
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

BindingKind = Literal["descriptor", "cli"]

_BUNDLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SRI_SHA256 = re.compile(r"sha256-[A-Za-z0-9+/]{43}=\Z")
_STORE_PREFIX = Path("/nix/store")


class BundleError(ValueError):
    """Base class for a bundle configuration error."""


class BundleBindingError(BundleError):
    """A bundle source descriptor or mutable override is invalid."""


class BundleSchemaError(BundleError):
    """A checked bundle declaration is invalid."""


@dataclass(frozen=True)
class BundleDeclaration:
    """One logical bundle named by ``deploy.yaml``."""

    name: str
    manifest_path: Path


@dataclass(frozen=True)
class BundleSourceBinding:
    """Atomic source identity supplied by Nix or a development override."""

    name: str
    source_root: Path
    mutable: bool
    revision: str | None
    nar_hash: str | None
    version: str | None
    binding_kind: BindingKind

    @property
    def source_ref(self) -> str:
        """Return the logical source reference stored in provenance."""
        if self.mutable:
            return "MUTABLE"
        if self.revision is None:
            raise BundleBindingError(
                f"Immutable binding {self.name!r} lacks a revision"
            )
        return self.revision


@dataclass(frozen=True)
class BundleConfig:
    """A checked declaration joined to its authorized source."""

    name: str
    manifest_path: Path
    binding: BundleSourceBinding


def _require_bundle_name(value: object, *, where: str) -> str:
    if not isinstance(value, str) or _BUNDLE_NAME.fullmatch(value) is None:
        raise BundleSchemaError(f"{where} must be a lowercase canonical bundle name")
    return value


def _canonical_relative_path(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise BundleSchemaError(f"{where} must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise BundleSchemaError(f"{where} must be canonical and relative")
    return value


def _string_mapping(value: object, *, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BundleSchemaError(f"{where} must be a mapping with string keys")
    return cast(dict[str, object], value)


def parse_bundle_declarations(
    raw: object,
    *,
    config_directory: Path,
) -> tuple[BundleDeclaration, ...]:
    """Parse and confine the optional ``deploy.yaml`` ``bundles:`` map."""
    if raw is None:
        return ()
    declarations = _string_mapping(raw, where="deploy.yaml bundles")
    try:
        config_root = config_directory.resolve(strict=True)
    except OSError as exc:
        raise BundleSchemaError("deploy.yaml directory is not readable") from exc

    result: list[BundleDeclaration] = []
    for raw_name, raw_declaration in declarations.items():
        name = _require_bundle_name(raw_name, where="bundle name")
        declaration = _string_mapping(
            raw_declaration,
            where=f"bundle {name!r} declaration",
        )
        if set(declaration) != {"manifest"}:
            raise BundleSchemaError(
                f"bundle {name!r} declaration must contain only manifest"
            )
        relative = _canonical_relative_path(
            declaration["manifest"],
            where=f"bundle {name!r} manifest",
        )
        lexical = config_root / Path(PurePosixPath(relative))
        try:
            resolved = lexical.resolve(strict=True)
            mode = resolved.stat().st_mode
        except OSError as exc:
            raise BundleSchemaError(
                f"bundle {name!r} manifest is not safely readable: {relative}"
            ) from exc
        if not resolved.is_relative_to(config_root) or not stat.S_ISREG(mode):
            raise BundleSchemaError(
                f"bundle {name!r} manifest must be a confined regular file"
            )
        result.append(BundleDeclaration(name=name, manifest_path=resolved))
    return tuple(result)


def _resolve_source_root(path: Path, *, name: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise BundleBindingError(
            f"Source for bundle {name!r} is not safely readable: {path}"
        ) from exc
    if not stat.S_ISDIR(mode):
        raise BundleBindingError(f"Source for bundle {name!r} must be a directory")
    return resolved


def parse_bundle_source_overrides(values: Sequence[str]) -> dict[str, Path]:
    """Parse repeated, deliberately mutable ``NAME=ABSOLUTE_PATH`` values."""
    overrides: dict[str, Path] = {}
    for raw in values:
        raw_name, separator, raw_path = raw.partition("=")
        if separator == "" or not raw_name or not raw_path:
            raise BundleBindingError(
                f"Invalid --bundle-source {raw!r}; expected NAME=ABSOLUTE_PATH"
            )
        try:
            name = _require_bundle_name(raw_name, where="bundle override name")
        except BundleSchemaError as exc:
            raise BundleBindingError(str(exc)) from exc
        if name in overrides:
            raise BundleBindingError(f"Duplicate --bundle-source for {name!r}")
        try:
            path = Path(raw_path).expanduser()
        except RuntimeError as exc:
            raise BundleBindingError(
                f"--bundle-source for {name!r} has an unknown home directory"
            ) from exc
        if not path.is_absolute():
            raise BundleBindingError(
                f"--bundle-source for {name!r} must be an absolute path"
            )
        overrides[name] = _resolve_source_root(path, name=name)
    return overrides


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise BundleBindingError(f"Binding JSON has duplicate key {key!r}")
        value[key] = item
    return value


def load_bundle_bindings_file(path: Path) -> dict[str, BundleSourceBinding]:
    """Load a closed schema-1 JSON descriptor of atomic source identities."""
    if not path.is_absolute():
        raise BundleBindingError("Bundle bindings file path must be absolute")
    try:
        resolved_file = path.resolve(strict=True)
        if not stat.S_ISREG(resolved_file.stat().st_mode):
            raise BundleBindingError("Bundle bindings file must be regular")
        raw_text = resolved_file.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise BundleBindingError("Bundle bindings file is not valid UTF-8") from exc
    except OSError as exc:
        raise BundleBindingError("Bundle bindings file is not safely readable") from exc
    try:
        raw = json.loads(raw_text, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as exc:
        raise BundleBindingError("Bundle bindings file is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise BundleBindingError("Bundle bindings file root must be an object")
    root = cast(dict[str, object], raw)
    if set(root) != {"schema", "bindings"}:
        raise BundleBindingError(
            "Bundle bindings file must contain only schema and bindings"
        )
    if type(root["schema"]) is not int or root["schema"] != 1:
        raise BundleBindingError("Bundle bindings file schema must be integer 1")
    raw_bindings = root["bindings"]
    if not isinstance(raw_bindings, dict):
        raise BundleBindingError("Bundle bindings must be an object")

    result: dict[str, BundleSourceBinding] = {}
    for raw_name, raw_binding in raw_bindings.items():
        try:
            name = _require_bundle_name(raw_name, where="binding name")
        except BundleSchemaError as exc:
            raise BundleBindingError(str(exc)) from exc
        if not isinstance(raw_binding, dict):
            raise BundleBindingError(f"Binding {name!r} must be an object")
        binding = cast(dict[str, object], raw_binding)
        allowed = {"path", "revision", "narHash", "version", "mutable"}
        if set(binding) - allowed or not {"path", "mutable"} <= binding.keys():
            raise BundleBindingError(f"Binding {name!r} has missing or unknown fields")
        raw_path = binding["path"]
        if not isinstance(raw_path, str) or not raw_path:
            raise BundleBindingError(f"Binding {name!r} path must be a string")
        source_path = Path(raw_path)
        if not source_path.is_absolute():
            raise BundleBindingError(f"Binding {name!r} path must be absolute")
        source_root = _resolve_source_root(source_path, name=name)

        mutable = binding["mutable"]
        if type(mutable) is not bool:
            raise BundleBindingError(f"Binding {name!r} mutable must be boolean")
        revision_value = binding.get("revision")
        nar_hash_value = binding.get("narHash")
        version_value = binding.get("version")
        if version_value is not None and (
            not isinstance(version_value, str)
            or not version_value
            or version_value != version_value.strip()
        ):
            raise BundleBindingError(
                f"Binding {name!r} version must be a non-empty string or null"
            )

        if mutable:
            if revision_value is not None or nar_hash_value is not None:
                raise BundleBindingError(
                    f"Mutable binding {name!r} may not claim revision or narHash"
                )
            revision = None
            nar_hash = None
        else:
            if (
                not isinstance(revision_value, str)
                or _GIT_REVISION.fullmatch(revision_value) is None
            ):
                raise BundleBindingError(
                    f"Immutable binding {name!r} requires a full lowercase Git revision"
                )
            if (
                not isinstance(nar_hash_value, str)
                or _SRI_SHA256.fullmatch(nar_hash_value) is None
            ):
                raise BundleBindingError(
                    f"Immutable binding {name!r} requires an SRI SHA-256 narHash"
                )
            if not isinstance(version_value, str):
                raise BundleBindingError(
                    f"Immutable binding {name!r} requires a version"
                )
            revision = revision_value
            nar_hash = nar_hash_value

        result[name] = BundleSourceBinding(
            name=name,
            source_root=source_root,
            mutable=mutable,
            revision=revision,
            nar_hash=nar_hash,
            version=version_value,
            binding_kind="descriptor",
        )
    return result


def resolve_bundle_configs(
    declarations: Sequence[BundleDeclaration],
    *,
    descriptor_bindings: Mapping[str, BundleSourceBinding],
    source_overrides: Mapping[str, Path] | None = None,
    require_immutable: bool = False,
) -> tuple[BundleConfig, ...]:
    """Join declarations to descriptor identities or mutable CLI overrides."""
    overrides = source_overrides or {}
    declared_names = {declaration.name for declaration in declarations}
    unknown_overrides = set(overrides) - declared_names
    if unknown_overrides:
        rendered = ", ".join(sorted(unknown_overrides))
        raise BundleBindingError(f"Unknown bundle override(s): {rendered}")

    result: list[BundleConfig] = []
    for declaration in declarations:
        if declaration.name in overrides:
            root = overrides[declaration.name]
            if not root.is_absolute():
                raise BundleBindingError(
                    f"Override for {declaration.name!r} must be absolute"
                )
            binding = BundleSourceBinding(
                name=declaration.name,
                source_root=_resolve_source_root(root, name=declaration.name),
                mutable=True,
                revision=None,
                nar_hash=None,
                version=None,
                binding_kind="cli",
            )
        else:
            try:
                binding = descriptor_bindings[declaration.name]
            except KeyError as exc:
                raise BundleBindingError(
                    f"Bundle {declaration.name!r} has no source binding"
                ) from exc
        if binding.name != declaration.name:
            raise BundleBindingError(
                f"Binding key {declaration.name!r} contains identity {binding.name!r}"
            )
        if require_immutable:
            if binding.mutable:
                raise BundleBindingError(
                    f"Bundle {declaration.name!r} has a mutable source"
                )
            if not binding.source_root.is_relative_to(_STORE_PREFIX):
                raise BundleBindingError(
                    f"Bundle {declaration.name!r} is not bound under /nix/store"
                )
            if binding.revision is None or binding.nar_hash is None:
                raise BundleBindingError(
                    f"Bundle {declaration.name!r} lacks immutable provenance"
                )
        result.append(
            BundleConfig(
                name=declaration.name,
                manifest_path=declaration.manifest_path,
                binding=binding,
            )
        )
    return tuple(result)
