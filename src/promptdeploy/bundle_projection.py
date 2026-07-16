"""Pure target-specific projection for accepted Ponytail bundle snapshots.

This module deliberately contains no target filesystem access. Claude/Codex
registration semantics enter through a strictly typed projection produced by
the emitted-host-path renderer.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol, cast

from promptdeploy.bundles import BundleSchemaError
from promptdeploy.imported_tree import (
    MAX_PATH_BYTES,
    ImportedTreeEntry,
    ImportedTreeSnapshot,
    framed_tree_sha256,
    validate_imported_tree_snapshot,
)
from promptdeploy.manifest import ManifestSource, validate_manifest_source
from promptdeploy.ponytail import (
    CLAUDE_CODEX_RUNTIME_PAYLOAD,
    OPENCODE_PLUGIN_PAYLOAD,
    PONYTAIL_ALL_TARGET_TYPES,
    PONYTAIL_REVISION,
    PONYTAIL_VERSION,
)
from promptdeploy.source import BundlePayload, SourceItem

TargetType = Literal["claude", "codex", "droid", "opencode", "gptel"]
InstalledNodeKind = Literal["directory", "file"]

SUPPORT_PAYLOAD = "support-v1"
SUPPORT_LOGICAL_ROOT = "support/ponytail"
CLAUDE_CODEX_LOGICAL_ROOT = "runtime/claude-codex"
OPENCODE_LOGICAL_ROOT = "runtime/opencode"
REGISTRATION_OWNER = "bundle:ponytail"

_TARGET_TYPES = frozenset({"claude", "codex", "droid", "opencode", "gptel"})
_CLAUDE_CODEX_TARGETS = frozenset({"claude", "codex"})
_OPENCODE_TARGETS = frozenset({"opencode"})
_SUPPORT_TARGETS = frozenset({"droid", "gptel"})
_CANONICAL_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_VERSIONED_PAYLOAD = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-v[1-9][0-9]*\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")

_ADAPTER_ABIS: dict[TargetType, str] = {
    "claude": "ponytail-claude-runtime-v1",
    "codex": "ponytail-codex-runtime-v1",
    "droid": "ponytail-support-v1",
    "opencode": "ponytail-opencode-runtime-v1",
    "gptel": "ponytail-support-v1",
}
_REGISTRATION_ABIS: dict[TargetType, str | None] = {
    "claude": "claude-settings-hooks-v1",
    "codex": "codex-hooks-json-v1",
    "droid": None,
    "opencode": "opencode-plugin-array-v1",
    "gptel": None,
}
_KNOWN_REGISTRATION_ABIS = frozenset(
    abi for abi in _REGISTRATION_ABIS.values() if abi is not None
)


class BundleProjectionError(BundleSchemaError):
    """An accepted bundle cannot produce one unambiguous target projection."""


class _Digest(Protocol):
    def update(self, value: bytes) -> object: ...


def _frame(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BundleProjectionError(f"{field} must be lowercase SHA-256")
    return value


def _has_forbidden_text(value: str) -> bool:
    return value != unicodedata.normalize("NFC", value) or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in value
    )


def _require_identifier(value: str, *, field: str, versioned: bool = False) -> str:
    pattern = _VERSIONED_PAYLOAD if versioned else _CANONICAL_ID
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        suffix = " versioned" if versioned else ""
        raise BundleProjectionError(f"{field} must be a canonical{suffix} identifier")
    return value


def _require_relative_path(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BundleProjectionError(f"{field} must be a canonical relative path")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleProjectionError(f"{field} must be portable UTF-8") from exc
    if len(encoded) > MAX_PATH_BYTES:
        raise BundleProjectionError(f"{field} exceeds the path length limit")
    path = PurePosixPath(value)
    if (
        value == "."
        or path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or any(component in {"", ".", ".."} for component in path.parts)
        or _has_forbidden_text(value)
    ):
        raise BundleProjectionError(f"{field} must be a canonical relative path")
    return value


def _require_target_type(value: str) -> TargetType:
    if value not in _TARGET_TYPES:
        raise BundleProjectionError(f"unsupported target type {value!r}")
    return cast(TargetType, value)


def validate_bundle_payload(payload: BundlePayload) -> BundlePayload:
    """Reassert the generic, source-side ``BundlePayload`` invariants."""
    _require_identifier(payload.name, field="bundle payload name", versioned=True)
    if type(payload.target_types) is not frozenset or not payload.target_types:
        raise BundleProjectionError(
            "bundle payload target_types must be a nonempty frozenset"
        )
    if not all(isinstance(target, str) for target in payload.target_types):
        raise BundleProjectionError("bundle payload target_types must contain strings")
    unknown = payload.target_types - _TARGET_TYPES
    if unknown:
        raise BundleProjectionError(
            f"bundle payload has unsupported target types: {sorted(unknown)!r}"
        )
    if not isinstance(payload.imported_tree, ImportedTreeSnapshot):
        raise BundleProjectionError(
            "bundle payload must carry an imported tree snapshot"
        )
    validate_imported_tree_snapshot(payload.imported_tree)
    return payload


@dataclass(frozen=True, slots=True)
class SelectedBundlePayload:
    """One exact source payload selected for one semantic target type."""

    name: str
    target_type: TargetType
    logical_root: str
    payload_tree_sha256: str
    snapshot: ImportedTreeSnapshot

    def __post_init__(self) -> None:
        _require_identifier(self.name, field="selected payload name", versioned=True)
        _require_target_type(self.target_type)
        _require_relative_path(self.logical_root, field="selected logical root")
        _require_sha256(self.payload_tree_sha256, field="selected payload tree digest")
        validate_imported_tree_snapshot(self.snapshot)
        if self.snapshot.logical_root != self.logical_root:
            raise BundleProjectionError(
                "selected logical root does not match its snapshot"
            )
        if self.snapshot.tree_sha256 != self.payload_tree_sha256:
            raise BundleProjectionError(
                "selected payload digest does not match its snapshot"
            )


@dataclass(frozen=True, slots=True)
class InstalledTreeEntry:
    """One link-free target node."""

    kind: InstalledNodeKind
    relative_path: str
    normalized_mode: int
    content: bytes | None = None

    def __post_init__(self) -> None:
        if self.relative_path != ".":
            _require_relative_path(
                self.relative_path, field="installed tree entry path"
            )
        if not 0 <= self.normalized_mode <= 0o7777:
            raise BundleProjectionError("installed tree mode is outside range")
        if self.kind == "directory":
            if self.content is not None:
                raise BundleProjectionError(
                    "installed directory entries may not carry bytes"
                )
        elif self.kind == "file":
            if not isinstance(self.content, bytes):
                raise BundleProjectionError("installed file entries must carry bytes")
        else:
            raise BundleProjectionError(f"unsupported installed kind {self.kind!r}")


InstalledTreeSnapshot = tuple[InstalledTreeEntry, ...]


def installed_tree_sha256(entries: InstalledTreeSnapshot) -> str:
    """Hash exact installed kind/path/mode/bytes with explicit framing."""
    _validate_installed_tree(entries)
    digest = hashlib.sha256()
    _frame(digest, b"promptdeploy-installed-tree-v1")
    for entry in entries:
        _frame(digest, entry.kind.encode("ascii"))
        _frame(digest, entry.relative_path.encode("utf-8"))
        _frame(digest, f"{entry.normalized_mode:04o}".encode("ascii"))
        _frame(digest, entry.content or b"")
    return f"sha256:{digest.hexdigest()}"


def _validate_installed_tree(entries: InstalledTreeSnapshot) -> None:
    """Reuse imported-tree topology/budget validation after link projection."""
    imported_entries = tuple(
        ImportedTreeEntry(
            entry.kind,
            entry.relative_path,
            entry.normalized_mode,
            entry.content,
        )
        for entry in entries
    )
    snapshot = ImportedTreeSnapshot(
        "rendered/tree",
        imported_entries,
        framed_tree_sha256(imported_entries),
    )
    validate_imported_tree_snapshot(snapshot)


def project_installed_tree(
    snapshot: ImportedTreeSnapshot,
    *,
    exclude: frozenset[str] = frozenset(),
    added_files: tuple[tuple[str, int, bytes], ...] = (),
) -> InstalledTreeSnapshot:
    """Project links to files, omit reviewed render inputs, and add support."""
    validate_imported_tree_snapshot(snapshot)
    source_paths = {entry.relative_path for entry in snapshot.entries}
    if not exclude <= source_paths:
        missing = sorted(exclude - source_paths)
        raise BundleProjectionError(f"projection excludes missing paths: {missing!r}")

    entries_by_path = {entry.relative_path: entry for entry in snapshot.entries}

    def link_chain(relative_path: str) -> tuple[str, ...]:
        entry = entries_by_path[relative_path]
        chain = [entry.relative_path]
        while entry.kind == "link":
            assert entry.link_target is not None
            entry = entries_by_path[entry.link_target]
            chain.append(entry.relative_path)
        return tuple(chain)

    excluded_aliases = sorted(
        entry.relative_path
        for entry in snapshot.entries
        if entry.kind == "link"
        and entry.relative_path not in exclude
        and any(path in exclude for path in link_chain(entry.relative_path)[1:])
    )
    if excluded_aliases:
        raise BundleProjectionError(
            f"projection excludes a path with link aliases: {excluded_aliases!r}"
        )

    projected: dict[str, InstalledTreeEntry] = {}
    for entry in snapshot.entries:
        if entry.relative_path in exclude:
            if entry.kind == "directory":
                raise BundleProjectionError("projection may not omit a directory")
            continue
        if entry.kind == "directory":
            rendered = InstalledTreeEntry(
                "directory", entry.relative_path, entry.normalized_mode
            )
        else:
            assert entry.content is not None
            rendered = InstalledTreeEntry(
                "file", entry.relative_path, entry.normalized_mode, entry.content
            )
        projected[rendered.relative_path] = rendered

    for relative_path, mode, content in added_files:
        _require_relative_path(relative_path, field="added installed path")
        if relative_path in projected:
            raise BundleProjectionError(
                f"projection added-file collision at {relative_path!r}"
            )
        projected[relative_path] = InstalledTreeEntry(
            "file", relative_path, mode, content
        )

    entries = tuple(sorted(projected.values(), key=lambda entry: entry.relative_path))
    _validate_installed_tree(entries)
    return entries


def _opencode_registration_sha256(identity: str) -> str:
    canonical = json.dumps(
        {"plugin": identity},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _content_sha256(canonical)


@dataclass(frozen=True, slots=True)
class RegistrationProjection:
    """Typed placeholder supplied by a strict target registration renderer."""

    abi: str
    owner: str
    sha256: str
    identity: str | None = None

    def __post_init__(self) -> None:
        if type(self.abi) is not str:
            raise BundleProjectionError("registration ABI must be an exact string")
        if type(self.owner) is not str:
            raise BundleProjectionError("registration owner must be an exact string")
        if type(self.sha256) is not str:
            raise BundleProjectionError("registration digest must be an exact string")
        if self.identity is not None and type(self.identity) is not str:
            raise BundleProjectionError("registration identity must be an exact string")
        _require_identifier(self.abi, field="registration ABI", versioned=True)
        if self.abi not in _KNOWN_REGISTRATION_ABIS:
            raise BundleProjectionError("registration ABI is unsupported")
        if self.owner != REGISTRATION_OWNER:
            raise BundleProjectionError("registration owner must be bundle:ponytail")
        _require_sha256(self.sha256, field="registration digest")
        if self.abi == "opencode-plugin-array-v1":
            if not isinstance(self.identity, str) or not self.identity.startswith("./"):
                raise BundleProjectionError(
                    "OpenCode registration requires one relative plugin identity"
                )
            relative_identity = _require_relative_path(
                self.identity[2:], field="registration identity"
            )
            if not relative_identity.endswith("/.opencode/plugins/ponytail.mjs"):
                raise BundleProjectionError(
                    "OpenCode registration identity does not name Ponytail"
                )
            if self.sha256 != _opencode_registration_sha256(self.identity):
                raise BundleProjectionError(
                    "OpenCode registration digest does not match its identity"
                )
        elif self.identity is not None:
            raise BundleProjectionError(
                "Claude and Codex registration identities must be absent"
            )


@dataclass(frozen=True, slots=True)
class BundleHashDescriptor:
    """Closed, immutable inputs to the target-effective candidate hash."""

    bundle_name: Literal["ponytail"]
    target_type: TargetType
    support_content_sha256: str
    support_tree_sha256: str
    source: ManifestSource
    payload_name: str
    logical_root: str
    payload_tree_sha256: str
    adapter_abi: str
    runtime_tree_sha256: str | None
    runtime_path: str | None
    registration_abi: str | None
    registration_owner: str | None
    registration_sha256: str | None
    registration_identity: str | None

    def __post_init__(self) -> None:
        if self.bundle_name != "ponytail":
            raise BundleProjectionError("hash descriptor bundle must be ponytail")
        target_type = _require_target_type(self.target_type)
        _require_sha256(
            self.support_content_sha256,
            field="hash descriptor support content digest",
        )
        _require_sha256(
            self.support_tree_sha256,
            field="hash descriptor support tree digest",
        )
        if not isinstance(self.source, ManifestSource):
            raise BundleProjectionError("hash descriptor source is invalid")
        validate_manifest_source(self.source)
        _require_identifier(
            self.payload_name,
            field="hash descriptor payload name",
            versioned=True,
        )
        _require_relative_path(
            self.logical_root,
            field="hash descriptor logical root",
        )
        _require_sha256(
            self.payload_tree_sha256,
            field="hash descriptor payload digest",
        )
        if self.adapter_abi != _ADAPTER_ABIS[target_type]:
            raise BundleProjectionError("hash descriptor adapter ABI is inconsistent")

        registration_values = (
            self.registration_abi,
            self.registration_owner,
            self.registration_sha256,
            self.registration_identity,
        )
        if target_type in _SUPPORT_TARGETS:
            if (
                self.payload_name != SUPPORT_PAYLOAD
                or self.logical_root != SUPPORT_LOGICAL_ROOT
            ):
                raise BundleProjectionError(
                    "support hash descriptor selects the wrong payload"
                )
            if (
                self.runtime_tree_sha256 is not None
                or self.runtime_path is not None
                or any(value is not None for value in registration_values)
            ):
                raise BundleProjectionError(
                    "support hash descriptor may not claim runtime state"
                )
            return

        expected_payload, expected_root = (
            (OPENCODE_PLUGIN_PAYLOAD, OPENCODE_LOGICAL_ROOT)
            if target_type == "opencode"
            else (CLAUDE_CODEX_RUNTIME_PAYLOAD, CLAUDE_CODEX_LOGICAL_ROOT)
        )
        if self.payload_name != expected_payload or self.logical_root != expected_root:
            raise BundleProjectionError(
                "runtime hash descriptor selects the wrong payload"
            )
        if self.runtime_tree_sha256 is None or self.runtime_path is None:
            raise BundleProjectionError(
                "runtime hash descriptor lacks its installed tree"
            )
        rendered_digest = _require_sha256(
            self.runtime_tree_sha256,
            field="hash descriptor rendered tree digest",
        )
        if self.runtime_path != _runtime_path(target_type, rendered_digest):
            raise BundleProjectionError(
                "hash descriptor runtime path is outside the owned namespace"
            )
        if any(value is None for value in registration_values[:3]):
            raise BundleProjectionError(
                "runtime hash descriptor lacks registration fields"
            )
        expected_registration = _REGISTRATION_ABIS[target_type]
        if self.registration_abi != expected_registration:
            raise BundleProjectionError(
                "hash descriptor registration ABI is inconsistent"
            )
        assert self.registration_abi is not None
        assert self.registration_owner is not None
        assert self.registration_sha256 is not None
        RegistrationProjection(
            self.registration_abi,
            self.registration_owner,
            self.registration_sha256,
            self.registration_identity,
        )
        if target_type == "opencode":
            expected_identity = f"./{self.runtime_path}/.opencode/plugins/ponytail.mjs"
            if self.registration_identity != expected_identity:
                raise BundleProjectionError(
                    "hash descriptor OpenCode identity is outside its runtime"
                )


@dataclass(frozen=True, slots=True)
class BundleReceipt:
    """Persistable target-state witness; absolute and staging paths are absent."""

    payload_name: str
    target_type: TargetType
    logical_root: str
    payload_tree_sha256: str
    adapter_abi: str
    rendered_tree_sha256: str | None
    runtime_path: str | None
    registration_abi: str | None
    registration_owner: str | None
    registration_sha256: str | None
    registration_identity: str | None
    effective_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(
            self.payload_name, field="receipt payload name", versioned=True
        )
        target_type = _require_target_type(self.target_type)
        _require_relative_path(self.logical_root, field="receipt logical root")
        _require_sha256(self.payload_tree_sha256, field="receipt payload digest")
        _require_sha256(self.effective_sha256, field="receipt effective hash")
        expected_adapter = _ADAPTER_ABIS[target_type]
        if self.adapter_abi != expected_adapter:
            raise BundleProjectionError(
                f"receipt adapter ABI must be {expected_adapter!r}"
            )

        registration_values = (
            self.registration_abi,
            self.registration_owner,
            self.registration_sha256,
            self.registration_identity,
        )
        if target_type in _SUPPORT_TARGETS:
            if self.payload_name != SUPPORT_PAYLOAD:
                raise BundleProjectionError("support target receipt has wrong payload")
            if self.logical_root != SUPPORT_LOGICAL_ROOT:
                raise BundleProjectionError(
                    "support target receipt has wrong logical root"
                )
            if (
                self.rendered_tree_sha256 is not None
                or self.runtime_path is not None
                or any(value is not None for value in registration_values)
            ):
                raise BundleProjectionError(
                    "support target receipt may not claim runtime state"
                )
            return

        expected_payload, expected_root = (
            (OPENCODE_PLUGIN_PAYLOAD, OPENCODE_LOGICAL_ROOT)
            if target_type == "opencode"
            else (CLAUDE_CODEX_RUNTIME_PAYLOAD, CLAUDE_CODEX_LOGICAL_ROOT)
        )
        if self.payload_name != expected_payload or self.logical_root != expected_root:
            raise BundleProjectionError("runtime receipt selects the wrong payload")
        if self.rendered_tree_sha256 is None or self.runtime_path is None:
            raise BundleProjectionError("runtime receipt lacks its installed tree")
        rendered_digest = _require_sha256(
            self.rendered_tree_sha256, field="receipt rendered tree digest"
        )
        runtime_path = _require_relative_path(
            self.runtime_path, field="receipt runtime path"
        )
        if runtime_path.rsplit("/", 1)[-1] != rendered_digest.removeprefix("sha256:"):
            raise BundleProjectionError(
                "receipt runtime path does not match rendered tree digest"
            )
        if any(value is None for value in registration_values[:3]):
            raise BundleProjectionError("runtime receipt lacks registration fields")
        expected_registration = _REGISTRATION_ABIS[target_type]
        if self.registration_abi != expected_registration:
            raise BundleProjectionError(
                f"receipt registration ABI must be {expected_registration!r}"
            )
        if self.registration_owner != REGISTRATION_OWNER:
            raise BundleProjectionError("receipt registration owner is invalid")
        assert self.registration_sha256 is not None
        _require_sha256(self.registration_sha256, field="receipt registration digest")
        if runtime_path != _runtime_path(target_type, rendered_digest):
            raise BundleProjectionError(
                "receipt runtime path is outside the owned namespace"
            )
        assert self.registration_abi is not None
        assert self.registration_owner is not None
        RegistrationProjection(
            self.registration_abi,
            self.registration_owner,
            self.registration_sha256,
            self.registration_identity,
        )
        if target_type == "opencode":
            expected_identity = f"./{runtime_path}/.opencode/plugins/ponytail.mjs"
            if self.registration_identity != expected_identity:
                raise BundleProjectionError(
                    "receipt OpenCode identity is outside its runtime"
                )

    @classmethod
    def from_descriptor(cls, descriptor: BundleHashDescriptor) -> BundleReceipt:
        """Derive the complete compact witness from one effective authority."""
        return cls(
            payload_name=descriptor.payload_name,
            target_type=descriptor.target_type,
            logical_root=descriptor.logical_root,
            payload_tree_sha256=descriptor.payload_tree_sha256,
            adapter_abi=descriptor.adapter_abi,
            rendered_tree_sha256=descriptor.runtime_tree_sha256,
            runtime_path=descriptor.runtime_path,
            registration_abi=descriptor.registration_abi,
            registration_owner=descriptor.registration_owner,
            registration_sha256=descriptor.registration_sha256,
            registration_identity=descriptor.registration_identity,
            effective_sha256=_target_effective_hash(descriptor),
        )


def _support_content_sha256(entries: InstalledTreeSnapshot) -> str:
    if (
        len(entries) != 2
        or entries[0] != InstalledTreeEntry("directory", ".", 0o755)
        or entries[1].kind != "file"
        or entries[1].relative_path != "LICENSE"
        or entries[1].normalized_mode != 0o644
        or not isinstance(entries[1].content, bytes)
    ):
        raise BundleProjectionError("rendered support tree is not exact")
    return _content_sha256(entries[1].content)


@dataclass(frozen=True, slots=True)
class RenderedBundle:
    """Complete pure desired state for one target-specific bundle variant."""

    name: Literal["ponytail"]
    target_type: TargetType
    selected: SelectedBundlePayload
    adapter_abi: str
    support_tree: InstalledTreeSnapshot
    support_tree_sha256: str
    runtime_tree: InstalledTreeSnapshot | None
    runtime_tree_sha256: str | None
    runtime_path: str | None
    registration: RegistrationProjection | None
    hash_descriptor: BundleHashDescriptor
    receipt: BundleReceipt

    @property
    def source_hash(self) -> str:
        """Return the derived target-effective hash; it is never stored authority."""
        return _target_effective_hash(self.hash_descriptor)

    def __post_init__(self) -> None:
        if self.name != "ponytail":
            raise BundleProjectionError("rendered bundle name must be ponytail")
        _require_target_type(self.target_type)
        if self.selected.target_type != self.target_type:
            raise BundleProjectionError("rendered target does not match selection")
        if self.adapter_abi != _ADAPTER_ABIS[self.target_type]:
            raise BundleProjectionError("rendered adapter ABI is inconsistent")
        if installed_tree_sha256(self.support_tree) != self.support_tree_sha256:
            raise BundleProjectionError("rendered support tree digest is inconsistent")
        support_content_sha256 = _support_content_sha256(self.support_tree)
        if self.runtime_tree is None:
            if self.runtime_tree_sha256 is not None or self.runtime_path is not None:
                raise BundleProjectionError("runtime-free bundle claims runtime state")
        else:
            if installed_tree_sha256(self.runtime_tree) != self.runtime_tree_sha256:
                raise BundleProjectionError(
                    "rendered runtime tree digest is inconsistent"
                )
            assert self.runtime_tree_sha256 is not None
            if self.runtime_path != _runtime_path(
                self.target_type,
                self.runtime_tree_sha256,
            ):
                raise BundleProjectionError(
                    "rendered runtime path is outside the owned namespace"
                )
        expected_registration = _REGISTRATION_ABIS[self.target_type]
        if expected_registration is None:
            if self.registration is not None:
                raise BundleProjectionError(
                    "support-only rendered bundle has a registration"
                )
        elif (
            self.registration is None or self.registration.abi != expected_registration
        ):
            raise BundleProjectionError("rendered registration ABI is inconsistent")
        expected_descriptor = BundleHashDescriptor(
            bundle_name=self.name,
            target_type=self.target_type,
            support_content_sha256=support_content_sha256,
            support_tree_sha256=self.support_tree_sha256,
            source=self.hash_descriptor.source,
            payload_name=self.selected.name,
            logical_root=self.selected.logical_root,
            payload_tree_sha256=self.selected.payload_tree_sha256,
            adapter_abi=self.adapter_abi,
            runtime_tree_sha256=self.runtime_tree_sha256,
            runtime_path=self.runtime_path,
            registration_abi=(
                self.registration.abi if self.registration is not None else None
            ),
            registration_owner=(
                self.registration.owner if self.registration is not None else None
            ),
            registration_sha256=(
                self.registration.sha256 if self.registration is not None else None
            ),
            registration_identity=(
                self.registration.identity if self.registration is not None else None
            ),
        )
        if self.hash_descriptor != expected_descriptor:
            raise BundleProjectionError("rendered hash descriptor is inconsistent")
        if self.receipt != BundleReceipt.from_descriptor(self.hash_descriptor):
            raise BundleProjectionError("rendered receipt is inconsistent")


def _require_exact_fields(
    value: object,
    fields: tuple[tuple[str, type[object]], ...],
) -> None:
    for name, expected_type in fields:
        if type(getattr(value, name)) is not expected_type:
            raise BundleProjectionError(
                f"closed rendered bundle field {name!r} must be an exact "
                f"{expected_type.__name__}"
            )


def _require_exact_optional_fields(
    value: object,
    fields: tuple[tuple[str, type[object]], ...],
) -> None:
    for name, expected_type in fields:
        item = getattr(value, name)
        if item is not None and type(item) is not expected_type:
            raise BundleProjectionError(
                f"closed rendered bundle field {name!r} must be null or an exact "
                f"{expected_type.__name__}"
            )


def _validate_closed_manifest_source(source: ManifestSource) -> None:
    if type(source) is not ManifestSource:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact manifest source"
        )
    _require_exact_fields(
        source,
        (
            ("bundle", str),
            ("path", str),
            ("version", str),
            ("mutable", bool),
            ("license", str),
        ),
    )
    _require_exact_optional_fields(
        source,
        (("revision", str), ("nar_hash", str), ("transform", str)),
    )
    validate_manifest_source(source)


def _validate_closed_imported_tree(snapshot: ImportedTreeSnapshot) -> None:
    if type(snapshot) is not ImportedTreeSnapshot:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact imported snapshot"
        )
    _require_exact_fields(
        snapshot,
        (("logical_root", str), ("entries", tuple), ("tree_sha256", str)),
    )
    for entry in snapshot.entries:
        if type(entry) is not ImportedTreeEntry:
            raise BundleProjectionError(
                "closed rendered bundle requires exact imported entries"
            )
        _require_exact_fields(
            entry,
            (("kind", str), ("relative_path", str), ("normalized_mode", int)),
        )
        _require_exact_optional_fields(
            entry,
            (("content", bytes), ("link_target", str)),
        )
        ImportedTreeEntry.__post_init__(entry)
    ImportedTreeSnapshot.__post_init__(snapshot)
    validate_imported_tree_snapshot(snapshot)


def _validate_closed_selected_payload(selected: SelectedBundlePayload) -> None:
    if type(selected) is not SelectedBundlePayload:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact selected payload"
        )
    _require_exact_fields(
        selected,
        (
            ("name", str),
            ("target_type", str),
            ("logical_root", str),
            ("payload_tree_sha256", str),
        ),
    )
    _validate_closed_imported_tree(selected.snapshot)
    SelectedBundlePayload.__post_init__(selected)


def _validate_closed_installed_tree(entries: InstalledTreeSnapshot) -> None:
    if type(entries) is not tuple:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact installed-tree tuple"
        )
    for entry in entries:
        if type(entry) is not InstalledTreeEntry:
            raise BundleProjectionError(
                "closed rendered bundle requires exact installed-tree entries"
            )
        _require_exact_fields(
            entry,
            (("kind", str), ("relative_path", str), ("normalized_mode", int)),
        )
        _require_exact_optional_fields(entry, (("content", bytes),))
        InstalledTreeEntry.__post_init__(entry)
    _validate_installed_tree(entries)


def _validate_closed_registration(
    registration: RegistrationProjection,
) -> None:
    if type(registration) is not RegistrationProjection:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact registration projection"
        )
    RegistrationProjection.__post_init__(registration)


def _validate_closed_hash_descriptor(descriptor: BundleHashDescriptor) -> None:
    if type(descriptor) is not BundleHashDescriptor:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact hash descriptor"
        )
    _require_exact_fields(
        descriptor,
        (
            ("bundle_name", str),
            ("target_type", str),
            ("support_content_sha256", str),
            ("support_tree_sha256", str),
            ("payload_name", str),
            ("logical_root", str),
            ("payload_tree_sha256", str),
            ("adapter_abi", str),
        ),
    )
    _require_exact_optional_fields(
        descriptor,
        (
            ("runtime_tree_sha256", str),
            ("runtime_path", str),
            ("registration_abi", str),
            ("registration_owner", str),
            ("registration_sha256", str),
            ("registration_identity", str),
        ),
    )
    _validate_closed_manifest_source(descriptor.source)
    BundleHashDescriptor.__post_init__(descriptor)


def _validate_closed_receipt(receipt: BundleReceipt) -> None:
    if type(receipt) is not BundleReceipt:
        raise BundleProjectionError("closed rendered bundle requires an exact receipt")
    _require_exact_fields(
        receipt,
        (
            ("payload_name", str),
            ("target_type", str),
            ("logical_root", str),
            ("payload_tree_sha256", str),
            ("adapter_abi", str),
            ("effective_sha256", str),
        ),
    )
    _require_exact_optional_fields(
        receipt,
        (
            ("rendered_tree_sha256", str),
            ("runtime_path", str),
            ("registration_abi", str),
            ("registration_owner", str),
            ("registration_sha256", str),
            ("registration_identity", str),
        ),
    )
    BundleReceipt.__post_init__(receipt)


def validate_closed_rendered_bundle(bundle: RenderedBundle) -> RenderedBundle:
    """Revalidate one deeply immutable projected plan without subclass dispatch."""
    if type(bundle) is not RenderedBundle:
        raise BundleProjectionError(
            "closed rendered bundle requires an exact projected bundle"
        )
    _require_exact_fields(
        bundle,
        (
            ("name", str),
            ("target_type", str),
            ("adapter_abi", str),
            ("support_tree", tuple),
            ("support_tree_sha256", str),
        ),
    )
    _require_exact_optional_fields(
        bundle,
        (
            ("runtime_tree", tuple),
            ("runtime_tree_sha256", str),
            ("runtime_path", str),
        ),
    )
    _validate_closed_selected_payload(bundle.selected)
    _validate_closed_installed_tree(bundle.support_tree)
    if bundle.runtime_tree is not None:
        _validate_closed_installed_tree(bundle.runtime_tree)
    if bundle.registration is not None:
        _validate_closed_registration(bundle.registration)
    _validate_closed_hash_descriptor(bundle.hash_descriptor)
    _validate_closed_receipt(bundle.receipt)
    RenderedBundle.__post_init__(bundle)
    return bundle


def _support_snapshot(content: bytes) -> ImportedTreeSnapshot:
    entries = (
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("file", "LICENSE", 0o644, content),
    )
    return ImportedTreeSnapshot(
        SUPPORT_LOGICAL_ROOT,
        entries,
        framed_tree_sha256(entries),
    )


def _validated_bundle_item(item: SourceItem) -> ManifestSource:
    if item.item_type != "bundle" or item.name != "ponytail":
        raise BundleProjectionError("bundle projection requires bundle:ponytail")
    if item.target_types != PONYTAIL_ALL_TARGET_TYPES:
        raise BundleProjectionError("Ponytail bundle target applicability is not exact")
    source = item.provenance.source
    if source is None:
        raise BundleProjectionError("Ponytail bundle lacks imported provenance")
    validate_manifest_source(source)
    if not isinstance(item.content, bytes):
        raise BundleProjectionError("Ponytail LICENSE content must be bytes")
    if source.bundle != "ponytail" or source.path != "LICENSE":
        raise BundleProjectionError("Ponytail support provenance is not exact")
    if source.version != PONYTAIL_VERSION or (
        not source.mutable and source.revision != PONYTAIL_REVISION
    ):
        raise BundleProjectionError("Ponytail support pin is not exact")
    if source.license != "MIT" or source.transform is not None:
        raise BundleProjectionError("Ponytail support provenance is not verbatim MIT")
    if item.provenance.input_sha256 != _content_sha256(item.content):
        raise BundleProjectionError("Ponytail LICENSE bytes do not match provenance")
    if item.imported_tree is not None or item.provenance.tree_sha256 is not None:
        raise BundleProjectionError(
            "Ponytail support item has unexpected tree authority"
        )

    expected = (
        (
            CLAUDE_CODEX_RUNTIME_PAYLOAD,
            _CLAUDE_CODEX_TARGETS,
            CLAUDE_CODEX_LOGICAL_ROOT,
        ),
        (OPENCODE_PLUGIN_PAYLOAD, _OPENCODE_TARGETS, OPENCODE_LOGICAL_ROOT),
    )
    if len(item.bundle_payloads) != len(expected):
        raise BundleProjectionError("Ponytail bundle must contain exactly two payloads")
    for payload, (name, targets, logical_root) in zip(
        item.bundle_payloads, expected, strict=True
    ):
        validate_bundle_payload(payload)
        if (
            payload.name != name
            or payload.target_types != targets
            or payload.imported_tree.logical_root != logical_root
        ):
            raise BundleProjectionError("Ponytail payload tuple is not exact")
    return source


def select_bundle_payload(item: SourceItem, target_type: str) -> SelectedBundlePayload:
    """Select exactly one source payload for every supported target type."""
    checked_target = _require_target_type(target_type)
    _validated_bundle_item(item)

    if checked_target in _SUPPORT_TARGETS:
        snapshot = _support_snapshot(item.content)
        return SelectedBundlePayload(
            SUPPORT_PAYLOAD,
            checked_target,
            snapshot.logical_root,
            snapshot.tree_sha256,
            snapshot,
        )

    payload = next(
        payload
        for payload in item.bundle_payloads
        if checked_target in payload.target_types
    )
    return SelectedBundlePayload(
        payload.name,
        checked_target,
        payload.imported_tree.logical_root,
        payload.imported_tree.tree_sha256,
        payload.imported_tree,
    )


def _runtime_path(target_type: TargetType, tree_sha256: str) -> str:
    digest = _require_sha256(tree_sha256, field="rendered runtime digest").removeprefix(
        "sha256:"
    )
    if target_type == "opencode":
        return f".promptdeploy/bundles/ponytail/{digest}"
    return f".promptdeploy/bundles/ponytail/runtimes/{digest}"


def _opencode_registration(runtime_path: str) -> RegistrationProjection:
    plugin = f"./{runtime_path}/.opencode/plugins/ponytail.mjs"
    return RegistrationProjection(
        abi="opencode-plugin-array-v1",
        owner=REGISTRATION_OWNER,
        sha256=_opencode_registration_sha256(plugin),
        identity=plugin,
    )


def _target_effective_hash(descriptor: BundleHashDescriptor) -> str:
    digest = hashlib.sha256()
    _frame(digest, b"promptdeploy-rendered-bundle-v1")
    for value in (
        descriptor.bundle_name,
        descriptor.target_type,
        descriptor.support_content_sha256,
        descriptor.support_tree_sha256,
        descriptor.source.bundle,
        descriptor.source.path,
        descriptor.source.version,
        descriptor.source.revision or "",
        descriptor.source.nar_hash or "",
        "mutable" if descriptor.source.mutable else "immutable",
        descriptor.source.transform or "",
        descriptor.source.license,
        descriptor.payload_name,
        descriptor.logical_root,
        descriptor.payload_tree_sha256,
        descriptor.adapter_abi,
        descriptor.runtime_tree_sha256 or "",
        descriptor.runtime_path or "",
        descriptor.registration_abi or "",
        descriptor.registration_owner or "",
        descriptor.registration_sha256 or "",
        descriptor.registration_identity or "",
    ):
        _frame(digest, value.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def render_bundle(
    item: SourceItem,
    target_type: str,
    *,
    registration: RegistrationProjection | None = None,
) -> RenderedBundle:
    """Build target-effective desired state without reading or writing paths."""
    checked_target = _require_target_type(target_type)
    source = _validated_bundle_item(item)
    selected = select_bundle_payload(item, checked_target)
    adapter_abi = _ADAPTER_ABIS[checked_target]

    support_tree = project_installed_tree(_support_snapshot(item.content))
    support_digest = installed_tree_sha256(support_tree)

    runtime_tree: InstalledTreeSnapshot | None
    runtime_digest: str | None
    runtime_path: str | None
    if checked_target in _SUPPORT_TARGETS:
        if registration is not None:
            raise BundleProjectionError(
                "support-only target may not supply registration state"
            )
        runtime_tree = None
        runtime_digest = None
        runtime_path = None
    elif checked_target in _CLAUDE_CODEX_TARGETS:
        expected_abi = _REGISTRATION_ABIS[checked_target]
        if registration is None or registration.abi != expected_abi:
            raise BundleProjectionError(
                f"{checked_target} requires {expected_abi!r} registration"
            )
        runtime_tree = project_installed_tree(
            selected.snapshot,
            exclude=frozenset({"hooks/claude-codex-hooks.json"}),
        )
        runtime_digest = installed_tree_sha256(runtime_tree)
        runtime_path = _runtime_path(checked_target, runtime_digest)
    else:
        if registration is not None:
            raise BundleProjectionError(
                "OpenCode registration is derived from its runtime digest"
            )
        runtime_tree = project_installed_tree(
            selected.snapshot,
            added_files=(("LICENSE", 0o644, item.content),),
        )
        runtime_digest = installed_tree_sha256(runtime_tree)
        runtime_path = _runtime_path(checked_target, runtime_digest)
        registration = _opencode_registration(runtime_path)

    hash_descriptor = BundleHashDescriptor(
        bundle_name="ponytail",
        target_type=checked_target,
        support_content_sha256=_content_sha256(item.content),
        support_tree_sha256=support_digest,
        source=source,
        payload_name=selected.name,
        logical_root=selected.logical_root,
        payload_tree_sha256=selected.payload_tree_sha256,
        adapter_abi=adapter_abi,
        runtime_tree_sha256=runtime_digest,
        runtime_path=runtime_path,
        registration_abi=(registration.abi if registration is not None else None),
        registration_owner=(registration.owner if registration is not None else None),
        registration_sha256=(registration.sha256 if registration is not None else None),
        registration_identity=(
            registration.identity if registration is not None else None
        ),
    )
    receipt = BundleReceipt.from_descriptor(hash_descriptor)
    return validate_closed_rendered_bundle(
        RenderedBundle(
            name="ponytail",
            target_type=checked_target,
            selected=selected,
            adapter_abi=adapter_abi,
            support_tree=support_tree,
            support_tree_sha256=support_digest,
            runtime_tree=runtime_tree,
            runtime_tree_sha256=runtime_digest,
            runtime_path=runtime_path,
            registration=registration,
            hash_descriptor=hash_descriptor,
            receipt=receipt,
        )
    )


def revalidate_rendered_bundle(
    item: SourceItem,
    target_type: str,
    expected: RenderedBundle,
    *,
    registration: RegistrationProjection | None = None,
) -> None:
    """Recompute desired state and reject any changed source/plan field."""
    validate_closed_rendered_bundle(expected)
    actual = render_bundle(item, target_type, registration=registration)
    if actual != expected:
        raise BundleProjectionError("rendered bundle changed before target mutation")
