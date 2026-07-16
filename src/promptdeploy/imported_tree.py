"""Immutable, descriptor-checked snapshots of selected external source trees."""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from .bundles import BundleSchemaError

_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = (
    _READ_FLAGS | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = _READ_FLAGS | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_CHUNK_SIZE = 128 * 1024
MAX_TREE_DEPTH = 64
MAX_TREE_ENTRIES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TREE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_LINK_EXPANSIONS = 40

_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


class ImportedSourceError(BundleSchemaError):
    """An external source cannot be captured as one confined snapshot."""


@dataclass(frozen=True, slots=True)
class ImportedTreeEntry:
    """One portable node in an imported-tree snapshot."""

    kind: Literal["directory", "file", "link"]
    relative_path: str
    normalized_mode: int
    content: bytes | None = None
    link_target: str | None = None

    def __post_init__(self) -> None:
        if self.relative_path != ".":
            _canonical_relative_path(self.relative_path, what="snapshot path")
        if not 0 <= self.normalized_mode <= 0o7777:
            raise ImportedSourceError("snapshot mode is outside the portable range")
        if self.kind == "directory":
            if self.content is not None or self.link_target is not None:
                raise ImportedSourceError("directory snapshot entries carry no bytes")
        elif self.kind == "file":
            if self.content is None or self.link_target is not None:
                raise ImportedSourceError("file snapshot entries require only bytes")
        elif self.kind == "link":
            if self.content is None or self.link_target is None:
                raise ImportedSourceError(
                    "link snapshot entries require target identity and bytes"
                )
            _canonical_relative_path(self.link_target, what="snapshot link target")
        else:
            raise ImportedSourceError(f"unsupported snapshot node kind {self.kind!r}")


@dataclass(frozen=True, slots=True)
class ImportedTreeSnapshot:
    """The exact tree accepted for later hashing, materialization, and verify."""

    logical_root: str
    entries: tuple[ImportedTreeEntry, ...]
    tree_sha256: str

    def __post_init__(self) -> None:
        _canonical_relative_path(self.logical_root, what="snapshot logical root")
        paths = [entry.relative_path for entry in self.entries]
        root_entries = [
            entry
            for entry in self.entries
            if entry.relative_path == "." and entry.kind == "directory"
        ]
        if len(root_entries) != 1 or len(paths) != len(set(paths)):
            raise ImportedSourceError(
                "snapshot must contain one root and unique sorted paths"
            )
        if paths != sorted(paths):
            raise ImportedSourceError("snapshot paths must be sorted")
        if self.tree_sha256 != framed_tree_sha256(self.entries):
            raise ImportedSourceError("snapshot digest does not match its entries")


@dataclass(frozen=True, slots=True)
class ImportedFileSnapshot:
    """One descriptor-checked metadata/support file."""

    relative_path: str
    normalized_mode: int
    content: bytes


DirectoryChildKind = Literal["directory", "file", "link"]


@dataclass(frozen=True, slots=True)
class ImportedDirectorySnapshot:
    """One descriptor-checked shallow source-directory inventory."""

    relative_path: str
    normalized_mode: int
    children: tuple[tuple[str, DirectoryChildKind], ...]
    identity: tuple[int, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _canonical_relative_path(self.relative_path, what="inventory path")
        if (
            _normalize_mode(self.normalized_mode, directory=True)
            != self.normalized_mode
        ):
            raise ImportedSourceError("inventory directory mode is not normalized")
        names = [name for name, _kind in self.children]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ImportedSourceError("inventory children must be unique and sorted")
        folded: dict[str, str] = {}
        for name, kind in self.children:
            _canonical_component(name, what="inventory child")
            previous = folded.get(name.casefold())
            if previous is not None and previous != name:
                raise ImportedSourceError(
                    f"inventory has a case-fold collision: {previous!r} and {name!r}"
                )
            folded[name.casefold()] = name
            if kind not in {"directory", "file", "link"}:
                raise ImportedSourceError(f"unsupported inventory node kind {kind!r}")


@dataclass(slots=True)
class _TreeBudget:
    entries: int = 0
    content_bytes: int = 0

    def add_entry(self, relative_path: str, depth: int) -> None:
        self.entries += 1
        if self.entries > MAX_TREE_ENTRIES:
            raise ImportedSourceError("selected tree exceeds the entry limit")
        if depth > MAX_TREE_DEPTH:
            raise ImportedSourceError("selected tree exceeds the depth limit")
        if len(relative_path.encode("utf-8")) > MAX_PATH_BYTES:
            raise ImportedSourceError("selected tree path exceeds the length limit")

    def add_bytes(self, size: int) -> None:
        self.content_bytes += size
        if self.content_bytes > MAX_TREE_BYTES:
            raise ImportedSourceError("selected tree exceeds the byte limit")


def _has_forbidden_text(value: str) -> bool:
    return value != unicodedata.normalize("NFC", value) or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in value
    )


def _canonical_component(value: str, *, what: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ImportedSourceError(f"{what} is not valid portable UTF-8") from exc
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _has_forbidden_text(value)
        or any(character in _WINDOWS_FORBIDDEN for character in value)
        or value.endswith((" ", "."))
        or value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
    ):
        raise ImportedSourceError(f"{what} is not a canonical portable component")
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise ImportedSourceError(f"{what} exceeds the portable path limit")
    return value


def _canonical_relative_path(value: str, *, what: str) -> tuple[str, ...]:
    if not value or PurePosixPath(value).is_absolute() or "\\" in value:
        raise ImportedSourceError(f"{what} must be a canonical relative path")
    path = PurePosixPath(value)
    if not path.parts or path.as_posix() != value:
        raise ImportedSourceError(f"{what} must be a canonical relative path")
    parts = tuple(
        _canonical_component(component, what=what) for component in path.parts
    )
    if len(parts) > MAX_TREE_DEPTH or len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise ImportedSourceError(f"{what} exceeds the portable path limit")
    return parts


def _normalize_mode(mode: int, *, directory: bool) -> int:
    """Mirror target materialization's owner-writable normalization."""
    if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise ImportedSourceError("selected source mode contains special bits")
    return stat.S_IMODE(mode) | (0o700 if directory else 0o600)


def _stable_stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _same_node(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _require_exact_name(directory: int, name: str) -> None:
    """Prove one component's spelling without allocating an unbounded list."""
    try:
        with os.scandir(directory) as iterator:
            for count, entry in enumerate(iterator, start=1):
                if count > MAX_TREE_ENTRIES:
                    raise ImportedSourceError(
                        "source directory exceeds the entry limit"
                    )
                if entry.name == name:
                    return
    except OSError as exc:
        raise ImportedSourceError(
            "selected source directory could not be listed safely"
        ) from exc
    raise ImportedSourceError("selected source path does not match on-disk spelling")


def _bounded_directory_names(
    directory: int,
    *,
    limit: int,
    overflow_message: str,
) -> list[str]:
    """Return sorted names after rejecting the first entry beyond ``limit``."""
    names: list[str] = []
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(names) >= limit:
                    raise ImportedSourceError(overflow_message)
                names.append(entry.name)
    except OSError as exc:
        raise ImportedSourceError(
            "selected source directory could not be listed safely"
        ) from exc
    return sorted(names)


def _open_root(source_root: Path) -> int:
    if not source_root.is_absolute():
        raise ImportedSourceError("bundle source root must be absolute")
    descriptor: int | None = None
    try:
        descriptor = os.open(source_root, _DIRECTORY_FLAGS)
        current = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ImportedSourceError("bundle source root is not safely readable") from exc
    if not stat.S_ISDIR(current.st_mode):
        os.close(descriptor)
        raise ImportedSourceError("bundle source root must be a real directory")
    return descriptor


def _open_child_directory(parent: int, name: str) -> int:
    descriptor: int | None = None
    try:
        _require_exact_name(parent, name)
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ImportedSourceError("selected tree contains an unsafe directory") from exc
    if not stat.S_ISDIR(before.st_mode) or not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise ImportedSourceError("selected tree path must contain real directories")
    if not _same_node(before, opened):
        os.close(descriptor)
        raise ImportedSourceError("selected tree directory changed during capture")
    return descriptor


def _open_directory_path(root: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root)
    try:
        for part in parts:
            child = _open_child_directory(current, part)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _read_descriptor(descriptor: int) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ImportedSourceError("selected source node must be a regular file")
    if before.st_size > MAX_FILE_BYTES:
        raise ImportedSourceError("selected source file exceeds the size limit")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, _CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise ImportedSourceError("selected source file exceeds the size limit")
    after = os.fstat(descriptor)
    if _stable_stat_key(before) != _stable_stat_key(after):
        raise ImportedSourceError("selected source file changed during capture")
    return b"".join(chunks), before


def _read_regular_child(
    parent: int,
    name: str,
    before: os.stat_result,
    consume: Callable[[int], None] | None = None,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
    except OSError as exc:
        raise ImportedSourceError(
            "selected source file is not safely readable"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise ImportedSourceError("selected source node must be a regular file")
        if not _same_node(before, opened):
            raise ImportedSourceError("selected source file changed during capture")
        content, captured = _read_descriptor(descriptor)
        if consume is not None:
            consume(len(content))
        after_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _stable_stat_key(before) != _stable_stat_key(after_path):
            raise ImportedSourceError("selected source file changed during capture")
        return content, captured
    except OSError as exc:
        raise ImportedSourceError(
            "selected source file is not safely readable"
        ) from exc
    finally:
        os.close(descriptor)


def _read_regular_path(
    root: int,
    parts: tuple[str, ...],
    consume: Callable[[int], None] | None = None,
) -> tuple[bytes, os.stat_result]:
    if not parts:
        raise ImportedSourceError("selected source file path is empty")
    parent = _open_directory_path(root, parts[:-1])
    try:
        try:
            _require_exact_name(parent, parts[-1])
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise ImportedSourceError(
                "selected source file is not safely readable"
            ) from exc
        return _read_regular_child(parent, parts[-1], before, consume)
    finally:
        os.close(parent)


def _normalize_link_target(parent_relative: str, target_text: str) -> str:
    if not target_text or PurePosixPath(target_text).is_absolute():
        raise ImportedSourceError("selected tree links must be relative")
    if (
        "\\" in target_text
        or _has_forbidden_text(target_text)
        or target_text.endswith("/")
        or "//" in target_text
    ):
        raise ImportedSourceError("selected tree link target is not portable")
    try:
        target_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ImportedSourceError("selected tree link target is not UTF-8") from exc
    raw_parts = target_text.split("/")
    if any(component in {"", "."} for component in raw_parts):
        raise ImportedSourceError("selected tree link target is not canonical")
    saw_name = False
    for component in raw_parts:
        if component == ".." and saw_name:
            raise ImportedSourceError("selected tree link target is reducible")
        if component != "..":
            saw_name = True

    stack = [] if parent_relative == "." else parent_relative.split("/")
    for component in raw_parts:
        if component == "..":
            if not stack:
                raise ImportedSourceError("selected tree contains an external link")
            stack.pop()
            continue
        stack.append(_canonical_component(component, what="link target"))
    if not stack:
        raise ImportedSourceError("selected tree link may not target its root")
    return "/".join(stack)


def _snapshot_link(
    selected_root: int,
    parent: int,
    name: str,
    before: os.stat_result,
    parent_relative: str,
    relative_path: str,
    consume: Callable[[int], None] | None = None,
) -> ImportedTreeEntry:
    try:
        target_text = os.readlink(name, dir_fd=parent)
        logical_target = _normalize_link_target(parent_relative, target_text)
        content, target_stat = _resolve_regular_target(
            selected_root,
            logical_target,
            consume=consume,
        )
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        after_text = os.readlink(name, dir_fd=parent)
    except OSError as exc:
        raise ImportedSourceError("selected tree contains a broken link") from exc
    if _stable_stat_key(before) != _stable_stat_key(after) or target_text != after_text:
        raise ImportedSourceError("selected tree link changed during capture")
    return ImportedTreeEntry(
        kind="link",
        relative_path=relative_path,
        normalized_mode=_normalize_mode(target_stat.st_mode, directory=False),
        content=content,
        link_target=logical_target,
    )


def _resolve_regular_target(
    selected_root: int,
    logical_target: str,
    *,
    consume: Callable[[int], None] | None,
    visited: frozenset[tuple[int, int]] = frozenset(),
    expansions: int = 0,
) -> tuple[bytes, os.stat_result]:
    parts = _canonical_relative_path(logical_target, what="link target")
    parent = _open_directory_path(selected_root, parts[:-1])
    try:
        try:
            _require_exact_name(parent, parts[-1])
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise ImportedSourceError("selected tree contains a broken link") from exc
        if stat.S_ISREG(before.st_mode):
            return _read_regular_child(parent, parts[-1], before, consume)
        if not stat.S_ISLNK(before.st_mode):
            raise ImportedSourceError(
                "selected tree link target must resolve to a regular file"
            )
        if expansions >= MAX_LINK_EXPANSIONS:
            raise ImportedSourceError("selected tree link expansion limit exceeded")
        identity = (before.st_dev, before.st_ino)
        if identity in visited:
            raise ImportedSourceError("selected tree contains a link cycle")
        try:
            target_text = os.readlink(parts[-1], dir_fd=parent)
            parent_relative = "/".join(parts[:-1]) or "."
            next_target = _normalize_link_target(parent_relative, target_text)
            after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            after_text = os.readlink(parts[-1], dir_fd=parent)
        except OSError as exc:
            raise ImportedSourceError("selected tree contains a broken link") from exc
        if (
            _stable_stat_key(before) != _stable_stat_key(after)
            or target_text != after_text
        ):
            raise ImportedSourceError("selected tree link changed during capture")
        return _resolve_regular_target(
            selected_root,
            next_target,
            consume=consume,
            visited=visited | {identity},
            expansions=expansions + 1,
        )
    finally:
        os.close(parent)


def _portable_child_path(parent_relative: str, name: str) -> str:
    return name if parent_relative == "." else f"{parent_relative}/{name}"


def _scan_directory(
    selected_root: int,
    directory: int,
    relative_path: str,
    entries: list[ImportedTreeEntry],
    portable_paths: dict[str, str],
    budget: _TreeBudget,
    consume: Callable[[int], None],
    depth: int,
    parent: int | None = None,
    name_in_parent: str | None = None,
) -> None:
    try:
        before = os.fstat(directory)
    except OSError as exc:
        raise ImportedSourceError("selected tree could not be listed safely") from exc
    budget.add_entry(relative_path, depth)
    names = _bounded_directory_names(
        directory,
        limit=MAX_TREE_ENTRIES - budget.entries,
        overflow_message="selected tree exceeds the entry limit",
    )
    entries.append(
        ImportedTreeEntry(
            kind="directory",
            relative_path=relative_path,
            normalized_mode=_normalize_mode(before.st_mode, directory=True),
        )
    )
    for name in names:
        _canonical_component(name, what="selected tree path")
        child_relative = _portable_child_path(relative_path, name)
        folded = child_relative.casefold()
        previous = portable_paths.get(folded)
        if previous is not None and previous != child_relative:
            raise ImportedSourceError(
                f"selected tree has a case-fold collision: {previous!r} and "
                f"{child_relative!r}"
            )
        portable_paths[folded] = child_relative
        try:
            child_stat = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            raise ImportedSourceError(
                "selected tree node is not safely readable"
            ) from exc
        if stat.S_ISDIR(child_stat.st_mode):
            child = _open_child_directory(directory, name)
            try:
                _scan_directory(
                    selected_root,
                    child,
                    child_relative,
                    entries,
                    portable_paths,
                    budget,
                    consume,
                    depth + 1,
                    directory,
                    name,
                )
            finally:
                os.close(child)
        elif stat.S_ISREG(child_stat.st_mode):
            budget.add_entry(child_relative, depth + 1)
            content, captured = _read_regular_child(
                directory, name, child_stat, consume
            )
            entries.append(
                ImportedTreeEntry(
                    kind="file",
                    relative_path=child_relative,
                    normalized_mode=_normalize_mode(captured.st_mode, directory=False),
                    content=content,
                )
            )
        elif stat.S_ISLNK(child_stat.st_mode):
            budget.add_entry(child_relative, depth + 1)
            entries.append(
                _snapshot_link(
                    selected_root,
                    directory,
                    name,
                    child_stat,
                    relative_path,
                    child_relative,
                    consume,
                )
            )
        else:
            raise ImportedSourceError(
                "selected tree contains a special filesystem node"
            )
    try:
        after_names = _bounded_directory_names(
            directory,
            limit=len(names),
            overflow_message="selected tree changed during capture",
        )
        after = os.fstat(directory)
    except OSError as exc:
        raise ImportedSourceError("selected tree changed during capture") from exc
    if names != after_names or _stable_stat_key(before) != _stable_stat_key(after):
        raise ImportedSourceError("selected tree changed during capture")
    if parent is not None and name_in_parent is not None:
        try:
            after_path = os.stat(name_in_parent, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise ImportedSourceError("selected tree changed during capture") from exc
        if _stable_stat_key(before) != _stable_stat_key(after_path):
            raise ImportedSourceError("selected tree changed during capture")


class _Digest(Protocol):
    def update(self, value: bytes) -> object: ...


def _frame(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def framed_tree_sha256(entries: tuple[ImportedTreeEntry, ...]) -> str:
    """Hash node kind/path/mode/link identity/content with explicit framing."""
    digest = hashlib.sha256()
    _frame(digest, b"promptdeploy-imported-tree-v1")
    for entry in entries:
        _frame(digest, entry.kind.encode("ascii"))
        _frame(digest, entry.relative_path.encode("utf-8"))
        _frame(digest, f"{entry.normalized_mode:04o}".encode("ascii"))
        _frame(digest, (entry.link_target or "").encode("utf-8"))
        _frame(digest, entry.content or b"")
    return f"sha256:{digest.hexdigest()}"


def validate_imported_tree_snapshot(snapshot: ImportedTreeSnapshot) -> None:
    """Reject a self-hashed snapshot whose topology cannot be materialized."""
    if snapshot.tree_sha256 != framed_tree_sha256(snapshot.entries):
        raise ImportedSourceError("snapshot digest does not match its entries")
    if len(snapshot.entries) > MAX_TREE_ENTRIES:
        raise ImportedSourceError("snapshot exceeds the entry limit")

    entries = {entry.relative_path: entry for entry in snapshot.entries}
    portable_paths: dict[str, str] = {}
    total_bytes = 0
    for entry in snapshot.entries:
        folded = entry.relative_path.casefold()
        previous = portable_paths.get(folded)
        if previous is not None and previous != entry.relative_path:
            raise ImportedSourceError(
                f"snapshot has a case-fold collision: {previous!r} and "
                f"{entry.relative_path!r}"
            )
        portable_paths[folded] = entry.relative_path
        if entry.content is not None:
            if len(entry.content) > MAX_FILE_BYTES:
                raise ImportedSourceError("snapshot file exceeds the size limit")
            total_bytes += len(entry.content)
            if total_bytes > MAX_TREE_BYTES:
                raise ImportedSourceError("snapshot exceeds the byte limit")
        normalized = _normalize_mode(
            entry.normalized_mode,
            directory=entry.kind == "directory",
        )
        if normalized != entry.normalized_mode:
            raise ImportedSourceError("snapshot entry mode is not normalized")
        if entry.relative_path == ".":
            continue
        parent_path = (
            entry.relative_path.rsplit("/", 1)[0] if "/" in entry.relative_path else "."
        )
        parent = entries.get(parent_path)
        if parent is None:
            raise ImportedSourceError("snapshot entry parent is missing")
        if parent.kind != "directory":
            raise ImportedSourceError(
                "snapshot entry is nested beneath a non-directory"
            )

    for entry in snapshot.entries:
        if entry.kind != "link":
            continue
        assert entry.link_target is not None
        target = entries.get(entry.link_target)
        if target is None:
            raise ImportedSourceError("snapshot link target is missing")
        visited = {entry.relative_path}
        expansions = 0
        while target.kind == "link":
            expansions += 1
            if expansions > MAX_LINK_EXPANSIONS:
                raise ImportedSourceError("snapshot link expansion limit exceeded")
            if target.relative_path in visited:
                raise ImportedSourceError("snapshot contains a link cycle")
            visited.add(target.relative_path)
            assert target.link_target is not None
            next_target = entries.get(target.link_target)
            if next_target is None:
                raise ImportedSourceError("snapshot link target is missing")
            target = next_target
        if target.kind != "file":
            raise ImportedSourceError(
                "snapshot link target must resolve to a regular file"
            )
        if (
            entry.content != target.content
            or entry.normalized_mode != target.normalized_mode
        ):
            raise ImportedSourceError(
                "snapshot link payload does not match its regular-file target"
            )


class BundleSnapshotSession:
    """Capture all selected payloads from one held bundle-root descriptor."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        root = _open_root(source_root)
        try:
            root_identity = os.fstat(root)
        except OSError as exc:
            os.close(root)
            raise ImportedSourceError(
                "bundle source root is not safely readable"
            ) from exc
        self._root = root
        self._root_identity = root_identity
        self._bundle_bytes = 0
        self._closed = False

    def __enter__(self) -> BundleSnapshotSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.close(audit=exc_type is None)

    def _require_open(self) -> None:
        if self._closed:
            raise ImportedSourceError("bundle snapshot session is closed")

    def _consume_bundle_bytes(self, size: int) -> None:
        self._bundle_bytes += size
        if self._bundle_bytes > MAX_BUNDLE_BYTES:
            raise ImportedSourceError("bundle snapshot exceeds the byte limit")

    def _audit_root(self) -> None:
        self._require_open()
        replacement = _open_root(self.source_root)
        try:
            if not _same_node(self._root_identity, os.fstat(replacement)):
                raise ImportedSourceError("bundle source root changed during capture")
        finally:
            os.close(replacement)

    def read_regular(self, relative_path: str) -> ImportedFileSnapshot:
        """Capture one metadata/support file from the held source root."""
        self._require_open()
        parts = _canonical_relative_path(relative_path, what="selected file path")
        content, captured = _read_regular_path(
            self._root,
            parts,
            self._consume_bundle_bytes,
        )
        self._audit_root()
        return ImportedFileSnapshot(
            relative_path=relative_path,
            normalized_mode=_normalize_mode(captured.st_mode, directory=False),
            content=content,
        )

    def scan_tree(self, relative_path: str) -> ImportedTreeSnapshot:
        """Capture one selected tree as immutable entries and a framed digest."""
        self._require_open()
        parts = _canonical_relative_path(relative_path, what="selected tree path")
        selected = _open_directory_path(self._root, parts)
        budget = _TreeBudget()

        def consume(size: int) -> None:
            budget.add_bytes(size)
            self._consume_bundle_bytes(size)

        try:
            try:
                selected_before = os.fstat(selected)
            except OSError as exc:
                raise ImportedSourceError(
                    "selected tree is not safely readable"
                ) from exc
            entries: list[ImportedTreeEntry] = []
            _scan_directory(
                selected,
                selected,
                ".",
                entries,
                {},
                budget,
                consume,
                0,
            )
        finally:
            os.close(selected)
        replacement = _open_directory_path(self._root, parts)
        try:
            if _stable_stat_key(selected_before) != _stable_stat_key(
                os.fstat(replacement)
            ):
                raise ImportedSourceError("selected tree changed during capture")
        finally:
            os.close(replacement)
        self._audit_root()
        ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
        digest = framed_tree_sha256(ordered)
        return ImportedTreeSnapshot(relative_path, ordered, digest)

    def scan_directory_shallow(self, relative_path: str) -> ImportedDirectorySnapshot:
        """Capture names and node kinds without reading child file contents."""
        self._require_open()
        parts = _canonical_relative_path(relative_path, what="inventory path")
        selected = _open_directory_path(self._root, parts)
        try:
            try:
                before = os.fstat(selected)
            except OSError as exc:
                raise ImportedSourceError(
                    "inventory directory could not be listed safely"
                ) from exc
            names = _bounded_directory_names(
                selected,
                limit=MAX_TREE_ENTRIES,
                overflow_message="inventory directory exceeds the entry limit",
            )
            children: list[tuple[str, DirectoryChildKind]] = []
            child_stats: dict[str, tuple[int, ...]] = {}
            folded: dict[str, str] = {}
            for name in names:
                _canonical_component(name, what="inventory child")
                previous = folded.get(name.casefold())
                if previous is not None and previous != name:
                    raise ImportedSourceError(
                        f"inventory has a case-fold collision: {previous!r} and "
                        f"{name!r}"
                    )
                folded[name.casefold()] = name
                try:
                    child = os.stat(name, dir_fd=selected, follow_symlinks=False)
                except OSError as exc:
                    raise ImportedSourceError(
                        "inventory child is not safely readable"
                    ) from exc
                if stat.S_ISDIR(child.st_mode):
                    kind: DirectoryChildKind = "directory"
                elif stat.S_ISREG(child.st_mode):
                    kind = "file"
                elif stat.S_ISLNK(child.st_mode):
                    kind = "link"
                else:
                    raise ImportedSourceError(
                        "inventory contains a special filesystem node"
                    )
                children.append((name, kind))
                child_stats[name] = _stable_stat_key(child)

            after_names = _bounded_directory_names(
                selected,
                limit=len(names),
                overflow_message="inventory directory changed during capture",
            )
            try:
                after = os.fstat(selected)
            except OSError as exc:
                raise ImportedSourceError(
                    "inventory directory changed during capture"
                ) from exc
            if names != after_names or _stable_stat_key(before) != _stable_stat_key(
                after
            ):
                raise ImportedSourceError("inventory directory changed during capture")
            for name in names:
                try:
                    after_child = os.stat(name, dir_fd=selected, follow_symlinks=False)
                except OSError as exc:
                    raise ImportedSourceError(
                        "inventory directory changed during capture"
                    ) from exc
                if child_stats[name] != _stable_stat_key(after_child):
                    raise ImportedSourceError(
                        "inventory directory changed during capture"
                    )
        finally:
            os.close(selected)
        replacement = _open_directory_path(self._root, parts)
        try:
            try:
                replacement_stat = os.fstat(replacement)
            except OSError as exc:
                raise ImportedSourceError(
                    "inventory directory changed during capture"
                ) from exc
            if _stable_stat_key(before) != _stable_stat_key(replacement_stat):
                raise ImportedSourceError("inventory directory changed during capture")
        finally:
            os.close(replacement)
        self._audit_root()
        return ImportedDirectorySnapshot(
            relative_path,
            _normalize_mode(before.st_mode, directory=True),
            tuple(children),
            _stable_stat_key(before),
        )

    def close(self, *, audit: bool = True) -> None:
        if self._closed:
            return
        try:
            if audit:
                self._audit_root()
        finally:
            os.close(self._root)
            self._closed = True


def capture_imported_tree(
    source_root: Path, relative_path: str
) -> ImportedTreeSnapshot:
    """Capture one selected tree without retaining live filesystem authority."""
    with BundleSnapshotSession(source_root) as session:
        return session.scan_tree(relative_path)


def capture_imported_file(
    source_root: Path, relative_path: str
) -> ImportedFileSnapshot:
    """Capture one selected regular file with no followed path components."""
    with BundleSnapshotSession(source_root) as session:
        return session.read_regular(relative_path)
