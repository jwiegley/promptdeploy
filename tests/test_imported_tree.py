"""Descriptor-safe immutable imported-tree snapshot tests."""

from __future__ import annotations

import os
import socket
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from promptdeploy import imported_tree as tree


def _root_entry() -> tree.ImportedTreeEntry:
    return tree.ImportedTreeEntry("directory", ".", 0o755)


def _file_entry(
    path: str = "SKILL.md", content: bytes = b"body\n", mode: int = 0o644
) -> tree.ImportedTreeEntry:
    return tree.ImportedTreeEntry("file", path, mode, content)


def _snapshot(
    entries: tuple[tree.ImportedTreeEntry, ...] | None = None,
) -> tree.ImportedTreeSnapshot:
    selected = entries or (_root_entry(), _file_entry())
    return tree.ImportedTreeSnapshot(
        "skills/demo", selected, tree.framed_tree_sha256(selected)
    )


def _make_tree(root: Path) -> Path:
    selected = root / "skills" / "demo"
    (selected / "empty").mkdir(parents=True)
    (root / "skills").chmod(0o755)
    selected.chmod(0o755)
    (selected / "empty").chmod(0o755)
    (selected / "SKILL.md").write_bytes(b"skill\n")
    (selected / "SKILL.md").chmod(0o644)
    executable = selected / "run.sh"
    executable.write_bytes(b"#!/bin/sh\n")
    executable.chmod(0o555)
    (selected / "alias").symlink_to("SKILL.md")
    return selected


def _fake_stat(value: os.stat_result, **changes: int) -> SimpleNamespace:
    fields = {
        name: getattr(value, name)
        for name in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_snapshot_invariants_and_hash_are_deterministic() -> None:
    snapshot = _snapshot()
    assert snapshot.entries[0].relative_path == "."
    assert snapshot.tree_sha256.startswith("sha256:")
    assert snapshot == _snapshot()


@pytest.mark.parametrize(
    "entry",
    [
        tree.ImportedTreeEntry("directory", ".", 0o755, None, None),
        _file_entry(),
        tree.ImportedTreeEntry("link", "alias", 0o644, b"body", "SKILL.md"),
    ],
)
def test_each_snapshot_entry_kind_accepts_its_exact_fields(
    entry: tree.ImportedTreeEntry,
) -> None:
    assert entry.kind in {"directory", "file", "link"}


@pytest.mark.parametrize(
    "arguments",
    [
        ("directory", "dir", 0o755, b"bad", None),
        ("directory", "dir", 0o755, None, "target"),
        ("file", "file", 0o644, None, None),
        ("file", "file", 0o644, b"ok", "target"),
        ("link", "link", 0o644, None, "target"),
        ("link", "link", 0o644, b"ok", None),
        ("other", "node", 0o644, None, None),
        ("file", "../escape", 0o644, b"ok", None),
        ("file", "file", 0o10000, b"ok", None),
        ("link", "link", 0o644, b"ok", "../escape"),
    ],
)
def test_snapshot_entry_rejects_impossible_state(arguments: tuple[object, ...]) -> None:
    with pytest.raises(tree.ImportedSourceError):
        tree.ImportedTreeEntry(*arguments)  # type: ignore[arg-type]


def test_snapshot_rejects_missing_duplicate_unsorted_or_wrong_digest() -> None:
    root = _root_entry()
    file = _file_entry()
    cases = (
        ("skills/demo", (file,), tree.framed_tree_sha256((file,))),
        ("skills/demo", (root, root), tree.framed_tree_sha256((root, root))),
        ("skills/demo", (file, root), tree.framed_tree_sha256((file, root))),
        ("skills/demo", (root, file), "sha256:" + "0" * 64),
        ("../demo", (root, file), tree.framed_tree_sha256((root, file))),
    )
    for arguments in cases:
        with pytest.raises(tree.ImportedSourceError):
            tree.ImportedTreeSnapshot(*arguments)


def test_framed_hash_covers_content_mode_empty_directory_and_link_identity() -> None:
    base = (_root_entry(), _file_entry())
    variants = (
        (_root_entry(), _file_entry(content=b"changed")),
        (_root_entry(), _file_entry(mode=0o755)),
        (
            _root_entry(),
            tree.ImportedTreeEntry("directory", "empty", 0o755),
            _file_entry(),
        ),
        (
            _root_entry(),
            tree.ImportedTreeEntry("link", "SKILL.md", 0o644, b"body\n", "target"),
        ),
    )
    digest = tree.framed_tree_sha256(base)
    assert all(tree.framed_tree_sha256(value) != digest for value in variants)


def test_mode_normalization_preserves_execute_but_adds_owner_access() -> None:
    assert tree._normalize_mode(0o444, directory=False) == 0o644
    assert tree._normalize_mode(0o644, directory=False) == 0o644
    assert tree._normalize_mode(0o555, directory=False) == 0o755
    assert tree._normalize_mode(0o555, directory=True) == 0o755
    with pytest.raises(tree.ImportedSourceError, match="special bits"):
        tree._normalize_mode(stat.S_ISUID | 0o644, directory=False)


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "a/b",
        "a\\b",
        "bad\nname",
        "e\u0301",
        "\udcff",
        "CON",
        "nul.txt",
        "COM1.log",
        "LPT¹",
        "a:b",
        "bad?name",
        "trailing.",
        "trailing ",
    ],
)
def test_component_policy_rejects_nonportable_spelling(value: str) -> None:
    with pytest.raises(tree.ImportedSourceError):
        tree._canonical_component(value, what="test component")


