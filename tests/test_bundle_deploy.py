"""Operational Ponytail catalog deployment across every supported target tier."""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from promptdeploy import cli
from promptdeploy.bundle_catalog import imported_skill_snapshot
from promptdeploy.bundles import BundleConfig, BundleSourceBinding
from promptdeploy.catalog import discover_operation_catalog
from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import deploy
from promptdeploy.manifest import ManifestSource, load_manifest, save_manifest
from promptdeploy.ponytail import PONYTAIL_NAMES
from promptdeploy.source import SourceItem, SourceProvenance
from promptdeploy.status import get_status
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.validate import validate_all
from promptdeploy.verify import verify_items

ROOT = Path(__file__).resolve().parents[1]
PONYTAIL_MANIFEST = ROOT / "bundles" / "ponytail.yaml"
ALL_SELECTORS = [
    ("bundle", "ponytail"),
    *(
        (item_type, name)
        for name in PONYTAIL_NAMES
        for item_type in ("skill", "prompt")
    ),
]


@pytest.fixture(scope="module")
def ponytail_root() -> Path:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    return root.resolve()


def _bundle(source: Path) -> BundleConfig:
    return BundleConfig(
        name="ponytail",
        manifest_path=PONYTAIL_MANIFEST,
        binding=BundleSourceBinding(
            name="ponytail",
            source_root=source,
            mutable=True,
            revision=None,
            nar_hash=None,
            version=None,
            binding_kind="cli",
        ),
    )


def _config(
    tmp_path: Path,
    ponytail_root: Path,
    *target_types: str,
) -> Config:
    source = tmp_path / "primary"
    source.mkdir(parents=True)
    return Config(
        source_root=source,
        targets={
            target_type: TargetConfig(
                id=target_type,
                type=target_type,
                path=tmp_path / "targets" / target_type,
            )
            for target_type in target_types
        },
        groups={},
        bundles=(_bundle(ponytail_root),),
    )


def _manifest_path(config: Config, target_type: str) -> Path:
    root = config.targets[target_type].path
    if target_type == "codex":
        return root / ".codex" / ".prompt-deploy-manifest.json"
    return root / ".prompt-deploy-manifest.json"


def _support_path(config: Config, target_type: str) -> Path:
    return (
        config.targets[target_type].path
        / ".promptdeploy"
        / "bundles"
        / "ponytail"
        / "LICENSE"
    )


