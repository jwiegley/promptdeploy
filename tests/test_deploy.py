"""Integration tests for the deploy orchestration."""

import json
import tomllib
from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import (
    _CLI_TYPE_TO_ITEM_TYPE,
    _TYPE_TO_CATEGORY,
    _deploy_item,
    compute_item_hash,
    deploy,
)
from promptdeploy.manifest import (
    MANIFEST_FILENAME,
    BundleManifestReceipt,
    Manifest,
    ManifestItem,
    compute_file_hash,
    load_manifest,
    save_manifest,
)
from promptdeploy.source import SourceDiscovery
from promptdeploy.targets import create_target


def _make_source(tmp_path: Path) -> Path:
    """Create a minimal source tree with one agent, one command, one skill."""
    src = tmp_path / "source"
    src.mkdir()

    agents = src / "agents"
    agents.mkdir()
    (agents / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent body.\n")

    commands = src / "commands"
    commands.mkdir()
    (commands / "fix.md").write_bytes(b"---\nname: fix\n---\nFix things.\n")

    skills = src / "skills"
    skills.mkdir()
    skill_dir = skills / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(
        b"---\nname: my-skill\ndescription: A skill\n---\nSkill body.\n"
    )
    (skill_dir / "helper.py").write_text("print('hi')")

    return src


def _make_config(source_root: Path, targets: dict[str, TargetConfig]) -> Config:
    return Config(source_root=source_root, targets=targets, groups={})


def _make_claude_target(tmp_path: Path, target_id: str = "test-claude") -> TargetConfig:
    target_dir = tmp_path / target_id
    target_dir.mkdir()
    return TargetConfig(id=target_id, type="claude", path=target_dir)


class TestFullDeploy:
    """Deploy all items to a fresh target."""

    def test_creates_all_items(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config)

        creates = [a for a in actions if a.action == "create"]
        assert len(creates) == 3
        names = {a.name for a in creates}
        assert names == {"helper", "fix", "my-skill"}

        # Verify files exist on disk
        assert (tc.path / "agents" / "helper.md").exists()
        assert (tc.path / "commands" / "fix.md").exists()
        assert (tc.path / "skills" / "my-skill" / "SKILL.md").exists()
        assert (tc.path / "skills" / "my-skill" / "helper.py").exists()

    def test_manifest_created(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" in manifest.items["agents"]
        assert "fix" in manifest.items["commands"]
        assert "my-skill" in manifest.items["skills"]


class TestDeployModelInjection:
    def test_agent_deployed_with_injected_model_from_models_yaml(
        self, tmp_path: Path
    ) -> None:
        # Full integration: deploy() reads models.yaml, threads the default
        # through create_target, which threads it into ClaudeTarget.
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        (source_root / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(id="c", type="claude", path=target_dir),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert meta is not None
        assert meta["model"] == "claude-opus-4-7"

    def test_per_target_model_overrides_global(self, tmp_path: Path) -> None:
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        (source_root / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=target_dir,
                    model="claude-sonnet-4-6",
                ),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert meta is not None
        assert meta["model"] == "claude-sonnet-4-6"

    def test_no_models_yaml_means_no_injection(self, tmp_path: Path) -> None:
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        # No models.yaml at all.

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(id="c", type="claude", path=target_dir),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert meta is not None
        assert "model" not in meta


class TestIdempotency:
    """Second deploy with no changes should produce all skips."""

    def test_second_deploy_all_skips(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        actions = deploy(config)

        skips = [a for a in actions if a.action == "skip"]
        non_skips = [a for a in actions if a.action != "skip"]
        assert len(skips) == 3
        assert len(non_skips) == 0


class TestUpdate:
    """Modifying source content triggers update, not create."""

    def test_changed_content_produces_update(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)

        # Modify the agent content
        (src / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nUpdated body.\n"
        )

        actions = deploy(config)
        updates = [a for a in actions if a.action == "update"]
        assert len(updates) == 1
        assert updates[0].name == "helper"
        assert updates[0].item_type == "agent"

        # Verify content on disk is updated
        deployed = (tc.path / "agents" / "helper.md").read_bytes()
        assert b"Updated body." in deployed


class TestRemoval:
    """Items removed from source get removed from target."""

    def test_removes_deleted_source(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        assert (tc.path / "agents" / "helper.md").exists()

        # Remove the agent from source
        (src / "agents" / "helper.md").unlink()

        actions = deploy(config)
        removes = [a for a in actions if a.action == "remove"]
        assert len(removes) == 1
        assert removes[0].name == "helper"
        assert removes[0].item_type == "agent"

        # Verify removed from disk
        assert not (tc.path / "agents" / "helper.md").exists()

    def test_removal_updates_manifest(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        (src / "agents" / "helper.md").unlink()
        deploy(config)

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" not in manifest.items.get("agents", {})

    def test_unknown_manifest_category_dropped_gracefully(self, tmp_path: Path):
        """A manifest from a newer promptdeploy may hold unknown categories.

        The stale-removal pass must not crash on one: there is nothing to
        unlink on the target, so it reports a remove and drops the entry
        from the manifest.
        """
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        manifest_path = tc.path / MANIFEST_FILENAME
        manifest = load_manifest(manifest_path)
        manifest.items["widgets"] = {"gizmo": ManifestItem(source_hash="sha256:0")}
        save_manifest(manifest, manifest_path)

        actions = deploy(config)
        removes = [a for a in actions if a.action == "remove"]
        assert [(a.item_type, a.name) for a in removes] == [("widgets", "gizmo")]

        manifest = load_manifest(manifest_path)
        assert "widgets" not in manifest.items


class TestDryRun:
    """Dry run computes actions without writing."""

    def test_dry_run_makes_no_changes(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, dry_run=True)

        creates = [a for a in actions if a.action == "create"]
        assert len(creates) == 3

        # Nothing written to disk
        assert not (tc.path / "agents" / "helper.md").exists()
        assert not (tc.path / MANIFEST_FILENAME).exists()

    def test_dry_run_removal_no_delete(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        (src / "agents" / "helper.md").unlink()

        actions = deploy(config, dry_run=True)
        removes = [a for a in actions if a.action == "remove"]
        assert len(removes) == 1

        # File still on disk because dry run
        assert (tc.path / "agents" / "helper.md").exists()


class TestTargetFilter:
    """--target limits deployment to specific targets."""

    def test_deploy_to_single_target(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc1 = _make_claude_target(tmp_path, "target-a")
        tc2 = _make_claude_target(tmp_path, "target-b")
        config = _make_config(src, {tc1.id: tc1, tc2.id: tc2})

        actions = deploy(config, target_ids=["target-a"])

        target_ids = {a.target_id for a in actions}
        assert target_ids == {"target-a"}

        assert (tc1.path / "agents" / "helper.md").exists()
        assert not (tc2.path / "agents").exists()


class TestOnlyType:
    """--only-type limits which item types are deployed."""

    def test_only_agents(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, item_types=["agents"])

        types = {a.item_type for a in actions}
        assert types == {"agent"}
        assert (tc.path / "agents" / "helper.md").exists()
        assert not (tc.path / "commands").exists()
        assert not (tc.path / "skills").exists()

    def test_only_type_preserves_other_manifest_items(self, tmp_path: Path):
        """When using --only-type, items of other types stay in manifest."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Deploy everything first
        deploy(config)
        manifest_path = tc.path / MANIFEST_FILENAME
        manifest = load_manifest(manifest_path)
        runtime_path = ".promptdeploy/bundles/ponytail/runtimes/" + "a" * 64
        receipt = BundleManifestReceipt(
            payload_name="claude-codex-runtime-v1",
            target_type="claude",
            logical_root="runtime/claude-codex",
            payload_tree_sha256="sha256:" + "c" * 64,
            rendered_tree_sha256="sha256:" + "a" * 64,
            adapter_abi="ponytail-claude-runtime-v1",
            runtime_path=runtime_path,
            registration_kind="claude-hooks",
            registration_owner="bundle:ponytail",
            registration_abi="claude-settings-hooks-v1",
            registration_sha256="sha256:" + "d" * 64,
        )
        manifest.items["bundles"] = {
            "ponytail": ManifestItem(
                "sha256:" + "b" * 64,
                target_path=runtime_path,
                bundle_receipt=receipt,
            )
        }
        save_manifest(manifest, manifest_path)
        # Now deploy only agents
        deploy(config, item_types=["agents"])

        manifest = load_manifest(manifest_path)
        # Agent should still be in manifest
        assert "helper" in manifest.items["agents"]
        # Other types should be preserved (not removed)
        assert "fix" in manifest.items["commands"]
        assert "my-skill" in manifest.items["skills"]
        assert manifest.items["bundles"]["ponytail"].bundle_receipt == receipt

    def test_only_type_removes_stale_items_of_matching_type(self, tmp_path: Path):
        """--only-type still removes stale items of the selected type."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        (src / "agents" / "helper.md").unlink()

        actions = deploy(config, item_types=["agents"])
        removes = [a for a in actions if a.action == "remove"]
        assert [(a.item_type, a.name) for a in removes] == [("agent", "helper")]
        assert not (tc.path / "agents" / "helper.md").exists()

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" not in manifest.items.get("agents", {})
        # Items of unselected types stay deployed and tracked.
        assert "fix" in manifest.items["commands"]
        assert (tc.path / "commands" / "fix.md").exists()


class TestEnvironmentFilters:
    """Frontmatter only/except filters control target selection."""

    def test_only_filter_excludes_target(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        agents = src / "agents"
        agents.mkdir()
        (agents / "limited.md").write_bytes(
            b"---\nname: limited\nonly:\n  - target-a\n---\nLimited agent.\n"
        )

        tc_a = _make_claude_target(tmp_path, "target-a")
        tc_b = _make_claude_target(tmp_path, "target-b")
        config = _make_config(src, {tc_a.id: tc_a, tc_b.id: tc_b})

        actions = deploy(config)

        a_actions = [a for a in actions if a.target_id == "target-a"]
        b_actions = [a for a in actions if a.target_id == "target-b"]
        assert len(a_actions) == 1
        assert a_actions[0].action == "create"
        assert len(b_actions) == 0

    def test_except_filter_excludes_target(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        agents = src / "agents"
        agents.mkdir()
        (agents / "wide.md").write_bytes(
            b"---\nname: wide\nexcept:\n  - target-b\n---\nWide agent.\n"
        )

        tc_a = _make_claude_target(tmp_path, "target-a")
        tc_b = _make_claude_target(tmp_path, "target-b")
        config = _make_config(src, {tc_a.id: tc_a, tc_b.id: tc_b})

        actions = deploy(config)

        a_actions = [a for a in actions if a.target_id == "target-a"]
        b_actions = [a for a in actions if a.target_id == "target-b"]
        assert len(a_actions) == 1
        assert len(b_actions) == 0


class TestMultipleTargets:
    """Deploy to multiple targets simultaneously."""

    def test_deploys_to_all_targets(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc1 = _make_claude_target(tmp_path, "t1")
        tc2 = _make_claude_target(tmp_path, "t2")
        config = _make_config(src, {tc1.id: tc1, tc2.id: tc2})

        actions = deploy(config)

        creates = [a for a in actions if a.action == "create"]
        # 3 items * 2 targets = 6
        assert len(creates) == 6

        for tc in [tc1, tc2]:
            assert (tc.path / "agents" / "helper.md").exists()
            assert (tc.path / "commands" / "fix.md").exists()


class TestMcpDeploy:
    """Deploy MCP server items through the deploy orchestration."""

    def test_deploys_mcp_server(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "my-server.yaml").write_bytes(
            b"name: my-server\ncommand: npx\nargs:\n  - my-server\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)

        creates = [a for a in actions if a.action == "create"]
        assert any(a.name == "my-server" and a.item_type == "mcp" for a in creates)

        # Verify MCP server was written to .claude.json (the surface Claude
        # Code reads), not settings.json.
        claude_json = json.loads((tc.path / ".claude.json").read_text())
        assert "my-server" in claude_json["mcpServers"]
        assert not (tc.path / "settings.json").exists()

    def test_target_root_preview_writes_secrets_verbatim(
        self, tmp_path: Path, monkeypatch
    ):
        # End to end: a --target-root preview (remap_targets_to_root marks
        # targets preview=True -> create_target passes expand_secrets=False)
        # writes ${VAR} verbatim into the preview .claude.json even when the
        # variables are set -- secrets never land in the preview directory.
        from promptdeploy.config import remap_targets_to_root

        monkeypatch.setenv("PREVIEW_SECRET", "super-secret")
        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "srv.yaml").write_bytes(
            b"name: srv\n"
            b"url: https://x/mcp?apiKey=${PREVIEW_SECRET}\n"
            b"headers:\n"
            b"  Authorization: Bearer ${PREVIEW_SECRET}\n"
        )

        tc = _make_claude_target(tmp_path)
        config = remap_targets_to_root(
            _make_config(src, {tc.id: tc}), tmp_path / "preview"
        )
        preview_dir = config.targets[tc.id].path
        preview_dir.mkdir(parents=True)
        deploy(config)

        text = (preview_dir / ".claude.json").read_text()
        srv = json.loads(text)["mcpServers"]["srv"]
        assert srv["url"] == "https://x/mcp?apiKey=${PREVIEW_SECRET}"
        assert srv["headers"] == {"Authorization": "Bearer ${PREVIEW_SECRET}"}
        assert "super-secret" not in text
        # The real target directory is untouched.
        assert not (tc.path / ".claude.json").exists()

    def test_removes_stale_mcp_server(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "server.yaml").write_bytes(b"name: server\ncommand: echo\n")

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        # Remove MCP source
        (mcp_dir / "server.yaml").unlink()
        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "server" and a.item_type == "mcp" for a in removes)


class TestRemoveCommands:
    """Removal of commands from target."""

    def test_removes_stale_command(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        (src / "commands" / "fix.md").unlink()
        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "fix" and a.item_type == "command" for a in removes)


class TestPromptDeploy:
    """End-to-end deploy of a .poet prompt to claude/droid/gptel/opencode."""

    def _build_source(self, tmp_path: Path) -> Path:
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "spanish.poet").write_bytes(
            b"- role: system\n  content: Be helpful.\n"
            b"- role: user\n  content: Translate this.\n"
        )
        return src

    def test_claude_target_renders_command_md(self, tmp_path: Path):
        src = self._build_source(tmp_path)
        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="c", type="claude", path=target_dir)
        config = _make_config(src, {"c": tc})
        deploy(config)

        cmd = target_dir / "commands" / "spanish.md"
        assert cmd.exists()
        text = cmd.read_text()
        assert "<instructions>\nBe helpful.\n</instructions>" in text
        assert "<task>" in text

    def test_droid_target_renders_skill(self, tmp_path: Path):
        src = self._build_source(tmp_path)
        target_dir = tmp_path / "droid"
        target_dir.mkdir()
        tc = TargetConfig(id="d", type="droid", path=target_dir)
        config = _make_config(src, {"d": tc})
        deploy(config)

        skill = target_dir / "skills" / "spanish" / "SKILL.md"
        assert skill.exists()
        assert "<instructions>" in skill.read_text()

    def test_gptel_target_copies_poet(self, tmp_path: Path):
        src = self._build_source(tmp_path)
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})
        deploy(config)

        out = target_dir / "spanish.poet"
        assert out.exists()
        assert out.read_bytes() == (src / "prompts" / "spanish.poet").read_bytes()

    def test_prompt_removed_when_source_deleted(self, tmp_path: Path):
        src = self._build_source(tmp_path)
        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="c", type="claude", path=target_dir)
        config = _make_config(src, {"c": tc})
        deploy(config)
        assert (target_dir / "commands" / "spanish.md").exists()

        (src / "prompts" / "spanish.poet").unlink()
        actions = deploy(config)
        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "spanish" for a in removes)
        assert not (target_dir / "commands" / "spanish.md").exists()

    def test_gptel_records_target_path_in_manifest(self, tmp_path: Path):
        # Deploy a .md prompt to gptel; the manifest must remember that
        # the deployed file is foo.md (not e.g. foo.json) so a later
        # removal targets only that file.
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.md").write_bytes(b"# heading\n")
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})
        deploy(config)

        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        item = manifest.items["prompts"]["foo"]
        assert item.target_path == "foo.md"

    def test_gptel_safe_removal_via_target_path(self, tmp_path: Path):
        # Deploy a foo.md prompt; the user later authors an unrelated
        # foo.txt at the same dir. Removing foo (after its source is
        # deleted) must touch ONLY foo.md, not the user's foo.txt.
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.md").write_bytes(b"# h\n")
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})
        deploy(config)
        assert (target_dir / "foo.md").exists()

        # User authors an unrelated file with the same stem.
        unrelated = target_dir / "foo.txt"
        unrelated.write_text("user authored")

        # Source removed -> stale removal kicks in.
        (prompts / "foo.md").unlink()
        deploy(config)

        assert not (target_dir / "foo.md").exists()
        # The user's unrelated file MUST NOT have been deleted.
        assert unrelated.exists()
        assert unrelated.read_text() == "user authored"

    def test_warnings_attached_to_deploy_action(self, tmp_path: Path):
        # A .poet with an undefined Jinja variable should produce a
        # DeployAction with non-empty warnings.
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "warny.poet").write_bytes(
            b"- role: system\n  content: 'hi {{ missing }}'\n"
        )
        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="c", type="claude", path=target_dir)
        config = _make_config(src, {"c": tc})
        actions = deploy(config)

        warny_actions = [a for a in actions if a.name == "warny"]
        assert warny_actions
        act = warny_actions[0]
        assert any("missing" in w for w in act.warnings)

    def test_target_path_preserved_on_skip(self, tmp_path: Path):
        # On the second deploy when nothing changed, the manifest must
        # still preserve the previously-recorded target_path even
        # though the target's deployed_artifact_path is None for skips.
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.md").write_bytes(b"hello\n")
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})

        deploy(config)
        # Second deploy: skip path.
        deploy(config)

        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert manifest.items["prompts"]["foo"].target_path == "foo.md"

    def test_gptel_extension_change_removes_old_artifact(self, tmp_path: Path):
        """Changing a prompt's source extension moves the deployed artifact;
        the previous artifact (recorded in the manifest) must be unlinked
        instead of lingering as an orphan (B28)."""
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.poet").write_bytes(b"- role: system\n  content: hi\n")
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})
        deploy(config)
        assert (target_dir / "foo.poet").exists()

        # A user-authored stem-sibling must survive both transitions.
        unrelated = target_dir / "foo.org"
        unrelated.write_text("user authored")

        # foo.poet -> foo.md: foo.md is written, the old foo.poet removed.
        (prompts / "foo.poet").unlink()
        (prompts / "foo.md").write_bytes(b"# heading\n")
        deploy(config)
        assert (target_dir / "foo.md").exists()
        assert not (target_dir / "foo.poet").exists()
        assert unrelated.read_text() == "user authored"
        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert manifest.items["prompts"]["foo"].target_path == "foo.md"

        # And the reverse: foo.md -> foo.poet removes the old foo.md.
        (prompts / "foo.md").unlink()
        (prompts / "foo.poet").write_bytes(b"- role: system\n  content: hi\n")
        deploy(config)
        assert (target_dir / "foo.poet").exists()
        assert not (target_dir / "foo.md").exists()
        assert unrelated.read_text() == "user authored"
        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert manifest.items["prompts"]["foo"].target_path == "foo.poet"

    def test_gptel_legacy_poet_json_migrates_to_poet(self, tmp_path: Path):
        """A gptel prompt that was previously rendered to .json must move to
        .poet even when the source hash itself has not changed."""
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        body = b"- role: system\n  content: hi\n"
        (prompts / "foo.poet").write_bytes(body)
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})

        target = create_target(tc)
        item = next(SourceDiscovery(src).discover_prompts())
        manifest = Manifest()
        manifest.items["prompts"] = {
            "foo": ManifestItem(
                source_hash=compute_item_hash(item, target, config),
                target_path="foo.json",
            )
        }
        save_manifest(manifest, target_dir / MANIFEST_FILENAME)
        (target_dir / "foo.json").write_text("[]")

        actions = deploy(config)

        assert any(a.name == "foo" and a.action == "update" for a in actions)
        assert (target_dir / "foo.poet").read_bytes() == body
        assert not (target_dir / "foo.json").exists()
        new_manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert new_manifest.items["prompts"]["foo"].target_path == "foo.poet"

    def test_gptel_stem_sibling_does_not_cause_churn(self, tmp_path: Path):
        """A user-authored stem-sibling must not shadow the deployed
        artifact in drift detection (B29): the follow-up deploy skips
        instead of reporting a phantom update forever."""
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.md").write_bytes(b"hello\n")
        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="g", type="gptel", path=target_dir)
        config = _make_config(src, {"g": tc})
        deploy(config)

        # User authors an unrelated file earlier in the probe order.
        sibling = target_dir / "foo.json"
        sibling.write_text("user data")

        actions = deploy(config)
        prompt_actions = [a for a in actions if a.name == "foo"]
        assert [a.action for a in prompt_actions] == ["skip"]
        assert sibling.read_text() == "user data"

    def test_target_path_none_when_target_does_not_track(self, tmp_path: Path) -> None:
        # claude target does not implement deployed_artifact_path, so
        # prompts deployed there have no recorded target_path. The
        # manifest entry just has source_hash.
        src = tmp_path / "source"
        src.mkdir()
        prompts = src / "prompts"
        prompts.mkdir()
        (prompts / "foo.poet").write_bytes(b"- role: system\n  content: x\n")
        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="c", type="claude", path=target_dir)
        config = _make_config(src, {"c": tc})
        deploy(config)

        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert manifest.items["prompts"]["foo"].target_path is None