@pytest.mark.parametrize(
    "value",
    ["", ".", "/absolute", "a//b", "a/./b", "a/../b", "a\\b"],
)
def test_relative_path_policy_rejects_noncanonical_spelling(value: str) -> None:
    with pytest.raises(tree.ImportedSourceError):
        tree._canonical_relative_path(value, what="test path")


def test_exact_name_lookup_is_streamed_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "one").write_text("1")
    (tmp_path / "two").write_text("2")
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        tree._require_exact_name(descriptor, "one")
        with pytest.raises(tree.ImportedSourceError, match="spelling"):
            tree._require_exact_name(descriptor, "missing")
        monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 1)
        with pytest.raises(tree.ImportedSourceError, match="entry limit"):
            tree._require_exact_name(descriptor, "missing")
        monkeypatch.undo()
        monkeypatch.setattr(
            os,
            "scandir",
            lambda _fd: (_ for _ in ()).throw(OSError("scan")),
        )
        with pytest.raises(tree.ImportedSourceError, match="listed safely"):
            tree._require_exact_name(descriptor, "one")
    finally:
        os.close(descriptor)


def test_capture_tree_and_file_snapshot_are_path_independent(tmp_path: Path) -> None:
    selected = _make_tree(tmp_path)
    first = tree.capture_imported_tree(tmp_path, "skills/demo")
    support = tree.capture_imported_file(tmp_path, "skills/demo/SKILL.md")
    assert first.logical_root == "skills/demo"
    assert [entry.relative_path for entry in first.entries] == [
        ".",
        "SKILL.md",
        "alias",
        "empty",
        "run.sh",
    ]
    alias = next(entry for entry in first.entries if entry.relative_path == "alias")
    assert alias.kind == "link"
    assert alias.link_target == "SKILL.md"
    assert alias.content == b"skill\n"
    assert support.content == b"skill\n"
    assert support.normalized_mode == 0o644

    copied = tmp_path / "copy"
    copied.mkdir()
    target = copied / "skills" / "demo"
    target.parent.mkdir()
    for entry in first.entries:
        if entry.relative_path == ".":
            target.mkdir()
            target.chmod(entry.normalized_mode)
        elif entry.kind == "directory":
            (target / entry.relative_path).mkdir()
            (target / entry.relative_path).chmod(entry.normalized_mode)
        elif entry.kind == "link":
            assert entry.link_target is not None
            (target / entry.relative_path).symlink_to(entry.link_target)
        else:
            path = target / entry.relative_path
            path.write_bytes(entry.content or b"")
            path.chmod(entry.normalized_mode)
    second = tree.capture_imported_tree(copied, "skills/demo")
    assert second.entries == first.entries
    assert second.tree_sha256 == first.tree_sha256
    assert selected.exists()


