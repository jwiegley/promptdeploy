"""Tests for the targets package __init__ module."""

from pathlib import Path

import pytest

from promptdeploy.config import TargetConfig
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.opencode import OpenCodeTarget


class TestCreateTarget:
    def test_creates_claude_target(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="t", type="claude", path=tmp_path)
        target = create_target(tc)
        assert isinstance(target, ClaudeTarget)

    def test_creates_droid_target(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="t", type="droid", path=tmp_path)
        target = create_target(tc)
        assert isinstance(target, DroidTarget)

    def test_creates_opencode_target(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="t", type="opencode", path=tmp_path)
        target = create_target(tc)
        assert isinstance(target, OpenCodeTarget)

    def test_unknown_target_type_raises(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="t", type="unknown_tool", path=tmp_path)
        with pytest.raises(ValueError, match="Unknown target type: unknown_tool"):
            create_target(tc)
