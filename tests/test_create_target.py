"""Tests for the target factory in promptdeploy.targets.create_target."""

from pathlib import Path

from promptdeploy.config import TargetConfig
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget


class TestCreateTarget:
    def test_claude_target_without_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc)
        assert isinstance(target, ClaudeTarget)
        assert target._model is None  # noqa: SLF001
        assert target._injected is None  # noqa: SLF001

    def test_claude_target_with_per_target_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc)
        assert target._model == "claude-sonnet-4-6"  # noqa: SLF001

    def test_claude_target_with_global_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert target._model == "claude-opus-4-7"  # noqa: SLF001

    def test_per_target_overrides_global(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc, global_model="claude-opus-4-7")
        assert target._model == "claude-sonnet-4-6"  # noqa: SLF001

    def test_non_claude_target_does_not_get_model(self, tmp_path: Path) -> None:
        # Droid and OpenCode constructors don't accept `model`.
        from promptdeploy.targets.droid import DroidTarget

        tc = TargetConfig(id="d", type="droid", path=tmp_path / "d")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert isinstance(target, DroidTarget)