def test_owner_only_mode_changes_do_not_churn_digest(tmp_path: Path) -> None:
    selected = _make_tree(tmp_path)
    first = tree.capture_imported_tree(tmp_path, "skills/demo")
    (selected / "SKILL.md").chmod(0o644)
    (selected / "run.sh").chmod(0o755)
    second = tree.capture_imported_tree(tmp_path, "skills/demo")
    assert first.tree_sha256 == second.tree_sha256


@pytest.mark.parametrize("source_kind", ["missing", "file", "link"])
def test_bundle_root_must_be_real_absolute_directory(
    tmp_path: Path, source_kind: str
) -> None:
    if source_kind == "missing":
        root = tmp_path / "missing"
    elif source_kind == "file":
        root = tmp_path / "file"
        root.write_text("x")
    else:
        real = tmp_path / "real"
        real.mkdir()
        root = tmp_path / "link"
        root.symlink_to(real)
    with pytest.raises(tree.ImportedSourceError, match="root"):
        tree.BundleSnapshotSession(root)
    with pytest.raises(tree.ImportedSourceError, match="absolute"):
        tree.BundleSnapshotSession(Path("relative"))


@pytest.mark.parametrize("kind", ["missing", "file", "ancestor-link", "leaf-link"])
def test_selected_tree_components_must_be_real_directories(
    tmp_path: Path, kind: str
) -> None:
    skills = tmp_path / "skills"
    if kind == "missing":
        skills.mkdir()
    elif kind == "file":
        skills.mkdir()
        (skills / "demo").write_text("x")
    elif kind == "ancestor-link":
        real = tmp_path / "real"
        real.mkdir()
        skills.symlink_to(real, target_is_directory=True)
    else:
        skills.mkdir()
        real = tmp_path / "real"
        real.mkdir()
        (skills / "demo").symlink_to(real, target_is_directory=True)
    with pytest.raises(tree.ImportedSourceError):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_exact_component_spelling_is_required(tmp_path: Path) -> None:
    (tmp_path / "Skills" / "demo").mkdir(parents=True)
    with pytest.raises(tree.ImportedSourceError, match="spelling"):
        tree.capture_imported_tree(tmp_path, "skills/demo")


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("/tmp/absolute", "relative"),
        ("", "relative"),
        ("../outside", "external"),
        ("./SKILL.md", "canonical"),
        ("dir//file", "portable"),
        ("dir/../SKILL.md", "reducible"),
        ("bad\\name", "portable"),
    ],
)
def test_link_target_policy_is_portable(
    tmp_path: Path, target: str, message: str
) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    (selected / "SKILL.md").write_text("skill")
    (selected / "alias").symlink_to(target)
    with pytest.raises(tree.ImportedSourceError, match=message):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_relative_parent_link_and_link_chain_are_captured(tmp_path: Path) -> None:
    selected = tmp_path / "skills" / "demo"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    (selected / "target").write_bytes(b"value")
    (selected / "first").symlink_to("target")
    (nested / "second").symlink_to("../first")
    snapshot = tree.capture_imported_tree(tmp_path, "skills/demo")
    second = next(
        entry for entry in snapshot.entries if entry.relative_path == "nested/second"
    )
    assert second.link_target == "first"
    assert second.content == b"value"


