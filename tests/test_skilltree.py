"""Complete transformed skill-tree verification regressions."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from promptdeploy.manifest import compute_directory_hash
from promptdeploy.skilltree import _walk_error, scan_skill_source
from promptdeploy.targets.base import (
    Target,
    _make_tree_owner_writable,
    _raise_walk_error,
    _replace_read_only_file,
    _skill_tree_snapshot,
    materialize_skill_tree,
    transformed_skill_tree_matches,
)
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.opencode import OpenCodeTarget


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "source-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "empty").mkdir()
    (skill / "SKILL.md").write_bytes(
        b"---\nname: anvil\ntools: Read, Bash(grep:*), Frobnicate\n---\nBody.\n"
    )
    (skill / "references" / "tools.md").write_bytes(b"tool reference\n")
    return skill


def _target(kind: str, tmp_path: Path) -> tuple[Target, Path]:
    target: Target
    if kind == "claude":
        target = ClaudeTarget("t", tmp_path / "claude", model="sonnet")
        destination = tmp_path / "claude" / "skills" / "anvil"
    elif kind == "codex":
        target = CodexTarget("t", tmp_path / "codex-home")
        destination = tmp_path / "codex-home" / ".agents" / "skills" / "anvil"
    elif kind == "droid":
        target = DroidTarget("t", tmp_path / "droid")
        destination = tmp_path / "droid" / "skills" / "anvil"
    else:
        target = OpenCodeTarget("t", tmp_path / "opencode")
        destination = tmp_path / "opencode" / "skills" / "anvil"
    return target, destination


@pytest.mark.parametrize("kind", ["claude", "codex", "droid", "opencode"])
def test_target_skill_match_covers_complete_transformed_tree(
    kind: str, tmp_path: Path
) -> None:
    source = _skill(tmp_path)
    target, destination = _target(kind, tmp_path)
    target.deploy_skill("anvil", source)
    if kind == "opencode":
        assert target.consume_warnings()
        assert target.consume_warnings() == []

    assert (
        target.item_matches_source(
            "skill",
            "anvil",
            (source / "SKILL.md").read_bytes(),
            {},
            source_path=source / "SKILL.md",
        )
        is True
    )
    assert target.consume_warnings() == []
    assert (
        target.item_matches_source(
            "skill",
            "anvil",
            b"",
            {},
            source_path=None,
        )
        is None
    )
    assert (
        target.item_matches_source(
            "agent",
            "helper",
            b"",
            {},
            source_path=source / "SKILL.md",
        )
        is None
    )

    reference = destination / "references" / "tools.md"
    reference.write_bytes(b"drift\n")
    assert (
        target.item_matches_source(
            "skill",
            "anvil",
            b"",
            {},
            source_path=source / "SKILL.md",
        )
        is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "skill-body",
        "extra-file",
        "missing-file",
        "extra-empty-directory",
        "missing-empty-directory",
        "mode",
        "file-symlink",
        "directory-symlink",
    ],
)
def test_transformed_tree_detects_every_structural_drift(
    mutation: str, tmp_path: Path
) -> None:
    source = _skill(tmp_path)
    destination = tmp_path / "deployed"
    materialize_skill_tree(source, destination, lambda data: data.upper())
    assert transformed_skill_tree_matches(
        source, destination, lambda data: data.upper()
    )

    if mutation == "skill-body":
        (destination / "SKILL.md").write_bytes(b"changed")
    elif mutation == "extra-file":
        (destination / "extra").write_bytes(b"extra")
    elif mutation == "missing-file":
        (destination / "references" / "tools.md").unlink()
    elif mutation == "extra-empty-directory":
        (destination / "unexpected-empty").mkdir()
    elif mutation == "missing-empty-directory":
        (destination / "empty").rmdir()
    elif mutation == "mode":
        skill_md = destination / "SKILL.md"
        skill_md.chmod(skill_md.stat().st_mode ^ 0o100)
    elif mutation == "file-symlink":
        path = destination / "references" / "tools.md"
        path.unlink()
        path.symlink_to(destination / "SKILL.md")
    else:
        path = destination / "empty"
        path.rmdir()
        path.symlink_to(destination / "references", target_is_directory=True)

    assert not transformed_skill_tree_matches(
        source, destination, lambda data: data.upper()
    )


def test_repo_local_top_level_and_in_tree_file_symlinks_are_dereferenced(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    source = repository / "shared" / "skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    resource = source / "resource.md"
    resource.write_bytes(b"resource\n")
    (source / "linked.md").symlink_to(resource)
    lexical_source = repository / "skills" / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)
    destination = tmp_path / "destination"

    materialize_skill_tree(lexical_source, destination, lambda data: data.upper())

    assert not (destination / "SKILL.md").is_symlink()
    assert not (destination / "linked.md").is_symlink()
    assert (destination / "linked.md").read_bytes() == b"resource\n"
    assert transformed_skill_tree_matches(
        lexical_source, destination, lambda data: data.upper()
    )


@pytest.mark.parametrize("kind", ["external-file", "directory", "broken"])
def test_materialize_rejects_unsafe_source_symlinks(kind: str, tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "skills" / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    link = source / "bad"
    if kind == "external-file":
        outside = tmp_path / "outside"
        outside.write_bytes(b"secret")
        link.symlink_to(outside)
    elif kind == "directory":
        directory = source / "directory"
        directory.mkdir()
        link.symlink_to(directory, target_is_directory=True)
    else:
        link.symlink_to(tmp_path / "missing")

    destination = tmp_path / "destination"
    with pytest.raises(ValueError, match="Skill source"):
        materialize_skill_tree(source, destination, lambda data: data)
    assert not destination.exists()


def test_top_level_skill_symlink_cannot_leave_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_bytes(b"secret")
    source = repository / "skills" / "escaped"
    source.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="inside its promptdeploy repository"):
        materialize_skill_tree(source, tmp_path / "destination", lambda data: data)


def test_nested_skill_symlink_cannot_read_repository_sibling_secret(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    source = repository / "skills" / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body")
    secret = repository / ".secret"
    secret.write_bytes(b"do not deploy")
    (source / "leak").symlink_to(secret)

    with pytest.raises(ValueError, match="outside its allowed tree"):
        materialize_skill_tree(source, tmp_path / "destination", lambda data: data)


def test_top_level_linked_skill_parent_secret_is_rejected_by_every_operation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    source = repository / "shared"
    source.mkdir()
    secret = repository / ".repository-secret.md"
    secret.write_bytes(b"do not deploy")
    (source / "SKILL.md").symlink_to(secret)
    lexical_source = repository / "skills" / "leak"
    lexical_source.symlink_to(source, target_is_directory=True)
    destination = tmp_path / "destination"

    operations = (
        lambda: scan_skill_source(lexical_source),
        lambda: compute_directory_hash(lexical_source),
        lambda: materialize_skill_tree(lexical_source, destination, lambda data: data),
        lambda: transformed_skill_tree_matches(
            lexical_source, destination, lambda data: data
        ),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="outside its allowed tree"):
            operation()
    assert not destination.exists()


def test_top_level_linked_skill_uses_only_exact_approved_auxiliary_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    source = repository / "shared" / "skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    glossary = repository / "shared" / "glossary.csv"
    glossary.write_bytes(b"term,meaning\n")
    (source / "GLOSSARY.csv").symlink_to(glossary)
    lexical_source = repository / "skills" / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)
    (repository / "skills" / ".promptdeploy-skill-links.json").write_text(
        '{"linked":{"GLOSSARY.csv":"shared/glossary.csv"}}\n'
    )
    destination = tmp_path / "destination"

    _root, files = scan_skill_source(lexical_source)
    assert dict(files)["GLOSSARY.csv"] == glossary
    assert compute_directory_hash(lexical_source)
    materialize_skill_tree(lexical_source, destination, lambda data: data.upper())
    assert (destination / "GLOSSARY.csv").read_bytes() == b"term,meaning\n"
    assert transformed_skill_tree_matches(
        lexical_source, destination, lambda data: data.upper()
    )


@pytest.mark.parametrize(
    "manifest_content",
    [
        "not-json",
        "[]",
        '{"linked":[]}',
        '{"linked":{"GLOSSARY.csv":7}}',
        '{"linked":{"/GLOSSARY.csv":"shared/glossary.csv"}}',
        '{"linked":{"GLOSSARY.csv":"/shared/glossary.csv"}}',
        '{"linked":{"nested//GLOSSARY.csv":"shared/glossary.csv"}}',
        '{"linked":{"GLOSSARY.csv":"shared//glossary.csv"}}',
        '{"linked":{"nested/../GLOSSARY.csv":"shared/glossary.csv"}}',
        '{"linked":{"GLOSSARY.csv":"shared/../glossary.csv"}}',
        '{"linked":{"GLOSSARY.csv":"shared/missing.csv"}}',
    ],
)
def test_skill_link_allowlist_rejects_malformed_entries(
    manifest_content: str, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    source = repository / "shared" / "skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    glossary = repository / "shared" / "glossary.csv"
    glossary.write_bytes(b"term,meaning\n")
    (source / "GLOSSARY.csv").symlink_to(glossary)
    lexical_source = repository / "skills" / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)
    (repository / "skills" / ".promptdeploy-skill-links.json").write_text(
        manifest_content
    )

    with pytest.raises(ValueError, match=r"Skill link|Approved skill"):
        scan_skill_source(lexical_source)


def test_skill_link_allowlist_rejects_unreadable_and_nonregular_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    skills = repository / "skills"
    skills.mkdir(parents=True)
    source = repository / "shared"
    source.mkdir()
    (source / "SKILL.md").write_bytes(b"body\n")
    lexical_source = skills / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)
    manifest = skills / ".promptdeploy-skill-links.json"
    manifest.mkdir()

    with pytest.raises(ValueError, match="must be a regular file"):
        scan_skill_source(lexical_source)

    manifest.rmdir()
    manifest.write_text("{}")
    real_lstat = Path.lstat

    def fail_manifest(path: Path):
        if path == manifest:
            raise PermissionError("manifest denied")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_manifest)
    with pytest.raises(ValueError, match="not safely readable"):
        scan_skill_source(lexical_source)


@pytest.mark.parametrize("target_kind", ["outside", "directory"])
def test_skill_link_allowlist_rejects_unsafe_approved_target(
    target_kind: str, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    skills = repository / "skills"
    skills.mkdir(parents=True)
    source = repository / "shared" / "skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    lexical_source = skills / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)

    if target_kind == "outside":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "glossary.csv").write_bytes(b"secret")
        (repository / "escape").symlink_to(outside, target_is_directory=True)
        target = "escape/glossary.csv"
    else:
        target = "shared"
    (skills / ".promptdeploy-skill-links.json").write_text(
        f'{{"linked":{{"GLOSSARY.csv":"{target}"}}}}'
    )

    with pytest.raises(ValueError, match="regular and repository-confined"):
        scan_skill_source(lexical_source)


def test_skill_link_allowlist_rejects_unused_approval(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    skills = repository / "skills"
    skills.mkdir(parents=True)
    source = repository / "shared" / "skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"body\n")
    glossary = repository / "shared" / "glossary.csv"
    glossary.write_bytes(b"term,meaning\n")
    lexical_source = skills / "linked"
    lexical_source.symlink_to(source, target_is_directory=True)
    (skills / ".promptdeploy-skill-links.json").write_text(
        '{"linked":{"GLOSSARY.csv":"shared/glossary.csv"}}'
    )

    with pytest.raises(ValueError, match="unused or mismatched"):
        scan_skill_source(lexical_source)


def test_materialize_without_skill_markdown_skips_transform(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "resource").write_bytes(b"resource")
    destination = tmp_path / "destination"

    def forbidden(_data: bytes) -> bytes:
        raise AssertionError("transform must not be called")

    materialize_skill_tree(source, destination, forbidden)
    assert (destination / "resource").read_bytes() == b"resource"


def test_snapshot_rejects_missing_root_and_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    assert _skill_tree_snapshot(missing) is None

    source = _skill(tmp_path)
    destination = tmp_path / "destination"
    materialize_skill_tree(source, destination, lambda data: data)
    original = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if destination in path.parents:
            raise PermissionError("injected")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert _skill_tree_snapshot(destination) is None


def test_snapshot_rejects_a_directory_replaced_during_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    raced = root / "raced"
    raced.write_bytes(b"now a file")
    monkeypatch.setattr(
        os,
        "walk",
        lambda *_args, **_kwargs: [(str(raced), [], [])],
    )
    assert _skill_tree_snapshot(root) is None


def test_walk_errors_are_raised_for_snapshot_to_fail_closed() -> None:
    error = PermissionError("injected")
    with pytest.raises(PermissionError, match="injected"):
        _raise_walk_error(error)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform lacks FIFOs")
def test_snapshot_rejects_special_nodes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    os.mkfifo(root / "fifo")
    assert _skill_tree_snapshot(root) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform lacks FIFOs")
def test_materialize_rejects_fifo_before_copy_or_read(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(b"body")
    os.mkfifo(source / "fifo")

    with pytest.raises(ValueError, match="special filesystem node"):
        materialize_skill_tree(source, tmp_path / "destination", lambda data: data)
    with pytest.raises(ValueError, match="special filesystem node"):
        compute_directory_hash(source)


@pytest.mark.parametrize("kind", ["claude", "codex", "droid", "opencode"])
def test_read_only_nix_style_skill_tree_deploys_twice(
    kind: str, tmp_path: Path
) -> None:
    source = _skill(tmp_path)
    for path in source.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    source.chmod(0o555)
    target, destination = _target(kind, tmp_path)

    target.deploy_skill("anvil", source)
    target.deploy_skill("anvil", source)

    assert destination.is_dir()
    assert (destination / "SKILL.md").is_file()
    assert (destination / "SKILL.md").stat().st_mode & 0o200
    assert (destination / "references").stat().st_mode & 0o200


@pytest.mark.parametrize("kind", ["claude", "codex", "droid", "opencode"])
def test_failed_skill_swap_restores_previous_tree(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _skill(tmp_path)
    target, destination = _target(kind, tmp_path)
    target.deploy_skill("anvil", source)
    previous = (destination / "references" / "tools.md").read_bytes()
    (source / "references" / "tools.md").write_bytes(b"new tree\n")
    real_replace = os.replace

    def fail_install(src: str | Path, dst: str | Path) -> None:
        if Path(src).name == "skill" and Path(dst) == destination:
            raise OSError("injected install failure")
        real_replace(src, dst)

    monkeypatch.setattr("promptdeploy.targets.base.os.replace", fail_install)
    with pytest.raises(OSError, match="injected install failure"):
        target.deploy_skill("anvil", source)

    assert (destination / "references" / "tools.md").read_bytes() == previous


def test_failed_skill_restore_retains_only_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _skill(tmp_path)
    target, destination = _target("claude", tmp_path)
    target.deploy_skill("anvil", source)
    old_body = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_bytes(b"new body")
    real_replace = os.replace

    def fail_install_and_restore(src: str | Path, dst: str | Path) -> None:
        source_path = Path(src)
        destination_path = Path(dst)
        if destination_path == destination and source_path.name in {
            "skill",
            "previous",
        }:
            raise OSError(f"injected {source_path.name} failure")
        real_replace(src, dst)

    monkeypatch.setattr(
        "promptdeploy.targets.base.os.replace", fail_install_and_restore
    )
    with pytest.raises(RuntimeError, match="backup retained at"):
        target.deploy_skill("anvil", source)

    backups = list(destination.parent.glob(".promptdeploy-*/previous/SKILL.md"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_body


def test_skilltree_walk_error_is_reraised() -> None:
    error = PermissionError("walk denied")
    with pytest.raises(PermissionError, match="walk denied"):
        _walk_error(error)


def test_scan_rejects_missing_source_and_regular_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        scan_skill_source(tmp_path / "missing")
    regular_file = tmp_path / "file"
    regular_file.write_bytes(b"not a tree")
    with pytest.raises(ValueError, match="must resolve inside"):
        scan_skill_source(regular_file)


def test_scan_rejects_root_lstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _skill(tmp_path)
    real_lstat = Path.lstat
    calls = 0

    def fail_root(path: Path):
        nonlocal calls
        if path == source:
            calls += 1
            if calls == 2:
                raise PermissionError("root denied")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_root)
    with pytest.raises(ValueError, match="readable tree"):
        scan_skill_source(source)


def test_scan_rejects_unreadable_symlink_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target"
    target.write_bytes(b"target")
    (source / "link").symlink_to(target)
    real_stat = Path.stat

    def fail_target(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("target denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_target)
    with pytest.raises(ValueError, match="unreadable symlink target"):
        scan_skill_source(source)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform lacks FIFOs")
def test_scan_rejects_symlink_to_special_node(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fifo = source / "z-fifo"
    os.mkfifo(fifo)
    (source / "a-link").symlink_to(fifo)
    with pytest.raises(ValueError, match="regular files only"):
        scan_skill_source(source)


def test_scan_rejects_directory_races_and_walk_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    raced = tmp_path / "raced"
    raced.write_bytes(b"file")
    monkeypatch.setattr(
        "promptdeploy.skilltree.os.walk",
        lambda *_args, **_kwargs: [(str(raced), [], [])],
    )
    with pytest.raises(ValueError, match="directory changed"):
        scan_skill_source(source)

    def fail_walk(*_args, **_kwargs):
        raise PermissionError("walk failed")

    monkeypatch.setattr("promptdeploy.skilltree.os.walk", fail_walk)
    with pytest.raises(ValueError, match="validated safely"):
        scan_skill_source(source)


def test_scan_rejects_bad_directory_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source = repository / "skills" / "source"
    source.mkdir(parents=True)
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (source / "linked-directory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside its tree"):
        scan_skill_source(source)

    regular = source / "regular"
    regular.write_bytes(b"file")
    monkeypatch.setattr(
        "promptdeploy.skilltree.os.walk",
        lambda *_args, **_kwargs: [(str(source), [regular.name], [])],
    )
    with pytest.raises(ValueError, match="non-directory node"):
        scan_skill_source(source)


def test_make_tree_writable_handles_missing_and_symlink_nodes(tmp_path: Path) -> None:
    _make_tree_owner_writable(tmp_path / "missing")
    outside = tmp_path / "outside"
    outside.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(outside, target_is_directory=True)
    _make_tree_owner_writable(root_link)

    root = tmp_path / "root"
    root.mkdir()
    directory_target = root / "directory-target"
    directory_target.mkdir()
    (root / "directory-link").symlink_to(directory_target, target_is_directory=True)
    file_target = root / "file-target"
    file_target.write_bytes(b"file")
    (root / "file-link").symlink_to(file_target)
    _make_tree_owner_writable(root)
    assert (root / "directory-link").is_symlink()
    assert (root / "file-link").is_symlink()


def test_replace_read_only_file_cleans_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "SKILL.md"
    path.write_bytes(b"old")
    monkeypatch.setattr(
        "promptdeploy.targets.base.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        _replace_read_only_file(path, b"new")
    assert path.read_bytes() == b"old"
    assert list(tmp_path.glob("*.tmp")) == []


def test_materialize_normalizes_partial_copy_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _skill(tmp_path)
    destination = tmp_path / "partial"

    def fail_copy(*_args, **_kwargs):
        destination.mkdir()
        partial = destination / "partial"
        partial.write_bytes(b"partial")
        partial.chmod(0o400)
        raise OSError("copy failed")

    monkeypatch.setattr("promptdeploy.targets.base.shutil.copytree", fail_copy)
    with pytest.raises(OSError, match="copy failed"):
        materialize_skill_tree(source, destination, lambda data: data)
    assert (destination / "partial").stat().st_mode & 0o200
    shutil.rmtree(destination)


def test_failed_first_skill_install_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _skill(tmp_path)
    target, destination = _target("claude", tmp_path)
    real_replace = os.replace

    def fail_install(src: str | Path, dst: str | Path) -> None:
        if Path(src).name == "skill" and Path(dst) == destination:
            raise OSError("first install failed")
        real_replace(src, dst)

    monkeypatch.setattr("promptdeploy.targets.base.os.replace", fail_install)
    with pytest.raises(OSError, match="first install failed"):
        target.deploy_skill("anvil", source)
    assert not destination.exists()
    assert list(destination.parent.glob(".promptdeploy-*")) == []