def test_full_catalog_converges_and_verifies_across_all_target_tiers(
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    target_types = ("claude", "codex", "droid", "opencode", "gptel")
    config = _config(tmp_path, ponytail_root, *target_types)
    assert [issue for issue in validate_all(config) if issue.level == "error"] == []

    first = deploy(config)

    expected = {
        "claude": {
            ("bundle", "ponytail"),
            *(("skill", name) for name in PONYTAIL_NAMES),
        },
        "codex": {
            ("bundle", "ponytail"),
            *(("skill", name) for name in PONYTAIL_NAMES),
        },
        "droid": {
            ("bundle", "ponytail"),
            *(("skill", name) for name in PONYTAIL_NAMES),
        },
        "opencode": {("bundle", "ponytail")},
        "gptel": {
            ("bundle", "ponytail"),
            *(("prompt", name) for name in PONYTAIL_NAMES),
        },
    }
    for target_type in target_types:
        identities = {
            (action.item_type, action.name)
            for action in first
            if action.target_id == target_type and action.action == "create"
        }
        assert identities == expected[target_type]
        assert (
            _support_path(config, target_type).read_bytes()
            == (ponytail_root / "LICENSE").read_bytes()
        )

        manifest = load_manifest(_manifest_path(config, target_type))
        manifest_items = {
            (category, name): item
            for category, entries in manifest.items.items()
            for name, item in entries.items()
        }
        assert len(manifest_items) == len(expected[target_type])
        assert all(item.source is not None for item in manifest_items.values())

    assert all(
        action.source_path is not None
        and action.source_path.startswith("ponytail:")
        and str(ponytail_root) not in action.source_path
        for action in first
    )
    assert (
        config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    ).is_file()
    assert (
        config.targets["codex"].path
        / ".agents"
        / "skills"
        / "ponytail-review"
        / "SKILL.md"
    ).is_file()
    assert not (config.targets["opencode"].path / "skills" / "ponytail").exists()
    for name in PONYTAIL_NAMES:
        assert (config.targets["gptel"].path / f"{name}.md").is_file()

    second = deploy(config)
    assert second
    assert {action.action for action in second} == {"skip"}
    assert {entry.state for entry in get_status(config)} == {"current"}
    assert (
        verify_items(
            config,
            target_ids=list(target_types),
            item_selectors=ALL_SELECTORS,
        )
        == []
    )

    claude_manifest_path = _manifest_path(config, "claude")
    claude_manifest = load_manifest(claude_manifest_path)
    recorded_skill = claude_manifest.items["skills"]["ponytail"]
    assert recorded_skill.source is not None
    recorded_skill.source = replace(recorded_skill.source, version="4.8.5")
    save_manifest(claude_manifest, claude_manifest_path)
    assert any(
        entry.target_id == "claude"
        and entry.item_type == "skill"
        and entry.name == "ponytail"
        and entry.state == "changed"
        for entry in get_status(config, ["claude"])
    )
    repaired = deploy(
        config,
        target_ids=["claude"],
        item_selectors=[("skill", "ponytail")],
    )
    assert any(
        action.item_type == "skill" and action.action == "update" for action in repaired
    )

    deployed_skill = config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    deployed_skill.write_bytes(b"drift")
    failures = verify_items(
        config,
        target_ids=["claude"],
        item_selectors=[("skill", "ponytail")],
    )
    assert any(
        failure.item_type == "skill" and failure.reason == "mismatch"
        for failure in failures
    )


@pytest.mark.parametrize(
    ("target_type", "selector", "expected"),
    [
        (
            "claude",
            ("skill", "ponytail"),
            {("bundle", "ponytail"), ("skill", "ponytail")},
        ),
        (
            "gptel",
            ("prompt", "ponytail"),
            {("bundle", "ponytail"), ("prompt", "ponytail")},
        ),
        ("gptel", ("skill", "ponytail"), set()),
        ("opencode", ("skill", "ponytail"), set()),
    ],
)
def test_exact_selection_closes_dependencies_only_on_applicable_targets(
    target_type: str,
    selector: tuple[str, str],
    expected: set[tuple[str, str]],
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, target_type)

    actions = deploy(config, item_selectors=[selector])

    assert {
        (action.item_type, action.name)
        for action in actions
        if action.action == "create"
    } == expected
    assert _support_path(config, target_type).exists() is bool(expected)


def test_only_type_skills_keeps_the_required_support_bundle(
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude", "gptel")

    actions = deploy(config, item_types=["skills"])

    assert {
        (action.item_type, action.name)
        for action in actions
        if action.target_id == "claude" and action.action == "create"
    } == {
        ("bundle", "ponytail"),
        *(("skill", name) for name in PONYTAIL_NAMES),
    }
    assert not any(action.target_id == "gptel" for action in actions)
    assert not _support_path(config, "gptel").exists()


def test_matching_preexisting_support_is_adopted_without_rewrite(
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    target = ClaudeTarget("claude", config.targets["claude"].path)
    target.deploy_bundle("ponytail", (ponytail_root / "LICENSE").read_bytes())

    actions = deploy(
        config,
        item_selectors=[("bundle", "ponytail")],
    )

    assert [(action.item_type, action.action) for action in actions] == [
        ("bundle", "skip")
    ]
    recorded = load_manifest(_manifest_path(config, "claude"))
    assert recorded.items["bundles"]["ponytail"].source is not None


def test_matching_preexisting_gptel_prompt_records_exact_owned_path(
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, "gptel")
    prompt = next(
        item
        for item in discover_operation_catalog(config)
        if (item.item_type, item.name) == ("prompt", "ponytail")
    )
    target = config.targets["gptel"].path
    target.mkdir(parents=True)
    (target / "ponytail.md").write_bytes(prompt.content)
    sibling = target / "ponytail.org"
    sibling.write_text("unrelated user prompt")

    actions = deploy(config, item_selectors=[("prompt", "ponytail")])

    assert any(
        action.item_type == "prompt"
        and action.name == "ponytail"
        and action.action == "skip"
        for action in actions
    )
    manifest = load_manifest(_manifest_path(config, "gptel"))
    assert manifest.items["prompts"]["ponytail"].target_path == "ponytail.md"

    deploy(replace(config, bundles=()))

    assert not (target / "ponytail.md").exists()
    assert sibling.read_text() == "unrelated user prompt"


@pytest.mark.parametrize("target_type", ["claude", "codex", "droid"])
@pytest.mark.parametrize("mismatching", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_unmanaged_ponytail_skill_blocks_before_mutation_even_with_force(
    target_type: str,
    mismatching: bool,
    force: bool,
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, target_type)
    item = next(
        catalog_item
        for catalog_item in discover_operation_catalog(config)
        if (catalog_item.item_type, catalog_item.name) == ("skill", "ponytail")
    )
    target = create_target(config.targets[target_type])
    target.deploy_skill("ponytail", imported_skill_snapshot(item))
    if target_type == "codex":
        skill_md = (
            config.targets[target_type].path
            / ".agents"
            / "skills"
            / "ponytail"
            / "SKILL.md"
        )
    else:
        skill_md = config.targets[target_type].path / "skills" / "ponytail" / "SKILL.md"
    if mismatching:
        skill_md.write_bytes(b"unmanaged modified skill")
    before = skill_md.read_bytes()

    with pytest.raises(ValueError, match="Cannot deploy unmanaged Ponytail skill"):
        deploy(
            config,
            item_selectors=[("skill", "ponytail")],
            force=force,
        )

    assert skill_md.read_bytes() == before
    assert not _support_path(config, target_type).exists()
    assert not _manifest_path(config, target_type).exists()


def test_mismatching_unmanaged_requirement_blocks_dependent_before_mutation(
    tmp_path: Path,
    ponytail_root: Path,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    target = ClaudeTarget("claude", config.targets["claude"].path)
    target.deploy_bundle("ponytail", b"different license")

    with pytest.raises(ValueError, match="required bundle:ponytail"):
        deploy(
            config,
            item_selectors=[("skill", "ponytail")],
        )

    assert target.bundle_matches("ponytail", b"different license")
    assert not (
        config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    ).exists()
    assert not _manifest_path(config, "claude").exists()

    forced = deploy(
        config,
        item_selectors=[("skill", "ponytail")],
        force=True,
    )
    assert {(action.item_type, action.action) for action in forced} == {
        ("bundle", "create"),
        ("skill", "create"),
    }


def test_requirement_recheck_blocks_dependent_after_preflight_race(
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    target = ClaudeTarget("claude", config.targets["claude"].path)
    target.deploy_bundle("ponytail", (ponytail_root / "LICENSE").read_bytes())
    from promptdeploy import deploy as deploy_module

    outcomes = iter((True, False))
    monkeypatch.setattr(
        deploy_module,
        "_disk_matches_source",
        lambda _target, _item: next(outcomes),
    )

    with pytest.raises(ValueError, match="was not successfully materialized"):
        deploy(
            config,
            item_selectors=[("skill", "ponytail")],
        )

    assert not (
        config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    ).exists()
    assert not _manifest_path(config, "claude").exists()


def test_requirement_recheck_blocks_manifest_after_late_race(
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    target = ClaudeTarget("claude", config.targets["claude"].path)
    target.deploy_bundle("ponytail", (ponytail_root / "LICENSE").read_bytes())
    from promptdeploy import deploy as deploy_module

    outcomes = iter((True, True, True, False))
    monkeypatch.setattr(
        deploy_module,
        "target_item_matches_source",
        lambda _target, _item: next(outcomes),
    )

    with pytest.raises(ValueError, match="changed before manifest commit"):
        deploy(
            config,
            item_selectors=[("skill", "ponytail")],
        )

    assert (config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md").exists()
    assert not _manifest_path(config, "claude").exists()


def test_requirement_recheck_blocks_dependent_after_materialization_race(
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    target = ClaudeTarget("claude", config.targets["claude"].path)
    target.deploy_bundle("ponytail", (ponytail_root / "LICENSE").read_bytes())
    from promptdeploy import deploy as deploy_module

    outcomes = iter((True, True, False))
    monkeypatch.setattr(
        deploy_module,
        "target_item_matches_source",
        lambda _target, _item: next(outcomes),
    )

    with pytest.raises(ValueError, match="changed after materialization"):
        deploy(
            config,
            item_selectors=[("skill", "ponytail")],
        )

    assert not (
        config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    ).exists()
    assert not _manifest_path(config, "claude").exists()


def _selected_source_copy(source: Path, destination: Path) -> Path:
    destination.mkdir()
    shutil.copy2(source / "package.json", destination / "package.json")
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    (destination / "skills").mkdir()
    for name in PONYTAIL_NAMES:
        shutil.copytree(source / "skills" / name, destination / "skills" / name)
    for path in (destination, *destination.rglob("*")):
        mode = path.stat().st_mode
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))
    return destination


def test_deploy_retains_no_live_bundle_authority_after_composition(
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _selected_source_copy(ponytail_root, tmp_path / "mutable-ponytail")
    config = _config(tmp_path, source, "claude")
    from promptdeploy import deploy as deploy_module
    from promptdeploy.catalog import discover_operation_catalog

    discover = discover_operation_catalog

    def capture_then_delete(operation_config: Config):
        items = discover(operation_config)
        shutil.rmtree(source)
        return items

    monkeypatch.setattr(
        deploy_module,
        "discover_operation_catalog",
        capture_then_delete,
    )

    actions = deploy(config, item_selectors=[("skill", "ponytail")])

    assert {(action.item_type, action.name) for action in actions} == {
        ("bundle", "ponytail"),
        ("skill", "ponytail"),
    }
    deployed = config.targets["claude"].path / "skills" / "ponytail" / "SKILL.md"
    assert deployed.is_file()
    manifest_before = _manifest_path(config, "claude").read_bytes()

    with pytest.raises(ValueError):
        deploy(config, item_selectors=[("skill", "ponytail")])
    assert _manifest_path(config, "claude").read_bytes() == manifest_before


def test_cli_target_root_deploy_and_fresh_verify_leave_originals_untouched(
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target_types = ("claude", "codex", "droid", "opencode", "gptel")
    config = _config(tmp_path / "configured", ponytail_root, *target_types)
    original_paths = [target.path for target in config.targets.values()]
    preview_root = tmp_path / "preview"
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)
    selector_args = [f"{item_type}:{name}" for item_type, name in ALL_SELECTORS]

    cli._run_deploy(
        argparse.Namespace(
            verbose=False,
            quiet=True,
            dry_run=False,
            target=None,
            local_only=False,
            only_type=None,
            only_item=selector_args,
            target_root=preview_root,
            force=False,
        )
    )
    cli._run_verify(
        argparse.Namespace(
            target=None,
            local_only=False,
            only_item=selector_args,
            target_root=preview_root,
        )
    )

    assert all(not path.exists() for path in original_paths)
    assert "Verified 13 exact item selector" in capsys.readouterr().out


@pytest.mark.parametrize("operation", ["deploy", "status", "verify"])
def test_catalog_failure_precedes_target_construction(
    operation: str,
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    from promptdeploy import deploy as deploy_module
    from promptdeploy import status as status_module
    from promptdeploy import verify as verify_module

    module = {
        "deploy": deploy_module,
        "status": status_module,
        "verify": verify_module,
    }[operation]
    monkeypatch.setattr(
        module,
        "discover_operation_catalog",
        lambda _config: (_ for _ in ()).throw(ValueError("catalog failed")),
    )
    monkeypatch.setattr(
        module,
        "create_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("target construction must not run")
        ),
    )

    with pytest.raises(ValueError, match="catalog failed"):
        if operation == "deploy":
            deploy(config)
        elif operation == "status":
            get_status(config)
        else:
            verify_items(
                config,
                target_ids=["claude"],
                item_selectors=[("bundle", "ponytail")],
            )
    assert not config.targets["claude"].path.exists()


@pytest.mark.parametrize("operation", ["deploy", "status", "verify"])
def test_operation_captures_composed_catalog_once(
    operation: str,
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    from promptdeploy import deploy as deploy_module
    from promptdeploy import status as status_module
    from promptdeploy import verify as verify_module

    module = {
        "deploy": deploy_module,
        "status": status_module,
        "verify": verify_module,
    }[operation]
    original = discover_operation_catalog
    captures = 0

    def count(operation_config: Config) -> tuple[SourceItem, ...]:
        nonlocal captures
        captures += 1
        return original(operation_config)

    monkeypatch.setattr(module, "discover_operation_catalog", count)
    if operation == "deploy":
        deploy(config, item_selectors=[("skill", "ponytail")])
    elif operation == "status":
        get_status(config)
    else:
        verify_items(
            config,
            target_ids=["claude"],
            item_selectors=[("skill", "ponytail")],
        )

    assert captures == 1


@pytest.mark.parametrize("operation", ["deploy", "status", "verify"])
def test_strict_catalog_errors_hide_bound_source_root(
    operation: str,
    tmp_path: Path,
    ponytail_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, ponytail_root, "claude")
    imported = SourceItem(
        item_type="skill",
        name="unsafe",
        path=ponytail_root / "skills" / "ponytail" / "SKILL.md",
        metadata={"name": "unsafe", "description": "Invalid test item."},
        content=b"invalid",
        provenance=SourceProvenance.imported(
            ManifestSource(
                bundle="ponytail",
                path="skills/unsafe",
                version="4.8.4",
                revision=None,
                nar_hash=None,
                mutable=True,
                transform=None,
                license="MIT",
            )
        ),
        target_types=frozenset({"claude"}),
        requires=(("bundle", "missing"),),
    )
    monkeypatch.setattr(
        "promptdeploy.bundle_catalog.discover_bundle_items",
        lambda _bundle: (imported,),
    )

    with pytest.raises(ValueError) as caught:
        if operation == "deploy":
            deploy(config, item_selectors=[("skill", "unsafe")])
        elif operation == "status":
            get_status(config)
        else:
            verify_items(
                config,
                target_ids=["claude"],
                item_selectors=[("skill", "unsafe")],
            )

    message = str(caught.value)
    assert "ponytail:skills/unsafe" in message
    assert str(ponytail_root) not in message