@pytest.mark.parametrize("target_kind", ["broken", "directory", "cycle"])
def test_link_must_resolve_to_regular_file(tmp_path: Path, target_kind: str) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    if target_kind == "broken":
        (selected / "alias").symlink_to("missing")
    elif target_kind == "directory":
        (selected / "dir").mkdir()
        (selected / "alias").symlink_to("dir")
    else:
        (selected / "one").symlink_to("two")
        (selected / "two").symlink_to("one")
    with pytest.raises(tree.ImportedSourceError):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_special_nodes_fail_closed_and_unselected_nodes_are_ignored(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    selected.chmod(0o755)
    fifo = selected / "fifo"
    os.mkfifo(fifo)
    try:
        with pytest.raises(tree.ImportedSourceError, match="special filesystem"):
            tree.capture_imported_tree(tmp_path, "skills/demo")
    finally:
        fifo.unlink()

    outside = tmp_path / "benchmarks"
    outside.mkdir()
    os.mkfifo(outside / "ignored")
    try:
        assert tree.capture_imported_tree(tmp_path, "skills/demo").entries == (
            tree.ImportedTreeEntry("directory", ".", 0o755),
        )
    finally:
        (outside / "ignored").unlink()


def test_socket_node_fails_closed() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        selected = root / "skills" / "demo"
        selected.mkdir(parents=True)
        sock = socket.socket(socket.AF_UNIX)
        path = selected / "socket"
        sock.bind(str(path))
        try:
            with pytest.raises(tree.ImportedSourceError, match="special filesystem"):
                tree.capture_imported_tree(root, "skills/demo")
        finally:
            sock.close()


def test_casefold_collision_is_rejected_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    (selected / "STRASSE").write_text("one")
    original_names = tree._bounded_directory_names

    def colliding_names(
        descriptor: int, *, limit: int, overflow_message: str
    ) -> list[str]:
        names = original_names(
            descriptor,
            limit=limit,
            overflow_message=overflow_message,
        )
        if names == ["STRASSE"]:
            return ["STRASSE", "Straße"]
        return names

    monkeypatch.setattr(tree, "_bounded_directory_names", colliding_names)
    with pytest.raises(tree.ImportedSourceError, match="case-fold collision"):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_resource_limits_fail_without_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    (selected / "one").write_bytes(b"1234")
    monkeypatch.setattr(tree, "MAX_FILE_BYTES", 3)
    with pytest.raises(tree.ImportedSourceError, match="file exceeds"):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_entry_tree_bundle_and_path_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected = tmp_path / "skills" / "demo"
    selected.mkdir(parents=True)
    (selected / "one").write_bytes(b"12")
    (selected / "two").write_bytes(b"34")

    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 2)
    with pytest.raises(tree.ImportedSourceError, match="entry limit"):
        tree.capture_imported_tree(tmp_path, "skills/demo")
    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 10)
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 3)
    with pytest.raises(tree.ImportedSourceError, match="tree exceeds"):
        tree.capture_imported_tree(tmp_path, "skills/demo")
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 100)
    monkeypatch.setattr(tree, "MAX_BUNDLE_BYTES", 3)
    with pytest.raises(tree.ImportedSourceError, match="bundle snapshot"):
        tree.capture_imported_tree(tmp_path, "skills/demo")
    monkeypatch.setattr(tree, "MAX_PATH_BYTES", 2)
    with pytest.raises(tree.ImportedSourceError, match="path limit"):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_direct_depth_and_complete_path_budget_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = tree._TreeBudget()
    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 0)
    with pytest.raises(tree.ImportedSourceError, match="entry limit"):
        budget.add_entry("path", 0)
    monkeypatch.undo()
    with pytest.raises(tree.ImportedSourceError, match="depth limit"):
        budget.add_entry("path", tree.MAX_TREE_DEPTH + 1)
    with pytest.raises(tree.ImportedSourceError, match="length limit"):
        budget.add_entry("x" * (tree.MAX_PATH_BYTES + 1), 0)
    monkeypatch.setattr(tree, "MAX_TREE_DEPTH", 2)
    with pytest.raises(tree.ImportedSourceError, match="portable path limit"):
        tree._canonical_relative_path("a/b/c", what="path")


