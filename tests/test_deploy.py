"""Integration tests for the deploy orchestration."""

import json
from pathlib import Path


from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import deploy
from promptdeploy.manifest import MANIFEST_FILENAME, load_manifest


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
        # Now deploy only agents
        deploy(config, item_types=["agents"])

        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        # Agent should still be in manifest
        assert "helper" in manifest.items["agents"]
        # Other types should be preserved (not removed)
        assert "fix" in manifest.items["commands"]
        assert "my-skill" in manifest.items["skills"]


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

        # Verify MCP server was written to settings.json
        settings = json.loads((tc.path / "settings.json").read_text())
        assert "my-server" in settings["mcpServers"]

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


class TestHookDeploy:
    """Deploy hook items through the deploy orchestration."""

    def test_deploys_hook(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        hooks_dir = src / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  PostToolUse:\n    - matcher: 'Write'\n      hooks:\n        - command: 'echo hi'\n          type: command\n"
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
            b"name: hook\nhooks:\n  Stop:\n    - matcher: ''\n      hooks:\n        - command: 'echo'\n          type: command\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)

        # Remove hook source
        (hooks_dir / "hook.yaml").unlink()
        actions = deploy(config)

        removes = [a for a in actions if a.action == "remove"]
        assert any(a.name == "hook" and a.item_type == "hook" for a in removes)