class TestRemoveSkills:
    """Removal of skills from target."""

    def test_removes_stale_skill(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        import shutil

        shutil.rmtree(src / "skills" / "my-skill")
        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "my-skill" and a.item_type == "skill" for a in removes)


class TestUnmanagedFiles:
    """Pre-existing files not in manifest are left alone."""

    def test_unmanaged_files_preserved(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Create an unmanaged file in the target
        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        unmanaged = agents_dir / "custom.md"
        unmanaged.write_text("my custom agent")

        deploy(config)

        # Unmanaged file should still be there
        assert unmanaged.exists()
        assert unmanaged.read_text() == "my custom agent"


class TestPreExisting:
    """Pre-existing items at the target are not overwritten."""

    def test_pre_existing_agent_skipped(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Place a pre-existing agent file before first deploy
        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_text("I was here first")

        actions = deploy(config)

        pre = [a for a in actions if a.action == "pre-existing"]
        assert len(pre) == 1
        assert pre[0].name == "helper"
        assert pre[0].item_type == "agent"

        # Pre-existing file should not be overwritten
        assert (agents_dir / "helper.md").read_text() == "I was here first"

    def test_pre_existing_skill_symlink_skipped(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Place a pre-existing symlink for the skill
        skills_dir = tc.path / "skills"
        skills_dir.mkdir(parents=True)
        real_dir = tmp_path / "real-skill"
        real_dir.mkdir()
        (skills_dir / "my-skill").symlink_to(real_dir)

        actions = deploy(config)

        pre = [a for a in actions if a.action == "pre-existing"]
        assert any(a.name == "my-skill" for a in pre)

        # Symlink should be left alone
        assert (skills_dir / "my-skill").is_symlink()

    def test_pre_existing_not_in_manifest(self, tmp_path: Path):
        """Pre-existing items are not recorded in the manifest."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_text("pre-existing")

        deploy(config)

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        # helper was pre-existing, should not be in manifest
        assert "helper" not in manifest.items.get("agents", {})

    def test_matching_disk_content_is_silently_adopted(self, tmp_path: Path):
        """A pre-existing file with content identical to deploy output is
        adopted into the manifest instead of being flagged on every run."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Pre-populate the target with the exact bytes deploy would write
        # for the ``helper`` agent.
        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent body.\n")

        actions = deploy(config)

        # No pre-existing action for helper; it was silently adopted.
        assert not any(
            a.action == "pre-existing" and a.name == "helper" for a in actions
        )
        # Adoption is reported as a skip (no write needed).
        adopted = [a for a in actions if a.action == "skip" and a.name == "helper"]
        assert len(adopted) == 1

        # The manifest now tracks helper, so subsequent deploys treat it
        # as managed and skip it without warning.
        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" in manifest.items.get("agents", {})

        # Second deploy: still no pre-existing, content untouched.
        actions2 = deploy(config)
        assert not any(a.action == "pre-existing" for a in actions2)
        assert (agents_dir / "helper.md").read_bytes() == (
            b"---\nname: helper\n---\nAgent body.\n"
        )

    def test_differing_disk_content_remains_pre_existing(self, tmp_path: Path):
        """A pre-existing file with different content stays pre-existing
        (and is not overwritten without ``--force``)."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        # Different bytes from what the source would deploy.
        (agents_dir / "helper.md").write_bytes(b"old content; do not touch")

        actions = deploy(config)

        pre = [a for a in actions if a.action == "pre-existing" and a.name == "helper"]
        assert len(pre) == 1
        # Original on-disk content is preserved.
        assert (agents_dir / "helper.md").read_bytes() == (b"old content; do not touch")
        # Helper is *not* recorded -- pre-existing items stay protected.
        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" not in manifest.items.get("agents", {})


class TestDiskMatchesSource:
    """Unit-level tests for the _disk_matches_source helper."""

    def test_disk_missing_returns_false(self, tmp_path: Path):
        """If item_exists says yes but read_deployed_bytes can't recover
        the file (race / inconsistency), the helper must return False so
        the caller falls back to the protective pre-existing path."""
        from unittest.mock import MagicMock

        from promptdeploy.deploy import _disk_matches_source
        from promptdeploy.source import SourceItem

        target = MagicMock()
        target.would_deploy_bytes.return_value = b"hello"
        target.read_deployed_bytes.return_value = None

        item = SourceItem(
            item_type="agent",
            name="x",
            path=tmp_path / "x.md",
            metadata=None,
            content=b"hello",
        )
        assert _disk_matches_source(target, item) is False


class TestDriftDetection:
    """When the deployed artifact no longer matches what we would write,
    redeploy even if the source hash still matches the manifest."""

    def test_externally_edited_file_is_redeployed(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # First deploy creates the agent and records it in the manifest.
        deploy(config)

        # Hand-edit the deployed file so it no longer matches source.
        agent = tc.path / "agents" / "helper.md"
        agent.write_bytes(b"manually edited content")

        actions = deploy(config)

        updates = [a for a in actions if a.action == "update"]
        assert any(a.name == "helper" for a in updates)
        # The deployed file is restored to the source bytes.
        assert agent.read_bytes() == b"---\nname: helper\n---\nAgent body.\n"

    def test_transform_change_redeploys_without_source_change(
        self, tmp_path: Path
    ) -> None:
        """If the target's transformation pipeline changes between deploys,
        the on-disk artifact drifts from what we would now write.  Simulate
        this by stubbing would_deploy_bytes to return different bytes than
        what was originally written, and verify that deploy redeploys."""
        from unittest.mock import patch

        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        agent = tc.path / "agents" / "helper.md"
        original = agent.read_bytes()

        # Stub the target so would_deploy_bytes diverges from on-disk.  The
        # source hash is unchanged, so manifest-based change detection alone
        # would miss this drift -- drift detection catches it.
        new_bytes = original + b"# extra transform output\n"
        with patch.object(
            type(_make_target_from_config(tc)),
            "would_deploy_bytes",
            autospec=True,
            return_value=new_bytes,
        ):
            actions = deploy(config)

        updates = [a for a in actions if a.action == "update"]
        assert any(a.name == "helper" for a in updates)


def _make_target_from_config(tc: TargetConfig):
    from promptdeploy.targets import create_target

    return create_target(tc)


class TestHookDeploy:
    """Deploy hook items through the deploy orchestration."""

    def test_deploys_hook(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        hooks_dir = src / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  PostToolUse:\n    - matcher: 'Write'\n"
            b"      hooks:\n        - command: 'echo hi'\n          type: command\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)

        creates = [a for a in actions if a.action == "create"]
        assert any(a.name == "my-hook" and a.item_type == "hook" for a in creates)

        # Verify hook was written to settings.json
        settings = json.loads((tc.path / "settings.json").read_text())
        assert "hooks" in settings
        assert "PostToolUse" in settings["hooks"]

    def test_removes_stale_hook(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        hooks_dir = src / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hook.yaml").write_bytes(
            b"name: hook\nhooks:\n  Stop:\n    - matcher: ''\n"
            b"      hooks:\n        - command: 'echo'\n          type: command\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        # Remove hook source
        (hooks_dir / "hook.yaml").unlink()
        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "hook" and a.item_type == "hook" for a in removes)


class TestShouldSkipIntegration:
    """Items that a target would no-op are excluded from deploy and manifest."""

    def test_droid_skips_plain_commands_and_hooks(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()

        # Create an agent (should deploy), a plain command (should skip),
        # and a hook (should skip).
        agents = src / "agents"
        agents.mkdir()
        (agents / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")

        commands = src / "commands"
        commands.mkdir()
        (commands / "fix.md").write_bytes(b"---\nname: fix\n---\nFix things.\n")

        hooks = src / "hooks"
        hooks.mkdir()
        (hooks / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  Stop:\n    - matcher: ''\n"
            b"      hooks:\n        - command: 'echo'\n          type: command\n"
        )

        target_dir = tmp_path / "droid-target"
        target_dir.mkdir()
        tc = TargetConfig(id="droid-t", type="droid", path=target_dir)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config)
        creates = [a for a in actions if a.action == "create"]
        # Only the agent should be created
        assert len(creates) == 1
        assert creates[0].name == "helper"

        # Second deploy should be fully idempotent
        actions2 = deploy(config)
        non_skips = [a for a in actions2 if a.action != "skip"]
        assert len(non_skips) == 0

        # Skipped items should not be in manifest
        manifest = load_manifest(target_dir / MANIFEST_FILENAME)
        assert "fix" not in manifest.items.get("commands", {})
        assert "my-hook" not in manifest.items.get("hooks", {})

    def test_claude_skips_models(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()

        agents = src / "agents"
        agents.mkdir()
        (agents / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")

        # Create a models.yaml
        (src / "models.yaml").write_bytes(
            b"providers:\n  acme:\n    display_name: Acme\n"
            b"    base_url: https://acme.com\n    api_key: key\n"
            b"    models:\n      m1:\n        display_name: Model 1\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config)
        creates = [a for a in actions if a.action == "create"]
        # Only agent, not models
        assert len(creates) == 1
        assert creates[0].name == "helper"

        # Second deploy should be fully idempotent
        actions2 = deploy(config)
        non_skips = [a for a in actions2 if a.action != "skip"]
        assert len(non_skips) == 0


class TestFiletagDeploy:
    """Filetags in filenames control which targets receive items."""

    def test_filetag_restricts_deployment(self, tmp_path: Path):
        """A tagged file only deploys to targets with matching labels."""
        src = tmp_path / "source"
        src.mkdir()
        commands = src / "commands"
        commands.mkdir()
        # Create a command tagged for positron only
        (commands / "heavy -- positron.md").write_bytes(b"Heavy command body.\n")

        tc_personal = _make_claude_target(tmp_path, "claude-personal")
        tc_positron = _make_claude_target(tmp_path, "claude-positron")

        targets = {
            tc_personal.id: TargetConfig(
                id=tc_personal.id,
                type="claude",
                path=tc_personal.path,
                labels=["claude", "personal", "local"],
            ),
            tc_positron.id: TargetConfig(
                id=tc_positron.id,
                type="claude",
                path=tc_positron.path,
                labels=["claude", "positron", "local"],
            ),
        }
        groups: dict[str, list[str]] = {}
        for tid, tc in targets.items():
            for label in tc.labels:
                groups.setdefault(label, [])
                if tid not in groups[label]:
                    groups[label].append(tid)
        config = Config(source_root=src, targets=targets, groups=groups)

        actions = deploy(config)

        # Should only deploy to positron
        positron_actions = [a for a in actions if a.target_id == "claude-positron"]
        personal_actions = [a for a in actions if a.target_id == "claude-personal"]
        assert len(positron_actions) == 1
        assert positron_actions[0].action == "create"
        assert positron_actions[0].name == "heavy"
        assert len(personal_actions) == 0

        # File should exist on positron as heavy.md (not heavy -- positron.md)
        assert (tc_positron.path / "commands" / "heavy.md").exists()
        assert not (tc_personal.path / "commands").exists()

    def test_positron_filetag_restricts_codex_commands(self, tmp_path: Path):
        """A Positron-tagged command deploys only to Positron Codex targets."""
        src = tmp_path / "source"
        src.mkdir()
        commands = src / "commands"
        commands.mkdir()
        (commands / "retest -- positron.md").write_bytes(
            b"---\ndescription: Retest.\n---\nRetest $ARGUMENTS.\n"
        )

        personal_path = tmp_path / "codex-personal"
        positron_path = tmp_path / "codex-positron"
        personal_path.mkdir()
        positron_path.mkdir()
        targets = {
            "codex-personal": TargetConfig(
                id="codex-personal",
                type="codex",
                path=personal_path,
                labels=["codex", "personal"],
            ),
            "codex-positron": TargetConfig(
                id="codex-positron",
                type="codex",
                path=positron_path,
                labels=["codex", "personal", "positron"],
            ),
        }
        config = Config(
            source_root=src,
            targets=targets,
            groups={
                "codex": ["codex-personal", "codex-positron"],
                "personal": ["codex-personal", "codex-positron"],
                "positron": ["codex-positron"],
            },
        )

        actions = deploy(config)

        assert [
            (a.action, a.name, a.target_id) for a in actions if a.item_type == "command"
        ] == [("create", "retest", "codex-positron")]
        assert not (personal_path / ".codex" / "prompts" / "retest.md").exists()
        assert not (positron_path / ".codex" / "prompts" / "retest.md").exists()
        assert (
            positron_path / ".agents" / "skills" / "command-retest" / "SKILL.md"
        ).exists()

    def test_filetag_removal_on_tag_change(self, tmp_path: Path):
        """When tags change, item is removed from previously matching targets."""
        src = tmp_path / "source"
        src.mkdir()
        commands = src / "commands"
        commands.mkdir()
        # Initially untagged — deploys everywhere
        (commands / "heavy.md").write_bytes(b"Heavy body.\n")

        tc_personal = _make_claude_target(tmp_path, "claude-personal")
        tc_positron = _make_claude_target(tmp_path, "claude-positron")

        targets = {
            tc_personal.id: TargetConfig(
                id=tc_personal.id,
                type="claude",
                path=tc_personal.path,
                labels=["claude", "personal"],
            ),
            tc_positron.id: TargetConfig(
                id=tc_positron.id,
                type="claude",
                path=tc_positron.path,
                labels=["claude", "positron"],
            ),
        }
        groups: dict[str, list[str]] = {}
        for tid, tc in targets.items():
            for label in tc.labels:
                groups.setdefault(label, [])
                if tid not in groups[label]:
                    groups[label].append(tid)
        config = Config(source_root=src, targets=targets, groups=groups)

        # First deploy — untagged, goes to both
        deploy(config)
        assert (tc_personal.path / "commands" / "heavy.md").exists()
        assert (tc_positron.path / "commands" / "heavy.md").exists()

        # Now rename with a tag — only positron
        (commands / "heavy.md").rename(commands / "heavy -- positron.md")

        actions = deploy(config)

        # Personal should get a remove, positron should skip (same content)
        personal_removes = [
            a
            for a in actions
            if a.target_id == "claude-personal" and a.action == "remove"
        ]
        assert len(personal_removes) == 1
        assert personal_removes[0].name == "heavy"

        # File removed from personal
        assert not (tc_personal.path / "commands" / "heavy.md").exists()
        # File still on positron
        assert (tc_positron.path / "commands" / "heavy.md").exists()


class TestForce:
    """--force bypasses checksum and pre-existing checks."""

    def test_force_redeploys_unchanged_items(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        actions = deploy(config, force=True)

        updates = [a for a in actions if a.action == "update"]
        skips = [a for a in actions if a.action == "skip"]
        assert len(updates) == 3
        assert len(skips) == 0

    def test_force_overwrites_pre_existing(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_text("I was here first")

        actions = deploy(config, force=True)

        creates = [a for a in actions if a.action == "create"]
        pre = [a for a in actions if a.action == "pre-existing"]
        assert any(a.name == "helper" for a in creates)
        assert len(pre) == 0

        # File should be overwritten
        assert (agents_dir / "helper.md").read_text() != "I was here first"

    def test_force_records_in_manifest(self, tmp_path: Path):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_text("pre-existing")

        deploy(config, force=True)

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert "helper" in manifest.items.get("agents", {})

    def test_force_overwrites_codex_unmanaged_mcp_table(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "context-hub.yaml").write_text(
            "command: managed-context-hub\nargs:\n  - --stdio\n"
        )

        target_root = tmp_path / "codex-home"
        target_root.mkdir()
        config_path = target_root / ".codex" / "config.toml"
        config_path.parent.mkdir()
        config_path.write_text(
            "[mcp_servers.context-hub]\n"
            'command = "manual-context-hub"\n'
            "\n"
            "[mcp_servers.keep]\n"
            'command = "keep"\n'
        )

        tc = TargetConfig(id="codex", type="codex", path=target_root)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, force=True)

        assert [a.action for a in actions] == ["create"]
        data = tomllib.loads(config_path.read_text("utf-8"))
        assert data["mcp_servers"]["context-hub"]["command"] == ("managed-context-hub")
        assert data["mcp_servers"]["keep"]["command"] == "keep"

    def test_force_overwrites_codex_unmanaged_model_provider(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "models.yaml").write_text(
            "providers:\n"
            "  proxy:\n"
            "    display_name: Proxy\n"
            "    base_url: https://proxy.example/v1\n"
            "    codex: {}\n"
            "    models:\n"
            "      gpt-x: {}\n"
        )

        target_root = tmp_path / "codex-home"
        target_root.mkdir()
        config_path = target_root / ".codex" / "config.toml"
        config_path.parent.mkdir()
        config_path.write_text(
            "[model_providers.proxy]\n"
            'name = "Manual"\n'
            "\n"
            "[model_providers.keep]\n"
            'name = "Keep"\n'
        )

        tc = TargetConfig(id="codex", type="codex", path=target_root)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, force=True)

        assert [a.action for a in actions] == ["create"]
        data = tomllib.loads(config_path.read_text("utf-8"))
        assert data["model_providers"]["proxy"]["name"] == "Proxy"
        assert data["model_providers"]["proxy"]["base_url"] == (
            "https://proxy.example/v1"
        )
        assert data["model_providers"]["keep"] == {"name": "Keep"}


class TestCacheInvalidation:
    """Config-dependent transforms must invalidate the manifest cache.

    The manifest tracks the effective deployed content -- not just source
    bytes -- so that changing an input that affects the transform (e.g. the
    Anthropic default_model or a per-target model override) invalidates the
    cached hash and causes a redeploy.
    """

    def _write_models_yaml(self, root: Path, default_model: str) -> None:
        (root / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            f"      default_model: {default_model}\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Opus 4.7\n"
            "      claude-sonnet-4-6:\n"
            "        display_name: Sonnet 4.6\n"
        )

    def test_default_model_change_invalidates_agent_cache(self, tmp_path: Path):
        src = _make_source(tmp_path)
        self._write_models_yaml(src, "claude-opus-4-7")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)

        # Source content unchanged, but models.yaml default_model flipped.
        self._write_models_yaml(src, "claude-sonnet-4-6")

        actions = deploy(config)
        updates = [
            a for a in actions if a.action == "update" and a.item_type == "agent"
        ]
        assert len(updates) == 1

        from promptdeploy.frontmatter import parse_frontmatter

        meta, _ = parse_frontmatter((tc.path / "agents" / "helper.md").read_bytes())
        assert meta is not None
        assert meta["model"] == "claude-sonnet-4-6"

    def test_default_model_change_invalidates_skill_cache(self, tmp_path: Path):
        src = _make_source(tmp_path)
        self._write_models_yaml(src, "claude-opus-4-7")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)

        self._write_models_yaml(src, "claude-sonnet-4-6")

        actions = deploy(config)
        updates = [
            a for a in actions if a.action == "update" and a.item_type == "skill"
        ]
        assert len(updates) == 1

        from promptdeploy.frontmatter import parse_frontmatter

        skill_md = tc.path / "skills" / "my-skill" / "SKILL.md"
        meta, _ = parse_frontmatter(skill_md.read_bytes())
        assert meta is not None
        assert meta["model"] == "claude-sonnet-4-6"

    def test_default_model_change_does_not_update_commands(self, tmp_path: Path):
        src = _make_source(tmp_path)
        self._write_models_yaml(src, "claude-opus-4-7")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        self._write_models_yaml(src, "claude-sonnet-4-6")

        actions = deploy(config)
        command_updates = [
            a for a in actions if a.action == "update" and a.item_type == "command"
        ]
        assert command_updates == []

    def test_per_target_model_change_invalidates_cache(self, tmp_path: Path):
        from promptdeploy.config import Config, TargetConfig

        src = _make_source(tmp_path)
        target_dir = tmp_path / "t"
        target_dir.mkdir()

        cfg_v1 = Config(
            source_root=src,
            targets={
                "t": TargetConfig(
                    id="t", type="claude", path=target_dir, model="claude-opus-4-7"
                ),
            },
            groups={},
        )
        deploy(cfg_v1)

        cfg_v2 = Config(
            source_root=src,
            targets={
                "t": TargetConfig(
                    id="t",
                    type="claude",
                    path=target_dir,
                    model="claude-sonnet-4-6",
                ),
            },
            groups={},
        )
        actions = deploy(cfg_v2)
        updates = [a for a in actions if a.action == "update"]
        assert any(a.item_type == "agent" for a in updates)
        assert any(a.item_type == "skill" for a in updates)

    def test_same_config_still_skips(self, tmp_path: Path):
        """Regression: unchanged source + unchanged config = skip."""
        src = _make_source(tmp_path)
        self._write_models_yaml(src, "claude-opus-4-7")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        actions = deploy(config)
        non_skips = [a for a in actions if a.action != "skip"]
        assert non_skips == []


class TestDeployItemDispatch:
    """_deploy_item dispatches on item type and rejects anything else."""

    def test_imported_item_uses_only_the_accepted_snapshot(
        self, tmp_path: Path
    ) -> None:
        from promptdeploy.imported_tree import (
            ImportedTreeEntry,
            ImportedTreeSnapshot,
            framed_tree_sha256,
        )
        from promptdeploy.manifest import ManifestSource
        from promptdeploy.source import SourceItem, SourceProvenance
        from promptdeploy.targets.claude import ClaudeTarget

        source_dir = tmp_path / "mutable" / "demo"
        source_dir.mkdir(parents=True)
        skill_md = source_dir / "SKILL.md"
        skill_md.write_bytes(b"accepted\n")
        entries = (
            ImportedTreeEntry("directory", ".", 0o755),
            ImportedTreeEntry("file", "SKILL.md", 0o644, b"accepted\n"),
        )
        snapshot = ImportedTreeSnapshot(
            "skills/demo", entries, framed_tree_sha256(entries)
        )
        provenance = SourceProvenance.imported(
            ManifestSource(
                "ponytail",
                "skills/demo",
                "4.8.4",
                None,
                None,
                True,
                None,
                "MIT",
            ),
            input_sha256=compute_file_hash(b"accepted\n"),
            tree_sha256=snapshot.tree_sha256,
        )
        item = SourceItem(
            "skill",
            "demo",
            skill_md,
            {"name": "demo"},
            b"accepted\n",
            provenance=provenance,
            imported_tree=snapshot,
        )
        target = ClaudeTarget("t", tmp_path / "target")

        accepted_hash = compute_item_hash(item, target)
        skill_md.write_bytes(b"mutated\n")
        assert compute_item_hash(item, target) == accepted_hash
        skill_md.unlink()
        _deploy_item(target, item)
        assert (
            tmp_path / "target" / "skills" / "demo" / "SKILL.md"
        ).read_bytes() == b"accepted\n"

    def test_settings_item_type_rejected(self, tmp_path: Path):
        # settings items are dispatched through Target.deploy_settings in
        # deploy(), never through _deploy_item; reaching it with one (or any
        # unknown type) is a programming error and must fail loudly instead
        # of silently deploying nothing.
        from promptdeploy.source import SourceItem
        from promptdeploy.targets.claude import ClaudeTarget

        target = ClaudeTarget("t", tmp_path)
        item = SourceItem("settings", "settings", tmp_path / "settings.yaml", {}, b"")
        with pytest.raises(ValueError, match="settings"):
            _deploy_item(target, item)

    def test_unsupported_imported_item_type_fails_closed(self, tmp_path: Path) -> None:
        from promptdeploy.manifest import ManifestSource
        from promptdeploy.source import SourceItem, SourceProvenance
        from promptdeploy.targets.claude import ClaudeTarget

        item = SourceItem(
            "agent",
            "imported",
            tmp_path / "diagnostic.md",
            None,
            b"body",
            provenance=SourceProvenance.imported(
                ManifestSource(
                    "ponytail",
                    "agents/imported.md",
                    "4.8.4",
                    None,
                    None,
                    True,
                    None,
                    "MIT",
                )
            ),
        )
        with pytest.raises(RuntimeError, match="unsupported imported item type"):
            _deploy_item(ClaudeTarget("claude", tmp_path / "target"), item)


class TestTypeMappings:
    def test_marketplace_maps_to_category(self) -> None:
        assert _TYPE_TO_CATEGORY["marketplace"] == "marketplaces"

    def test_cli_marketplaces_maps_to_item_type(self) -> None:
        assert _CLI_TYPE_TO_ITEM_TYPE["marketplaces"] == "marketplace"


@pytest.mark.parametrize(
    ("target_type", "relative_config"),
    [
        ("codex", Path(".codex/config.toml")),
        ("opencode", Path("opencode.json")),
    ],
)
def test_non_claude_preview_mcp_never_bakes_secrets(
    target_type: str,
    relative_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from promptdeploy.config import remap_targets_to_root

    source = tmp_path / "source"
    mcp = source / "mcp"
    mcp.mkdir(parents=True)
    (mcp / "secret.yaml").write_bytes(
        b"name: secret\n"
        b"url: https://example.invalid/?token=$"
        b"{PREVIEW_SECRET}\n"
        b"env:\n  TOKEN: $"
        b"{PREVIEW_SECRET}\n"
    )
    config = Config(
        source_root=source,
        targets={
            "local": TargetConfig("local", target_type, tmp_path / "original"),
        },
        groups={},
    )
    preview = remap_targets_to_root(config, tmp_path / "preview")
    monkeypatch.setenv("PREVIEW_SECRET", "first-sensitive-value")

    deploy(preview, item_selectors=[("mcp", "secret")])
    deployed = (preview.targets["local"].path / relative_config).read_bytes()
    assert b"first-sensitive-value" not in deployed
    assert b"${PREVIEW_SECRET}" in deployed

    monkeypatch.setenv("PREVIEW_SECRET", "rotated-sensitive-value")
    assert {
        action.action for action in deploy(preview, item_selectors=[("mcp", "secret")])
    } == {"skip"}


@pytest.mark.parametrize(
    ("target_type", "relative_config"),
    [
        ("droid", Path("settings.json")),
        ("opencode", Path("opencode.json")),
    ],
)
def test_preview_models_never_bake_secrets(
    target_type: str,
    relative_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from promptdeploy.config import remap_targets_to_root

    source = tmp_path / "source"
    source.mkdir()
    (source / "models.yaml").write_bytes(
        b"providers:\n"
        b"  preview:\n"
        b"    api_key: $"
        b"{PREVIEW_SECRET}\n"
        b"    base_url: https://example.invalid/v1\n"
        b"    droid: {}\n"
        b"    opencode:\n"
        b"      name: Preview\n"
        b"    models:\n"
        b"      demo: {}\n"
    )
    config = Config(
        source_root=source,
        targets={
            "local": TargetConfig("local", target_type, tmp_path / "original"),
        },
        groups={},
    )
    preview = remap_targets_to_root(config, tmp_path / "preview")
    monkeypatch.setenv("PREVIEW_SECRET", "first-sensitive-value")

    deploy(preview, item_selectors=[("models", "models")])
    deployed = (preview.targets["local"].path / relative_config).read_bytes()
    assert b"first-sensitive-value" not in deployed
    assert b"${PREVIEW_SECRET}" in deployed

    monkeypatch.setenv("PREVIEW_SECRET", "rotated-sensitive-value")
    assert {
        action.action
        for action in deploy(preview, item_selectors=[("models", "models")])
    } == {"skip"}


class TestActionType:
    def test_literal_covers_all_emitted_values(self) -> None:
        """DeployAction.action is a Literal including 'pre-existing' (SC2)."""
        from typing import get_args

        from promptdeploy.deploy import ActionType

        assert set(get_args(ActionType)) == {
            "create",
            "update",
            "remove",
            "skip",
            "pre-existing",
        }


class TestItemSelected:
    """item_selected() is the single selection predicate shared with status."""

    def test_rejects_on_filters_and_target_skip(self, tmp_path: Path):
        from promptdeploy.deploy import item_selected
        from promptdeploy.source import SourceDiscovery
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        (src / "commands").mkdir(parents=True)
        # Filetagged for positron only; also a plain command, which droid skips.
        (src / "commands" / "heavy -- positron.md").write_bytes(b"Heavy.\n")
        (src / "agents").mkdir()
        (src / "agents" / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")

        target_dir = tmp_path / "droid-target"
        target_dir.mkdir()
        tc = TargetConfig(id="droid-t", type="droid", path=target_dir, labels=[])
        config = Config(
            source_root=src,
            targets={tc.id: tc},
            groups={"positron": []},
        )
        target = create_target(tc)

        items = {i.name: i for i in SourceDiscovery(src).discover_all()}
        # 'heavy' fails the filetag filter (droid-t is not in positron).
        assert not item_selected(items["heavy"], target, tc.id, config)
        # 'helper' passes filters and droid deploys agents.
        assert item_selected(items["helper"], target, tc.id, config)


class TestCodexCommandDeploy:
    """Codex command deployment writes generated skills."""

    def test_refreshes_legacy_command_manifest_path(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        commands = src / "commands"
        commands.mkdir()
        body = b"---\ndescription: Fix.\n---\nFix $ARGUMENTS.\n"
        (commands / "fix.md").write_bytes(body)

        target_dir = tmp_path / "codex"
        target_dir.mkdir()
        tc = TargetConfig(id="codex", type="codex", path=target_dir)
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)

        legacy_skill = target_dir / ".agents" / "skills" / "command-fix"
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SKILL.md").write_text("legacy skill")
        manifest = Manifest()
        manifest.items["commands"] = {
            "fix": ManifestItem(
                source_hash="old",
                target_path=".codex/prompts/fix.md",
            )
        }
        save_manifest(manifest, target.manifest_path())

        actions = deploy(config)

        assert any(a.name == "fix" and a.action == "update" for a in actions)
        assert not (target_dir / ".codex" / "prompts" / "fix.md").exists()
        assert (legacy_skill / "SKILL.md").exists()
        manifest = load_manifest(target.manifest_path())
        assert (
            manifest.items["commands"]["fix"].target_path
            == ".agents/skills/command-fix"
        )


class TestMarketplaceDeploy:
    """Deploy marketplace items through the deploy orchestration."""

    def _src(self, tmp_path: Path, body: bytes) -> Path:
        src = tmp_path / "source"
        src.mkdir()
        mk = src / "marketplaces"
        mk.mkdir()
        (mk / "acme.yaml").write_bytes(body)
        return src

    def test_deploys_marketplace(self, tmp_path: Path):
        src = self._src(
            tmp_path,
            b"name: acme\nsource:\n  source: github\n  repo: acme/plugins\n"
            b"plugins:\n  formatter: true\n",
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)

        creates = [a for a in actions if a.action == "create"]
        assert any(a.name == "acme" and a.item_type == "marketplace" for a in creates)
        s = json.loads((tc.path / "settings.json").read_text())
        assert "acme" in s["extraKnownMarketplaces"]
        assert s["enabledPlugins"]["formatter@acme"] is True

    def test_second_deploy_skips(self, tmp_path: Path):
        src = self._src(
            tmp_path, b"name: acme\nsource:\n  source: github\n  repo: a/b\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        actions = deploy(config)
        market = [a for a in actions if a.item_type == "marketplace"]
        assert len(market) == 1
        assert market[0].action == "skip"

    def test_removes_stale_marketplace(self, tmp_path: Path):
        src = self._src(
            tmp_path, b"name: acme\nsource:\n  source: github\n  repo: a/b\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        (src / "marketplaces" / "acme.yaml").unlink()
        actions = deploy(config)
        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "acme" and a.item_type == "marketplace" for a in removes)
        s = json.loads((tc.path / "settings.json").read_text())
        assert "extraKnownMarketplaces" not in s

    def test_droid_skips_marketplaces(self, tmp_path: Path):
        src = self._src(tmp_path, b"name: acme\nplugins:\n  p: true\n")
        target_dir = tmp_path / "droid-target"
        target_dir.mkdir()
        tc = TargetConfig(id="droid-t", type="droid", path=target_dir)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)
        assert all(a.item_type != "marketplace" for a in actions)

    def test_stale_removal_preserves_surviving_marketplace(self, tmp_path: Path):
        # Two co-resident marketplace files; deleting one must strip exactly
        # its entries via deploy()'s stale-detection branch, leaving the other.
        src = self._src(
            tmp_path,
            b"name: acme\nsource:\n  source: github\n  repo: acme/plugins\n"
            b"plugins:\n  formatter: true\n",
        )
        (src / "marketplaces" / "beta.yaml").write_bytes(
            b"name: beta\nsource:\n  source: git\n  url: https://b\n"
            b"plugins:\n  linter: true\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        (src / "marketplaces" / "acme.yaml").unlink()
        actions = deploy(config)
        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "acme" and a.item_type == "marketplace" for a in removes)
        assert not any(a.name == "beta" for a in removes)
        s = json.loads((tc.path / "settings.json").read_text())
        assert set(s["extraKnownMarketplaces"]) == {"beta"}
        assert "formatter@acme" not in s["enabledPlugins"]
        assert s["enabledPlugins"]["linter@beta"] is True


class TestMarketplaceMigration:
    """Migration invariant: when settings.yaml previously managed
    extraKnownMarketplaces/enabledPlugins (recorded in the settings manifest's
    managed_keys), and the keys now move to marketplaces/*.yaml, the settings
    item must run first to pop the stale keys before the marketplace item
    re-adds its own entries in the same deploy run."""

    def test_settings_pop_precedes_marketplace_readd(self, tmp_path: Path):
        from promptdeploy.manifest import Manifest, save_manifest

        src = tmp_path / "source"
        src.mkdir()
        # New world: settings.yaml no longer carries the marketplace keys.
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
        mk = src / "marketplaces"
        mk.mkdir()
        (mk / "acme.yaml").write_bytes(
            b"name: acme\nsource:\n  source: github\n  repo: acme/plugins\n"
            b"plugins:\n  formatter: true\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Old world on the target: settings.json AND a manifest in which the
        # settings item previously managed the two marketplace keys.
        (tc.path / "settings.json").write_text(
            json.dumps(
                {
                    "effortLevel": "low",
                    "extraKnownMarketplaces": {
                        "stale": {"source": {"source": "git", "url": "https://old"}}
                    },
                    "enabledPlugins": {"old@stale": True},
                }
            )
        )
        prior = Manifest()
        prior.items["settings"] = {
            "settings": ManifestItem(
                source_hash="sha256:old",
                managed_keys=[
                    "effortLevel",
                    "extraKnownMarketplaces",
                    "enabledPlugins",
                ],
            )
        }
        save_manifest(prior, tc.path / MANIFEST_FILENAME)

        deploy(config)

        expected_markets = {
            "acme": {"source": {"source": "github", "repo": "acme/plugins"}}
        }
        s = json.loads((tc.path / "settings.json").read_text())
        # The stale settings.yaml-managed entries are gone; only the
        # marketplace-file entries remain.
        assert s["extraKnownMarketplaces"] == expected_markets
        assert s["enabledPlugins"] == {"formatter@acme": True}
        assert s["effortLevel"] == "low"

        # Steady state: a second deploy must converge, not re-strip the
        # marketplace-owned keys. The new manifest recorded settings
        # managed_keys = ['effortLevel'], so deploy_settings no longer treats
        # extraKnownMarketplaces/enabledPlugins as previously-managed.
        deploy(config)
        s2 = json.loads((tc.path / "settings.json").read_text())
        assert s2["extraKnownMarketplaces"] == expected_markets
        assert s2["enabledPlugins"] == {"formatter@acme": True}
        assert s2["effortLevel"] == "low"


class TestDeploySettingsItem:
    def _src_with_settings(self, tmp_path: Path, yaml_text: str) -> Path:
        src = tmp_path / "source"
        src.mkdir()
        (src / "settings.yaml").write_text(yaml_text)
        return src

    def test_create_then_skip_then_update(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        a1 = deploy(config)
        assert next(a for a in a1 if a.item_type == "settings").action == "create"
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["effortLevel"] == "low"

        a2 = deploy(config)
        assert next(a for a in a2 if a.item_type == "settings").action == "skip"

        (src / "settings.yaml").write_text("base:\n  effortLevel: high\n")
        a3 = deploy(config)
        s3 = next(a for a in a3 if a.item_type == "settings")
        assert s3.action == "update"
        assert (
            json.loads((tc.path / "settings.json").read_text())["effortLevel"] == "high"
        )

    def test_manifest_records_managed_keys(self, tmp_path: Path):
        src = self._src_with_settings(
            tmp_path, "base:\n  effortLevel: low\n  model: opus\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        managed_keys = manifest.items["settings"]["settings"].managed_keys
        assert managed_keys is not None
        assert set(managed_keys) == {"effortLevel", "model"}

    def test_override_applies_per_target(self, tmp_path: Path):
        src = self._src_with_settings(
            tmp_path,
            (
                "base:\n  effortLevel: low\n"
                "overrides:\n  claude-positron:\n    effortLevel: high\n"
            ),
        )
        personal = _make_claude_target(tmp_path, "claude-personal")
        positron = _make_claude_target(tmp_path, "claude-positron")
        config = _make_config(src, {personal.id: personal, positron.id: positron})
        deploy(config)
        assert (
            json.loads((personal.path / "settings.json").read_text())["effortLevel"]
            == "low"
        )
        assert (
            json.loads((positron.path / "settings.json").read_text())["effortLevel"]
            == "high"
        )

    def test_removing_settings_yaml_removes_managed_keys(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        (src / "settings.yaml").unlink()
        actions = deploy(config)
        removed = [
            a for a in actions if a.item_type == "settings" and a.action == "remove"
        ]
        assert len(removed) == 1
        assert "effortLevel" not in json.loads((tc.path / "settings.json").read_text())

    def test_settings_preserves_hooks_and_mcp(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        (tc.path / "settings.json").write_text(
            json.dumps({"hooks": {"Stop": [1]}, "mcpServers": {"pal": {}}})
        )
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["hooks"] == {"Stop": [1]}
        assert data["mcpServers"] == {"pal": {}}
        assert data["effortLevel"] == "low"

    def test_only_type_settings_filters(self, tmp_path: Path):
        src = _make_source(tmp_path)  # has agent/command/skill
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config, item_types=["settings"])
        assert {a.item_type for a in actions if a.action == "create"} == {"settings"}

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config, dry_run=True)
        assert not (tc.path / "settings.json").exists()
        assert not (tc.path / MANIFEST_FILENAME).exists()

    def test_group_override_change_triggers_redeploy(self, tmp_path: Path):
        """A deploy.yaml change that alters the rendered settings must
        redeploy even though settings.yaml bytes are unchanged (B5): the
        manifest hash covers the rendered output, not the source bytes."""
        src = self._src_with_settings(
            tmp_path,
            "base:\n  effortLevel: low\noverrides:\n  fast:\n    effortLevel: high\n",
        )
        tc = _make_claude_target(tmp_path)
        grouped = Config(source_root=src, targets={tc.id: tc}, groups={"fast": [tc.id]})
        deploy(grouped)
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["effortLevel"] == "high"

        # Same settings.yaml, but the target has left the 'fast' group.
        ungrouped = Config(source_root=src, targets={tc.id: tc}, groups={})
        actions = deploy(ungrouped)
        s = next(a for a in actions if a.item_type == "settings")
        assert s.action == "update"
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["effortLevel"] == "low"

    def test_group_change_removes_previously_managed_keys(self, tmp_path: Path):
        """A key managed only via a group override is removed when the
        target leaves the group (B5): the redeploy passes the previous
        managed_keys so deploy_settings can drop the stale key."""
        src = self._src_with_settings(
            tmp_path,
            "base:\n  effortLevel: low\noverrides:\n  fast:\n    extraKey: enabled\n",
        )
        tc = _make_claude_target(tmp_path)
        grouped = Config(source_root=src, targets={tc.id: tc}, groups={"fast": [tc.id]})
        deploy(grouped)
        assert json.loads((tc.path / "settings.json").read_text())["extraKey"] == (
            "enabled"
        )

        ungrouped = Config(source_root=src, targets={tc.id: tc}, groups={})
        deploy(ungrouped)
        data = json.loads((tc.path / "settings.json").read_text())
        assert "extraKey" not in data
        assert data["effortLevel"] == "low"
        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert manifest.items["settings"]["settings"].managed_keys == ["effortLevel"]

    def test_skip_preserves_recorded_managed_keys(self, tmp_path: Path):
        """A skipping deploy must carry the previous deploy's managed_keys
        forward (B5), not record keys from a render that never deployed."""
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        # Simulate an earlier deploy that managed an extra key.
        mp = tc.path / MANIFEST_FILENAME
        manifest = load_manifest(mp)
        manifest.items["settings"]["settings"].managed_keys = [
            "effortLevel",
            "ghostKey",
        ]
        save_manifest(manifest, mp)

        actions = deploy(config)
        assert next(a for a in actions if a.item_type == "settings").action == "skip"
        m2 = load_manifest(mp)
        assert set(m2.items["settings"]["settings"].managed_keys or []) == {
            "effortLevel",
            "ghostKey",
        }


class TestRemoteDeployIntegration:
    """deploy() with a host: target drives the RemoteTarget lifecycle:
    prepare (ssh_pull) -> deploy into staging -> manifest write ->
    finalize (ssh_push), with cleanup-only on dry-run and staging cleanup
    when an SSH operation fails."""

    def _remote_config(self, tmp_path: Path) -> Config:
        src = _make_source(tmp_path)
        tc = TargetConfig(
            id="remote-claude",
            type="claude",
            path=tmp_path / "remote-claude",
            host="user@fakehost",
        )
        return _make_config(src, {tc.id: tc})

    def test_pull_deploy_manifest_push_ordering(self, tmp_path: Path, monkeypatch):
        config = self._remote_config(tmp_path)
        events: list[str] = []
        seen: dict = {}

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            events.append("pull")
            seen["staging"] = local_path

        def fake_push(host, remote_path, local_path, *, verbose=False, includes=None):
            events.append("push")
            # By push time the staging dir must already contain the deployed
            # artifacts AND the saved manifest (manifest before finalize).
            seen["artifact_present"] = (local_path / "agents" / "helper.md").exists()
            seen["manifest_present"] = (local_path / MANIFEST_FILENAME).exists()
            seen["host"] = host
            seen["remote"] = remote_path

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr("promptdeploy.targets.remote.ssh_push", fake_push)

        actions = deploy(config)

        assert events == ["pull", "push"]
        assert seen["artifact_present"] is True
        assert seen["manifest_present"] is True
        assert seen["host"] == "user@fakehost"
        assert seen["remote"] == tmp_path / "remote-claude"
        creates = {a.name for a in actions if a.action == "create"}
        assert creates == {"helper", "fix", "my-skill"}
        # finalize() removed the staging dir after pushing.
        assert not seen["staging"].exists()

    def test_dry_run_pulls_but_never_pushes(self, tmp_path: Path, monkeypatch):
        config = self._remote_config(tmp_path)
        pulls: list = []
        pushes: list = []
        seen: dict = {}

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            pulls.append(host)
            seen["staging"] = local_path

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push",
            lambda *a, **kw: pushes.append(a),
        )

        actions = deploy(config, dry_run=True)

        assert pulls == ["user@fakehost"]
        assert pushes == []
        # Nothing was written and the staging dir was cleaned up.
        assert not seen["staging"].exists()
        assert {a.action for a in actions} == {"create"}

    def test_push_failure_propagates_and_cleans_staging(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.ssh import SSHError

        config = self._remote_config(tmp_path)
        seen: dict = {}

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            seen["staging"] = local_path

        def fake_push(host, remote_path, local_path, *, verbose=False, includes=None):
            raise SSHError("rsync push failed")

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr("promptdeploy.targets.remote.ssh_push", fake_push)

        with pytest.raises(SSHError, match="rsync push failed"):
            deploy(config)

        # The deploy loop's cleanup() removed the staging dir.
        assert not seen["staging"].exists()

    def test_pull_failure_propagates_and_cleans_staging(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.ssh import SSHError

        config = self._remote_config(tmp_path)
        seen: dict = {}
        pushes: list = []

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            seen["staging"] = local_path
            raise SSHError("host unreachable")

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push",
            lambda *a, **kw: pushes.append(a),
        )

        with pytest.raises(SSHError, match="host unreachable"):
            deploy(config)

        assert pushes == []
        assert not seen["staging"].exists()


# ----------------------------------------------------------------------
# Remote MCP (SSH-stdin direct merge) orchestration + hash + convergence
# ----------------------------------------------------------------------


def _remote_mcp_config(tmp_path: Path, *, mcp_yaml: bytes) -> Config:
    """Build a remote claude config with a single mcp source."""
    src = tmp_path / "source"
    src.mkdir(exist_ok=True)
    mcp_dir = src / "mcp"
    mcp_dir.mkdir(exist_ok=True)
    (mcp_dir / "srv.yaml").write_bytes(mcp_yaml)
    tc = TargetConfig(
        id="remote-claude",
        type="claude",
        path=tmp_path / "remote-claude",
        host="user@fakehost",
    )
    return _make_config(src, {tc.id: tc})


def _patch_pull_push(monkeypatch, *, seed: dict | None = None):
    """Patch ssh_pull/ssh_push so the staging dir is created (and optionally a
    seeded manifest is written there)."""
    from promptdeploy.manifest import (
        MANIFEST_FILENAME,
        Manifest,
        ManifestItem,
        save_manifest,
    )

    def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
        local_path.mkdir(parents=True, exist_ok=True)
        if seed:
            save_manifest(
                Manifest(
                    items={
                        "mcp_servers": {
                            n: ManifestItem(source_hash=h) for n, h in seed.items()
                        }
                    }
                ),
                local_path / MANIFEST_FILENAME,
            )

    monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
    monkeypatch.setattr("promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None)


def _hash_for(config: Config, *, target_id: str = "remote-claude") -> str:
    """Compute the remote-mcp env-folded hash for the srv item under the
    CURRENT env (so a seeded manifest can be made to match)."""
    from promptdeploy.deploy import compute_item_hash
    from promptdeploy.source import SourceDiscovery
    from promptdeploy.targets import create_target

    target = create_target(config.targets[target_id])
    item = next(
        i
        for i in SourceDiscovery(config.source_root).discover_all()
        if i.item_type == "mcp"
    )
    return compute_item_hash(item, target, config)


class TestRemoteMcpDeploy:
    def test_remote_mcp_deploy_flushes_before_save_manifest(
        self, tmp_path: Path, monkeypatch
    ):
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        _patch_pull_push(monkeypatch)
        order: list[str] = []
        captured: dict = {}

        def fake_stdin(host, script):
            order.append("stdin")
            captured["script"] = script

        def fake_save(manifest, path):
            order.append("save")

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_stdin", fake_stdin)
        monkeypatch.setattr("promptdeploy.deploy.save_manifest", fake_save)

        actions = deploy(config)

        creates = [a for a in actions if a.action == "create" and a.item_type == "mcp"]
        assert len(creates) == 1
        assert order[order.index("stdin")] == "stdin"
        assert order.index("stdin") < order.index("save")
        # The script encodes a single set op for srv.
        import base64
        import json as _json

        marker = 'base64.b64decode("'
        s = captured["script"]
        b = s[
            s.index(marker) + len(marker) : s.index('"', s.index(marker) + len(marker))
        ]
        ops = _json.loads(base64.b64decode(b).decode())
        assert ops == [{"action": "set", "name": "srv", "entry": {"command": "c"}}]

    def test_remote_mcp_dry_run_no_ssh_stdin_no_write(
        self, tmp_path: Path, monkeypatch
    ):
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        pulls: list = []
        saves: list = []
        stdins: list = []

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            pulls.append(host)
            local_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: stdins.append(a),
        )
        monkeypatch.setattr(
            "promptdeploy.deploy.save_manifest", lambda *a, **kw: saves.append(a)
        )

        actions = deploy(config, dry_run=True)

        assert any(a.action == "create" and a.item_type == "mcp" for a in actions)
        assert stdins == []
        assert saves == []
        assert pulls == ["user@fakehost"]

    def test_remote_mcp_unchanged_skips_no_ssh_stdin(self, tmp_path: Path, monkeypatch):
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        seed_hash = _hash_for(config)
        _patch_pull_push(monkeypatch, seed={"srv": seed_hash})
        stdins: list = []
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: stdins.append(a),
        )

        actions = deploy(config)

        mcp_actions = [a for a in actions if a.item_type == "mcp"]
        assert all(a.action == "skip" for a in mcp_actions)
        assert stdins == []

    def test_remote_mcp_rotated_secret_redeploys(self, tmp_path: Path, monkeypatch):
        config = _remote_mcp_config(
            tmp_path,
            mcp_yaml=b'name: srv\nurl: https://x\nenv:\n  K: "${TOK}"\n',
        )
        monkeypatch.setenv("TOK", "v1")
        seed_hash = _hash_for(config)
        monkeypatch.setenv("TOK", "v2")
        _patch_pull_push(monkeypatch, seed={"srv": seed_hash})
        captured: dict = {}
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: captured.update(script=script),
        )

        actions = deploy(config)

        updates = [a for a in actions if a.action == "update" and a.item_type == "mcp"]
        assert len(updates) == 1
        import base64
        import json as _json

        marker = 'base64.b64decode("'
        s = captured["script"]
        b = s[
            s.index(marker) + len(marker) : s.index('"', s.index(marker) + len(marker))
        ]
        ops = _json.loads(base64.b64decode(b).decode())
        assert ops[0]["action"] == "set"
        assert ops[0]["entry"]["env"]["K"] == "v2"

    def test_remote_mcp_enabled_flip_triggers_pop(self, tmp_path: Path, monkeypatch):
        config = _remote_mcp_config(
            tmp_path, mcp_yaml=b"name: srv\ncommand: c\nenabled: true\n"
        )
        seed_hash = _hash_for(config)
        # Flip source to disabled.
        (config.source_root / "mcp" / "srv.yaml").write_bytes(
            b"name: srv\ncommand: c\nenabled: false\n"
        )
        _patch_pull_push(monkeypatch, seed={"srv": seed_hash})
        captured: dict = {}
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: captured.update(script=script),
        )

        actions = deploy(config)

        updates = [a for a in actions if a.action == "update" and a.item_type == "mcp"]
        assert len(updates) == 1
        import base64
        import json as _json

        marker = 'base64.b64decode("'
        s = captured["script"]
        b = s[
            s.index(marker) + len(marker) : s.index('"', s.index(marker) + len(marker))
        ]
        ops = _json.loads(base64.b64decode(b).decode())
        assert ops == [{"action": "pop", "name": "srv", "entry": None}]

    def test_remote_mcp_stripped_scope_flip_does_not_redeploy(
        self, tmp_path: Path, monkeypatch
    ):
        config = _remote_mcp_config(
            tmp_path, mcp_yaml=b"name: srv\ncommand: c\nscope: user\n"
        )
        seed_hash = _hash_for(config)
        (config.source_root / "mcp" / "srv.yaml").write_bytes(
            b"name: srv\ncommand: c\nscope: local\n"
        )
        _patch_pull_push(monkeypatch, seed={"srv": seed_hash})
        captured: dict = {}
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: captured.update(script=script),
        )

        actions = deploy(config)

        assert [action.action for action in actions if action.item_type == "mcp"] == [
            "skip"
        ]
        assert captured == {}

    def test_remote_mcp_stale_removal_flushes_pop(self, tmp_path: Path, monkeypatch):
        # No source for "gone", but the seeded manifest has it.
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        _patch_pull_push(monkeypatch, seed={"gone": "sha256:stale"})
        captured: dict = {}
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: captured.update(script=script),
        )

        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove" and a.name == "gone"]
        assert len(removes) == 1
        import base64
        import json as _json

        marker = 'base64.b64decode("'
        s = captured["script"]
        b = s[
            s.index(marker) + len(marker) : s.index('"', s.index(marker) + len(marker))
        ]
        ops = _json.loads(base64.b64decode(b).decode())
        assert {"action": "pop", "name": "gone", "entry": None} in ops

    def test_remote_mcp_stale_removal_dry_run_no_ssh_stdin(
        self, tmp_path: Path, monkeypatch
    ):
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        _patch_pull_push(monkeypatch, seed={"gone": "sha256:stale"})
        stdins: list = []
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: stdins.append(a),
        )

        actions = deploy(config, dry_run=True)

        assert any(a.action == "remove" and a.name == "gone" for a in actions)
        assert stdins == []

    def test_remote_mcp_flush_failure_leaves_manifest_unchanged(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.ssh import SSHError

        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        _patch_pull_push(monkeypatch)
        saves: list = []
        monkeypatch.setattr(
            "promptdeploy.deploy.save_manifest", lambda *a, **kw: saves.append(a)
        )
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: (_ for _ in ()).throw(SSHError("merge failed")),
        )

        with pytest.raises(SSHError, match="merge failed"):
            deploy(config)
        assert saves == []

        # Re-run with a working stdin: the same op is re-flushed (self-healing).
        captured: dict = {}
        monkeypatch.setattr("promptdeploy.deploy.save_manifest", lambda *a, **kw: None)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: captured.update(script=script),
        )
        deploy(config)
        import base64
        import json as _json

        marker = 'base64.b64decode("'
        s = captured["script"]
        b = s[
            s.index(marker) + len(marker) : s.index('"', s.index(marker) + len(marker))
        ]
        ops = _json.loads(base64.b64decode(b).decode())
        assert ops == [{"action": "set", "name": "srv", "entry": {"command": "c"}}]

    def test_remote_mcp_push_failure_after_flush_self_heals(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.ssh import SSHError

        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        flushes: list = []

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            local_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda host, script: flushes.append(script),
        )
        # First run: flush succeeds, push fails.
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push",
            lambda *a, **kw: (_ for _ in ()).throw(SSHError("push fail")),
        )
        with pytest.raises(SSHError, match="push fail"):
            deploy(config)
        assert len(flushes) == 1

        # Second run: push succeeds; the idempotent op is re-flushed harmlessly.
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None
        )
        deploy(config)
        assert len(flushes) == 2

    def test_remote_mcp_missing_secret_aborts_deploy(self, tmp_path: Path, monkeypatch):
        from promptdeploy.envsubst import EnvVarError

        config = _remote_mcp_config(
            tmp_path,
            mcp_yaml=b'name: srv\ncommand: c\nenv:\n  K: "${ABSENT}"\n',
        )
        monkeypatch.delenv("ABSENT", raising=False)
        seen: dict = {}
        stdins: list = []

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            seen["staging"] = local_path
            local_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: stdins.append(a),
        )

        with pytest.raises(EnvVarError):
            deploy(config)
        assert stdins == []
        assert not seen["staging"].exists()

    def test_local_claude_mcp_hash_changes_when_env_rotates(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.source import SourceDiscovery
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "srv.yaml").write_bytes(
            b'name: srv\ncommand: c\nenv:\n  K: "${TOK}"\n'
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)
        item = next(
            i for i in SourceDiscovery(src).discover_all() if i.item_type == "mcp"
        )

        monkeypatch.setenv("TOK", "v1")
        h1 = compute_item_hash(item, target, config)
        monkeypatch.setenv("TOK", "v2")
        assert compute_item_hash(item, target, config) != h1

    def test_claude_mcp_hash_ignores_secret_in_stripped_codex_override(
        self, tmp_path: Path, monkeypatch
    ):
        from promptdeploy.source import SourceDiscovery
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "srv.yaml").write_bytes(
            b'name: srv\ncommand: c\ncodex:\n  env:\n    IGNORED: "${IGNORED_SECRET}"\n'
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)
        item = next(
            source_item
            for source_item in SourceDiscovery(src).discover_all()
            if source_item.item_type == "mcp"
        )

        monkeypatch.setenv("IGNORED_SECRET", "first-sensitive-value")
        deploy(config)
        output = (tc.path / ".claude.json").read_bytes()
        first_hash = compute_item_hash(item, target, config)

        monkeypatch.setenv("IGNORED_SECRET", "rotated-sensitive-value")
        assert compute_item_hash(item, target, config) == first_hash
        actions = deploy(config)
        assert [action.action for action in actions if action.item_type == "mcp"] == [
            "skip"
        ]
        assert (tc.path / ".claude.json").read_bytes() == output

    @staticmethod
    def _url_mcp_item_and_config(src: Path):
        """Write a url-with-${VAR} mcp source under ``src`` and return the item."""
        from promptdeploy.source import SourceDiscovery

        src.mkdir(exist_ok=True)
        mcp_dir = src / "mcp"
        mcp_dir.mkdir(exist_ok=True)
        (mcp_dir / "srv.yaml").write_bytes(
            b'name: srv\nurl: "https://x/mcp?apiKey=${URL_TOK}"\n'
        )
        return next(
            i for i in SourceDiscovery(src).discover_all() if i.item_type == "mcp"
        )

    def test_remote_mcp_hash_changes_when_url_env_rotates(
        self, tmp_path: Path, monkeypatch
    ):
        # Remote-mcp targets bake the deploy-time-expanded URL secret into
        # the remote .claude.json, so their target-rendered hash input changes
        # when the referenced secret rotates.
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        item = self._url_mcp_item_and_config(src)
        tc = TargetConfig(
            id="rc", type="claude", path=tmp_path / "rc", host="user@fakehost"
        )
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)

        monkeypatch.setenv("URL_TOK", "v1")
        h1 = compute_item_hash(item, target, config)
        monkeypatch.setenv("URL_TOK", "v2")
        assert compute_item_hash(item, target, config) != h1

    def test_local_claude_mcp_hash_changes_when_url_env_rotates(
        self, tmp_path: Path, monkeypatch
    ):
        # Local Claude also bakes the URL secret at deploy time, so its
        # target-rendered hash input changes just like the remote path.
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        item = self._url_mcp_item_and_config(src)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)

        monkeypatch.setenv("URL_TOK", "v1")
        h1 = compute_item_hash(item, target, config)
        monkeypatch.setenv("URL_TOK", "v2")
        assert compute_item_hash(item, target, config) != h1

    def test_droid_mcp_hash_ignores_url_env_rotation(self, tmp_path: Path, monkeypatch):
        # Droid writes the URL reference verbatim and expands it at runtime,
        # so the target-rendered hash input ignores secret-value rotation.
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        item = self._url_mcp_item_and_config(src)
        target_dir = tmp_path / "droid"
        target_dir.mkdir()
        tc = TargetConfig(id="d", type="droid", path=target_dir)
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)

        monkeypatch.setenv("URL_TOK", "v1")
        h1 = compute_item_hash(item, target, config)
        monkeypatch.setenv("URL_TOK", "v2")
        assert compute_item_hash(item, target, config) == h1

    def test_flush_remote_mcp_helper_noop_for_non_remote_target(self, tmp_path: Path):
        from promptdeploy.deploy import _flush_remote_mcp
        from promptdeploy.targets.claude import ClaudeTarget

        target = ClaudeTarget("c", tmp_path)
        _flush_remote_mcp(target)  # must not raise

    def test_remote_mcp_hash_metadata_none(self, tmp_path: Path):
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.source import SourceItem
        from promptdeploy.targets import create_target

        src = tmp_path / "source"
        src.mkdir()
        tc = TargetConfig(
            id="rc", type="claude", path=tmp_path / "rc", host="user@fakehost"
        )
        config = _make_config(src, {tc.id: tc})
        target = create_target(tc)
        item = SourceItem(
            item_type="mcp",
            name="srv",
            path=src / "mcp" / "srv.yaml",
            metadata=None,
            content=b"just a scalar",
        )
        h = compute_item_hash(item, target, config)
        assert h.startswith("sha256:")

    def test_remote_mcp_queued_but_unmanaged_not_pre_existing(
        self, tmp_path: Path, monkeypatch
    ):
        # First-ever deploy of a server (no prior manifest) must be `create`,
        # never `pre-existing`.
        config = _remote_mcp_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        _patch_pull_push(monkeypatch)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin", lambda *a, **kw: None
        )

        actions = deploy(config)

        mcp_actions = [a for a in actions if a.item_type == "mcp"]
        assert mcp_actions
        assert all(a.action != "pre-existing" for a in mcp_actions)
        assert any(a.action == "create" for a in mcp_actions)
