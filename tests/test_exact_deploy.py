"""Exact-item deployment regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import deploy, parse_item_selector
from promptdeploy.manifest import (
    MANIFEST_FILENAME,
    ManifestItem,
    load_manifest,
    save_manifest,
)


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "helper.md").write_bytes(b"---\nname: helper\n---\nHelper.\n")
    (root / "mcp").mkdir()
    (root / "mcp" / "file-name-is-not-selector.yaml").write_text(
        "name: alpha\ncommand: alpha-v1\n"
    )
    (root / "mcp" / "beta.yaml").write_text("name: beta\ncommand: beta-command\n")
    (root / "mcp" / "anvil-tools.yaml").write_text(
        "name: anvil-tools\ncommand: obsolete\nenabled: false\n"
    )
    return root


def _config(tmp_path: Path) -> Config:
    source = _source(tmp_path)
    target = TargetConfig(
        id="local",
        type="claude",
        path=tmp_path / "target",
    )
    return Config(source_root=source, targets={target.id: target}, groups={})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mcp:alpha", ("mcp", "alpha")),
        ("mcp:name:with:colons", ("mcp", "name:with:colons")),
        ("skill:anvil", ("skill", "anvil")),
    ],
)
def test_parse_item_selector(raw: str, expected: tuple[str, str]) -> None:
    assert parse_item_selector(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "mcp",
        ":name",
        "mcp:",
        "unknown:name",
        " mcp:name",
        "mcp:name ",
        "agent:../victim",
        "skill:/tmp/victim",
        "prompt:a\\b",
        "command:.",
        "command:..",
        "agent:line\nbreak",
    ],
)
def test_parse_item_selector_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError, match="Invalid item selector"):
        parse_item_selector(raw)


def test_exact_item_preserves_same_category_siblings_and_manifest_metadata(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    deploy(config)
    target_path = config.targets["local"].path
    manifest_path = target_path / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path)
    manifest.items["mcp_servers"]["beta"] = ManifestItem(
        source_hash=manifest.items["mcp_servers"]["beta"].source_hash,
        target_path="kept/path",
        managed_keys=["kept-key"],
    )
    manifest.items["future"] = {
        "opaque": ManifestItem(
            source_hash="sha256:opaque",
            target_path="future/path",
            managed_keys=["future-key"],
        )
    }
    save_manifest(manifest, manifest_path)

    before_config = json.loads((target_path / ".claude.json").read_text())
    before_beta = before_config["mcpServers"]["beta"]
    before_manifest_beta = load_manifest(manifest_path).items["mcp_servers"]["beta"]
    before_future = load_manifest(manifest_path).items["future"]["opaque"]

    (config.source_root / "mcp" / "file-name-is-not-selector.yaml").write_text(
        "name: alpha\ncommand: alpha-v2\n"
    )
    actions = deploy(config, item_selectors=[("mcp", "alpha")])

    assert {(action.item_type, action.name) for action in actions} == {("mcp", "alpha")}
    after_config = json.loads((target_path / ".claude.json").read_text())
    assert after_config["mcpServers"]["alpha"]["command"] == "alpha-v2"
    assert after_config["mcpServers"]["beta"] == before_beta
    after_manifest = load_manifest(manifest_path)
    assert after_manifest.items["mcp_servers"]["beta"] == before_manifest_beta
    assert after_manifest.items["future"]["opaque"] == before_future
    assert "helper" in after_manifest.items["agents"]


def test_exact_item_preserves_unselected_stale_sibling(tmp_path: Path) -> None:
    config = _config(tmp_path)
    deploy(config)
    beta_source = config.source_root / "mcp" / "beta.yaml"
    beta_source.unlink()

    deploy(config, item_selectors=[("mcp", "alpha")])

    target_path = config.targets["local"].path
    data = json.loads((target_path / ".claude.json").read_text())
    assert "beta" in data["mcpServers"]
    manifest = load_manifest(target_path / MANIFEST_FILENAME)
    assert "beta" in manifest.items["mcp_servers"]


def test_exact_item_skips_unselected_missing_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    missing = "PD_TEST_EXACT_UNSELECTED_SECRET"
    monkeypatch.delenv(missing, raising=False)
    (config.source_root / "mcp" / "secret.yaml").write_text(
        'name: secret\nurl: "https://x/mcp?apiKey=${' + missing + '}"\n'
    )

    actions = deploy(config, item_selectors=[("mcp", "alpha")])

    assert {(action.item_type, action.name) for action in actions} == {("mcp", "alpha")}
    data = json.loads((config.targets["local"].path / ".claude.json").read_text())
    assert set(data["mcpServers"]) == {"alpha"}


def test_empty_exact_selection_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    deploy(config)
    target_path = config.targets["local"].path
    config_before = (target_path / ".claude.json").read_bytes()
    manifest_path = target_path / MANIFEST_FILENAME
    manifest_before = manifest_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)

    assert deploy(config, item_selectors=[]) == []

    assert (target_path / ".claude.json").read_bytes() == config_before
    assert manifest_path.read_bytes() == manifest_before


def test_unknown_selector_fails_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)
    with pytest.raises(ValueError, match="Unknown source item selector"):
        deploy(config, item_selectors=[("mcp", "missing")])
    assert not config.targets["local"].path.exists()


def test_duplicate_exact_selector_fails_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.source_root / "mcp" / "duplicate.yaml").write_text(
        "name: alpha\ncommand: duplicate\n"
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)
    with pytest.raises(ValueError, match="Ambiguous source item selector"):
        deploy(config, item_selectors=[("mcp", "alpha")])
    assert not config.targets["local"].path.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform lacks FIFOs")
def test_unselected_unsafe_skill_fails_discovery_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    unsafe = config.source_root / "skills" / "unsafe"
    unsafe.mkdir(parents=True)
    os.mkfifo(unsafe / "SKILL.md")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)
    with pytest.raises(ValueError, match="special filesystem node"):
        deploy(config, item_selectors=[("mcp", "alpha")])
    assert not config.targets["local"].path.exists()


@pytest.mark.parametrize("target_type", ["claude", "codex", "droid", "opencode"])
def test_unsafe_source_name_fails_before_every_target_type(
    target_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    (source / "skills" / "escaped").mkdir(parents=True)
    (source / "skills" / "escaped" / "SKILL.md").write_text(
        "---\nname: ../../victim\ndescription: unsafe\n---\nbody\n"
    )
    target_path = tmp_path / "target"
    sentinel = tmp_path / "victim"
    sentinel.write_bytes(b"preserve")
    config = Config(
        source_root=source,
        targets={
            "local": TargetConfig("local", target_type, target_path),
        },
        groups={},
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)
    with pytest.raises(ValueError, match="Unsafe skill name"):
        deploy(config)
    assert sentinel.read_bytes() == b"preserve"
    assert not target_path.exists()


def test_direct_api_rejects_unsafe_selector_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target construction must not occur")

    monkeypatch.setattr("promptdeploy.deploy.create_target", forbidden)
    with pytest.raises(ValueError, match="Unsafe skill name"):
        deploy(config, item_selectors=[("skill", "../victim")])

    with pytest.raises(ValueError, match="Unknown source item type"):
        deploy(config, item_selectors=[("unknown", "name")])


def test_unsafe_stale_manifest_name_fails_before_any_target_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    target_path = config.targets["local"].path
    target_path.mkdir()
    manifest_path = target_path / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "deployed_at": "2026-07-13T00:00:00+00:00",
                "items": {
                    "skills": {
                        "../../victim": {"source_hash": "sha256:x"},
                    }
                },
            },
            indent=2,
        )
        + "\n"
    )
    before = manifest_path.read_bytes()
    sentinel = tmp_path / "victim"
    sentinel.write_bytes(b"preserve")

    with pytest.raises(ValueError, match="Unsafe skill name"):
        deploy(config)

    assert sentinel.read_bytes() == b"preserve"
    assert manifest_path.read_bytes() == before
    assert not (target_path / "agents" / "helper.md").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": 2},
        {"future_top_level": True},
        {"item_field": True},
        {"source_hash": ["not", "a", "string"]},
        {"target_path": "../victim"},
        {"target_path": ["not", "a", "path"]},
        {"managed_keys": ["valid", 3]},
    ],
)
def test_exact_deploy_rejects_unsafe_or_unknown_manifest_without_rewrite(
    mutation: dict[str, object], tmp_path: Path
) -> None:
    config = _config(tmp_path)
    deploy(config)
    manifest_path = config.targets["local"].path / MANIFEST_FILENAME
    raw = json.loads(manifest_path.read_text())
    if "version" in mutation:
        raw["version"] = mutation["version"]
    elif "future_top_level" in mutation:
        raw["future_top_level"] = True
    else:
        entry = raw["items"]["mcp_servers"]["alpha"]
        if "item_field" in mutation:
            entry["future_item_field"] = True
        else:
            entry.update(mutation)
    manifest_path.write_text(json.dumps(raw, indent=3) + "\n")
    before = manifest_path.read_bytes()

    with pytest.raises(ValueError):
        deploy(config, item_selectors=[("mcp", "alpha")])

    assert manifest_path.read_bytes() == before


def test_direct_api_rejects_type_and_exact_filters(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        deploy(
            config,
            item_types=["mcp"],
            item_selectors=[("mcp", "alpha")],
        )


def test_forced_disabled_tombstone_removes_only_named_entry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target_path = config.targets["local"].path
    target_path.mkdir()
    config_path = target_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "anvil-tools": {"command": "resurrected"},
                    "unrelated": {"command": "keep", "env": {"TOKEN": "sentinel"}},
                },
                "other": {"keep": True},
            }
        )
    )

    actions = deploy(
        config,
        item_selectors=[("mcp", "anvil-tools")],
        force=True,
    )

    assert [(action.action, action.name) for action in actions] == [
        ("create", "anvil-tools")
    ]
    data = json.loads(config_path.read_text())
    assert "anvil-tools" not in data["mcpServers"]
    assert data["mcpServers"]["unrelated"]["env"]["TOKEN"] == "sentinel"
    assert data["other"] == {"keep": True}
    manifest = load_manifest(target_path / MANIFEST_FILENAME)
    assert "anvil-tools" in manifest.items["mcp_servers"]