def test_open_root_closes_descriptor_when_fstat_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_open = os.open
    descriptor = original_open(tmp_path, os.O_RDONLY)
    monkeypatch.setattr(os, "open", lambda *_args, **_kwargs: descriptor)
    monkeypatch.setattr(
        os, "fstat", lambda _fd: (_ for _ in ()).throw(OSError("fstat"))
    )
    with pytest.raises(tree.ImportedSourceError, match="root"):
        tree._open_root(tmp_path)
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_open_root_and_child_reject_non_directory_after_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file = tmp_path / "file"
    file.write_text("x")
    monkeypatch.setattr(tree, "_DIRECTORY_FLAGS", tree._READ_FLAGS)
    with pytest.raises(tree.ImportedSourceError, match="real directory"):
        tree._open_root(file)
    parent = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(tree.ImportedSourceError, match="real directories"):
            tree._open_child_directory(parent, "file")
    finally:
        os.close(parent)


def test_open_child_closes_after_fstat_error_and_detects_inode_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    parent = os.open(tmp_path, os.O_RDONLY)
    original_fstat = os.fstat
    opened: list[int] = []
    original_open = os.open

    def recording_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda fd: (
            (_ for _ in ()).throw(OSError("fstat"))
            if fd != parent
            else original_fstat(fd)
        ),
    )
    try:
        with pytest.raises(tree.ImportedSourceError, match="unsafe directory"):
            tree._open_child_directory(parent, "child")
        assert opened
        with pytest.raises(OSError):
            original_fstat(opened[-1])
    finally:
        os.close(parent)

    parent = os.open(tmp_path, os.O_RDONLY)
    actual = child.stat()
    monkeypatch.setattr(os, "open", original_open)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda fd: (
            _fake_stat(original_fstat(fd), st_ino=actual.st_ino + 1)
            if fd != parent
            else original_fstat(fd)
        ),
    )
    try:
        with pytest.raises(tree.ImportedSourceError, match="changed"):
            tree._open_child_directory(parent, "child")
    finally:
        os.close(parent)


def test_descriptor_reader_rejects_nonregular_streaming_overflow_and_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(tree.ImportedSourceError, match="regular file"):
            tree._read_descriptor(directory)
    finally:
        os.close(directory)

    file = tmp_path / "file"
    file.write_bytes(b"12")
    descriptor = os.open(file, os.O_RDONLY)
    chunks = iter((b"12", b"34", b""))
    monkeypatch.setattr(tree, "MAX_FILE_BYTES", 3)
    monkeypatch.setattr(os, "read", lambda _fd, _size: next(chunks))
    try:
        with pytest.raises(tree.ImportedSourceError, match="size limit"):
            tree._read_descriptor(descriptor)
    finally:
        os.close(descriptor)

    descriptor = os.open(file, os.O_RDONLY)
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(fd: int) -> object:
        nonlocal calls
        calls += 1
        value = original_fstat(fd)
        if calls == 2:
            return _fake_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
        return value

    monkeypatch.setattr(tree, "MAX_FILE_BYTES", tree.MAX_TREE_BYTES)
    monkeypatch.setattr(os, "read", os.read)
    monkeypatch.setattr(os, "fstat", changing_fstat)
    try:
        with pytest.raises(tree.ImportedSourceError, match="changed"):
            tree._read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_regular_child_fault_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"value")
    parent = os.open(tmp_path, os.O_RDONLY)
    before = file.stat()
    try:
        with pytest.raises(tree.ImportedSourceError, match="safely readable"):
            tree._read_regular_child(parent, "missing", before)

        monkeypatch.setattr(tree, "_FILE_FLAGS", tree._READ_FLAGS)
        directory = tmp_path / "directory"
        directory.mkdir()
        with pytest.raises(tree.ImportedSourceError, match="regular file"):
            tree._read_regular_child(parent, "directory", directory.stat())

        original_fstat = os.fstat
        monkeypatch.setattr(
            os,
            "fstat",
            lambda fd: _fake_stat(original_fstat(fd), st_ino=before.st_ino + 1),
        )
        with pytest.raises(tree.ImportedSourceError, match="changed"):
            tree._read_regular_child(parent, "file", before)
        monkeypatch.setattr(os, "fstat", original_fstat)

        content, _captured = tree._read_regular_child(parent, "file", before)
        assert content == b"value"

        original_reader = tree._read_descriptor
        monkeypatch.setattr(
            tree,
            "_read_descriptor",
            lambda _fd: (_ for _ in ()).throw(OSError("read")),
        )
        with pytest.raises(tree.ImportedSourceError, match="safely readable"):
            tree._read_regular_child(parent, "file", before)
        monkeypatch.setattr(tree, "_read_descriptor", original_reader)

        original_stat = os.stat
        calls = 0

        def changing_stat(*args: Any, **kwargs: Any) -> object:
            nonlocal calls
            calls += 1
            value = original_stat(*args, **kwargs)
            if calls == 1:
                return _fake_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
            return value

        monkeypatch.setattr(os, "stat", changing_stat)
        with pytest.raises(tree.ImportedSourceError, match="changed"):
            tree._read_regular_child(parent, "file", before)
    finally:
        os.close(parent)


