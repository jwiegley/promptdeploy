"""Manifest tracking with v1 migration and logical bundle provenance."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast

from .names import require_canonical_item_name
from .skilltree import scan_skill_source

MANIFEST_VERSION = 2
LEGACY_MANIFEST_VERSION = 1
MANIFEST_FILENAME = ".prompt-deploy-manifest.json"

_SUPPORTED_MANIFEST_VERSIONS = frozenset({LEGACY_MANIFEST_VERSION, MANIFEST_VERSION})
_BUNDLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SRI_SHA256 = re.compile(r"sha256-[A-Za-z0-9+/]{43}=\Z")
_TRANSFORM_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")

_TOP_LEVEL_FIELDS = frozenset({"version", "deployed_at", "items"})
_V1_ITEM_FIELDS = frozenset({"source_hash", "target_path", "managed_keys"})
_V2_ITEM_FIELDS = _V1_ITEM_FIELDS | {"source"}
_SOURCE_FIELDS = frozenset(
    {
        "bundle",
        "path",
        "version",
        "revision",
        "narHash",
        "mutable",
        "transform",
        "license",
    }
)


@dataclass(frozen=True)
class ManifestSource:
    """Logical origin of one imported manifest item.

    ``revision`` and ``nar_hash`` are null for a deliberately mutable binding
    and required for an immutable binding.  ``transform`` is null for a
    verbatim import and names the closed transform for a projection.
    """

    bundle: str
    path: str
    version: str
    revision: str | None
    nar_hash: str | None
    mutable: bool
    transform: str | None
    license: str


@dataclass
class ManifestItem:
    """Tracks a single deployed item."""

    source_hash: str
    target_path: str | None = None
    managed_keys: list[str] | None = None
    source: ManifestSource | None = None


@dataclass
class Manifest:
    """Tracks all deployed items across categories.

    Categories are created on demand (``items.setdefault``); an empty
    manifest carries no pre-seeded category keys.
    """

    version: int = MANIFEST_VERSION
    deployed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    items: dict[str, dict[str, ManifestItem]] = field(default_factory=dict)


def compute_file_hash(content: bytes) -> str:
    """Compute SHA256 hash of file content."""
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def compute_directory_hash(directory: Path) -> str:
    """Compute the unchanged primary-source directory hash.

    Imported trees need the stronger node/mode/empty-directory framing from
    the bundle layer.  This function intentionally retains the version-1
    algorithm so bundle-free primary hashes do not churn.
    """
    h = hashlib.sha256()
    _root, validated_files = scan_skill_source(directory)
    entries = [(relative, path.read_bytes()) for relative, path in validated_files]
    entries.sort(key=lambda entry: entry[0])
    for rel_path, content in entries:
        encoded = rel_path.encode()
        h.update(len(encoded).to_bytes(8, "big"))
        h.update(encoded)
        h.update(len(content).to_bytes(8, "big"))
        h.update(content)
    return f"sha256:{h.hexdigest()}"


def _fallback_manifest(manifest_path: Path, reason: str) -> Manifest:
    """Warn and return an empty manifest for rebuildable-cache corruption."""
    print(
        f"WARNING: ignoring unreadable manifest {manifest_path} ({reason}); "
        f"treating all items as new",
        file=sys.stderr,
    )
    return Manifest()


class UnsafeManifestError(ValueError):
    """Raised when a manifest could steer a filesystem operation unsafely."""


_PATH_CATEGORIES = {
    "agents": "agent",
    "commands": "command",
    "skills": "skill",
    "prompts": "prompt",
    "bundles": "bundle",
}


def _has_forbidden_text(value: str) -> bool:
    return value != unicodedata.normalize("NFC", value) or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in value
    )


def _validate_manifest_item_name(category: str, name: str) -> None:
    item_type = _PATH_CATEGORIES.get(category)
    if item_type is None:
        return
    if item_type == "bundle":
        if _BUNDLE_NAME.fullmatch(name) is None:
            raise UnsafeManifestError(
                "Unsafe bundle name: expected one lowercase canonical path component"
            )
        return
    try:
        require_canonical_item_name(item_type, name)
    except ValueError as exc:
        raise UnsafeManifestError(str(exc)) from exc


def _validate_manifest_target_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Manifest target_path must be a string or null")
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in parts)
        or _has_forbidden_text(value)
    ):
        raise UnsafeManifestError(
            "Manifest target_path is not a confined relative path"
        )
    return value


def _validate_source_bundle(value: object) -> str:
    if not isinstance(value, str) or _BUNDLE_NAME.fullmatch(value) is None:
        raise UnsafeManifestError(
            "Manifest source bundle is not a lowercase canonical name"
        )
    return value


def _validate_source_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise UnsafeManifestError(
            "Manifest source path is not a canonical relative path"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or _has_forbidden_text(value)
    ):
        raise UnsafeManifestError(
            "Manifest source path is not a canonical relative path"
        )
    return value


def _validate_nonempty_text(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _has_forbidden_text(value)
    ):
        raise ValueError(
            f"Manifest source {field_name} must be a canonical non-empty string"
        )
    return value


def _validate_manifest_source_values(
    *,
    bundle: object,
    path: object,
    version: object,
    revision: object,
    nar_hash: object,
    mutable: object,
    transform: object,
    license_name: object,
) -> ManifestSource:
    checked_bundle = _validate_source_bundle(bundle)
    checked_path = _validate_source_path(path)
    checked_version = _validate_nonempty_text(version, field_name="version")
    checked_license = _validate_nonempty_text(license_name, field_name="license")

    if type(mutable) is not bool:
        raise ValueError("Manifest source mutable must be boolean")
    if mutable:
        if revision is not None or nar_hash is not None:
            raise ValueError(
                "Mutable manifest source may not claim revision or narHash"
            )
        checked_revision = None
        checked_nar_hash = None
    else:
        if not isinstance(revision, str) or _GIT_REVISION.fullmatch(revision) is None:
            raise ValueError(
                "Immutable manifest source requires a full lowercase Git revision"
            )
        if not isinstance(nar_hash, str) or _SRI_SHA256.fullmatch(nar_hash) is None:
            raise ValueError(
                "Immutable manifest source requires an SRI SHA-256 narHash"
            )
        checked_revision = revision
        checked_nar_hash = nar_hash

    if transform is None:
        checked_transform = None
    elif isinstance(transform, str) and _TRANSFORM_ID.fullmatch(transform) is not None:
        checked_transform = transform
    else:
        raise ValueError(
            "Manifest source transform must be a canonical identifier or null"
        )

    return ManifestSource(
        bundle=checked_bundle,
        path=checked_path,
        version=checked_version,
        revision=checked_revision,
        nar_hash=checked_nar_hash,
        mutable=mutable,
        transform=checked_transform,
        license=checked_license,
    )


def _manifest_source_from_mapping(
    value: object, *, strict: bool
) -> ManifestSource | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Manifest source must be an object or null")
    source = cast(dict[str, object], value)
    if strict and set(source) != _SOURCE_FIELDS:
        raise ValueError(
            "Exact deployment requires all and only known manifest source fields"
        )
    if not set(source) >= _SOURCE_FIELDS:
        raise ValueError("Manifest source has missing required fields")
    return _validate_manifest_source_values(
        bundle=source["bundle"],
        path=source["path"],
        version=source["version"],
        revision=source["revision"],
        nar_hash=source["narHash"],
        mutable=source["mutable"],
        transform=source["transform"],
        license_name=source["license"],
    )


def _source_to_mapping(source: ManifestSource) -> dict[str, object]:
    checked = _validate_manifest_source_values(
        bundle=source.bundle,
        path=source.path,
        version=source.version,
        revision=source.revision,
        nar_hash=source.nar_hash,
        mutable=source.mutable,
        transform=source.transform,
        license_name=source.license,
    )
    return {
        "bundle": checked.bundle,
        "path": checked.path,
        "version": checked.version,
        "revision": checked.revision,
        "narHash": checked.nar_hash,
        "mutable": checked.mutable,
        "transform": checked.transform,
        "license": checked.license,
    }


def _manifest_version(data: dict[str, object]) -> int:
    version = data.get("version", LEGACY_MANIFEST_VERSION)
    if type(version) is not int:
        raise ValueError("Manifest version must be an integer")
    return version


def _manifest_from_mapping(
    data: dict[str, object], *, version: int, strict: bool = False
) -> Manifest:
    raw_items = data.get("items", {})
    if not isinstance(raw_items, dict):
        raise ValueError("Manifest items must be an object")
    items: dict[str, dict[str, ManifestItem]] = {}
    for category, entries in raw_items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Manifest categories must be objects")
        converted: dict[str, ManifestItem] = {}
        for name, values in entries.items():
            if not isinstance(name, str) or not isinstance(values, dict):
                raise ValueError("Manifest entries must be named objects")
            _validate_manifest_item_name(category, name)
            source_hash = values.get("source_hash", "")
            target_path = values.get("target_path")
            managed_keys = values.get("managed_keys")
            source_value = values.get("source")
            if not isinstance(source_hash, str):
                raise ValueError("Manifest source_hash must be a string")
            if strict or target_path is not None:
                target_path = _validate_manifest_target_path(target_path)
            if managed_keys is not None and (
                not isinstance(managed_keys, list)
                or not all(isinstance(key, str) for key in managed_keys)
            ):
                raise ValueError(
                    "Manifest managed_keys must be a list of strings or null"
                )
            source = _manifest_source_from_mapping(source_value, strict=strict)
            converted[name] = ManifestItem(
                source_hash=source_hash,
                target_path=target_path,
                managed_keys=managed_keys,
                source=source,
            )
        items[category] = converted
    deployed_at = data.get("deployed_at", "")
    if not isinstance(deployed_at, str):
        raise ValueError("Manifest deployed_at must be a string")
    return Manifest(version=version, deployed_at=deployed_at, items=items)


def load_manifest_strict(manifest_path: Path) -> Manifest:
    """Load a strict known v1/v2 manifest for exact deployment or verify."""
    if not manifest_path.exists():
        return Manifest()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Exact deployment requires a readable manifest") from exc
    if not isinstance(data, dict):
        raise ValueError("Exact deployment requires an object manifest")
    raw = cast(dict[str, object], data)
    if set(raw) - _TOP_LEVEL_FIELDS:
        raise ValueError("Exact deployment refuses unknown manifest fields")
    try:
        version = _manifest_version(raw)
    except ValueError as exc:
        raise ValueError(
            "Exact deployment refuses an unsupported manifest version"
        ) from exc
    if version not in _SUPPORTED_MANIFEST_VERSIONS:
        raise ValueError("Exact deployment refuses an unsupported manifest version")
    raw_items = raw.get("items", {})
    if not isinstance(raw_items, dict):
        raise ValueError("Exact deployment requires object manifest items")
    allowed_item_fields = (
        _V1_ITEM_FIELDS if version == LEGACY_MANIFEST_VERSION else _V2_ITEM_FIELDS
    )
    for category, entries in raw_items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Exact deployment requires object manifest categories")
        for name, values in entries.items():
            if not isinstance(name, str) or not isinstance(values, dict):
                raise ValueError("Exact deployment requires object manifest entries")
            if set(values) - allowed_item_fields:
                raise ValueError(
                    "Exact deployment refuses unknown manifest item fields"
                )
    return _manifest_from_mapping(raw, version=version, strict=True)


def load_manifest(manifest_path: Path) -> Manifest:
    """Load the rebuildable manifest cache without weakening path safety.

    Malformed, non-path state falls back to empty.  Item names, target paths,
    bundle IDs, and logical bundle paths always fail closed, including in
    future-version manifests whose other unknown fields are ignored.
    """
    if not manifest_path.exists():
        return Manifest()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fallback_manifest(manifest_path, str(exc))
    if not isinstance(data, dict):
        return _fallback_manifest(manifest_path, "top level is not a JSON object")
    raw = cast(dict[str, object], data)
    try:
        version = _manifest_version(raw)
        if version not in _SUPPORTED_MANIFEST_VERSIONS:
            print(
                f"WARNING: manifest {manifest_path} has version {version!r} "
                f"(expected {MANIFEST_VERSION}); loading known fields only",
                file=sys.stderr,
            )
        return _manifest_from_mapping(raw, version=version)
    except UnsafeManifestError:
        raise
    except ValueError as exc:
        return _fallback_manifest(manifest_path, str(exc))


def save_manifest(manifest: Manifest, manifest_path: Path) -> None:
    """Atomically write a version-2 manifest, migrating loaded v1 entries."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    serialized: dict[str, dict[str, dict[str, object]]] = {}
    for category, entries in manifest.items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Manifest categories must be objects")
        serialized[category] = {}
        for name, item in entries.items():
            if not isinstance(name, str) or not isinstance(item, ManifestItem):
                raise ValueError("Manifest entries must be named ManifestItem objects")
            _validate_manifest_item_name(category, name)
            if not isinstance(item.source_hash, str):
                raise ValueError("Manifest source_hash must be a string")
            target_path = _validate_manifest_target_path(item.target_path)
            if item.managed_keys is not None and (
                not isinstance(item.managed_keys, list)
                or not all(isinstance(key, str) for key in item.managed_keys)
            ):
                raise ValueError(
                    "Manifest managed_keys must be a list of strings or null"
                )
            entry: dict[str, object] = {"source_hash": item.source_hash}
            if target_path is not None:
                entry["target_path"] = target_path
            if item.managed_keys is not None:
                entry["managed_keys"] = item.managed_keys
            if item.source is not None:
                entry["source"] = _source_to_mapping(item.source)
            serialized[category][name] = entry

    if not isinstance(manifest.deployed_at, str):
        raise ValueError("Manifest deployed_at must be a string")
    data = {
        "version": MANIFEST_VERSION,
        "deployed_at": manifest.deployed_at,
        "items": serialized,
    }

    fd, tmp_path = tempfile.mkstemp(dir=manifest_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, manifest_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def has_changed(
    manifest: Manifest, category: str, name: str, current_hash: str
) -> bool:
    """Check whether an item is new or its hash has changed."""
    category_items = manifest.items.get(category)
    if category_items is None:
        return True
    item = category_items.get(name)
    if item is None:
        return True
    return item.source_hash != current_hash


def has_item_changed(
    manifest: Manifest,
    category: str,
    name: str,
    current_hash: str,
    expected_source: ManifestSource | None,
) -> bool:
    """Compare the effective hash and expected logical provenance.

    A v1 entry's missing source is an implicit match only for a primary item
    (``expected_source is None``).  An import always requires exact source
    provenance even when its content hash happens to match.
    """
    if has_changed(manifest, category, name, current_hash):
        return True
    item = manifest.items[category][name]
    return item.source != expected_source
