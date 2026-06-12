"""Tests for the Claude Code target implementation."""

import json

from pathlib import Path

import pytest

from promptdeploy.frontmatter import parse_frontmatter
from promptdeploy.manifest import MANIFEST_FILENAME
from promptdeploy.targets.claude import ClaudeTarget


def _make_target(tmp_path: Path) -> ClaudeTarget:
    config = tmp_path / ".claude"
    config.mkdir()
    return ClaudeTarget("my-target", config)


# ------------------------------------------------------------------
# Agents
# ------------------------------------------------------------------


class TestDeployAgent:
    def test_creates_file_with_transformed_content(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: helper\nonly:\n  - other\n---\nAgent body.\n"
        target.deploy_agent("helper", content)

        dest = tmp_path / ".claude" / "agents" / "helper.md"
        assert dest.exists()
        meta, body = parse_frontmatter(dest.read_bytes())
        assert meta is not None
        assert "only" not in meta
        assert meta["name"] == "helper"
        assert body == b"Agent body.\n"

    def test_creates_agents_directory(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("a", b"plain content")
        assert (tmp_path / ".claude" / "agents" / "a.md").exists()


class TestClaudeTargetModelInjection:
    def test_constructor_without_model_is_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        # Two-argument form must continue to work — no model, no injection.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config)
        target.deploy_agent("a", b"---\nname: a\n---\nBody.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert meta is not None
        assert "model" not in meta

    def test_agent_frontmatter_gets_injected_model(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent("a", b"---\nname: a\n---\nBody.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert meta is not None
        assert meta["model"] == "claude-opus-4-7"

    def test_agent_existing_model_is_overwritten(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent("a", b"---\nname: a\nmodel: sonnet\n---\nBody.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert meta is not None
        assert meta["model"] == "claude-opus-4-7"

    def test_agent_no_frontmatter_is_unchanged(self, tmp_path: Path) -> None:
        # Source files without frontmatter are written as-is.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent("plain", b"Plain body, no frontmatter.\n")
        assert (
            tmp_path / ".claude" / "agents" / "plain.md"
        ).read_bytes() == b"Plain body, no frontmatter.\n"

    def test_skill_md_gets_injected_model(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")

        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"---\nname: s\n---\nSkill body.\n")

        target.deploy_skill("s", src)

        deployed_md = tmp_path / ".claude" / "skills" / "s" / "SKILL.md"
        meta, _ = parse_frontmatter(deployed_md.read_bytes())
        assert meta is not None
        assert meta["model"] == "claude-opus-4-7"

    def test_command_is_not_injected(self, tmp_path: Path) -> None:
        # Commands must never receive the injected model field.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_command("fix", b"---\nname: fix\n---\nFix things.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "commands" / "fix.md").read_bytes()
        )
        assert meta is not None
        assert "model" not in meta


class TestRemoveAgent:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("a", b"content")
        target.remove_agent("a")
        assert not (tmp_path / ".claude" / "agents" / "a.md").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_agent("nonexistent")  # should not raise


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------


class TestDeployCommand:
    def test_creates_file(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: fix\nexcept:\n  - x\n---\nFix things.\n"
        target.deploy_command("fix", content)

        dest = tmp_path / ".claude" / "commands" / "fix.md"
        assert dest.exists()
        meta, body = parse_frontmatter(dest.read_bytes())
        assert meta is not None
        assert "except" not in meta
        assert body == b"Fix things.\n"


class TestDeployPrompt:
    def test_poet_renders_to_command_md(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = (
            b"- role: system\n  content: Be concise.\n"
            b"- role: user\n  content: Hi\n"
            b"- role: assistant\n  content: Hello\n"
        )
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)

        dest = tmp_path / ".claude" / "commands" / "demo.md"
        assert dest.exists()
        text = dest.read_text()
        assert "<instructions>\nBe concise.\n</instructions>" in text
        assert "<user>\nHi\n</user>" in text
        assert "<assistant>\nHello\n</assistant>" in text
        assert "<task>\n$ARGUMENTS\n</task>" in text

    def test_plain_md_wraps_as_system_only(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        body = b"# Plain heading\nbody\n"
        src.write_bytes(body)
        target.deploy_prompt("doc", body, src)
        text = (tmp_path / ".claude" / "commands" / "doc.md").read_text()
        assert "<instructions>" in text
        assert "Plain heading" in text

    def test_undefined_var_recorded_as_warning(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: 'hi {{ missing }}'\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        assert target.consume_warnings() == [
            ("demo", ["Undefined Jinja variable: missing"])
        ]


class TestRemovePrompt:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        target.remove_prompt("demo")
        assert not (tmp_path / ".claude" / "commands" / "demo.md").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_prompt("nonexistent")


class TestItemExistsPrompt:
    def test_prompt_treats_commands_dir(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("prompt", "demo")
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        assert target.item_exists("prompt", "demo")


class TestRemoveCommand:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_command("fix", b"content")
        target.remove_command("fix")
        assert not (tmp_path / ".claude" / "commands" / "fix.md").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_command("nonexistent")


# ------------------------------------------------------------------
# Skills
# ------------------------------------------------------------------


class TestDeploySkill:
    def test_copies_directory_and_transforms_skill_md(self, tmp_path: Path):
        target = _make_target(tmp_path)

        # Create source skill directory.
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(
            b"---\nname: my-skill\nonly:\n  - t\n---\nSkill body.\n"
        )
        (src / "helper.py").write_text("print('hi')")

        target.deploy_skill("my-skill", src)

        dest = tmp_path / ".claude" / "skills" / "my-skill"
        assert dest.is_dir()
        assert (dest / "helper.py").read_text() == "print('hi')"

        meta, body = parse_frontmatter((dest / "SKILL.md").read_bytes())
        assert meta is not None
        assert "only" not in meta
        assert meta["name"] == "my-skill"
        assert body == b"Skill body.\n"

    def test_resolves_symlinks(self, tmp_path: Path):
        target = _make_target(tmp_path)

        real_file = tmp_path / "real.txt"
        real_file.write_text("real content")

        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "link.txt").symlink_to(real_file)

        target.deploy_skill("linked", src)

        deployed = tmp_path / ".claude" / "skills" / "linked" / "link.txt"
        assert deployed.exists()
        assert not deployed.is_symlink()
        assert deployed.read_text() == "real content"

    def test_overwrites_symlinked_skill(self, tmp_path: Path):
        target = _make_target(tmp_path)

        # Create a symlink where the skill directory would be deployed.
        real_dir = tmp_path / "real-skill"
        real_dir.mkdir()
        (real_dir / "SKILL.md").write_bytes(b"old")

        dest = tmp_path / ".claude" / "skills" / "s"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(real_dir)

        src = tmp_path / "new-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"new")

        target.deploy_skill("s", src)
        assert dest.is_dir()
        assert not dest.is_symlink()
        assert (dest / "SKILL.md").read_bytes() == b"new"

    def test_overwrites_existing_skill(self, tmp_path: Path):
        target = _make_target(tmp_path)

        src_v1 = tmp_path / "v1"
        src_v1.mkdir()
        (src_v1 / "SKILL.md").write_bytes(b"v1")

        src_v2 = tmp_path / "v2"
        src_v2.mkdir()
        (src_v2 / "SKILL.md").write_bytes(b"v2")

        target.deploy_skill("s", src_v1)
        target.deploy_skill("s", src_v2)
        assert (
            tmp_path / ".claude" / "skills" / "s" / "SKILL.md"
        ).read_bytes() == b"v2"


class TestRemoveSkill:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"body")
        target.deploy_skill("s", src)
        target.remove_skill("s")
        assert not (tmp_path / ".claude" / "skills" / "s").exists()

    def test_removes_symlinked_skill(self, tmp_path: Path):
        target = _make_target(tmp_path)
        real_dir = tmp_path / "real-skill"
        real_dir.mkdir()

        dest = tmp_path / ".claude" / "skills" / "s"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(real_dir)

        target.remove_skill("s")
        assert not dest.exists()
        assert not dest.is_symlink()
        # Original directory should be untouched.
        assert real_dir.is_dir()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_skill("nonexistent")


# ------------------------------------------------------------------
# Models (no-op for Claude)
# ------------------------------------------------------------------


class TestDeployModels:
    def test_deploy_models_is_noop(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # deploy_models should not create any files or raise
        target.deploy_models({"providers": {"acme": {"models": {"m": {}}}}})
        # No settings.json should be created
        assert not (tmp_path / ".claude" / "settings.json").exists()


class TestRemoveModelsNoop:
    def test_remove_models_is_noop(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # remove_models should not raise even with no prior deploy
        target.remove_models()


# ------------------------------------------------------------------
# MCP Servers
# ------------------------------------------------------------------


class TestDeployMcpServer:
    def test_merges_into_settings_json(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Pre-populate settings with another key.
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"allowedTools": ["Edit"]}))

        config = {
            "name": "my-server",
            "description": "A server",
            "scope": "project",
            "enabled": True,
            "command": "npx",
            "args": ["-y", "my-server"],
            "env": {"API_KEY": "xxx"},
        }
        target.deploy_mcp_server("my-server", config)

        result = json.loads(settings_path.read_text())
        assert result["allowedTools"] == ["Edit"]
        assert "my-server" in result["mcpServers"]
        srv = result["mcpServers"]["my-server"]
        assert srv == {
            "command": "npx",
            "args": ["-y", "my-server"],
            "env": {"API_KEY": "xxx"},
        }
        # Deployment metadata stripped.
        for key in ("name", "description", "scope", "enabled", "only", "except"):
            assert key not in srv

    def test_creates_settings_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo", "args": []})

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        result = json.loads(settings_path.read_text())
        assert result["mcpServers"]["srv"] == {"command": "echo", "args": []}

    def test_disabled_server_not_written(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo", "enabled": False})

        settings_path = tmp_path / ".claude" / "settings.json"
        result = json.loads(settings_path.read_text())
        assert "srv" not in result.get("mcpServers", {})

    def test_disabled_server_removed_if_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Deploy first, then disable.
        target.deploy_mcp_server("srv", {"command": "echo"})
        target.deploy_mcp_server("srv", {"command": "echo", "enabled": False})

        result = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "srv" not in result.get("mcpServers", {})

    def test_preserves_other_servers(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"mcpServers": {"existing": {"command": "keep"}}})
        )

        target.deploy_mcp_server("new", {"command": "added"})

        result = json.loads(settings_path.read_text())
        assert result["mcpServers"]["existing"] == {"command": "keep"}
        assert result["mcpServers"]["new"] == {"command": "added"}

    def test_expands_env_vars_in_env_dict(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret123")
        target = _make_target(tmp_path)
        config = {
            "command": "npx",
            "args": ["-y", "server"],
            "env": {"API_KEY": "${MY_API_KEY}"},
        }
        target.deploy_mcp_server("srv", config)

        settings_path = tmp_path / ".claude" / "settings.json"
        result = json.loads(settings_path.read_text())
        assert result["mcpServers"]["srv"]["env"]["API_KEY"] == "secret123"

    def test_non_env_keys_not_affected_by_expansion(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "expanded")
        target = _make_target(tmp_path)
        config = {
            "command": "${SOME_VAR}",
            "env": {"KEY": "${SOME_VAR}"},
        }
        target.deploy_mcp_server("srv", config)

        settings_path = tmp_path / ".claude" / "settings.json"
        result = json.loads(settings_path.read_text())
        # command is NOT in env sub-dict, so it stays literal
        assert result["mcpServers"]["srv"]["command"] == "${SOME_VAR}"
        # env values ARE expanded
        assert result["mcpServers"]["srv"]["env"]["KEY"] == "expanded"

    def test_unset_env_vars_preserved(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("UNSET_KEY_XYZ", raising=False)
        target = _make_target(tmp_path)
        config = {
            "command": "echo",
            "env": {"KEY": "${UNSET_KEY_XYZ}"},
        }
        target.deploy_mcp_server("srv", config)

        settings_path = tmp_path / ".claude" / "settings.json"
        result = json.loads(settings_path.read_text())
        assert result["mcpServers"]["srv"]["env"]["KEY"] == "${UNSET_KEY_XYZ}"

    def test_no_env_key_no_expansion(self, tmp_path: Path):
        target = _make_target(tmp_path)
        config = {"command": "echo", "args": ["hello"]}
        target.deploy_mcp_server("srv", config)

        settings_path = tmp_path / ".claude" / "settings.json"
        result = json.loads(settings_path.read_text())
        assert result["mcpServers"]["srv"] == {"command": "echo", "args": ["hello"]}


class TestRemoveMcpServer:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo"})
        target.remove_mcp_server("srv")

        result = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "srv" not in result.get("mcpServers", {})

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_mcp_server("nonexistent")

    def test_no_error_if_settings_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Remove auto-created .claude dir settings
        target.remove_mcp_server("anything")


# ------------------------------------------------------------------
# Properties / metadata
# ------------------------------------------------------------------


class TestSaveJsonError:
    def test_cleanup_on_replace_failure(self, tmp_path: Path):
        """When os.replace fails in _save_json, temp file is cleaned up."""
        from unittest.mock import patch

        target = _make_target(tmp_path)

        with patch("os.replace", side_effect=OSError("mock failure")):
            with pytest.raises(OSError, match="mock failure"):
                target.deploy_mcp_server("srv", {"command": "echo"})

        # No temp files left behind
        config_dir = tmp_path / ".claude"
        tmp_files = list(config_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_cleanup_on_replace_failure_unlink_also_fails(self, tmp_path: Path):
        """When both os.replace and os.unlink fail, original error propagates."""
        import os
        from unittest.mock import patch

        target = _make_target(tmp_path)

        original_unlink = os.unlink

        def failing_unlink(p):
            if str(p).endswith(".tmp"):
                raise OSError("unlink failed")
            return original_unlink(p)

        with patch("os.replace", side_effect=OSError("replace failed")):
            with patch("os.unlink", side_effect=failing_unlink):
                with pytest.raises(OSError, match="replace failed"):
                    target.deploy_mcp_server("srv", {"command": "echo"})


class TestAtomicArtifactWrites:
    """Agent/command/prompt .md files and skill directories are written via
    temp + os.replace so a failure never leaves a partial artifact."""

    def test_agent_write_failure_leaves_no_temp_or_partial_file(self, tmp_path: Path):
        from unittest.mock import patch

        target = _make_target(tmp_path)
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                target.deploy_agent("helper", b"---\nname: helper\n---\nBody.\n")

        agents_dir = tmp_path / ".claude" / "agents"
        assert list(agents_dir.glob("*.tmp")) == []
        assert not (agents_dir / "helper.md").exists()

    def test_agent_write_failure_unlink_also_fails(self, tmp_path: Path):
        import os
        from unittest.mock import patch

        target = _make_target(tmp_path)
        original_unlink = os.unlink

        def failing_unlink(p):
            if str(p).endswith(".tmp"):
                raise OSError("unlink failed")
            return original_unlink(p)

        with patch("os.replace", side_effect=OSError("replace failed")):
            with patch("os.unlink", side_effect=failing_unlink):
                with pytest.raises(OSError, match="replace failed"):
                    target.deploy_agent("helper", b"Body.\n")

    def test_agent_write_replaces_symlink(self, tmp_path: Path):
        """An existing symlink at the destination is replaced by the rename,
        never written through."""
        target = _make_target(tmp_path)
        real = tmp_path / "real.md"
        real.write_bytes(b"original")
        dest = tmp_path / ".claude" / "agents" / "helper.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(real)

        target.deploy_agent("helper", b"---\nname: helper\n---\nBody.\n")

        assert not dest.is_symlink()
        assert real.read_bytes() == b"original"

    def test_command_write_is_atomic(self, tmp_path: Path):
        from unittest.mock import patch

        target = _make_target(tmp_path)
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                target.deploy_command("fix", b"Body.\n")
        commands_dir = tmp_path / ".claude" / "commands"
        assert list(commands_dir.glob("*.tmp")) == []

    def test_prompt_write_is_atomic(self, tmp_path: Path):
        from unittest.mock import patch

        target = _make_target(tmp_path)
        src = tmp_path / "demo.md"
        src.write_bytes(b"Prompt body.\n")
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                target.deploy_prompt("demo", b"Prompt body.\n", src)
        commands_dir = tmp_path / ".claude" / "commands"
        assert list(commands_dir.glob("*.tmp")) == []

    def test_skill_deploy_leaves_no_staging_dir(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"---\nname: s\n---\nBody.\n")

        target.deploy_skill("s", src)

        skills_dir = tmp_path / ".claude" / "skills"
        assert sorted(p.name for p in skills_dir.iterdir()) == ["s"]

    def test_skill_copy_failure_keeps_previous_deploy(self, tmp_path: Path):
        """If staging fails, the previously deployed skill is untouched and
        no staging leftovers remain."""
        from unittest.mock import patch

        target = _make_target(tmp_path)
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"v1")
        target.deploy_skill("s", src)

        (src / "SKILL.md").write_bytes(b"v2")
        with patch("shutil.copytree", side_effect=OSError("copy failed")):
            with pytest.raises(OSError, match="copy failed"):
                target.deploy_skill("s", src)

        skills_dir = tmp_path / ".claude" / "skills"
        assert sorted(p.name for p in skills_dir.iterdir()) == ["s"]
        assert (skills_dir / "s" / "SKILL.md").read_bytes() != b"v2"


class TestItemExists:
    def test_agent_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("agent", "a")
        target.deploy_agent("a", b"content")
        assert target.item_exists("agent", "a")

    def test_command_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("command", "c")
        target.deploy_command("c", b"content")
        assert target.item_exists("command", "c")

    def test_skill_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("skill", "s")
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"body")
        target.deploy_skill("s", src)
        assert target.item_exists("skill", "s")

    def test_skill_symlink_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        real_dir = tmp_path / "real-skill"
        real_dir.mkdir()
        dest = tmp_path / ".claude" / "skills" / "s"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(real_dir)
        assert target.item_exists("skill", "s")

    def test_mcp_not_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("mcp", "srv")

    def test_mcp_exists_after_deploy(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"name": "srv", "command": "echo"})
        assert target.item_exists("mcp", "srv")

    def test_hook_not_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("hook", "myhook")

    def test_hook_exists_after_deploy(self, tmp_path: Path):
        target = _make_target(tmp_path)
        config = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [{"command": "echo", "type": "command"}],
                    }
                ]
            }
        }
        target.deploy_hook("myhook", config)
        assert target.item_exists("hook", "myhook")

    def test_hook_not_exists_different_source(self, tmp_path: Path):
        target = _make_target(tmp_path)
        config = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [{"command": "echo", "type": "command"}],
                    }
                ]
            }
        }
        target.deploy_hook("other", config)
        assert not target.item_exists("hook", "myhook")

    def test_models_returns_false(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("models", "m")


class TestShouldSkip:
    def test_skips_models(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.should_skip("models", "models") is True

    def test_does_not_skip_agents(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.should_skip("agent", "helper") is False

    def test_does_not_skip_commands(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.should_skip("command", "fix") is False

    def test_does_not_skip_hooks(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.should_skip("hook", "my-hook") is False


class TestTargetProperties:
    def test_id(self, tmp_path: Path):
        target = ClaudeTarget("my-id", tmp_path)
        assert target.id == "my-id"

    def test_exists_true(self, tmp_path: Path):
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("t", config)
        assert target.exists()

    def test_exists_false(self, tmp_path: Path):
        target = ClaudeTarget("t", tmp_path / "nonexistent")
        assert not target.exists()

    def test_manifest_path(self, tmp_path: Path):
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("t", config)
        assert target.manifest_path() == config / MANIFEST_FILENAME

    def test_rsync_includes(self, tmp_path: Path):
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("t", config)
        includes = target.rsync_includes()
        assert includes is not None
        assert "agents/" in includes
        assert "agents/**" in includes
        assert "commands/" in includes
        assert "settings.json" in includes
        assert MANIFEST_FILENAME in includes


# ------------------------------------------------------------------
# would_deploy_bytes / read_deployed_bytes
# ------------------------------------------------------------------


class TestWouldDeployBytes:
    def test_agent_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: helper\n---\nAgent body.\n"
        # Bytes produced by would_deploy_bytes must equal what deploy_agent
        # writes; otherwise the adoption check would falsely flag managed
        # files as pre-existing on every run.
        target.deploy_agent("helper", content)
        on_disk = (tmp_path / ".claude" / "agents" / "helper.md").read_bytes()
        assert target.would_deploy_bytes("agent", "helper", content) == on_disk

    def test_command_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: fix\n---\nFix things.\n"
        target.deploy_command("fix", content)
        on_disk = (tmp_path / ".claude" / "commands" / "fix.md").read_bytes()
        assert target.would_deploy_bytes("command", "fix", content) == on_disk

    def test_prompt_poet_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: Be concise.\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        on_disk = (tmp_path / ".claude" / "commands" / "demo.md").read_bytes()
        assert target.would_deploy_bytes("prompt", "demo", body, src) == on_disk

    def test_prompt_plain_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        body = b"Plain.\n"
        src.write_bytes(body)
        target.deploy_prompt("doc", body, src)
        on_disk = (tmp_path / ".claude" / "commands" / "doc.md").read_bytes()
        assert target.would_deploy_bytes("prompt", "doc", body, src) == on_disk

    def test_returns_none_for_directory_artifacts(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Skills, MCP, hooks, models are not single-file artifacts.
        assert target.would_deploy_bytes("skill", "x", b"") is None
        assert target.would_deploy_bytes("mcp", "x", b"") is None
        assert target.would_deploy_bytes("hook", "x", b"") is None
        assert target.would_deploy_bytes("models", "x", b"") is None


class TestReadDeployedBytes:
    def test_agent_returns_on_disk_bytes(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("helper", b"---\nname: helper\n---\nBody.\n")
        assert target.read_deployed_bytes("agent", "helper") is not None

    def test_command_returns_on_disk_bytes(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_command("fix", b"---\nname: fix\n---\nFix.\n")
        assert target.read_deployed_bytes("command", "fix") is not None

    def test_prompt_returns_on_disk_bytes(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        src.write_bytes(b"Plain.\n")
        target.deploy_prompt("doc", b"Plain.\n", src)
        assert target.read_deployed_bytes("prompt", "doc") is not None

    def test_returns_none_when_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.read_deployed_bytes("agent", "missing") is None
        assert target.read_deployed_bytes("command", "missing") is None

    def test_returns_none_for_directory_artifacts(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Skills/MCP/hooks/models report None.
        assert target.read_deployed_bytes("skill", "x") is None
        assert target.read_deployed_bytes("mcp", "x") is None
        assert target.read_deployed_bytes("hook", "x") is None
        assert target.read_deployed_bytes("models", "x") is None


class TestDeploySettings:
    def _seed(self, tmp_path: Path, data: dict) -> ClaudeTarget:
        target = _make_target(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(data))
        return target

    def test_creates_file_when_absent(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_settings({"effortLevel": "low"}, [])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data == {"effortLevel": "low"}

    def test_merges_without_touching_hooks_or_mcp(self, tmp_path: Path):
        target = self._seed(
            tmp_path,
            {
                "hooks": {"Stop": [{"_source": "claude-vault"}]},
                "mcpServers": {"pal": {"command": "x"}},
                "model": "opus",
            },
        )
        target.deploy_settings({"effortLevel": "high", "model": "sonnet"}, ["model"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["hooks"] == {"Stop": [{"_source": "claude-vault"}]}
        assert data["mcpServers"] == {"pal": {"command": "x"}}
        assert data["effortLevel"] == "high"
        assert data["model"] == "sonnet"

    def test_removes_previously_managed_key_dropped_from_render(self, tmp_path: Path):
        target = self._seed(tmp_path, {"model": "sonnet", "env": {"A": "1"}})
        # Previously managed {model, env}; now render only {env}. model must go.
        target.deploy_settings({"env": {"A": "1"}}, ["model", "env"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "model" not in data
        assert data["env"] == {"A": "1"}

    def test_preserves_unmanaged_keys(self, tmp_path: Path):
        target = self._seed(tmp_path, {"feedbackSurveyState": {"x": 1}})
        target.deploy_settings({"effortLevel": "low"}, [])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["feedbackSurveyState"] == {"x": 1}
        assert data["effortLevel"] == "low"


class TestRemoveAndReadSettings:
    def test_remove_settings_pops_keys_preserving_rest(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"model": "x", "env": {"A": "1"}, "hooks": {"Y": 1}})
        )
        target.remove_settings(["model", "env"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data == {"hooks": {"Y": 1}}

    def test_remove_settings_no_file_is_noop(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_settings(["model"])  # must not raise
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_read_settings_json_returns_dict_or_empty(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.read_settings_json() == {}
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "x"}))
        assert target.read_settings_json() == {"model": "x"}


class TestLoadJsonError:
    def test_corrupt_settings_json_raises_clear_error(self, tmp_path: Path):
        """A corrupt settings.json raises JsonConfigError naming the file."""
        from promptdeploy.targets.claude import JsonConfigError

        target = _make_target(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text("{not json")
        with pytest.raises(JsonConfigError, match="settings.json"):
            target.read_settings_json()

    def test_json_config_error_is_value_error(self):
        from promptdeploy.targets.claude import JsonConfigError

        assert issubclass(JsonConfigError, ValueError)
