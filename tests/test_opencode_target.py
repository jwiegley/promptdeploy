"""Tests for the OpenCode target implementation."""

import json
from pathlib import Path

import pytest

from promptdeploy.frontmatter import parse_frontmatter
from promptdeploy.manifest import MANIFEST_FILENAME
from promptdeploy.targets.opencode import OpenCodeTarget


def _make_target(tmp_path: Path) -> OpenCodeTarget:
    config = tmp_path / ".opencode"
    config.mkdir()
    return OpenCodeTarget("my-target", config)


# ------------------------------------------------------------------
# Agents
# ------------------------------------------------------------------


class TestDeployAgent:
    def test_creates_file_with_transformed_content(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: helper\nonly:\n  - other\n---\nAgent body.\n"
        target.deploy_agent("helper", content)

        dest = tmp_path / ".opencode" / "agents" / "helper.md"
        assert dest.exists()
        meta, body = parse_frontmatter(dest.read_bytes())
        assert meta is not None
        assert "only" not in meta
        assert meta["name"] == "helper"
        assert body == b"Agent body.\n"

    def test_creates_agents_directory(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("a", b"plain content")
        assert (tmp_path / ".opencode" / "agents" / "a.md").exists()


class TestRemoveAgent:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("a", b"content")
        target.remove_agent("a")
        assert not (tmp_path / ".opencode" / "agents" / "a.md").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_agent("nonexistent")


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------


class TestDeployCommand:
    def test_creates_file(self, tmp_path: Path):
        target = _make_target(tmp_path)
        content = b"---\nname: fix\nexcept:\n  - x\n---\nFix things.\n"
        target.deploy_command("fix", content)

        dest = tmp_path / ".opencode" / "commands" / "fix.md"
        assert dest.exists()
        meta, body = parse_frontmatter(dest.read_bytes())
        assert "except" not in meta
        assert body == b"Fix things.\n"


class TestRemoveCommand:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_command("fix", b"content")
        target.remove_command("fix")
        assert not (tmp_path / ".opencode" / "commands" / "fix.md").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_command("nonexistent")


# ------------------------------------------------------------------
# Skills
# ------------------------------------------------------------------


class TestDeploySkill:
    def test_copies_directory_and_transforms_skill_md(self, tmp_path: Path):
        target = _make_target(tmp_path)

        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(
            b"---\nname: my-skill\nonly:\n  - t\n---\nSkill body.\n"
        )
        (src / "helper.py").write_text("print('hi')")

        target.deploy_skill("my-skill", src)

        dest = tmp_path / ".opencode" / "skills" / "my-skill"
        assert dest.is_dir()
        assert (dest / "helper.py").read_text() == "print('hi')"

        meta, body = parse_frontmatter((dest / "SKILL.md").read_bytes())
        assert "only" not in meta
        assert meta["name"] == "my-skill"
        assert body == b"Skill body.\n"

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
            tmp_path / ".opencode" / "skills" / "s" / "SKILL.md"
        ).read_bytes() == b"v2"


class TestRemoveSkill:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"body")
        target.deploy_skill("s", src)
        target.remove_skill("s")
        assert not (tmp_path / ".opencode" / "skills" / "s").exists()

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_skill("nonexistent")


# ------------------------------------------------------------------
# MCP Servers
# ------------------------------------------------------------------


