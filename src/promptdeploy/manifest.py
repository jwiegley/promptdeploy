"""Manifest tracking for deployed prompts, agents, skills, and MCP servers."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .names import require_canonical_item_name
from .skilltree import scan_skill_source

MANIFEST_VERSION = 1
MANIFEST_FILENAME = ".prompt-deploy-manifest.json"


@dataclass
class ManifestItem:
    """Tracks a single deployed item."""

    source_hash: str
    target_path: str | None = None
    managed_keys: list[str] | None = None


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
    """Compute a deterministic hash of a directory's contents.

    Files are sorted by relative path. Each file contributes its
    relative path and content to the hash, each prefixed with its byte
    length so that the path/content boundaries are unambiguous --
    without framing, ``("ab", b"c")`` and ``("a", b"bc")`` would hash
    identically.
    """
    h = hashlib.sha256()
    _root, validated_files = scan_skill_source(directory)
    entries = [(relative, path.read_bytes()) for relative, path in validated_files]
    entries.sort(key=lambda e: e[0])
    for rel_path, content in entries:
        encoded = rel_path.encode()
        h.update(len(encoded).to_bytes(8, "big"))
        h.update(encoded)
        h.update(len(content).to_bytes(8, "big"))
        h.update(content)
    return f"sha256:{h.hexdigest()}"


def _fallback_manifest(manifest_path: Path, reason: str) -> Manifest:
    """Warn and return an empty manifest.

    The manifest is a rebuildable cache of what was deployed; when it cannot
    be read, the safe fallback is to treat every item as new rather than
    abort the deploy.
    """
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
}


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
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise UnsafeManifestError(
            "Manifest target_path is not a confined relative path"
        )
    return value


def _manifest_from_mapping(
    data: dict[str, object], *, strict: bool = False
) -> Manifest:
    version = data.get("version", MANIFEST_VERSION)
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
            item_type = _PATH_CATEGORIES.get(category)
            if item_type is not None:
                try:
                    require_canonical_item_name(item_type, name)
                except ValueError as exc:
                    raise UnsafeManifestError(str(exc)) from exc
            source_hash = values.get("source_hash", "")
            target_path = values.get("target_path")
            managed_keys = values.get("managed_keys")
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
            converted[name] = ManifestItem(
                source_hash=source_hash,
                target_path=target_path,
                managed_keys=managed_keys,
            )
        items[category] = converted
    deployed_at = data.get("deployed_at", "")
    if not isinstance(deployed_at, str):
        raise ValueError("Manifest deployed_at must be a string")
    return Manifest(
        version=version if isinstance(version, int) else MANIFEST_VERSION,
        deployed_at=deployed_at,
        items=items,
    )


def load_manifest_strict(manifest_path: Path) -> Manifest:
    """Load a manifest without discarding any state exact deploy might rewrite."""
    if not manifest_path.exists():
        return Manifest()
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Exact deployment requires a readable manifest") from exc
    if not isinstance(data, dict):
        raise ValueError("Exact deployment requires an object manifest")
    unknown_top = set(data) - {"version", "deployed_at", "items"}
    if unknown_top:
        raise ValueError("Exact deployment refuses unknown manifest fields")
    version = data.get("version", MANIFEST_VERSION)
    if type(version) is not int or version != MANIFEST_VERSION:
        raise ValueError("Exact deployment refuses an unsupported manifest version")
    raw_items = data.get("items", {})
    if not isinstance(raw_items, dict):
        raise ValueError("Exact deployment requires object manifest items")
    for category, entries in raw_items.items():
        if not isinstance(category, str) or not isinstance(entries, dict):
            raise ValueError("Exact deployment requires object manifest categories")
        for name, values in entries.items():
            if not isinstance(name, str) or not isinstance(values, dict):
                raise ValueError("Exact deployment requires object manifest entries")
            if set(values) - {"source_hash", "target_path", "managed_keys"}:
                raise ValueError(
                    "Exact deployment refuses unknown manifest item fields"
                )
    return _manifest_from_mapping(data, strict=True)


def load_manifest(manifest_path: Path) -> Manifest:
    """Load a manifest from disk, returning an empty one if it doesn't exist.

    A corrupt or non-mapping manifest falls back to an empty one with a
    warning. Items are constructed from known fields only, so manifests
    written by a newer promptdeploy (with extra fields or a higher version)
    still load.
    """
    if not manifest_path.exists():
        return Manifest()
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fallback_manifest(manifest_path, str(exc))
    if not isinstance(data, dict):
        return _fallback_manifest(manifest_path, "top level is not a JSON object")
    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        print(
            f"WARNING: manifest {manifest_path} has version {version!r} "
            f"(expected {MANIFEST_VERSION}); loading known fields only",
            file=sys.stderr,
        )
    try:
        return _manifest_from_mapping(data)
    except UnsafeManifestError:
        raise
    except ValueError as exc:
        return _fallback_manifest(manifest_path, str(exc))


def save_manifest(manifest: Manifest, manifest_path: Path) -> None:
    """Atomically write a manifest to disk."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    serialized: dict[str, dict[str, dict[str, object]]] = {}
    for category, entries in manifest.items.items():
        serialized[category] = {}
        for name, item in entries.items():
            entry: dict[str, object] = {"source_hash": item.source_hash}
            if item.target_path is not None:
                entry["target_path"] = item.target_path
            if item.managed_keys is not None:
                entry["managed_keys"] = item.managed_keys
            serialized[category][name] = entry

    data = {
        "version": manifest.version,
        "deployed_at": manifest.deployed_at,
        "items": serialized,
    }

    fd, tmp_path = tempfile.mkstemp(dir=manifest_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, manifest_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def has_changed(
    manifest: Manifest, category: str, name: str, current_hash: str
) -> bool:
    """Check whether an item is new or its hash has changed."""
    cat = manifest.items.get(category)
    if cat is None:
        return True
    item = cat.get(name)
    if item is None:
        return True
    return item.source_hash != current_hash