def test_regular_path_empty_spelling_and_stat_race_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(tree.ImportedSourceError, match="path is empty"):
            tree._read_regular_path(root, ())
        with pytest.raises(tree.ImportedSourceError, match="spelling"):
            tree._read_regular_path(root, ("missing",))
        monkeypatch.setattr(tree, "_require_exact_name", lambda _fd, _name: None)
        with pytest.raises(tree.ImportedSourceError, match="safely readable"):
            tree._read_regular_path(root, ("ghost",))
    finally:
        os.close(root)


def test_link_encoding_root_and_injected_change_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(tree.ImportedSourceError, match="not UTF-8"):
        tree._normalize_link_target(".", "\udcff")
    with pytest.raises(tree.ImportedSourceError, match="target its root"):
        tree._normalize_link_target("nested", "..")

    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "target").write_text("value")
    link = selected / "alias"
    link.symlink_to("target")
    descriptor = os.open(selected, os.O_RDONLY)
    before = link.lstat()
    try:
        monkeypatch.setattr(
            os,
            "readlink",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readlink")),
        )
        with pytest.raises(tree.ImportedSourceError, match="broken link"):
            tree._snapshot_link(descriptor, descriptor, "alias", before, ".", "alias")
        values = iter(("target", "other"))
        monkeypatch.setattr(os, "readlink", lambda *_args, **_kwargs: next(values))
        with pytest.raises(tree.ImportedSourceError, match="link changed"):
            tree._snapshot_link(descriptor, descriptor, "alias", before, ".", "alias")
        monkeypatch.undo()
        monkeypatch.setattr(
            tree,
            "_resolve_regular_target",
            lambda *_args, **_kwargs: (b"value", (selected / "target").stat()),
        )
        original_stat = os.stat

        def changed_link_stat(*args: Any, **kwargs: Any) -> object:
            value = original_stat(*args, **kwargs)
            return _fake_stat(value, st_ctime_ns=value.st_ctime_ns + 1)

        monkeypatch.setattr(os, "stat", changed_link_stat)
        with pytest.raises(tree.ImportedSourceError, match="link changed"):
            tree._snapshot_link(descriptor, descriptor, "alias", before, ".", "alias")
    finally:
        os.close(descriptor)