class TestDeployMcpServer:
    def test_merges_into_opencode_json(self, tmp_path: Path):
        target = _make_target(tmp_path)

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

        oc_path = tmp_path / ".opencode" / "opencode.json"
        assert oc_path.exists()
        result = json.loads(oc_path.read_text())
        srv = result["mcpServers"]["my-server"]
        # command is an array: command + args combined.
        assert srv["command"] == ["npx", "-y", "my-server"]
        # "environment" key, not "env".
        assert srv["environment"] == {"API_KEY": "xxx"}
        assert "env" not in srv
        assert "args" not in srv
        # Deployment metadata stripped.
        for key in ("name", "description", "scope", "enabled", "only", "except"):
            assert key not in srv

    def test_command_without_args(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo"})

        result = json.loads(
            (tmp_path / ".opencode" / "opencode.json").read_text()
        )
        assert result["mcpServers"]["srv"]["command"] == ["echo"]

    def test_creates_opencode_json_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo", "args": []})

        oc_path = tmp_path / ".opencode" / "opencode.json"
        assert oc_path.exists()
        result = json.loads(oc_path.read_text())
        assert "srv" in result["mcpServers"]

    def test_disabled_server_not_written(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo", "enabled": False})

        oc_path = tmp_path / ".opencode" / "opencode.json"
        result = json.loads(oc_path.read_text())
        assert "srv" not in result.get("mcpServers", {})

    def test_disabled_server_removed_if_exists(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo"})
        target.deploy_mcp_server("srv", {"command": "echo", "enabled": False})

        result = json.loads(
            (tmp_path / ".opencode" / "opencode.json").read_text()
        )
        assert "srv" not in result.get("mcpServers", {})

    def test_no_environment_key_when_env_empty(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo", "env": {}})

        result = json.loads(
            (tmp_path / ".opencode" / "opencode.json").read_text()
        )
        srv = result["mcpServers"]["srv"]
        assert "environment" not in srv

    def test_preserves_other_servers(self, tmp_path: Path):
        target = _make_target(tmp_path)
        oc_path = tmp_path / ".opencode" / "opencode.json"
        oc_path.write_text(
            json.dumps({"mcpServers": {"existing": {"command": ["keep"]}}})
        )

        target.deploy_mcp_server("new", {"command": "added"})

        result = json.loads(oc_path.read_text())
        assert result["mcpServers"]["existing"] == {"command": ["keep"]}
        assert "new" in result["mcpServers"]


class TestRemoveMcpServer:
    def test_removes_existing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_mcp_server("srv", {"command": "echo"})
        target.remove_mcp_server("srv")

        result = json.loads(
            (tmp_path / ".opencode" / "opencode.json").read_text()
        )
        assert "srv" not in result.get("mcpServers", {})

    def test_no_error_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_mcp_server("nonexistent")

    def test_no_error_if_opencode_json_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_mcp_server("anything")


# ------------------------------------------------------------------
# Properties / metadata
# ------------------------------------------------------------------


class TestExtraConfigKeys:
    def test_extra_keys_copied_to_config(self, tmp_path: Path):
        """Non-metadata, non-command/args/env keys are copied to the output."""
        target = _make_target(tmp_path)
        config = {
            "command": "npx",
            "args": ["-y", "server"],
            "custom_key": "custom_value",
            "timeout": 30,
        }
        target.deploy_mcp_server("srv", config)

        oc_path = tmp_path / ".opencode" / "opencode.json"
        result = json.loads(oc_path.read_text())
        srv = result["mcpServers"]["srv"]
        assert srv["custom_key"] == "custom_value"
        assert srv["timeout"] == 30


class TestSaveJsonError:
    def test_cleanup_on_replace_failure(self, tmp_path: Path):
        """When os.replace fails in _save_json, temp file is cleaned up."""
        import os
        from unittest.mock import patch

        target = _make_target(tmp_path)

        with patch("os.replace", side_effect=OSError("mock failure")):
            with pytest.raises(OSError, match="mock failure"):
                target.deploy_mcp_server("srv", {"command": "echo"})

        config_dir = tmp_path / ".opencode"
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


class TestTargetProperties:
    def test_id(self, tmp_path: Path):
        target = OpenCodeTarget("my-id", tmp_path)
        assert target.id == "my-id"

    def test_exists_true(self, tmp_path: Path):
        config = tmp_path / ".opencode"
        config.mkdir()
        target = OpenCodeTarget("t", config)
        assert target.exists()

    def test_exists_false(self, tmp_path: Path):
        target = OpenCodeTarget("t", tmp_path / "nonexistent")
        assert not target.exists()

    def test_manifest_path(self, tmp_path: Path):
        config = tmp_path / ".opencode"
        config.mkdir()
        target = OpenCodeTarget("t", config)
        assert target.manifest_path() == config / MANIFEST_FILENAME
