"""Strict Ponytail manifest and composed source-catalog tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from promptdeploy import bundle_catalog as catalog
from promptdeploy.bundles import (
    BundleConfig,
    BundleSchemaError,
    BundleSourceBinding,
)
from promptdeploy.imported_tree import (
    ImportedFileSnapshot,
    ImportedTreeEntry,
    ImportedTreeSnapshot,
    capture_imported_tree,
    framed_tree_sha256,
)
from promptdeploy.manifest import ManifestSource
from promptdeploy.ponytail import (
    PONYTAIL_ALL_TARGET_TYPES,
    PONYTAIL_NAMES,
    PONYTAIL_SKILL_TARGET_TYPES,
)
from promptdeploy.source import SourceItem, SourceProvenance

REPO_ROOT = Path(__file__).parent.parent
MANIFEST = REPO_ROOT / "bundles" / "ponytail.yaml"
REVISION = "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"
NAR_HASH = "sha256-Y7d4s7uqjH6IbEXhqAiQ+yaxr6iiGcv2X64LuMtG1T8="


@pytest.fixture(scope="module")
def ponytail_root() -> Path:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    return root.resolve()


def _binding(
    root: Path,
    *,
    mutable: bool = True,
    name: str = "ponytail",
    revision: str | None = None,
    nar_hash: str | None = None,
    version: str | None = None,
    binding_kind: str = "cli",
) -> BundleSourceBinding:
    return BundleSourceBinding(
        name=name,
        source_root=root,
        mutable=mutable,
        revision=revision,
        nar_hash=nar_hash,
        version=version,
        binding_kind=binding_kind,  # type: ignore[arg-type]
    )


def _bundle(
    root: Path,
    manifest: Path = MANIFEST,
    *,
    binding: BundleSourceBinding | None = None,
) -> BundleConfig:
    return BundleConfig(
        name="ponytail",
        manifest_path=manifest,
        binding=binding or _binding(root),
    )


def _manifest_data() -> dict[str, Any]:
    value = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_manifest(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "ponytail.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _minimal_source(source: Path, destination: Path) -> Path:
    destination.mkdir()
    shutil.copy2(source / "package.json", destination / "package.json")
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    (destination / "skills").mkdir()
    (destination / "skills").chmod((source / "skills").stat().st_mode | 0o700)
    for name in PONYTAIL_NAMES:
        shutil.copytree(source / "skills" / name, destination / "skills" / name)
    shutil.copytree(source / "hooks", destination / "hooks")
    shutil.copytree(source / ".opencode", destination / ".opencode")
    for path in (destination, *destination.rglob("*")):
        mode = path.stat().st_mode
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))
    return destination


@pytest.fixture(scope="module")
def items(ponytail_root: Path) -> tuple[SourceItem, ...]:
    return catalog.discover_bundle_items(_bundle(ponytail_root))


def test_manifest_and_catalog_are_exact_and_deterministic(
    ponytail_root: Path, items: tuple[SourceItem, ...]
) -> None:
    manifest = catalog.load_bundle_manifest(_bundle(ponytail_root))
    assert manifest.schema == 2
    assert manifest.revision == REVISION
    assert manifest.version.value == "4.8.4"
    assert tuple(export.name for export in manifest.exports) == PONYTAIL_NAMES
    assert [payload.name for payload in manifest.runtime.payloads] == [
        "claude-codex-runtime-v1",
        "opencode-plugin-v1",
    ]
    expected = ["bundle:ponytail"]
    for name in PONYTAIL_NAMES:
        expected.extend((f"skill:{name}", f"prompt:{name}"))
    assert [f"{item.item_type}:{item.name}" for item in items] == expected


def test_target_matrix_dependency_and_provenance(
    items: tuple[SourceItem, ...], ponytail_root: Path
) -> None:
    by_identity = {(item.item_type, item.name): item for item in items}
    support = by_identity[catalog.SUPPORT_IDENTITY]
    assert support.content == (ponytail_root / "LICENSE").read_bytes()
    assert support.target_types == PONYTAIL_ALL_TARGET_TYPES
    assert support.provenance.source == ManifestSource(
        "ponytail", "LICENSE", "4.8.4", None, None, True, None, "MIT"
    )
    assert [payload.name for payload in support.bundle_payloads] == [
        "claude-codex-runtime-v1",
        "opencode-plugin-v1",
    ]
    assert [payload.target_types for payload in support.bundle_payloads] == [
        frozenset({"claude", "codex"}),
        frozenset({"opencode"}),
    ]
    assert [
        (payload.imported_tree.logical_root, payload.imported_tree.tree_sha256)
        for payload in support.bundle_payloads
    ] == [
        (
            "runtime/claude-codex",
            "sha256:a2f4bbac93ba0359f7325621b1a7c7fb049c5b1244c21d9c0c37a89b47bc9894",
        ),
        (
            "runtime/opencode",
            "sha256:70becde0867bbe3f293b28a56744e60950c62b8758cf837dfeb82f780d29a15b",
        ),
    ]
    for name in PONYTAIL_NAMES:
        skill = by_identity[("skill", name)]
        prompt = by_identity[("prompt", name)]
        assert skill.target_types == PONYTAIL_SKILL_TARGET_TYPES
        assert "opencode" not in skill.target_types
        assert prompt.target_types == frozenset({"gptel"})
        assert skill.requires == prompt.requires == catalog.SUPPORT_REQUIREMENT
        assert skill.provenance.source is not None
        assert skill.provenance.source.path == f"skills/{name}"
        assert skill.provenance.source.transform is None
        assert skill.imported_tree is not None
        assert prompt.provenance.source is not None
        assert prompt.provenance.source.path == f"skills/{name}/SKILL.md"
        assert prompt.provenance.source.transform == "gptel-preset-v1"
        assert prompt.imported_tree is None


def test_runtime_payloads_are_closed_transformed_snapshots(
    items: tuple[SourceItem, ...],
) -> None:
    support = items[0]
    assert catalog.compute_imported_source_hash(support) == (
        "sha256:6cc78d369c83391cb9aee7a4f58fc626831782915bf1e0d01677a820863bdbb4"
    )
    payloads = {
        payload.name: payload.imported_tree for payload in support.bundle_payloads
    }
    claude = payloads["claude-codex-runtime-v1"]
    opencode = payloads["opencode-plugin-v1"]
    claude_entries = {entry.relative_path: entry for entry in claude.entries}
    opencode_entries = {entry.relative_path: entry for entry in opencode.entries}

    assert len(claude_entries) == 14
    assert len(opencode_entries) == 28
    assert "hooks/copilot-hooks.json" not in claude_entries
    assert "hooks/qoder-hooks.json" not in claude_entries
    assert "hooks/ponytail-mode-tracker.js" not in opencode_entries
    assert not any(
        "node_modules" in path for path in (*claude_entries, *opencode_entries)
    )
    strict = claude_entries["hooks/ponytail-instructions.js"]
    assert strict == opencode_entries["hooks/ponytail-instructions.js"]
    assert strict.content is not None
    assert b"getFallbackInstructions" not in strict.content
    tracker = claude_entries["hooks/ponytail-mode-tracker.js"]
    assert tracker.content is not None
    assert b"getPonytailInstructions('review')" in tracker.content
    for name in PONYTAIL_NAMES:
        assert f"skills/{name}" in opencode_entries
        assert f"skills/{name}/SKILL.md" in opencode_entries


@pytest.mark.parametrize(
    ("relative_path", "kind"),
    [
        ("hooks/new-runtime.js", "file"),
        (".opencode/command/new-command.md", "file"),
        (".opencode/plugins/new-plugin.mjs", "file"),
        ("skills/seventh", "directory"),
    ],
)
def test_runtime_inventory_rejects_unlisted_adapter_nodes_without_importing_them(
    ponytail_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    kind: str,
) -> None:
    source = _minimal_source(ponytail_root, tmp_path / "source")
    path = source / relative_path
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"must not enter payload")
    original_open = os.open

    def reject_unlisted_open(
        candidate: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if os.fspath(candidate) == path.name:
            raise AssertionError(f"unlisted adapter node was opened: {relative_path}")
        return original_open(candidate, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", reject_unlisted_open)

    with pytest.raises(BundleSchemaError, match="runtime inventory mismatch"):
        catalog.discover_bundle_items(_bundle(source))


def test_runtime_selected_byte_drift_fails_before_catalog_emission(
    ponytail_root: Path,
    tmp_path: Path,
) -> None:
    source = _minimal_source(ponytail_root, tmp_path / "source")
    selected = source / "hooks" / "ponytail-runtime.js"
    selected.write_bytes(selected.read_bytes() + b"\n")

    with pytest.raises(BundleSchemaError, match="transformed tree digest mismatch"):
        catalog.discover_bundle_items(_bundle(source))


@pytest.mark.parametrize("relative_path", ["missing", "."])
def test_runtime_file_selection_requires_one_regular_snapshot_entry(
    relative_path: str,
) -> None:
    entries = (
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("file", "file", 0o644, b"value"),
    )
    snapshot = ImportedTreeSnapshot(
        "skills/demo",
        entries,
        framed_tree_sha256(entries),
    )
    with pytest.raises(BundleSchemaError, match="runtime file is missing"):
        catalog._snapshot_regular_file(snapshot, relative_path)


def _runtime_manifest(
    include: tuple[str, ...],
    tree_sha256: str = "sha256:" + "0" * 64,
) -> catalog.BundleRuntimePayload:
    return catalog.BundleRuntimePayload(
        "test-runtime-v1",
        frozenset({"claude"}),
        include,
        (),
        tree_sha256,
        "runtime/test",
    )


def _assemble_test_runtime(
    runtime: catalog.BundleRuntimePayload,
    files: dict[str, ImportedFileSnapshot],
    skills: dict[str, ImportedTreeSnapshot] | None = None,
) -> ImportedTreeSnapshot:
    return catalog._assemble_runtime_snapshot(
        runtime,
        files,
        skills or {},
        {"skills": 0o755},
        bundle_name="ponytail",
        version="4.8.4",
        revision=REVISION,
    )


def test_runtime_assembler_rejects_conflicts_casefolds_and_missing_inputs() -> None:
    skill_entries = (
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("file", "SKILL.md", 0o644, b"skill"),
    )
    skill = ImportedTreeSnapshot(
        "skills/demo",
        skill_entries,
        framed_tree_sha256(skill_entries),
    )
    conflicting = _runtime_manifest(("skills/demo", "skills/demo/SKILL.md"))
    with pytest.raises(BundleSchemaError, match="conflicting runtime path"):
        _assemble_test_runtime(
            conflicting,
            {
                "skills/demo/SKILL.md": ImportedFileSnapshot(
                    "skills/demo/SKILL.md", 0o644, b"different"
                )
            },
            {"skills/demo": skill},
        )

    casefold = _runtime_manifest(("Name", "name"))
    with pytest.raises(BundleSchemaError, match="case-fold collision"):
        _assemble_test_runtime(
            casefold,
            {
                "Name": ImportedFileSnapshot("Name", 0o644, b"one"),
                "name": ImportedFileSnapshot("name", 0o644, b"two"),
            },
        )

    with pytest.raises(BundleSchemaError, match="missing runtime input"):
        _assemble_test_runtime(_runtime_manifest(("missing",)), {})


def test_runtime_assembler_preserves_complete_skill_tree_and_rebases_links() -> None:
    skill_entries = (
        ImportedTreeEntry("directory", ".", 0o750),
        ImportedTreeEntry("link", "alias", 0o644, b"value", "target"),
        ImportedTreeEntry("directory", "assets", 0o700),
        ImportedTreeEntry("directory", "assets/empty", 0o711),
        ImportedTreeEntry("file", "assets/helper.sh", 0o755, b"#!/bin/sh\n"),
        ImportedTreeEntry("file", "target", 0o644, b"value"),
    )
    skill = ImportedTreeSnapshot(
        "skills/demo",
        skill_entries,
        framed_tree_sha256(skill_entries),
    )
    expected = (
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("directory", "skills", 0o755),
        ImportedTreeEntry("directory", "skills/demo", 0o750),
        ImportedTreeEntry(
            "link", "skills/demo/alias", 0o644, b"value", "skills/demo/target"
        ),
        ImportedTreeEntry("directory", "skills/demo/assets", 0o700),
        ImportedTreeEntry("directory", "skills/demo/assets/empty", 0o711),
        ImportedTreeEntry(
            "file", "skills/demo/assets/helper.sh", 0o755, b"#!/bin/sh\n"
        ),
        ImportedTreeEntry("file", "skills/demo/target", 0o644, b"value"),
    )
    runtime = _runtime_manifest(("skills/demo",), framed_tree_sha256(expected))

    snapshot = _assemble_test_runtime(runtime, {}, {"skills/demo": skill})

    assert snapshot.entries == expected


def test_runtime_inventory_change_during_bundle_capture_is_rejected(
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = catalog._capture_runtime_inventory
    calls = 0

    def changing_inventory(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        captured = original(*args, **kwargs)
        if calls == 2:
            first = replace(captured[0], identity=(*captured[0].identity, 1))
            return (first, *captured[1:])
        return captured

    monkeypatch.setattr(catalog, "_capture_runtime_inventory", changing_inventory)
    with pytest.raises(BundleSchemaError, match="inventory changed"):
        catalog.discover_bundle_items(_bundle(ponytail_root))


def test_catalog_items_retain_no_live_source_authority(
    ponytail_root: Path, tmp_path: Path
) -> None:
    source = _minimal_source(ponytail_root, tmp_path / "source")
    discovered = catalog.discover_bundle_items(_bundle(source))
    before = {
        (item.item_type, item.name): (
            item.content,
            item.imported_tree,
            item.bundle_payloads,
            catalog.compute_imported_source_hash(item),
        )
        for item in discovered
    }
    shutil.rmtree(source)
    after = {
        (item.item_type, item.name): (
            item.content,
            item.imported_tree,
            item.bundle_payloads,
            catalog.compute_imported_source_hash(item),
        )
        for item in discovered
    }
    assert after == before


def test_immutable_binding_emits_exact_pin(ponytail_root: Path) -> None:
    binding = _binding(
        ponytail_root,
        mutable=False,
        revision=REVISION,
        nar_hash=NAR_HASH,
        version="4.8.4",
        binding_kind="descriptor",
    )
    item = catalog.discover_bundle_items(_bundle(ponytail_root, binding=binding))[1]
    assert item.provenance.source is not None
    assert item.provenance.source.revision == REVISION
    assert item.provenance.source.nar_hash == NAR_HASH
    assert not item.provenance.source.mutable


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("schema",), True),
        (("schema",), 1),
        (("name",), "other"),
        (("revision",), "0" * 40),
        (("version", "value"), "4.8.5"),
        (("version", "file"), "other.json"),
        (("version", "key"), "release"),
        (("license", "spdx"), "Apache-2.0"),
        (("license", "file"), "COPYING"),
        (("license", "sha256"), "bad"),
        (("exports", 0, "type"), "prompt"),
        (("exports", 0, "name"), "other"),
        (("exports", 0, "path"), "skills/other"),
        (("exports", 0, "tree_sha256"), "bad"),
        (("exports", 0, "skill_md_sha256"), "bad"),
        (("exports", 0, "target_types"), ["claude"]),
        (("exports", 0, "projections", 0, "type"), "skill"),
        (("exports", 0, "projections", 0, "name"), "other"),
        (("exports", 0, "projections", 0, "target_types"), ["codex"]),
        (("exports", 0, "projections", 0, "transform"), "other"),
        (("runtime", "inventory", "hooks"), ["other"]),
        (("runtime", "payloads", 0, "name"), "other"),
        (("runtime", "payloads", 0, "target_types"), ["claude"]),
        (("runtime", "payloads", 0, "include", 0), "../hooks.json"),
        (
            ("runtime", "payloads", 0, "transforms", "hooks/ponytail-instructions.js"),
            "other",
        ),
        (("runtime", "payloads", 0, "tree_sha256"), "bad"),
    ],
)
def test_manifest_rejects_wrong_reviewed_values(
    ponytail_root: Path,
    tmp_path: Path,
    mutation: tuple[str | int, ...],
    value: object,
) -> None:
    data: Any = _manifest_data()
    target = data
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value
    with pytest.raises(BundleSchemaError):
        catalog.load_bundle_manifest(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


@pytest.mark.parametrize(
    ("function", "value"),
    [
        (catalog._trimmed_string, ""),
        (catalog._trimmed_string, " spaced "),
        (catalog._canonical_name, "Bad_Name"),
        (catalog._canonical_relative_path, None),
        (catalog._canonical_relative_path, ""),
        (catalog._exact_string_list, "not-a-list"),
        (catalog._exact_string_list, ["gptel", 1]),
    ],
)
def test_manifest_scalar_helpers_reject_wrong_shapes(
    function: Any, value: object
) -> None:
    with pytest.raises(BundleSchemaError):
        if function is catalog._exact_string_list:
            function(value, expected=("gptel",), where="test")
        else:
            function(value, where="test")


def test_runtime_path_list_is_closed_ordered_and_unique() -> None:
    with pytest.raises(BundleSchemaError, match="list of paths"):
        catalog._exact_path_list("path", expected=("path",), where="test")
    with pytest.raises(BundleSchemaError, match="duplicates"):
        catalog._exact_path_list(["path", "path"], expected=("path",), where="test")
    with pytest.raises(BundleSchemaError, match="reviewed ordered"):
        catalog._exact_path_list(["other"], expected=("path",), where="test")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("name"),
        lambda data: data.update(extra=True),
        lambda data: data["version"].pop("key"),
        lambda data: data["license"].update(extra=True),
        lambda data: data.update(exports=data["exports"][:-1]),
        lambda data: data["exports"][0].pop("path"),
        lambda data: data["exports"][0].update(extra=True),
        lambda data: data["exports"][0].update(projections=[]),
        lambda data: data["exports"][0]["projections"][0].pop("name"),
        lambda data: data["exports"][0]["projections"][0].update(extra=True),
        lambda data: data["exports"][0].update(
            target_types=["claude", "claude", "droid"]
        ),
        lambda data: data.pop("runtime"),
        lambda data: data["runtime"].update(extra=True),
        lambda data: data["runtime"]["inventory"].pop("hooks"),
        lambda data: data["runtime"]["inventory"].update(extra=[]),
        lambda data: data["runtime"].update(payloads=[]),
        lambda data: data["runtime"]["payloads"][0].pop("include"),
        lambda data: data["runtime"]["payloads"][0].update(extra=True),
        lambda data: data["runtime"]["payloads"][0].update(
            include=data["runtime"]["payloads"][0]["include"] * 2
        ),
        lambda data: data["runtime"]["payloads"][0]["transforms"].update(extra="other"),
    ],
)
def test_manifest_closed_schema_rejects_missing_unknown_and_duplicate_lists(
    ponytail_root: Path,
    tmp_path: Path,
    mutate: Any,
) -> None:
    data = _manifest_data()
    mutate(data)
    with pytest.raises(BundleSchemaError):
        catalog.load_bundle_manifest(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


@pytest.mark.parametrize(
    "value",
    [None, [], {1: "bad"}],
)
def test_manifest_root_and_nested_values_must_be_mappings(
    ponytail_root: Path, tmp_path: Path, value: object
) -> None:
    data: object = value
    if value == []:
        data = {**_manifest_data(), "version": []}
    elif isinstance(value, dict):
        data = {**_manifest_data(), "license": value}
    with pytest.raises(BundleSchemaError):
        catalog.load_bundle_manifest(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


@pytest.mark.parametrize(
    "replacement",
    [
        "schema: 2\nschema: 2",
        "name: ponytail\nname: ponytail",
    ],
)
def test_manifest_rejects_duplicate_yaml_keys(
    ponytail_root: Path, tmp_path: Path, replacement: str
) -> None:
    original = MANIFEST.read_text(encoding="utf-8")
    first_line = replacement.splitlines()[0]
    path = tmp_path / "ponytail.yaml"
    path.write_text(original.replace(first_line, replacement, 1), encoding="utf-8")
    with pytest.raises(BundleSchemaError, match="duplicate key"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, path))


@pytest.mark.parametrize(
    ("relative", "replacement"),
    [
        (("version", "file"), "../package.json"),
        (("license", "file"), "/LICENSE"),
        (("exports", 0, "path"), "skills\\ponytail"),
        (("runtime", "payloads", 0, "include", 0), "/hooks.json"),
    ],
)
def test_manifest_paths_are_canonical(
    ponytail_root: Path,
    tmp_path: Path,
    relative: tuple[str | int, ...],
    replacement: str,
) -> None:
    data: Any = _manifest_data()
    target = data
    for key in relative[:-1]:
        target = target[key]
    target[relative[-1]] = replacement
    with pytest.raises(BundleSchemaError, match="canonical"):
        catalog.load_bundle_manifest(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


def test_adapter_manifest_must_be_stable_regular_utf8(
    ponytail_root: Path, tmp_path: Path
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(BundleSchemaError, match="regular"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, directory))
    invalid = tmp_path / "invalid.yaml"
    invalid.write_bytes(b"\xff")
    with pytest.raises(BundleSchemaError, match="UTF-8"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, invalid))
    link = tmp_path / "link.yaml"
    link.symlink_to(MANIFEST)
    with pytest.raises(BundleSchemaError, match="safely readable"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, link))


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


def test_adapter_manifest_detects_open_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = MANIFEST.stat()
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _fd: _fake_stat(actual, st_ino=actual.st_ino + 1),
    )
    with pytest.raises(BundleSchemaError, match="changed during capture"):
        catalog._read_adapter_manifest(MANIFEST)


def test_adapter_manifest_preflight_and_streaming_size_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog, "_MANIFEST_MAX_BYTES", 1)
    with pytest.raises(BundleSchemaError, match="size limit"):
        catalog._read_adapter_manifest(MANIFEST)

    actual = MANIFEST.stat()
    monkeypatch.setattr(catalog, "_MANIFEST_MAX_BYTES", 10)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _fd: _fake_stat(actual, st_size=0),
    )
    with pytest.raises(BundleSchemaError, match="size limit"):
        catalog._read_adapter_manifest(MANIFEST)


def test_adapter_manifest_detects_post_read_metadata_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_fstat = os.fstat
    calls = 0

    def changing_fstat(fd: int) -> object:
        nonlocal calls
        calls += 1
        value = actual_fstat(fd)
        if calls == 2:
            return _fake_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
        return value

    monkeypatch.setattr(os, "fstat", changing_fstat)
    with pytest.raises(BundleSchemaError, match="changed during capture"):
        catalog._read_adapter_manifest(MANIFEST)


@pytest.mark.parametrize(
    "binding",
    [
        _binding(Path("relative")),
        _binding(Path("/tmp"), name="other"),
        _binding(Path("/tmp"), revision=REVISION),
        _binding(
            Path("/tmp"),
            mutable=False,
            revision=None,
            nar_hash=None,
            version=None,
            binding_kind="descriptor",
        ),
        _binding(Path("/tmp"), binding_kind="other"),
    ],
)
def test_catalog_reasserts_direct_binding_invariants(
    binding: BundleSourceBinding,
) -> None:
    with pytest.raises(BundleSchemaError):
        catalog.load_bundle_manifest(BundleConfig("ponytail", MANIFEST, binding))


def test_manifest_rejects_binding_revision_and_version_mismatch(
    ponytail_root: Path,
) -> None:
    revision = _binding(
        ponytail_root,
        mutable=False,
        revision="0" * 40,
        nar_hash=NAR_HASH,
        version="4.8.4",
        binding_kind="descriptor",
    )
    with pytest.raises(BundleSchemaError, match="revision"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, binding=revision))
    version = _binding(ponytail_root, version="4.8.5")
    with pytest.raises(BundleSchemaError, match="version"):
        catalog.load_bundle_manifest(_bundle(ponytail_root, binding=version))


@pytest.mark.parametrize(
    ("package", "message"),
    [
        (b'{"version":"4.8.4","version":"4.8.4"}', "duplicate key"),
        (b'{"version":NaN}', "invalid constant"),
        (b"[]", "contain an object"),
        (b'{"version":"wrong"}', "does not match"),
        (b"\xff", "UTF-8 JSON"),
    ],
)
def test_source_version_file_is_strict(
    ponytail_root: Path,
    tmp_path: Path,
    package: bytes,
    message: str,
) -> None:
    root = _minimal_source(ponytail_root, tmp_path / "source")
    (root / "package.json").write_bytes(package)
    with pytest.raises(BundleSchemaError, match=message):
        catalog.discover_bundle_items(_bundle(root))


@pytest.mark.parametrize(
    ("license_content", "message"),
    [
        (b"", "empty"),
        (b"\xff", "UTF-8"),
        (b"different", "digest mismatch"),
    ],
)
def test_source_license_is_strict(
    ponytail_root: Path,
    tmp_path: Path,
    license_content: bytes,
    message: str,
) -> None:
    root = _minimal_source(ponytail_root, tmp_path / "source")
    (root / "LICENSE").write_bytes(license_content)
    with pytest.raises(BundleSchemaError, match=message):
        catalog.discover_bundle_items(_bundle(root))


def test_selected_tree_and_skill_digest_guards(
    ponytail_root: Path, tmp_path: Path
) -> None:
    data = _manifest_data()
    data["exports"][0]["tree_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(BundleSchemaError, match="tree digest"):
        catalog.discover_bundle_items(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


@pytest.mark.parametrize(
    ("skill_content", "message"),
    [
        (b"---\n: invalid: yaml\n---\nbody\n", "Invalid YAML"),
        (b"---\nname: wrong\n---\nbody\n", "frontmatter name"),
        (b"---\nname: ponytail\n---\n", "body is empty"),
    ],
)
def test_discovery_rejects_bad_skill_structure_after_matching_digest(
    ponytail_root: Path,
    tmp_path: Path,
    skill_content: bytes,
    message: str,
) -> None:
    root = _minimal_source(ponytail_root, tmp_path / "source")
    skill_md = root / "skills" / "ponytail" / "SKILL.md"
    skill_md.write_bytes(skill_content)
    snapshot = capture_imported_tree(root, "skills/ponytail")
    data = _manifest_data()
    data["exports"][0]["tree_sha256"] = snapshot.tree_sha256
    data["exports"][0]["skill_md_sha256"] = (
        "sha256:" + hashlib.sha256(skill_content).hexdigest()
    )
    with pytest.raises(BundleSchemaError, match=message):
        catalog.discover_bundle_items(_bundle(root, _write_manifest(tmp_path, data)))
    data = _manifest_data()
    data["exports"][0]["skill_md_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(BundleSchemaError, match="digest mismatch"):
        catalog.discover_bundle_items(
            _bundle(ponytail_root, _write_manifest(tmp_path, data))
        )


def test_skill_snapshot_requires_one_regular_skill_md() -> None:
    with pytest.raises(BundleSchemaError, match="lacks regular"):
        catalog._skill_md((ImportedTreeEntry("directory", ".", 0o755),), "skills/x")
    with pytest.raises(BundleSchemaError, match="has no bytes"):
        entry = ImportedTreeEntry("file", "SKILL.md", 0o644, b"x")
        object.__setattr__(entry, "content", None)
        catalog._skill_md((entry,), "skills/x")


def _primary(name: str = "local", item_type: str = "skill") -> SourceItem:
    return SourceItem(
        item_type,
        name,
        Path(f"/{name}"),
        None,
        b"content",
        provenance=SourceProvenance.primary(f"skills/{name}/SKILL.md"),
    )


def _support(
    name: str = "support", targets: frozenset[str] | None = None
) -> SourceItem:
    return SourceItem(
        "bundle",
        name,
        Path(f"/{name}"),
        None,
        b"support",
        target_types=targets,
    )


def test_catalog_preflight_duplicate_missing_inapplicable_and_cycle() -> None:
    duplicate = (_primary(), _primary())
    assert (
        "duplicate source identity" in catalog.preflight_catalog(duplicate)[0].message
    )

    missing = replace(_primary(), requires=(("bundle", "missing"),))
    assert "requires missing" in catalog.preflight_catalog((missing,))[0].message

    required = _support(targets=frozenset({"gptel"}))
    dependent = replace(
        _primary(),
        target_types=frozenset({"codex"}),
        requires=(("bundle", "support"),),
    )
    assert (
        "does not apply everywhere"
        in catalog.preflight_catalog((required, dependent))[0].message
    )

    first = replace(_support("first"), requires=(("bundle", "second"),))
    second = replace(_support("second"), requires=(("bundle", "first"),))
    assert any(
        "dependency cycle" in issue.message
        for issue in catalog.preflight_catalog((first, second))
    )


def test_compose_catalog_and_raise_issue_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _primary()

    def applies(_item: SourceItem, _target: str, _target_type: str) -> bool:
        return True

    assert catalog.compose_catalog(
        (primary,),
        (),
        configured_target_types={"local": "codex"},
        applies=applies,
    ) == (primary,)
    with pytest.raises(catalog.BundleCatalogError, match="duplicate"):
        catalog.compose_catalog(
            (primary, primary),
            (),
            configured_target_types={"local": "codex"},
            applies=applies,
        )
    catalog.raise_catalog_issues(())

    imported = replace(primary, name="imported")
    monkeypatch.setattr(catalog, "discover_bundle_items", lambda _bundle: (imported,))
    fake_bundle = _bundle(Path("/"))
    assert catalog.compose_catalog(
        (primary,),
        (fake_bundle,),
        configured_target_types={"local": "codex"},
        applies=applies,
    ) == (primary, imported)

    source = ManifestSource(
        "ponytail", "skills/local/SKILL.md", "4.8.4", None, None, True, None, "MIT"
    )
    collision = replace(
        imported,
        item_type="prompt",
        name="local",
        provenance=SourceProvenance.imported(source),
    )
    monkeypatch.setattr(catalog, "discover_bundle_items", lambda _bundle: (collision,))
    with pytest.raises(catalog.BundleCatalogError, match="slash-name collision"):
        catalog.compose_catalog(
            (primary,),
            (fake_bundle,),
            configured_target_types={"local": "codex"},
            applies=applies,
        )


def test_effective_collision_preflight_uses_caller_predicate() -> None:
    source = ManifestSource(
        "ponytail", "skills/same", "4.8.4", None, None, True, None, "MIT"
    )
    imported = replace(
        _primary("same", "prompt"),
        provenance=SourceProvenance.imported(source),
    )
    native = _primary("same", "skill")
    assert (
        catalog.preflight_name_collisions(
            (native, imported),
            configured_target_types={"gptel": "gptel"},
            applies=lambda item, _target, target_type: (
                item.item_type == "prompt" and target_type == "gptel"
            ),
        )
        == ()
    )
    same_type = replace(imported, provenance=SourceProvenance.imported(source))
    assert (
        catalog.preflight_name_collisions(
            (imported, same_type, _support()),
            configured_target_types={"gptel": "gptel"},
            applies=lambda _item, _target, _target_type: True,
        )
        == ()
    )
    issues = catalog.preflight_name_collisions(
        (native, imported),
        configured_target_types={"gptel": "gptel"},
        applies=lambda _item, _target, _target_type: True,
    )
    assert len(issues) == 1
    assert "both apply" in issues[0].message
    assert (
        catalog.preflight_name_collisions(
            (_primary("same", "skill"), _primary("same", "prompt")),
            configured_target_types={"x": "codex"},
            applies=lambda _item, _target, _target_type: True,
        )
        == ()
    )


def test_dependency_closure_is_per_target_and_requirements_bypass_filter() -> None:
    support = replace(
        _support("ponytail", PONYTAIL_ALL_TARGET_TYPES),
        provenance=SourceProvenance.primary("support/ponytail"),
    )
    skill = replace(
        _primary("ponytail"),
        target_types=frozenset({"codex"}),
        requires=(("bundle", "ponytail"),),
    )
    prompt = replace(
        _primary("prompt", "prompt"),
        target_types=frozenset({"gptel"}),
        requires=(("bundle", "ponytail"),),
    )
    items = (support, skill, prompt)
    assert catalog.dependency_closure_for_target(
        {("skill", "ponytail")},
        items,
        target_type="codex",
        requested_filter=lambda item: item.item_type == "skill",
    ) == {("skill", "ponytail"), ("bundle", "ponytail")}
    assert catalog.dependency_closure_for_target(
        {("skill", "ponytail"), ("bundle", "ponytail")},
        items,
        target_type="codex",
    ) == {("skill", "ponytail"), ("bundle", "ponytail")}
    assert (
        catalog.dependency_closure_for_target(
            {("skill", "ponytail")}, items, target_type="gptel"
        )
        == set()
    )
    with pytest.raises(catalog.BundleCatalogError, match="unknown selected"):
        catalog.dependency_closure_for_target(
            {("skill", "missing")}, items, target_type="codex"
        )


def test_dependency_closure_defensively_rejects_nonapplicable_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support = _support("support", frozenset({"gptel"}))
    dependent = replace(
        _primary(),
        target_types=frozenset({"codex"}),
        requires=(("bundle", "support"),),
    )
    monkeypatch.setattr(catalog, "preflight_catalog", lambda _items: ())
    with pytest.raises(catalog.BundleCatalogError, match="not applicable"):
        catalog.dependency_closure_for_target(
            {("skill", "local")}, (support, dependent), target_type="codex"
        )


def test_imported_hash_uses_payload_and_all_provenance_fields(
    items: tuple[SourceItem, ...],
) -> None:
    skill = items[1]
    original = catalog.compute_imported_source_hash(skill)
    assert original == catalog.compute_imported_source_hash(skill)
    assert (
        catalog.compute_imported_source_hash(replace(skill, name="other")) != original
    )
    with pytest.raises(catalog.BundleCatalogError, match="content does not match"):
        catalog.compute_imported_source_hash(
            replace(skill, content=skill.content + b"not the snapshot")
        )
    source = skill.provenance.source
    assert source is not None
    changed_source = replace(source, version="4.8.5")
    changed = replace(
        skill,
        provenance=SourceProvenance.imported(
            changed_source,
            input_sha256=skill.provenance.input_sha256,
            tree_sha256=skill.provenance.tree_sha256,
        ),
    )
    assert catalog.compute_imported_source_hash(changed) != original
    with pytest.raises(catalog.BundleCatalogError, match="primary"):
        catalog.compute_imported_source_hash(_primary())
    with pytest.raises(catalog.BundleCatalogError, match="accepted tree"):
        catalog.compute_imported_source_hash(replace(skill, imported_tree=None))


def test_imported_skill_snapshot_rejects_split_brain_state(
    items: tuple[SourceItem, ...],
) -> None:
    skill = items[1]
    snapshot = catalog.imported_skill_snapshot(skill)
    wrong_root = ImportedTreeSnapshot(
        "skills/other",
        snapshot.entries,
        snapshot.tree_sha256,
    )
    with pytest.raises(catalog.BundleCatalogError, match="root does not match"):
        catalog.imported_skill_snapshot(replace(skill, imported_tree=wrong_root))

    source = skill.provenance.source
    assert source is not None
    wrong_digest = replace(
        skill,
        provenance=SourceProvenance.imported(
            source,
            input_sha256="sha256:" + "0" * 64,
            tree_sha256=skill.provenance.tree_sha256,
        ),
    )
    with pytest.raises(catalog.BundleCatalogError, match="content digest"):
        catalog.imported_skill_snapshot(wrong_digest)
    wrong_tree = replace(
        skill,
        provenance=SourceProvenance.imported(
            source,
            input_sha256=skill.provenance.input_sha256,
            tree_sha256="sha256:" + "0" * 64,
        ),
    )
    with pytest.raises(catalog.BundleCatalogError, match="tree snapshot"):
        catalog.imported_skill_snapshot(wrong_tree)
    with pytest.raises(catalog.BundleCatalogError, match="not an imported skill"):
        catalog.imported_skill_snapshot(_primary())
    with pytest.raises(catalog.BundleCatalogError, match="primary item"):
        catalog.imported_tree_snapshot(_primary())
    with pytest.raises(catalog.BundleCatalogError, match="not an imported skill"):
        catalog.imported_skill_snapshot(items[0])


def test_imported_hash_uses_snapshot_for_every_tree_backed_item(
    items: tuple[SourceItem, ...],
) -> None:
    support = items[0]
    source = support.provenance.source
    assert source is not None
    entries = (
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("file", "runtime.js", 0o644, b"runtime\n"),
    )
    snapshot = ImportedTreeSnapshot(
        "runtime/ponytail", entries, framed_tree_sha256(entries)
    )
    tree_source = replace(source, path=snapshot.logical_root)
    tree_backed = replace(
        support,
        imported_tree=snapshot,
        provenance=SourceProvenance.imported(
            tree_source,
            input_sha256=support.provenance.input_sha256,
            tree_sha256=snapshot.tree_sha256,
        ),
    )
    original = catalog.compute_imported_source_hash(tree_backed)

    wrong_root = ImportedTreeSnapshot(
        "runtime/other",
        snapshot.entries,
        snapshot.tree_sha256,
    )
    with pytest.raises(catalog.BundleCatalogError, match="root does not match"):
        catalog.compute_imported_source_hash(
            replace(tree_backed, imported_tree=wrong_root)
        )

    changed_entries = (
        entries[0],
        replace(entries[1], normalized_mode=0o755),
    )
    changed_snapshot = ImportedTreeSnapshot(
        snapshot.logical_root,
        changed_entries,
        framed_tree_sha256(changed_entries),
    )
    changed = replace(
        tree_backed,
        imported_tree=changed_snapshot,
        provenance=SourceProvenance.imported(
            tree_source,
            input_sha256=support.provenance.input_sha256,
            tree_sha256=changed_snapshot.tree_sha256,
        ),
    )
    assert catalog.compute_imported_source_hash(changed) != original
    with pytest.raises(catalog.BundleCatalogError, match="tree snapshot"):
        catalog.compute_imported_source_hash(
            replace(changed, provenance=tree_backed.provenance)
        )


def test_catalog_summary_contains_only_logical_source(
    items: tuple[SourceItem, ...], ponytail_root: Path
) -> None:
    rendered = json.dumps(catalog.catalog_summary(items), sort_keys=True)
    assert str(ponytail_root) not in rendered
    assert '"count": 13' in rendered
    assert '"bundle": "ponytail"' in rendered


def test_item_applicability_default_and_restricted() -> None:
    assert catalog.item_applies_to_target_type(_primary(), "anything")
    assert catalog.item_applies_to_target_type(
        replace(_primary(), target_types=frozenset({"codex"})), "codex"
    )
    assert not catalog.item_applies_to_target_type(
        replace(_primary(), target_types=frozenset({"codex"})), "gptel"
    )


def test_content_digest_matches_license(items: tuple[SourceItem, ...]) -> None:
    support = items[0]
    assert support.provenance.input_sha256 == (
        "sha256:" + hashlib.sha256(support.content).hexdigest()
    )