def test_link_resolution_fault_and_expansion_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "alias").symlink_to("target")
    descriptor = os.open(selected, os.O_RDONLY)
    try:
        monkeypatch.setattr(tree, "_require_exact_name", lambda _fd, _name: None)
        with pytest.raises(tree.ImportedSourceError, match="broken link"):
            tree._resolve_regular_target(descriptor, "ghost", consume=None)
        monkeypatch.undo()

        with pytest.raises(tree.ImportedSourceError, match="expansion limit"):
            tree._resolve_regular_target(
                descriptor,
                "alias",
                consume=None,
                expansions=tree.MAX_LINK_EXPANSIONS,
            )
        monkeypatch.setattr(
            os,
            "readlink",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readlink")),
        )
        with pytest.raises(tree.ImportedSourceError, match="broken link"):
            tree._resolve_regular_target(descriptor, "alias", consume=None)
        values = iter(("target", "other"))
        monkeypatch.setattr(os, "readlink", lambda *_args, **_kwargs: next(values))
        with pytest.raises(tree.ImportedSourceError, match="link changed"):
            tree._resolve_regular_target(descriptor, "alias", consume=None)
        monkeypatch.undo()
        original_stat = os.stat
        calls = 0

        def changed_link_stat(*args: Any, **kwargs: Any) -> object:
            nonlocal calls
            calls += 1
            value = original_stat(*args, **kwargs)
            if calls == 2:
                return _fake_stat(value, st_ctime_ns=value.st_ctime_ns + 1)
            return value

        monkeypatch.setattr(os, "stat", changed_link_stat)
        with pytest.raises(tree.ImportedSourceError, match="link changed"):
            tree._resolve_regular_target(descriptor, "alias", consume=None)
    finally:
        os.close(descriptor)


def _call_scan_directory(descriptor: int) -> None:
    tree._scan_directory(
        descriptor,
        descriptor,
        ".",
        [],
        {},
        tree._TreeBudget(),
        lambda _size: None,
        0,
    )


def test_scan_directory_initial_child_and_final_list_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        monkeypatch.setattr(
            os,
            "fstat",
            lambda _fd: (_ for _ in ()).throw(OSError("initial stat")),
        )
        with pytest.raises(tree.ImportedSourceError, match="listed safely"):
            _call_scan_directory(descriptor)
        monkeypatch.undo()

        monkeypatch.setattr(
            os,
            "scandir",
            lambda _fd: (_ for _ in ()).throw(OSError("list")),
        )
        with pytest.raises(tree.ImportedSourceError, match="listed safely"):
            _call_scan_directory(descriptor)
        monkeypatch.undo()

        monkeypatch.setattr(
            tree,
            "_bounded_directory_names",
            lambda _fd, *, limit, overflow_message: ["ghost"],
        )
        with pytest.raises(tree.ImportedSourceError, match="node is not safely"):
            _call_scan_directory(descriptor)
        monkeypatch.undo()

        original_scandir = os.scandir
        calls = 0

        def fail_second_scan(fd: int) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second list")
            return original_scandir(fd)

        monkeypatch.setattr(os, "scandir", fail_second_scan)
        with pytest.raises(tree.ImportedSourceError, match="listed safely"):
            _call_scan_directory(descriptor)
        monkeypatch.undo()

        original_fstat = os.fstat
        calls = 0

        def fail_second_stat(fd: int) -> os.stat_result:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second stat")
            return original_fstat(fd)

        monkeypatch.setattr(os, "fstat", fail_second_stat)
        with pytest.raises(tree.ImportedSourceError, match="changed during capture"):
            _call_scan_directory(descriptor)
    finally:
        os.close(descriptor)


def test_scan_directory_detects_name_and_stat_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        calls = 0

        def changing_names(_fd: int, *, limit: int, overflow_message: str) -> list[str]:
            nonlocal calls
            calls += 1
            return [] if calls == 1 else ["added"]

        monkeypatch.setattr(tree, "_bounded_directory_names", changing_names)
        with pytest.raises(tree.ImportedSourceError, match="changed during capture"):
            _call_scan_directory(descriptor)
        monkeypatch.undo()

        original_fstat = os.fstat
        calls = 0

        def changing_fstat(fd: int) -> object:
            nonlocal calls
            calls += 1
            value = original_fstat(fd)
            if calls == 2:
                return _fake_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
            return value

        monkeypatch.setattr(os, "fstat", changing_fstat)
        with pytest.raises(tree.ImportedSourceError, match="changed during capture"):
            _call_scan_directory(descriptor)
    finally:
        os.close(descriptor)


