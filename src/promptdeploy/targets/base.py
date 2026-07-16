from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ..imported_tree import (
    ImportedSourceError,
    ImportedTreeSnapshot,
    validate_imported_tree_snapshot,
)
from ..names import require_canonical_item_name
from ..skilltree import scan_skill_source

ANVIL_MCP_NAMES = frozenset({"anvil", "anvil-tools"})
MANAGED_BUNDLE_RSYNC_INCLUDES = (
    ".promptdeploy/",
    ".promptdeploy/bundles/",
    ".promptdeploy/bundles/**",
)
SkillTreeSource: TypeAlias = Path | ImportedTreeSnapshot
InstalledTreeEntry: TypeAlias = tuple[
    Literal["directory", "file"], str, int, bytes | None
]
InstalledTreeSnapshot: TypeAlias = tuple[InstalledTreeEntry, ...]

_IMPORTED_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_IMPORTED_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_INSTALLED_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_INSTALLED_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_INSTALLED_READ_CHUNK = 128 * 1024


class _InstalledTreeMismatch(Exception):
    """A target tree differs from its immutable expected view."""


class UnsafeManagedBundlePath(ValueError):
    """A target's owned bundle path contains an unsafe parent node."""


def _make_tree_owner_writable(root: Path) -> None:
    """Make a copied/staged tree removable and transformable by its owner."""
    if root.is_symlink() or not root.exists():
        return
    for current, directories, files in os.walk(
        root, followlinks=False, onerror=_raise_walk_error
    ):
        current_path = Path(current)
        current_stat = current_path.lstat()
        current_path.chmod(stat.S_IMODE(current_stat.st_mode) | 0o700)
        for name in directories:
            child = current_path / name
            child_stat = child.lstat()
            if stat.S_ISDIR(child_stat.st_mode):
                child.chmod(stat.S_IMODE(child_stat.st_mode) | 0o700)
        for name in files:
            child = current_path / name
            child_stat = child.lstat()
            if stat.S_ISREG(child_stat.st_mode):
                child.chmod(stat.S_IMODE(child_stat.st_mode) | 0o600)


