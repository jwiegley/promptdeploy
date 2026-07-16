"""Target-owned support-bundle tree regressions."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import (
    _CLI_TYPE_TO_ITEM_TYPE,
    _TYPE_TO_CATEGORY,
    _remove_item,
    deploy,
    parse_item_selector,
)
from promptdeploy.manifest import (
    MANIFEST_FILENAME,
    Manifest,
    ManifestItem,
    ManifestSource,
    load_manifest,
    save_manifest,
)
from promptdeploy.names import require_canonical_item_name
from promptdeploy.targets.base import (
    MANAGED_BUNDLE_RSYNC_INCLUDES,
    Target,
    UnsafeManagedBundlePath,
)
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.gptel import GptelTarget
from promptdeploy.targets.opencode import OpenCodeTarget
from promptdeploy.targets.remote import RemoteTarget


def _target(kind: str, tmp_path: Path) -> Target:
    root = tmp_path / kind
    if kind == "claude":
        return ClaudeTarget(kind, root)
    if kind == "codex":
        return CodexTarget(kind, root)
    if kind == "droid":
        return DroidTarget(kind, root)
    if kind == "opencode":
        return OpenCodeTarget(kind, root)
    return GptelTarget(kind, root)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.mark.parametrize("kind", ["claude", "codex", "droid", "opencode", "gptel"])
def test_bundle_path_and_remote_include_matrix(kind: str, tmp_path: Path) -> None:
    target = _target(kind, tmp_path)
    expected_root = tmp_path / kind
    assert target.bundle_path("ponytail") == (
        expected_root / ".promptdeploy" / "bundles" / "ponytail"
    )
    includes = target.rsync_includes()
    assert includes is not None
    positions = [includes.index(pattern) for pattern in MANAGED_BUNDLE_RSYNC_INCLUDES]
    assert positions == sorted(positions)
    if kind == "codex":
        push = target.rsync_push_includes()
        assert push is not None
        assert all(pattern in push for pattern in MANAGED_BUNDLE_RSYNC_INCLUDES)
        assert not any(".codex/.promptdeploy" in pattern for pattern in includes)


def test_codex_dot_codex_constructor_still_uses_home_bundle_root(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    target = CodexTarget("codex", home / ".codex")
    assert target.managed_root() == home
    assert target.bundle_path("ponytail") == (
        home / ".promptdeploy" / "bundles" / "ponytail"
    )


def test_remote_delegates_staging_managed_root(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    inner = CodexTarget("codex", staging)
    remote = RemoteTarget(inner, "example", Path("~"), staging)
    assert remote.managed_root() == staging
    assert remote.bundle_path("ponytail") == (
        staging / ".promptdeploy" / "bundles" / "ponytail"
    )


@pytest.mark.parametrize("kind", ["claude", "codex", "droid", "opencode", "gptel"])
def test_support_bundle_deploy_is_exact_and_umask_independent(
    kind: str,
    tmp_path: Path,
) -> None:
    target = _target(kind, tmp_path)
    destination = target.bundle_path("ponytail")
    previous_umask = os.umask(0o077)
    try:
        target.deploy_bundle("ponytail", b"license\n")
    finally:
        os.umask(previous_umask)

    assert target.bundle_exists("ponytail")
    assert target.bundle_matches("ponytail", b"license\n")
    assert destination.is_dir()
    assert _mode(destination) == 0o755
    assert (destination / "LICENSE").read_bytes() == b"license\n"
    assert _mode(destination / "LICENSE") == 0o644


def test_bundle_redeploy_is_exact_and_preserves_sibling(tmp_path: Path) -> None:
    target = ClaudeTarget("claude", tmp_path / "claude")
    target.deploy_bundle("ponytail", b"old")
    target.deploy_bundle("other", b"sibling")
    destination = target.bundle_path("ponytail")
    (destination / "extra").write_bytes(b"drift")

    target.deploy_bundle("ponytail", b"new")

    assert target.bundle_matches("ponytail", b"new")
    assert sorted(path.name for path in destination.iterdir()) == ["LICENSE"]
    assert target.bundle_matches("other", b"sibling")


def test_failed_bundle_swap_restores_previous_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = ClaudeTarget("claude", tmp_path / "claude")
    target.deploy_bundle("ponytail", b"old")
    destination = target.bundle_path("ponytail")
    original_replace = os.replace

    def fail_install(source: str | Path, target_path: str | Path) -> None:
        if Path(source).name == "bundle" and Path(target_path) == destination:
            raise OSError("install")
        original_replace(source, target_path)

    monkeypatch.setattr("promptdeploy.targets.base.os.replace", fail_install)
    with pytest.raises(OSError, match="install"):
        target.deploy_bundle("ponytail", b"new")
    assert target.bundle_matches("ponytail", b"old")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "bytes",
        "file-mode",
        "root-mode",
        "extra-file",
        "extra-directory",
        "file-link",
        "root-link",
        "special",
    ],
)
def test_bundle_match_rejects_exact_tree_drift(
    mutation: str,
    tmp_path: Path,
) -> None:
    if mutation == "special" and not hasattr(os, "mkfifo"):
        pytest.skip("platform lacks FIFOs")
    target = ClaudeTarget("claude", tmp_path / "claude")
    target.deploy_bundle("ponytail", b"license")
    destination = target.bundle_path("ponytail")
    license_path = destination / "LICENSE"

    if mutation == "missing":
        shutil.rmtree(destination)
    elif mutation == "bytes":
        license_path.write_bytes(b"changed")
    elif mutation == "file-mode":
        license_path.chmod(0o600)
    elif mutation == "root-mode":
        destination.chmod(0o700)
    elif mutation == "extra-file":
        (destination / "extra").write_bytes(b"extra")
    elif mutation == "extra-directory":
        (destination / "extra").mkdir()
    elif mutation == "file-link":
        license_path.unlink()
        license_path.symlink_to(tmp_path / "outside")
    elif mutation == "root-link":
        shutil.rmtree(destination)
        outside = tmp_path / "outside"
        outside.mkdir()
        destination.symlink_to(outside, target_is_directory=True)
    else:
        os.mkfifo(destination / "extra")

    assert not target.bundle_matches("ponytail", b"license")


@pytest.mark.parametrize("leaf_kind", ["tree", "file", "symlink", "special"])
def test_remove_bundle_deletes_only_exact_leaf(
    leaf_kind: str,
    tmp_path: Path,
) -> None:
    if leaf_kind == "special" and not hasattr(os, "mkfifo"):
        pytest.skip("platform lacks FIFOs")
    target = ClaudeTarget("claude", tmp_path / "claude")
    target.deploy_bundle("ponytail", b"license")
    target.deploy_bundle("other", b"sibling")
    destination = target.bundle_path("ponytail")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"untouched")
    if leaf_kind != "tree":
        shutil.rmtree(destination)
        if leaf_kind == "file":
            destination.write_bytes(b"leaf")
        elif leaf_kind == "symlink":
            destination.symlink_to(outside, target_is_directory=True)
        else:
            os.mkfifo(destination)

    target.remove_bundle("ponytail")

    assert not destination.is_symlink() and not destination.exists()
    assert target.bundle_matches("other", b"sibling")
    assert sentinel.read_bytes() == b"untouched"
    assert target.bundle_root().is_dir()


@pytest.mark.parametrize(
    ("parent", "kind"),
    [
        ("managed", "file"),
        (".promptdeploy", "file"),
        (".promptdeploy", "symlink"),
        ("bundles", "file"),
        ("bundles", "symlink"),
    ],
)
def test_bundle_parent_nodes_fail_closed(
    parent: str,
    kind: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    target = ClaudeTarget("claude", root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if parent == "managed":
        path = root
    elif parent == ".promptdeploy":
        root.mkdir()
        path = root / ".promptdeploy"
    else:
        (root / ".promptdeploy").mkdir(parents=True)
        path = root / ".promptdeploy" / "bundles"
    if kind == "file":
        path.write_bytes(b"unsafe")
    else:
        path.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeManagedBundlePath):
        target.deploy_bundle("ponytail", b"license")
    assert not (outside / "ponytail").exists()


def test_missing_bundle_parents_and_leaf_are_absent_without_creation(
    tmp_path: Path,
) -> None:
    target = ClaudeTarget("claude", tmp_path / "claude")
    assert not target.bundle_exists("ponytail")
    assert not target.bundle_matches("ponytail", b"license")
    target.remove_bundle("ponytail")
    assert not target.managed_root().exists()


def test_read_only_bundle_checks_cover_missing_second_parent_and_bad_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    target = ClaudeTarget("claude", root)
    (root / ".promptdeploy").mkdir(parents=True)
    assert not target.bundle_exists("ponytail")

    shutil.rmtree(root)
    root.write_bytes(b"not a directory")
    with pytest.raises(UnsafeManagedBundlePath, match="real directory"):
        target.bundle_exists("ponytail")


def test_bundle_parent_creation_handles_race_and_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "claude"
    target = ClaudeTarget("claude", root)
    original_mkdir = Path.mkdir

    def racing_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == root / ".promptdeploy":
            original_mkdir(path)
            raise FileExistsError("raced")
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    target.deploy_bundle("ponytail", b"license")
    assert target.bundle_matches("ponytail", b"license")

    monkeypatch.undo()
    shutil.rmtree(root)
    root.mkdir()

    def failing_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == root / ".promptdeploy":
            raise PermissionError("denied")
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    with pytest.raises(UnsafeManagedBundlePath, match="safely writable"):
        target.deploy_bundle("ponytail", b"license")


def test_bundle_parent_and_leaf_lstat_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "claude"
    target = ClaudeTarget("claude", root)
    root.mkdir()
    original_lstat = Path.lstat

    def fail_root(path: Path) -> os.stat_result:
        if path == root:
            raise PermissionError("root denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_root)
    with pytest.raises(UnsafeManagedBundlePath, match="root is not safely readable"):
        target.bundle_exists("ponytail")

    monkeypatch.undo()
    promptdeploy = root / ".promptdeploy"
    calls = 0

    def fail_created_parent(path: Path) -> os.stat_result:
        nonlocal calls
        if path == promptdeploy:
            calls += 1
            if calls == 2:
                raise PermissionError("created parent denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_created_parent)
    with pytest.raises(UnsafeManagedBundlePath, match="parent is not safely readable"):
        target.deploy_bundle("ponytail", b"license")

    monkeypatch.undo()
    shutil.rmtree(promptdeploy)
    promptdeploy.mkdir()

    def fail_existing_parent(path: Path) -> os.stat_result:
        if path == promptdeploy:
            raise PermissionError("parent denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_existing_parent)
    with pytest.raises(UnsafeManagedBundlePath, match="parent is not safely readable"):
        target.bundle_exists("ponytail")

    monkeypatch.undo()
    bundles = promptdeploy / "bundles"
    bundles.mkdir()
    leaf = bundles / "ponytail"
    target.remove_bundle("ponytail")

    def fail_leaf(path: Path) -> os.stat_result:
        if path == leaf:
            raise PermissionError("leaf denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_leaf)
    with pytest.raises(UnsafeManagedBundlePath, match="leaf is not safely readable"):
        target.bundle_exists("ponytail")
    with pytest.raises(UnsafeManagedBundlePath, match="leaf is not safely readable"):
        target.remove_bundle("ponytail")


@pytest.mark.parametrize(
    "name",
    ["Ponytail", "pony_tail", "pony.tail", "1ponytail", "pony--tail", "ponytail-"],
)
def test_bundle_name_grammar_rejects_noncanonical_spelling(
    name: str,
    tmp_path: Path,
) -> None:
    target = ClaudeTarget("claude", tmp_path / "claude")
    with pytest.raises(ValueError, match="Unsafe bundle name"):
        target.bundle_path(name)
    assert require_canonical_item_name("bundle", "ponytail") == "ponytail"


def test_bundle_type_category_selector_and_removal_plumbing(tmp_path: Path) -> None:
    assert _TYPE_TO_CATEGORY["bundle"] == "bundles"
    assert _CLI_TYPE_TO_ITEM_TYPE["bundles"] == "bundle"
    assert parse_item_selector("bundle:ponytail") == ("bundle", "ponytail")
    target = ClaudeTarget("claude", tmp_path / "claude")
    target.deploy_bundle("ponytail", b"license")
    _remove_item(target, "bundles", "ponytail")
    assert not target.bundle_exists("ponytail")


def test_stale_bundle_is_retained_with_filtered_dependent_then_removed_last(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    target_root = tmp_path / "target"
    target = ClaudeTarget("claude", target_root)
    target.deploy_bundle("ponytail", b"license")
    skill_path = target_root / "skills" / "ponytail"
    skill_path.mkdir(parents=True)
    (skill_path / "SKILL.md").write_bytes(b"skill")

    support_source = ManifestSource(
        "ponytail", "LICENSE", "4.8.4", None, None, True, None, "MIT"
    )
    skill_source = ManifestSource(
        "ponytail",
        "skills/ponytail",
        "4.8.4",
        None,
        None,
        True,
        None,
        "MIT",
    )
    manifest = Manifest(
        items={
            "bundles": {
                "ponytail": ManifestItem("sha256:" + "0" * 64, source=support_source)
            },
            "skills": {
                "ponytail": ManifestItem("sha256:" + "1" * 64, source=skill_source)
            },
        }
    )
    save_manifest(manifest, target_root / MANIFEST_FILENAME)
    config = Config(
        source_root=source_root,
        targets={
            "claude": TargetConfig("claude", "claude", target_root),
        },
        groups={},
    )

    assert deploy(config, item_types=["bundles"]) == []
    retained = load_manifest(target_root / MANIFEST_FILENAME)
    assert "ponytail" in retained.items["skills"]
    assert "ponytail" in retained.items["bundles"]
    assert target.bundle_exists("ponytail")

    actions = deploy(config)
    assert [(action.item_type, action.name) for action in actions] == [
        ("skill", "ponytail"),
        ("bundle", "ponytail"),
    ]
    assert not skill_path.exists()
    assert not target.bundle_exists("ponytail")


def test_gptel_accepts_only_prompts_and_support_bundle(tmp_path: Path) -> None:
    target = GptelTarget("gptel", tmp_path / "gptel")
    assert not target.should_skip("prompt", "one")
    assert not target.should_skip("bundle", "ponytail")
    assert target.should_skip("skill", "one")