def test_nested_directory_final_parent_audit_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    parent = os.open(tmp_path, os.O_RDONLY)
    child = os.open(nested, os.O_RDONLY)
    try:
        monkeypatch.setattr(
            os,
            "stat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stat")),
        )
        with pytest.raises(tree.ImportedSourceError, match="changed during capture"):
            tree._scan_directory(
                child,
                child,
                ".",
                [],
                {},
                tree._TreeBudget(),
                lambda _size: None,
                0,
                parent,
                "nested",
            )
        monkeypatch.undo()

        actual = nested.stat()
        monkeypatch.setattr(
            os,
            "stat",
            lambda *_args, **_kwargs: _fake_stat(
                actual, st_mtime_ns=actual.st_mtime_ns + 1
            ),
        )
        with pytest.raises(tree.ImportedSourceError, match="changed during capture"):
            tree._scan_directory(
                child,
                child,
                ".",
                [],
                {},
                tree._TreeBudget(),
                lambda _size: None,
                0,
                parent,
                "nested",
            )
    finally:
        os.close(child)
        os.close(parent)


def test_scan_tree_detects_selected_root_replacement_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    original = tree._open_directory_path
    calls = 0

    def replacement(root: int, parts: tuple[str, ...]) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            return os.open(other, os.O_RDONLY)
        return original(root, parts)

    monkeypatch.setattr(tree, "_open_directory_path", replacement)
    with pytest.raises(tree.ImportedSourceError, match="selected tree changed"):
        tree.capture_imported_tree(tmp_path, "skills/demo")


def test_context_error_skips_root_audit_and_explicit_close_without_audit(
    tmp_path: Path,
) -> None:
    session = tree.BundleSnapshotSession(tmp_path)
    session.close(audit=False)
    with pytest.raises(RuntimeError), tree.BundleSnapshotSession(tmp_path):
        raise RuntimeError("body")


def test_session_reuses_root_and_rejects_use_after_close(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    session = tree.BundleSnapshotSession(tmp_path)
    assert session.read_regular("skills/demo/SKILL.md").content == b"skill\n"
    assert session.scan_tree("skills/demo").entries
    session.close()
    session.close()
    with pytest.raises(tree.ImportedSourceError, match="closed"):
        session.scan_tree("skills/demo")


def test_root_replacement_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    session = tree.BundleSnapshotSession(root)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.mkdir()
    try:
        with pytest.raises(tree.ImportedSourceError, match="root changed"):
            session.close()
    finally:
        if not session._closed:
            session.close(audit=False)


def test_real_pinned_ponytail_tree_digests_are_stable() -> None:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    expected = {
        "ponytail": "c8a4e819082fc6fe7eed764e8114e7cbc2b259dba7293b63e53e1aaa7f0682e6",
        "ponytail-review": (
            "be62cd143b53c3714548a7e2a702e24a13302ba14d482ebfdd74995b5e64cae1"
        ),
        "ponytail-audit": (
            "332988d3e1ed3494e8aea0c84e936a1ca8724401ddfae65b2a70ed0c48567233"
        ),
        "ponytail-debt": (
            "aee7b933d7c3c9164b067a82f97faf0ee3a7efa439d6eec9246745b4302d4499"
        ),
        "ponytail-gain": (
            "6464c14bc7d69088d3386a64b46d99e4828be5eb979a0272af7b0f5d2f0ec508"
        ),
        "ponytail-help": (
            "2c99f0ede3bc31217f83e4ea4f9352b042bc970e12203709baf4f9b0105d662a"
        ),
    }
    with tree.BundleSnapshotSession(root.resolve()) as session:
        for name, digest in expected.items():
            snapshot = session.scan_tree(f"skills/{name}")
            assert snapshot.tree_sha256 == f"sha256:{digest}"
