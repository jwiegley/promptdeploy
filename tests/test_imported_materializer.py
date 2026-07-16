"""Snapshot-only imported skill materialization regressions."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from promptdeploy import imported_tree as tree
from promptdeploy.targets import base
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.opencode import OpenCodeTarget
from promptdeploy.targets.remote import RemoteTarget


def _snapshot(*entries: tree.ImportedTreeEntry) -> tree.ImportedTreeSnapshot:
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    return tree.ImportedTreeSnapshot(
        "skills/demo",
        ordered,
        tree.framed_tree_sha256(ordered),
    )


def _complete_snapshot(skill: bytes = b"skill\n") -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o750),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o640, skill),
        tree.ImportedTreeEntry("file", "empty.bin", 0o600, b""),
        tree.ImportedTreeEntry("directory", "empty", 0o700),
        tree.ImportedTreeEntry("directory", "refs", 0o750),
        tree.ImportedTreeEntry("link", "refs/alias", 0o644, b"\x00body", "refs/body"),
        tree.ImportedTreeEntry("file", "refs/body", 0o644, b"\x00body"),
        tree.ImportedTreeEntry("file", "run", 0o755, b"#!/bin/sh\n"),
        tree.ImportedTreeEntry(
            "link",
            "top-alias",
            0o644,
            b"\x00body",
            "refs/alias",
        ),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_snapshot_materialization_is_exact_and_uses_no_primary_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("primary source I/O must not run")

    monkeypatch.setattr(base, "scan_skill_source", forbidden)
    monkeypatch.setattr("promptdeploy.targets.base.shutil.copytree", forbidden)
    previous_umask = os.umask(0o077)
    try:
        base.materialize_skill_tree(
            snapshot,
            destination,
            lambda contents: b"rendered:" + contents,
        )
    finally:
        os.umask(previous_umask)

    assert (destination / "SKILL.md").read_bytes() == b"rendered:skill\n"
    assert (destination / "empty.bin").read_bytes() == b""
    assert (destination / "empty").is_dir()
    assert (destination / "refs" / "alias").is_file()
    assert not (destination / "refs" / "alias").is_symlink()
    assert (destination / "top-alias").read_bytes() == b"\x00body"
    assert not (destination / "top-alias").is_symlink()
    assert _mode(destination) == 0o750
    assert _mode(destination / "SKILL.md") == 0o640
    assert _mode(destination / "empty") == 0o700
    assert _mode(destination / "refs") == 0o750
    assert _mode(destination / "run") == 0o755
    assert base.transformed_skill_tree_matches(
        snapshot,
        destination,
        lambda contents: b"rendered:" + contents,
    )


def test_linked_skill_markdown_is_transformed_and_materialized_as_regular_file(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("link", "SKILL.md", 0o644, b"body", "source.md"),
        tree.ImportedTreeEntry("file", "source.md", 0o644, b"body"),
    )
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(snapshot, destination, bytes.upper)
    assert (destination / "SKILL.md").read_bytes() == b"BODY"
    assert not (destination / "SKILL.md").is_symlink()


@pytest.mark.parametrize(
    "target_factory",
    [
        lambda path: ClaudeTarget("local", path),
        lambda path: CodexTarget("local", path),
        lambda path: DroidTarget("local", path),
        lambda path: OpenCodeTarget("local", path),
    ],
)
def test_local_target_skill_interfaces_use_the_accepted_snapshot(
    target_factory: Callable[[Path], base.Target],
    tmp_path: Path,
) -> None:
    snapshot = _complete_snapshot()
    mismatching_snapshot = _complete_snapshot(b"different\n")
    target = target_factory(tmp_path / "target")
    unavailable_diagnostic_path = tmp_path / "deleted-source" / "SKILL.md"

    target.deploy_skill("demo", snapshot)

    assert target.item_matches_source(
        "skill",
        "demo",
        b"skill\n",
        None,
        source_path=unavailable_diagnostic_path,
        imported_tree=snapshot,
    )
    assert not target.item_matches_source(
        "skill",
        "demo",
        b"different\n",
        None,
        source_path=unavailable_diagnostic_path,
        imported_tree=mismatching_snapshot,
    )
    assert (
        target.item_matches_source(
            "skill",
            "demo",
            b"skill\n",
            None,
        )
        is None
    )


def test_remote_target_forwards_snapshot_skill_interfaces(tmp_path: Path) -> None:
    snapshot = _complete_snapshot()
    mismatching_snapshot = _complete_snapshot(b"different\n")
    inner = ClaudeTarget("remote", tmp_path / "staging")
    target = RemoteTarget(
        inner,
        "example.invalid",
        Path("/remote/config"),
        tmp_path / "staging",
    )

    target.deploy_skill("demo", snapshot)

    assert target.item_matches_source(
        "skill",
        "demo",
        b"skill\n",
        None,
        source_path=tmp_path / "deleted-source" / "SKILL.md",
        imported_tree=snapshot,
    )
    assert not target.item_matches_source(
        "skill",
        "demo",
        b"different\n",
        None,
        source_path=tmp_path / "deleted-source" / "SKILL.md",
        imported_tree=mismatching_snapshot,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "root-link",
        "file-link",
        "directory-link",
        "extra-file",
        "missing-file",
        "extra-directory",
        "missing-empty-directory",
        "wrong-bytes",
        "wrong-file-mode",
        "wrong-directory-mode",
    ],
)
def test_snapshot_comparison_rejects_installed_tree_drift(
    mutation: str,
    tmp_path: Path,
) -> None:
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"
    transform = bytes.upper
    base.materialize_skill_tree(snapshot, destination, transform)

    if mutation == "root-link":
        shutil.rmtree(destination)
        outside = tmp_path / "outside"
        outside.mkdir()
        destination.symlink_to(outside, target_is_directory=True)
    elif mutation == "file-link":
        path = destination / "refs" / "body"
        path.unlink()
        path.symlink_to(destination / "SKILL.md")
    elif mutation == "directory-link":
        path = destination / "empty"
        path.rmdir()
        path.symlink_to(destination / "refs", target_is_directory=True)
    elif mutation == "extra-file":
        (destination / "extra").write_bytes(b"extra")
    elif mutation == "missing-file":
        (destination / "run").unlink()
    elif mutation == "extra-directory":
        (destination / "extra").mkdir()
    elif mutation == "missing-empty-directory":
        (destination / "empty").rmdir()
    elif mutation == "wrong-bytes":
        (destination / "run").write_bytes(b"#!/bin/xx\n")
    elif mutation == "wrong-file-mode":
        (destination / "run").chmod(0o700)
    else:
        (destination / "refs").chmod(0o700)

    assert not base.transformed_skill_tree_matches(snapshot, destination, transform)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform lacks FIFOs")
def test_snapshot_comparison_rejects_special_nodes(tmp_path: Path) -> None:
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(snapshot, destination, lambda contents: contents)
    os.mkfifo(destination / "unexpected-fifo")
    assert not base.transformed_skill_tree_matches(
        snapshot,
        destination,
        lambda contents: contents,
    )


@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_snapshot_comparison_rejects_file_swap_before_descriptor_open(
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if replacement == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("platform lacks FIFOs")
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(snapshot, destination, lambda contents: contents)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    original_open = os.open
    swapped = False

    def swapping_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "run" and dir_fd is not None and not swapped:
            swapped = True
            deployed = destination / "run"
            deployed.unlink()
            if replacement == "symlink":
                deployed.symlink_to(outside)
            else:
                os.mkfifo(deployed)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("promptdeploy.targets.base.os.open", swapping_open)
    assert not base.transformed_skill_tree_matches(
        snapshot,
        destination,
        lambda contents: contents,
    )
    assert swapped
    assert outside.read_bytes() == b"outside"


def test_snapshot_comparison_rejects_oversize_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(snapshot, destination, lambda contents: contents)
    (destination / "SKILL.md").write_bytes(b"oversized")
    monkeypatch.setattr(
        "promptdeploy.targets.base.os.read",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    assert not base.transformed_skill_tree_matches(
        snapshot,
        destination,
        lambda contents: contents,
    )


def test_snapshot_comparison_bounds_directory_enumeration_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _complete_snapshot()
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(snapshot, destination, lambda contents: contents)
    for index in range(len(snapshot.entries) + 1):
        (destination / f"extra-{index:02d}").write_bytes(b"extra")
    monkeypatch.setattr(
        "promptdeploy.targets.base.os.read",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    assert not base.transformed_skill_tree_matches(
        snapshot,
        destination,
        lambda contents: contents,
    )


def _missing_parent() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("file", "missing/child", 0o644, b"child"),
    )


def _file_parent() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("file", "parent", 0o644, b"parent"),
        tree.ImportedTreeEntry("file", "parent/child", 0o644, b"child"),
    )


def _missing_link_target() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "alias", 0o644, b"missing", "missing"),
    )


def _link_cycle() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "a", 0o644, b"body", "b"),
        tree.ImportedTreeEntry("link", "b", 0o644, b"body", "a"),
    )


def _link_chain_missing_target() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "a", 0o644, b"body", "b"),
        tree.ImportedTreeEntry("link", "b", 0o644, b"body", "missing"),
    )


def _directory_link_target() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "alias", 0o755, b"body", "directory"),
        tree.ImportedTreeEntry("directory", "directory", 0o755),
    )


def _mismatched_link_payload() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "alias", 0o644, b"wrong", "target"),
        tree.ImportedTreeEntry("file", "target", 0o644, b"right"),
    )


def _mismatched_link_mode() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
        tree.ImportedTreeEntry("link", "alias", 0o755, b"right", "target"),
        tree.ImportedTreeEntry("file", "target", 0o644, b"right"),
    )


def _unnormalized_mode() -> tree.ImportedTreeSnapshot:
    return _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o555),
        tree.ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (_missing_parent, "parent is missing"),
        (_file_parent, "non-directory"),
        (_missing_link_target, "link target is missing"),
        (_link_chain_missing_target, "link target is missing"),
        (_link_cycle, "link cycle"),
        (_directory_link_target, "regular file"),
        (_mismatched_link_payload, "payload"),
        (_mismatched_link_mode, "payload"),
        (_unnormalized_mode, "mode is not normalized"),
    ],
)
def test_materialization_rejects_self_hashed_invalid_topology_before_writing(
    factory: Callable[[], tree.ImportedTreeSnapshot],
    message: str,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "deployed"
    with pytest.raises(tree.ImportedSourceError, match=message):
        base.materialize_skill_tree(factory(), destination, lambda contents: contents)
    assert not destination.exists()


def test_materialization_enforces_snapshot_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _complete_snapshot()
    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 1)
    with pytest.raises(tree.ImportedSourceError, match="entry limit"):
        base.materialize_skill_tree(
            snapshot,
            tmp_path / "too-many",
            lambda contents: contents,
        )
    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 100)
    monkeypatch.setattr(tree, "MAX_FILE_BYTES", 1)
    with pytest.raises(tree.ImportedSourceError, match="file exceeds"):
        base.materialize_skill_tree(
            snapshot,
            tmp_path / "file-too-large",
            lambda contents: contents,
        )
    monkeypatch.setattr(tree, "MAX_FILE_BYTES", tree.MAX_BUNDLE_BYTES)
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 0)
    with pytest.raises(tree.ImportedSourceError, match="byte limit"):
        base.materialize_skill_tree(
            snapshot,
            tmp_path / "too-large",
            lambda contents: contents,
        )

    monkeypatch.setattr(tree, "MAX_TREE_BYTES", tree.MAX_BUNDLE_BYTES)
    monkeypatch.setattr(tree, "MAX_LINK_EXPANSIONS", 0)
    with pytest.raises(tree.ImportedSourceError, match="link expansion limit"):
        base.materialize_skill_tree(
            snapshot,
            tmp_path / "too-many-links",
            lambda contents: contents,
        )


def test_materialization_rechecks_digest_and_requires_skill_markdown(
    tmp_path: Path,
) -> None:
    corrupted = _complete_snapshot()
    object.__setattr__(corrupted, "tree_sha256", "sha256:" + "0" * 64)
    with pytest.raises(tree.ImportedSourceError, match="digest"):
        base.materialize_skill_tree(
            corrupted,
            tmp_path / "corrupted",
            lambda contents: contents,
        )

    no_skill = _snapshot(
        tree.ImportedTreeEntry("directory", ".", 0o755),
        tree.ImportedTreeEntry("file", "resource", 0o644, b"resource"),
    )
    with pytest.raises(tree.ImportedSourceError, match=r"SKILL\.md"):
        base.materialize_skill_tree(
            no_skill,
            tmp_path / "no-skill",
            lambda contents: contents,
        )


def test_materialization_requires_a_bytes_transform(tmp_path: Path) -> None:
    def bad_transform(_contents: bytes) -> bytes:
        return "not bytes"  # type: ignore[return-value]

    with pytest.raises(TypeError, match="return bytes"):
        base.materialize_skill_tree(
            _complete_snapshot(),
            tmp_path / "deployed",
            bad_transform,
        )


@pytest.mark.parametrize("failure", ["transform", "write", "zero-write", "chmod"])
def test_imported_materialization_failure_leaves_no_partial_tree(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "deployed"

    def identity(contents: bytes) -> bytes:
        return contents

    transform: Callable[[bytes], bytes] = identity

    if failure == "transform":

        def fail_transform(_contents: bytes) -> bytes:
            raise RuntimeError("transform")

        transform = fail_transform

    elif failure == "write":
        monkeypatch.setattr(
            "promptdeploy.targets.base.os.write",
            lambda *_args: (_ for _ in ()).throw(OSError("write")),
        )
    elif failure == "zero-write":
        monkeypatch.setattr("promptdeploy.targets.base.os.write", lambda *_args: 0)
    else:
        monkeypatch.setattr(
            "promptdeploy.targets.base.os.fchmod",
            lambda *_args: (_ for _ in ()).throw(OSError("chmod")),
        )

    expected = "no progress" if failure == "zero-write" else failure
    with pytest.raises((OSError, RuntimeError), match=expected):
        base.materialize_skill_tree(_complete_snapshot(), destination, transform)
    assert not destination.exists()


def test_imported_materialization_handles_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write = os.write

    def short_write(descriptor: int, contents: bytes | memoryview) -> int:
        return original_write(descriptor, contents[:1])

    monkeypatch.setattr("promptdeploy.targets.base.os.write", short_write)
    destination = tmp_path / "deployed"
    base.materialize_skill_tree(
        _complete_snapshot(),
        destination,
        lambda contents: contents,
    )
    assert (destination / "run").read_bytes() == b"#!/bin/sh\n"


def test_preexisting_destination_is_not_removed_on_materialization_failure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "deployed"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_bytes(b"untouched")
    with pytest.raises(FileExistsError):
        base.materialize_skill_tree(
            _complete_snapshot(),
            destination,
            lambda contents: contents,
        )
    assert sentinel.read_bytes() == b"untouched"


def test_atomic_imported_install_restores_old_tree_and_replaces_symlink_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "skill"
    base.install_skill_tree_atomically(
        _complete_snapshot(b"old"),
        destination,
        lambda contents: contents,
    )
    original_replace = os.replace

    def fail_staged_install(source: str | Path, target: str | Path) -> None:
        if Path(source).name == "skill" and Path(target) == destination:
            raise OSError("install")
        original_replace(source, target)

    monkeypatch.setattr("promptdeploy.targets.base.os.replace", fail_staged_install)
    with pytest.raises(OSError, match="install"):
        base.install_skill_tree_atomically(
            _complete_snapshot(b"new"),
            destination,
            lambda contents: contents,
        )
    assert (destination / "SKILL.md").read_bytes() == b"old"

    monkeypatch.setattr("promptdeploy.targets.base.os.replace", original_replace)
    shutil.rmtree(destination)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"untouched")
    destination.symlink_to(outside, target_is_directory=True)
    base.install_skill_tree_atomically(
        _complete_snapshot(b"replacement"),
        destination,
        lambda contents: contents,
    )
    assert destination.is_dir() and not destination.is_symlink()
    assert (destination / "SKILL.md").read_bytes() == b"replacement"
    assert (outside / "sentinel").read_bytes() == b"untouched"
