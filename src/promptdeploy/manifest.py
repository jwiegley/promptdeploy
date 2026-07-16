"""Manifest tracking with v1/v2 migration and bundle runtime receipts."""

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

MANIFEST_VERSION = 3
LEGACY_MANIFEST_VERSION = 1
PROVENANCE_MANIFEST_VERSION = 2
MANIFEST_FILENAME = ".prompt-deploy-manifest.json"

_SUPPORTED_MANIFEST_VERSIONS = frozenset(
    {LEGACY_MANIFEST_VERSION, PROVENANCE_MANIFEST_VERSION, MANIFEST_VERSION}
)
_BUNDLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SRI_SHA256 = re.compile(r"sha256-[A-Za-z0-9+/]{43}=\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TRANSFORM_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_CLAUDE_RUNTIME_PATH = re.compile(
    r"\.promptdeploy/bundles/ponytail/runtimes/([0-9a-f]{64})\Z"
)

_TOP_LEVEL_FIELDS = frozenset({"version", "deployed_at", "items"})
_V1_ITEM_FIELDS = frozenset({"source_hash", "target_path", "managed_keys"})
_V2_ITEM_FIELDS = _V1_ITEM_FIELDS | {"source"}
_V3_ITEM_FIELDS = _V2_ITEM_FIELDS | {"bundle_receipt"}
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
_BUNDLE_RECEIPT_FIELDS = frozenset(
    {
        "payload_name",
        "target_type",
        "logical_root",
        "payload_tree_sha256",
        "rendered_tree_sha256",
        "adapter_abi",
        "runtime_path",
        "registration_kind",
        "registration_abi",
        "registration_owner",
        "registration_sha256",
    }
)