def _replace_read_only_file(path: Path, contents: bytes) -> None:
    """Atomically replace PATH with private writable transformed contents."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _materialize_primary_skill_tree(
    source_dir: Path,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> None:
    """Copy and transform one complete, source-confined skill tree."""
    source, _files = scan_skill_source(source_dir)
    try:
        shutil.copytree(source, destination, symlinks=False)
    except BaseException:
        with contextlib.suppress(OSError):
            _make_tree_owner_writable(destination)
        raise
    _make_tree_owner_writable(destination)
    skill_md = destination / "SKILL.md"
    if skill_md.exists():
        transformed = transform_skill_md(skill_md.read_bytes())
        _replace_read_only_file(skill_md, transformed)


def _expected_imported_skill_tree(
    source: ImportedTreeSnapshot,
    transform_skill_md: Callable[[bytes], bytes],
) -> InstalledTreeSnapshot:
    """Derive every target node and byte before filesystem mutation."""
    validate_imported_tree_snapshot(source)
    expected: list[InstalledTreeEntry] = []
    found_skill_md = False
    for entry in source.entries:
        if entry.kind == "directory":
            expected.append(
                ("directory", entry.relative_path, entry.normalized_mode, None)
            )
            continue
        assert entry.content is not None
        contents = entry.content
        if entry.relative_path == "SKILL.md":
            found_skill_md = True
            contents = transform_skill_md(contents)
            if not isinstance(contents, bytes):
                raise TypeError("skill transform must return bytes")
        expected.append(("file", entry.relative_path, entry.normalized_mode, contents))
    if not found_skill_md:
        raise ImportedSourceError("imported skill snapshot lacks root SKILL.md")
    return tuple(sorted(expected, key=lambda value: value[1]))


def _write_all(descriptor: int, contents: bytes) -> None:
    remaining = memoryview(contents)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("snapshot file write made no progress")
        remaining = remaining[written:]


def _materialize_expected_tree(
    expected: InstalledTreeSnapshot,
    destination: Path,
) -> None:
    directories = [entry for entry in expected if entry[0] == "directory"]
    files = [entry for entry in expected if entry[0] == "file"]
    directory_order = sorted(
        directories,
        key=lambda entry: (entry[1].count("/"), entry[1]),
    )
    created_destination = False
    try:
        os.mkdir(destination, 0o700)
        created_destination = True
        for _kind, relative_path, _mode, _contents in directory_order:
            if relative_path != ".":
                os.mkdir(destination / relative_path, 0o700)
        for _kind, relative_path, mode, contents in files:
            assert contents is not None
            descriptor = os.open(
                destination / relative_path,
                _IMPORTED_FILE_FLAGS,
                mode,
            )
            try:
                _write_all(descriptor, contents)
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
        for _kind, relative_path, mode, _contents in reversed(directory_order):
            directory = (
                destination if relative_path == "." else destination / relative_path
            )
            descriptor = os.open(directory, _IMPORTED_DIRECTORY_FLAGS)
            try:
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
    except BaseException:
        if created_destination:
            with contextlib.suppress(OSError):
                _make_tree_owner_writable(destination)
            shutil.rmtree(destination, ignore_errors=True)
        raise


def _materialize_imported_skill_tree(
    source: ImportedTreeSnapshot,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> None:
    expected = _expected_imported_skill_tree(source, transform_skill_md)
    _materialize_expected_tree(expected, destination)


def materialize_skill_tree(
    source: SkillTreeSource,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> None:
    """Materialize a primary path or an already accepted imported snapshot."""
    if isinstance(source, ImportedTreeSnapshot):
        _materialize_imported_skill_tree(source, destination, transform_skill_md)
        return
    _materialize_primary_skill_tree(source, destination, transform_skill_md)


def _install_tree_atomically(
    destination: Path,
    materialize: Callable[[Path], None],
    *,
    staged_name: str,
    artifact_label: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".promptdeploy-"))
    staged = temporary / staged_name
    backup = temporary / "previous"
    had_destination = destination.is_symlink() or destination.exists()
    cleanup_temporary = True
    try:
        materialize(staged)
        if had_destination:
            os.replace(destination, backup)
        try:
            os.replace(staged, destination)
        except BaseException:
            if had_destination:
                try:
                    os.replace(backup, destination)
                except BaseException as restore_error:
                    cleanup_temporary = False
                    raise RuntimeError(
                        f"{artifact_label} installation failed and the prior tree "
                        "could not be "
                        f"restored; backup retained at {backup}"
                    ) from restore_error
            raise
    finally:
        if cleanup_temporary:
            with contextlib.suppress(OSError):
                _make_tree_owner_writable(temporary)
            shutil.rmtree(temporary, ignore_errors=True)


def install_skill_tree_atomically(
    source: SkillTreeSource,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> None:
    """Stage a skill, swap it into place, and restore the old tree on failure."""
    _install_tree_atomically(
        destination,
        lambda staged: materialize_skill_tree(
            source,
            staged,
            transform_skill_md,
        ),
        staged_name="skill",
        artifact_label="Skill",
    )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _skill_tree_snapshot(
    root: Path,
) -> InstalledTreeSnapshot | None:
    """Return a strict recursive snapshot, or None for unsafe node types."""
    if root.is_symlink() or not root.is_dir():
        return None
    entries: list[InstalledTreeEntry] = []
    try:
        for current, directories, files in os.walk(
            root, followlinks=False, onerror=_raise_walk_error
        ):
            directories.sort()
            files.sort()
            current_path = Path(current)
            current_stat = current_path.lstat()
            if not stat.S_ISDIR(current_stat.st_mode):
                return None
            relative_dir = current_path.relative_to(root).as_posix() or "."
            entries.append(
                ("directory", relative_dir, stat.S_IMODE(current_stat.st_mode), None)
            )
            for name in directories:
                child_stat = (current_path / name).lstat()
                if not stat.S_ISDIR(child_stat.st_mode):
                    return None
            for name in files:
                child = current_path / name
                child_stat = child.lstat()
                if not stat.S_ISREG(child_stat.st_mode):
                    return None
                entries.append(
                    (
                        "file",
                        child.relative_to(root).as_posix(),
                        stat.S_IMODE(child_stat.st_mode),
                        child.read_bytes(),
                    )
                )
    except OSError:
        return None
    return tuple(entries)


def _require_installed_match(condition: bool) -> None:
    if not condition:
        raise _InstalledTreeMismatch


def _installed_stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_expected_installed_file(
    parent: int,
    name: str,
    mode: int,
    expected: bytes,
) -> None:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require_installed_match(
        stat.S_ISREG(before.st_mode)
        and stat.S_IMODE(before.st_mode) == mode
        and before.st_size == len(expected)
    )
    descriptor = os.open(name, _INSTALLED_FILE_FLAGS, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        _require_installed_match(
            stat.S_ISREG(opened.st_mode)
            and (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino)
            and _installed_stat_key(before) == _installed_stat_key(opened)
        )
        chunks: list[bytes] = []
        captured = 0
        while captured < len(expected):
            chunk = os.read(
                descriptor,
                min(_INSTALLED_READ_CHUNK, len(expected) - captured),
            )
            _require_installed_match(bool(chunk))
            chunks.append(chunk)
            captured += len(chunk)
        extra = os.read(descriptor, 1)
        _require_installed_match(not extra and b"".join(chunks) == expected)
        after = os.fstat(descriptor)
        after_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require_installed_match(
            _installed_stat_key(opened) == _installed_stat_key(after)
            and _installed_stat_key(before) == _installed_stat_key(after_path)
        )
    finally:
        os.close(descriptor)


def _compare_installed_directory(
    directory: int,
    relative_path: str,
    expected: dict[str, InstalledTreeEntry],
    children: dict[str, tuple[InstalledTreeEntry, ...]],
    seen: list[int],
) -> None:
    before = os.fstat(directory)
    expected_directory = expected[relative_path]
    _require_installed_match(
        stat.S_ISDIR(before.st_mode)
        and stat.S_IMODE(before.st_mode) == expected_directory[2]
    )

    names: list[str] = []
    with os.scandir(directory) as iterator:
        for entry in iterator:
            seen[0] += 1
            _require_installed_match(seen[0] <= len(expected))
            names.append(entry.name)
    expected_children = children.get(relative_path, ())
    expected_names = [entry[1].rsplit("/", 1)[-1] for entry in expected_children]
    _require_installed_match(sorted(names) == expected_names)

    for expected_child in expected_children:
        kind, child_relative, mode, content = expected_child
        name = child_relative.rsplit("/", 1)[-1]
        if kind == "file":
            assert content is not None
            _read_expected_installed_file(directory, name, mode, content)
            continue

        child_before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _require_installed_match(
            stat.S_ISDIR(child_before.st_mode)
            and stat.S_IMODE(child_before.st_mode) == mode
        )
        child = os.open(name, _INSTALLED_DIRECTORY_FLAGS, dir_fd=directory)
        try:
            child_opened = os.fstat(child)
            _require_installed_match(
                stat.S_ISDIR(child_opened.st_mode)
                and (child_before.st_dev, child_before.st_ino)
                == (child_opened.st_dev, child_opened.st_ino)
                and _installed_stat_key(child_before)
                == _installed_stat_key(child_opened)
            )
            _compare_installed_directory(
                child,
                child_relative,
                expected,
                children,
                seen,
            )
            child_after = os.fstat(child)
            child_after_path = os.stat(
                name,
                dir_fd=directory,
                follow_symlinks=False,
            )
            _require_installed_match(
                _installed_stat_key(child_opened) == _installed_stat_key(child_after)
                and _installed_stat_key(child_before)
                == _installed_stat_key(child_after_path)
            )
        finally:
            os.close(child)

    after = os.fstat(directory)
    _require_installed_match(_installed_stat_key(before) == _installed_stat_key(after))


def _installed_tree_matches_expected(
    root: Path,
    expected_tree: InstalledTreeSnapshot,
) -> bool:
    """Compare through held descriptors, bounded by the immutable expectation."""
    expected = {entry[1]: entry for entry in expected_tree}
    children_lists: dict[str, list[InstalledTreeEntry]] = {}
    for entry in expected_tree:
        if entry[1] == ".":
            continue
        parent = entry[1].rsplit("/", 1)[0] if "/" in entry[1] else "."
        children_lists.setdefault(parent, []).append(entry)
    children = {
        parent: tuple(sorted(entries, key=lambda entry: entry[1]))
        for parent, entries in children_lists.items()
    }

    descriptor: int | None = None
    try:
        before = root.stat(follow_symlinks=False)
        descriptor = os.open(root, _INSTALLED_DIRECTORY_FLAGS)
        opened = os.fstat(descriptor)
        _require_installed_match(
            stat.S_ISDIR(before.st_mode)
            and stat.S_ISDIR(opened.st_mode)
            and (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino)
            and _installed_stat_key(before) == _installed_stat_key(opened)
        )
        _compare_installed_directory(descriptor, ".", expected, children, [1])
        after = os.fstat(descriptor)
        after_path = root.stat(follow_symlinks=False)
        _require_installed_match(
            _installed_stat_key(opened) == _installed_stat_key(after)
            and _installed_stat_key(before) == _installed_stat_key(after_path)
        )
        return True
    except (OSError, _InstalledTreeMismatch):
        return False
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def transformed_skill_tree_matches(
    source: SkillTreeSource,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> bool:
    """Compare a deployed skill with the complete target-specific rendering."""
    if isinstance(source, ImportedTreeSnapshot):
        expected_tree = _expected_imported_skill_tree(source, transform_skill_md)
        return _installed_tree_matches_expected(destination, expected_tree)
    with tempfile.TemporaryDirectory(prefix="promptdeploy-skill-verify-") as temp:
        expected_path = Path(temp) / "skill"
        materialize_skill_tree(source, expected_path, transform_skill_md)
        expected_snapshot = _skill_tree_snapshot(expected_path)
        deployed_snapshot = _skill_tree_snapshot(destination)
        return expected_snapshot is not None and expected_snapshot == deployed_snapshot


def _support_bundle_tree(content: bytes) -> InstalledTreeSnapshot:
    return (
        ("directory", ".", 0o755, None),
        ("file", "LICENSE", 0o644, content),
    )


def _materialize_support_bundle(content: bytes, destination: Path) -> None:
    _materialize_expected_tree(_support_bundle_tree(content), destination)


class Target(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def exists(self) -> bool: ...

    def prepare(self, *, verbose: bool = False) -> None:
        """Called before any deploy/read operations. No-op for local targets."""

    def finalize(self, *, verbose: bool = False) -> None:
        """Called after all deploy operations complete. No-op for local targets."""

    def cleanup(self) -> None:
        """Called to release resources (e.g. temp dirs) without pushing changes."""

    def managed_root(self) -> Path:
        """Return the target root that owns cross-surface support artifacts."""
        return self.manifest_path().parent

    def bundle_root(self) -> Path:
        """Return the hidden root for target-owned support bundles."""
        return self.managed_root() / ".promptdeploy" / "bundles"

    def bundle_path(self, name: str) -> Path:
        """Return one canonical target-owned support-bundle leaf."""
        require_canonical_item_name("bundle", name)
        return self.bundle_root() / name

    def _checked_bundle_root(self, *, create: bool) -> Path | None:
        managed = self.managed_root()
        if create:
            try:
                managed.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise UnsafeManagedBundlePath(
                    "managed target root is not safely writable"
                ) from exc
        try:
            managed_stat = managed.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise UnsafeManagedBundlePath(
                "managed target root is not safely readable"
            ) from exc
        if not stat.S_ISDIR(managed_stat.st_mode):
            raise UnsafeManagedBundlePath(
                "managed target root must be a real directory"
            )

        current = managed
        for component in (".promptdeploy", "bundles"):
            child = current / component
            try:
                child_stat = child.lstat()
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    child.mkdir(mode=0o755)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise UnsafeManagedBundlePath(
                        "managed bundle parent is not safely writable"
                    ) from exc
                try:
                    child_stat = child.lstat()
                except OSError as exc:
                    raise UnsafeManagedBundlePath(
                        "managed bundle parent is not safely readable"
                    ) from exc
            except OSError as exc:
                raise UnsafeManagedBundlePath(
                    "managed bundle parent is not safely readable"
                ) from exc
            if not stat.S_ISDIR(child_stat.st_mode):
                raise UnsafeManagedBundlePath(
                    "managed bundle parent must be a real directory"
                )
            current = child
        return current

    def deploy_bundle(self, name: str, content: bytes) -> None:
        """Atomically install the exact support-v1 bundle tree."""
        require_canonical_item_name("bundle", name)
        root = self._checked_bundle_root(create=True)
        assert root is not None
        _install_tree_atomically(
            root / name,
            lambda staged: _materialize_support_bundle(content, staged),
            staged_name="bundle",
            artifact_label="Bundle",
        )

    def bundle_exists(self, name: str) -> bool:
        """Return whether any node occupies the exact owned bundle leaf."""
        require_canonical_item_name("bundle", name)
        root = self._checked_bundle_root(create=False)
        if root is None:
            return False
        try:
            (root / name).lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise UnsafeManagedBundlePath(
                "managed bundle leaf is not safely readable"
            ) from exc
        return True

    def bundle_matches(self, name: str, content: bytes) -> bool:
        """Compare an owned support bundle without following target links."""
        require_canonical_item_name("bundle", name)
        root = self._checked_bundle_root(create=False)
        if root is None:
            return False
        return _installed_tree_matches_expected(
            root / name,
            _support_bundle_tree(content),
        )

    def remove_bundle(self, name: str) -> None:
        """Remove exactly one owned support-bundle leaf and no parent."""
        require_canonical_item_name("bundle", name)
        root = self._checked_bundle_root(create=False)
        if root is None:
            return
        destination = root / name
        try:
            destination_stat = destination.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise UnsafeManagedBundlePath(
                "managed bundle leaf is not safely readable"
            ) from exc
        if stat.S_ISDIR(destination_stat.st_mode):
            _make_tree_owner_writable(destination)
            shutil.rmtree(destination)
        else:
            destination.unlink()

    def deploy_settings(
        self, rendered: dict[str, Any], previous_keys: list[str]
    ) -> None:
        """Merge rendered Claude settings into the target's settings.json.

        Default no-op so non-Claude targets need no changes.
        """

    def remove_settings(self, previous_keys: list[str]) -> None:
        """Remove previously-managed settings keys. No-op by default."""

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        """Merge a Claude marketplace + its enabled plugins into settings.json.

        Default no-op so non-Claude targets need no changes.
        """

    def remove_marketplace(self, name: str) -> None:
        """Remove a marketplace and its enabled plugins. No-op by default."""

    def read_settings_json(self) -> dict[str, Any]:
        """Return the target's current settings.json as a dict.

        Returns ``{}`` when the target has no Claude settings file (the default
        for non-Claude targets).
        """
        return {}

    def rsync_includes(self) -> list[str] | None:
        """Return rsync include patterns for managed paths.

        When non-None, only these paths are synced to/from the remote.
        Returning None (the default) syncs the entire directory.
        """
        return None

    def rsync_push_includes(self) -> list[str] | None:
        """Return rsync include patterns for the remote push.

        Defaults to :meth:`rsync_includes`. Targets with machine-local runtime
        state may pull a broader tree for staging while pushing back only the
        files promptdeploy is allowed to modify.
        """
        return self.rsync_includes()

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Return True if this target would no-op the deploy for this item.

        When True, the deploy loop will not call the deploy method and will
        not record the item in the manifest -- ensuring idempotency for
        items that a target silently ignores.
        """
        return False

    def content_fingerprint(self, item_type: str) -> str | None:
        """Return a string describing target-side transform inputs, or None.

        The deploy loop folds this value into the manifest hash so that a
        config change which alters deployed bytes (e.g. a flipped injected
        model) invalidates the cache even when source bytes are unchanged.
        Default: no target-side transforms.
        """
        return None

    def effective_hash_input(
        self,
        item_type: str,
        name: str,
        metadata: dict[str, Any],
    ) -> Any:
        """Return the target-rendered semantic value covered by the manifest.

        Merged configuration targets override this for MCP and model entries
        so stripped cross-target fields and runtime-only secret references do
        not affect the persistent hash. Values actually baked into target
        output remain part of the returned structure.
        """
        return metadata

    def prepare_force_deploy(
        self, item_type: str, name: str, metadata: dict[str, Any]
    ) -> None:
        """Clear target-specific unmanaged state before a forced deploy.

        Most targets overwrite files directly, so they need no preparation.
        Targets that merge into shared config files can override this to remove
        an unmanaged entry that would otherwise block the managed write.
        """

    @property
    def remote_mcp_hash(self) -> bool:
        """True when this target bakes deploy-time-expanded MCP secrets into a
        remote file. Retained for remote Claude merge behavior; use
        :attr:`mcp_hash_includes_env` for the broader "MCP config bakes env
        values" behavior.
        """
        return False

    @property
    def mcp_hash_includes_env(self) -> bool:
        """True when MCP env/header references are expanded at deploy time.

        Effective hash inputs and rendered entries use this same policy so
        rotating a baked secret triggers a redeploy without hashing references
        that remain runtime-indirect.
        """
        return False

    @property
    def models_hash_includes_env(self) -> bool:
        """True when model deployment bakes current environment values."""
        return True

    @abstractmethod
    def deploy_agent(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_command(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_skill(self, name: str, source_dir: SkillTreeSource) -> None:
        """Deploy a primary skill directory or accepted imported snapshot."""
        ...

    @abstractmethod
    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_models(self, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_hook(self, name: str, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None: ...

    @abstractmethod
    def remove_agent(self, name: str) -> None: ...

    @abstractmethod
    def remove_command(self, name: str) -> None: ...

    @abstractmethod
    def remove_skill(self, name: str) -> None: ...

    @abstractmethod
    def remove_mcp_server(self, name: str) -> None: ...

    @abstractmethod
    def remove_models(self) -> None: ...

    @abstractmethod
    def remove_hook(self, name: str) -> None: ...

    @abstractmethod
    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        """Remove a deployed prompt by ``name``.

        ``target_path`` is the relative path that was recorded in the manifest
        when the prompt was last deployed. When provided, targets should
        prefer it as the authoritative location to unlink so that stale
        prompts cannot collide with unrelated user-authored files. When
        ``None`` (e.g. legacy manifests written before path tracking), the
        target may fall back to its previous heuristic.
        """
        ...

    def deployed_artifact_path(self, item_type: str, name: str) -> Path | None:
        """Return the relative path the most recent deploy wrote, if any.

        The deploy loop calls this after a successful deploy and stores the
        result in the manifest. The path is relative to the target's root.
        Default: returns ``None`` so existing targets opt in incrementally.
        """
        return None

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        """Drain and return warnings collected during the last batch of deploys.

        Returns a list of ``(item_name, [warning, ...])`` pairs. Targets that
        render templated prompts (``.poet``/``.j2``/``.jinja``) collect any
        warnings emitted by :func:`promptdeploy.poet.parse_poet` and surface
        them here so the deploy loop can print them. Default: nothing to
        report.
        """
        return []

    @abstractmethod
    def item_exists(self, item_type: str, name: str) -> bool:
        """Check if an item already exists at the deploy target path.

        Used to detect pre-existing items that were not deployed by
        promptdeploy and should not be overwritten or removed.
        """
        ...

    def item_matches_source(
        self,
        item_type: str,
        name: str,
        content: bytes,
        metadata: dict[str, Any] | None,
        source_path: Path | None = None,
        imported_tree: ImportedTreeSnapshot | None = None,
    ) -> bool | None:
        """Compare one deployed item with the canonical source rendering.

        Return ``True`` or ``False`` only when the target can inspect the
        named item without comparing unrelated state. Return ``None`` when
        semantic comparison is unsupported; the deploy loop then falls back
        to the single-file byte comparison methods below.

        For imported skills, ``imported_tree`` is the already accepted,
        path-independent source authority. Merged configuration targets use
        this hook for named MCP entries so a matching manifest hash cannot
        hide a stale or missing registration.
        """

        return None

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        """Return the bytes this target would write for a single-file artifact.

        Used by the deploy loop to decide whether a pre-existing on-disk
        file is byte-identical to what we would write -- if so, the item
        is silently adopted into the manifest rather than reported as
        pre-existing on every deploy.

        Returns ``None`` for items that are not single-file artifacts
        (e.g. skill directories, MCP/hook entries merged into JSON).
        """
        return None

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
        """Read the bytes currently on disk for a single-file artifact.

        Mirrors :meth:`would_deploy_bytes` so the deploy loop can compare
        on-disk content to what it would write. Returns ``None`` when the
        item is not a single-file artifact or no file is present.
        """
        return None

    @abstractmethod
    def manifest_path(self) -> Path: ...
