"""Tests for the target factory in promptdeploy.targets.create_target."""

from pathlib import Path

from promptdeploy.config import TargetConfig
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget


class TestCreateTarget:
    def test_claude_target_without_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc)
        assert isinstance(target, ClaudeTarget)
        assert target._model is None
        assert target._injected is None

    def test_claude_target_with_per_target_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc)
        assert isinstance(target, ClaudeTarget)
        assert target._model == "claude-sonnet-4-6"

    def test_claude_target_with_global_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert isinstance(target, ClaudeTarget)
        assert target._model == "claude-opus-4-7"

    def test_per_target_overrides_global(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc, global_model="claude-opus-4-7")
        assert isinstance(target, ClaudeTarget)
        assert target._model == "claude-sonnet-4-6"

    def test_non_claude_target_does_not_get_model(self, tmp_path: Path) -> None:
        # Droid and OpenCode constructors don't accept `model`.
        from promptdeploy.targets.droid import DroidTarget

        tc = TargetConfig(id="d", type="droid", path=tmp_path / "d")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert isinstance(target, DroidTarget)

    def test_gptel_target_recognized(self, tmp_path: Path) -> None:
        from promptdeploy.targets.gptel import GptelTarget

        tc = TargetConfig(id="g", type="gptel", path=tmp_path / "g")
        target = create_target(tc)
        assert isinstance(target, GptelTarget)
        assert target.id == "g"

    def test_codex_target_recognized(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="cx", type="codex", path=tmp_path / "home")
        target = create_target(tc)
        assert isinstance(target, CodexTarget)
        assert target.id == "cx"

    def test_remote_claude_target_gets_remote_mcp(self, tmp_path: Path) -> None:
        from promptdeploy.targets.remote import RemoteTarget

        tc = TargetConfig(
            id="rc", type="claude", path=tmp_path / "rc", host="user@fakehost"
        )
        target = create_target(tc)
        assert isinstance(target, RemoteTarget)
        assert target.remote_mcp_hash is True

    def test_remote_non_claude_target_no_remote_mcp(self, tmp_path: Path) -> None:
        from promptdeploy.targets.remote import RemoteTarget

        tc = TargetConfig(
            id="ro", type="opencode", path=tmp_path / "ro", host="user@fakehost"
        )
        target = create_target(tc)
        assert isinstance(target, RemoteTarget)
        assert target.remote_mcp_hash is False
