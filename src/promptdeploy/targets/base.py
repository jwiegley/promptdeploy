from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..skilltree import scan_skill_source

ANVIL_MCP_NAMES = frozenset({"anvil", "anvil-tools"})


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


def materialize_skill_tree(
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


def install_skill_tree_atomically(
    source_dir: Path,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> None:
    """Stage a skill, swap it into place, and restore the old tree on failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".promptdeploy-"))
    staged = temporary / "skill"
    backup = temporary / "previous"
    had_destination = destination.is_symlink() or destination.exists()
    cleanup_temporary = True
    try:
        materialize_skill_tree(source_dir, staged, transform_skill_md)
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
                        "Skill installation failed and the prior tree could not be "
                        f"restored; backup retained at {backup}"
                    ) from restore_error
            raise
    finally:
        if cleanup_temporary:
            with contextlib.suppress(OSError):
                _make_tree_owner_writable(temporary)
            shutil.rmtree(temporary, ignore_errors=True)


def _raise_walk_error(error: OSError) -> None:
    raise error


def _skill_tree_snapshot(
    root: Path,
) -> tuple[tuple[str, str, int, bytes | None], ...] | None:
    """Return a strict recursive snapshot, or None for unsafe node types."""
    if root.is_symlink() or not root.is_dir():
        return None
    entries: list[tuple[str, str, int, bytes | None]] = []
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


def transformed_skill_tree_matches(
    source_dir: Path,
    destination: Path,
    transform_skill_md: Callable[[bytes], bytes],
) -> bool:
    """Compare a deployed skill with the complete target-specific rendering."""
    with tempfile.TemporaryDirectory(prefix="promptdeploy-skill-verify-") as temp:
        expected = Path(temp) / "skill"
        materialize_skill_tree(source_dir, expected, transform_skill_md)
        expected_snapshot = _skill_tree_snapshot(expected)
        deployed_snapshot = _skill_tree_snapshot(destination)
        return expected_snapshot is not None and expected_snapshot == deployed_snapshot


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
        remote file, so its mcp manifest hash must fold current env values
        (mirroring _expand_env_for_hash for models). Retained for remote
        Claude merge behavior; use :attr:`mcp_hash_includes_env` for the
        broader "MCP config bakes env values" behavior.
        """
        return False

    @property
    def mcp_hash_includes_env(self) -> bool:
        """True when MCP env/header references are expanded at deploy time.

        Targets that bake expanded MCP secrets into their config need manifest
        hashes to include current env values, so rotating a secret triggers a
        redeploy even when the source YAML is unchanged.
        """
        return False

    @abstractmethod
    def deploy_agent(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_command(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_skill(self, name: str, source_dir: Path) -> None: ...

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
    ) -> bool | None:
        """Compare one deployed item with the canonical source rendering.

        Return ``True`` or ``False`` only when the target can inspect the
        named item without comparing unrelated state. Return ``None`` when
        semantic comparison is unsupported; the deploy loop then falls back
        to the single-file byte comparison methods below.

        Merged configuration targets use this hook for named MCP entries so a
        matching manifest hash cannot hide a stale or missing registration.
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