class _DuplicateManifestKey(ValueError):
    """A JSON object repeated a key and is therefore ambiguous."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateManifestKey(f"duplicate manifest key {key!r}")
        value[key] = item
    return value


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


@dataclass(frozen=True, slots=True)
class BundleManifestReceipt:
    """Closed persisted witness for one active local Claude Ponytail runtime."""

    payload_name: str
    target_type: str
    logical_root: str
    payload_tree_sha256: str
    rendered_tree_sha256: str
    adapter_abi: str
    runtime_path: str
    registration_kind: str
    registration_owner: str
    registration_abi: str
    registration_sha256: str


@dataclass
class ManifestItem:
    """Tracks a single deployed item."""

    source_hash: str
    target_path: str | None = None
    managed_keys: list[str] | None = None
    source: ManifestSource | None = None
    bundle_receipt: BundleManifestReceipt | None = None


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
    checked = validate_manifest_source(source)
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


def validate_manifest_source(source: ManifestSource) -> ManifestSource:
    """Return checked logical provenance without serializing it."""
    return _validate_manifest_source_values(
        bundle=source.bundle,
        path=source.path,
        version=source.version,
        revision=source.revision,
        nar_hash=source.nar_hash,
        mutable=source.mutable,
        transform=source.transform,
        license_name=source.license,
    )


def _bundle_receipt_from_mapping(value: object) -> BundleManifestReceipt:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Manifest bundle_receipt must be an object")
    receipt = cast(dict[str, object], value)
    if "runtime_path" in receipt:
        _validate_bundle_runtime_path(receipt["runtime_path"])
    if set(receipt) != _BUNDLE_RECEIPT_FIELDS:
        raise ValueError(
            "Exact deployment requires all and only known bundle receipt fields"
        )
    return validate_bundle_manifest_receipt(
        BundleManifestReceipt(
            payload_name=cast(str, receipt["payload_name"]),
            target_type=cast(str, receipt["target_type"]),
            logical_root=cast(str, receipt["logical_root"]),
            payload_tree_sha256=cast(str, receipt["payload_tree_sha256"]),
            rendered_tree_sha256=cast(str, receipt["rendered_tree_sha256"]),
            adapter_abi=cast(str, receipt["adapter_abi"]),
            runtime_path=cast(str, receipt["runtime_path"]),
            registration_kind=cast(str, receipt["registration_kind"]),
            registration_owner=cast(str, receipt["registration_owner"]),
            registration_abi=cast(str, receipt["registration_abi"]),
            registration_sha256=cast(str, receipt["registration_sha256"]),
        )
    )


def _validate_bundle_runtime_path(value: object) -> re.Match[str]:
    if type(value) is not str:
        raise ValueError("Manifest bundle_receipt fields must be exact strings")
    runtime_match = _CLAUDE_RUNTIME_PATH.fullmatch(value)
    if runtime_match is None:
        raise UnsafeManifestError(
            "Manifest bundle receipt runtime path is outside the owned namespace"
        )
    return runtime_match


def validate_bundle_manifest_receipt(
    receipt: BundleManifestReceipt,
) -> BundleManifestReceipt:
    """Return one exact, fully validated local-Claude runtime witness."""
    if type(receipt) is not BundleManifestReceipt:
        raise ValueError(
            "Manifest bundle_receipt must be an exact BundleManifestReceipt"
        )
    runtime_match = _validate_bundle_runtime_path(receipt.runtime_path)
    fields = (
        receipt.payload_name,
        receipt.target_type,
        receipt.logical_root,
        receipt.payload_tree_sha256,
        receipt.rendered_tree_sha256,
        receipt.adapter_abi,
        receipt.runtime_path,
        receipt.registration_kind,
        receipt.registration_owner,
        receipt.registration_abi,
        receipt.registration_sha256,
    )
    if any(type(value) is not str for value in fields):
        raise ValueError("Manifest bundle_receipt fields must be exact strings")
    expected_values = (
        (receipt.payload_name, "claude-codex-runtime-v1", "payload name"),
        (receipt.target_type, "claude", "target type"),
        (receipt.logical_root, "runtime/claude-codex", "logical root"),
        (receipt.adapter_abi, "ponytail-claude-runtime-v1", "adapter ABI"),
        (receipt.registration_kind, "claude-hooks", "registration kind"),
        (receipt.registration_owner, "bundle:ponytail", "registration owner"),
        (
            receipt.registration_abi,
            "claude-settings-hooks-v1",
            "registration ABI",
        ),
    )
    for value, expected, field_name in expected_values:
        if value != expected:
            raise ValueError(
                f"Manifest bundle receipt {field_name} must be {expected!r}"
            )
    for value, field_name in (
        (receipt.payload_tree_sha256, "payload digest"),
        (receipt.rendered_tree_sha256, "rendered tree digest"),
        (receipt.registration_sha256, "registration digest"),
    ):
        if _SHA256.fullmatch(value) is None:
            raise ValueError(
                f"Manifest bundle receipt {field_name} must be lowercase SHA-256"
            )
    if runtime_match.group(1) != receipt.rendered_tree_sha256.removeprefix("sha256:"):
        raise ValueError(
            "Manifest bundle receipt runtime path disagrees with rendered digest"
        )
    return receipt


def _bundle_receipt_to_mapping(
    receipt: BundleManifestReceipt,
) -> dict[str, object]:
    checked = validate_bundle_manifest_receipt(receipt)
    return {
        "payload_name": checked.payload_name,
        "target_type": checked.target_type,
        "logical_root": checked.logical_root,
        "payload_tree_sha256": checked.payload_tree_sha256,
        "rendered_tree_sha256": checked.rendered_tree_sha256,
        "adapter_abi": checked.adapter_abi,
        "runtime_path": checked.runtime_path,
        "registration_kind": checked.registration_kind,
        "registration_owner": checked.registration_owner,
        "registration_abi": checked.registration_abi,
        "registration_sha256": checked.registration_sha256,
    }


def _validate_bundle_receipt_ownership(
    *,
    category: str,
    name: str,
    target_path: str | None,
    receipt: BundleManifestReceipt | None,
) -> None:
    if receipt is None:
        return
    validate_bundle_manifest_receipt(receipt)
    if type(category) is not str or type(name) is not str:
        raise ValueError(
            "Manifest receipt ownership requires exact category and item strings"
        )
    if target_path is not None and type(target_path) is not str:
        raise ValueError("Manifest receipt ownership requires an exact target_path")
    if category != "bundles" or name != "ponytail":
        raise ValueError("Manifest bundle_receipt is allowed only on bundles:ponytail")
    if target_path != receipt.runtime_path:
        raise ValueError(
            "Manifest bundle receipt runtime path disagrees with target_path"
        )


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
            receipt_present = "bundle_receipt" in values
            receipt_value = values.get("bundle_receipt")
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
            bundle_receipt = (
                _bundle_receipt_from_mapping(receipt_value)
                if version == MANIFEST_VERSION and receipt_present
                else None
            )
            _validate_bundle_receipt_ownership(
                category=category,
                name=name,
                target_path=target_path,
                receipt=bundle_receipt,
            )
            converted[name] = ManifestItem(
                source_hash=source_hash,
                target_path=target_path,
                managed_keys=managed_keys,
                source=source,
                bundle_receipt=bundle_receipt,
            )
        items[category] = converted
    deployed_at = data.get("deployed_at", "")
    if not isinstance(deployed_at, str):
        raise ValueError("Manifest deployed_at must be a string")
    return Manifest(version=version, deployed_at=deployed_at, items=items)


def load_manifest_strict(manifest_path: Path) -> Manifest:
    """Load a strict known v1/v2/v3 manifest for exact deployment or verify."""
    if not manifest_path.exists():
        return Manifest()
    try:
        data = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except _DuplicateManifestKey as exc:
        raise ValueError("Exact deployment refuses duplicate manifest keys") from exc
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
    if "items" not in raw:
        raise ValueError("Exact deployment requires object manifest items")
    raw_items = raw["items"]
    if not isinstance(raw_items, dict):
        raise ValueError("Exact deployment requires object manifest items")
    if version == LEGACY_MANIFEST_VERSION:
        allowed_item_fields = _V1_ITEM_FIELDS
    elif version == PROVENANCE_MANIFEST_VERSION:
        allowed_item_fields = _V2_ITEM_FIELDS
    else:
        allowed_item_fields = _V3_ITEM_FIELDS
    for category, entries in raw_items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Exact deployment requires object manifest categories")
        for name, values in entries.items():
            if not isinstance(name, str) or not isinstance(values, dict):
                raise ValueError("Exact deployment requires object manifest entries")
            if "source_hash" not in values:
                raise ValueError(
                    "Exact deployment requires source_hash on every manifest item"
                )
            if set(values) - allowed_item_fields:
                raise ValueError(
                    "Exact deployment refuses unknown manifest item fields"
                )
            if "bundle_receipt" in values and (
                category != "bundles" or name != "ponytail"
            ):
                raise ValueError(
                    "Exact deployment permits bundle_receipt only on bundles:ponytail"
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
        data = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        _DuplicateManifestKey,
    ) as exc:
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
    """Atomically write a version-3 manifest, migrating loaded v1/v2 entries."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    serialized: dict[str, dict[str, dict[str, object]]] = {}
    for category, entries in manifest.items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Manifest categories must be objects")
        serialized[category] = {}
        for name, item in entries.items():
            if not isinstance(name, str) or type(item) is not ManifestItem:
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
            receipt = item.bundle_receipt
            _validate_bundle_receipt_ownership(
                category=category,
                name=name,
                target_path=target_path,
                receipt=receipt,
            )
            if receipt is not None:
                entry["bundle_receipt"] = _bundle_receipt_to_mapping(receipt)
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
    expected_receipt: BundleManifestReceipt | None = None,
) -> bool:
    """Compare the effective hash, logical provenance, and bundle receipt.

    A v1 entry's missing source is an implicit match only for a primary item
    (``expected_source is None``).  An import always requires exact source
    provenance even when its content hash happens to match.
    """
    checked_expected: BundleManifestReceipt | None = None
    if expected_receipt is not None:
        checked_expected = validate_bundle_manifest_receipt(expected_receipt)
        _validate_bundle_receipt_ownership(
            category=category,
            name=name,
            target_path=checked_expected.runtime_path,
            receipt=checked_expected,
        )
    category_items = manifest.items.get(category)
    if category_items is None:
        return True
    item = category_items.get(name)
    if item is None:
        return True
    if type(item) is not ManifestItem:
        raise ValueError("Manifest currentness requires an exact ManifestItem")
    receipt = item.bundle_receipt
    _validate_bundle_receipt_ownership(
        category=category,
        name=name,
        target_path=item.target_path,
        receipt=receipt,
    )
    return (
        item.source_hash != current_hash
        or item.source != expected_source
        or receipt != checked_expected
    )
