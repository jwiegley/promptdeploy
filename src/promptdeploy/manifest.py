"""Manifest tracking for deployed prompts, agents, skills, and MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

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
    entries: list[tuple[str, bytes]] = []
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            fpath = Path(root) / fname
            rel = fpath.relative_to(directory).as_posix()
            entries.append((rel, fpath.read_bytes()))
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
    items: dict[str, dict[str, ManifestItem]] = {}
    for category, entries in data.get("items", {}).items():
        items[category] = {
            name: ManifestItem(
                source_hash=vals.get("source_hash", ""),
                target_path=vals.get("target_path"),
                managed_keys=vals.get("managed_keys"),
            )
            for name, vals in entries.items()
        }
    return Manifest(
        version=version,
        deployed_at=data.get("deployed_at", ""),
        items=items,
    )


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
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
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
