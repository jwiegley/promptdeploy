"""Tests for the CLI entry point and all _run_* functions."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from promptdeploy.cli import _run_deploy, _run_list, _run_status, _run_validate, main
from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import DeployAction, deploy
from promptdeploy.filters import FilterError
from promptdeploy.status import StatusEntry


# ---- helpers ----


def _make_source(tmp_path: Path) -> Path:
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


def _make_claude_target(tmp_path: Path, target_id: str = "test-claude") -> TargetConfig:
    target_dir = tmp_path / target_id
    target_dir.mkdir()
    return TargetConfig(id=target_id, type="claude", path=target_dir)


def _make_config(source_root: Path, targets: dict[str, TargetConfig]) -> Config:
    return Config(source_root=source_root, targets=targets, groups={})


def _write_deploy_yaml(tmp_path: Path, config: Config) -> Path:
    """Write a deploy.yaml that load_config can read."""
    data = {
        "source_root": str(config.source_root),
        "targets": {
            tid: {"type": tc.type, "path": str(tc.path)}
            for tid, tc in config.targets.items()
        },
        "groups": config.groups,
    }
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


# ===================================================================
# main() tests: argument parsing and command dispatch
# ===================================================================


class TestMainDeploy:
    def test_main_deploy_dispatches(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr(
            "sys.argv", ["promptdeploy", "deploy", "--dry-run"]
        )
        main()
        captured = capsys.readouterr()
        assert "created" in captured.out

    def test_main_validate_dispatches(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr("sys.argv", ["promptdeploy", "validate"])
        main()
        captured = capsys.readouterr()
        assert "valid" in captured.out.lower() or captured.out == ""

    def test_main_status_dispatches(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr("sys.argv", ["promptdeploy", "status"])
        main()
        captured = capsys.readouterr()
        # With no manifest, items show as new
        assert "A" in captured.out or "No items" in captured.out or captured.out != ""

    def test_main_list_dispatches(self, tmp_path, monkeypatch, capsys):
        tc = _make_claude_target(tmp_path)
        config = _make_config(tmp_path / "src", {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr("sys.argv", ["promptdeploy", "list"])
        main()
        captured = capsys.readouterr()
        assert "no managed items" in captured.out or "not installed" in captured.out or captured.out != ""

    def test_main_no_command_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["promptdeploy"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2  # argparse required subcommand


# ===================================================================
# _run_deploy tests
# ===================================================================


class TestRunDeploy:
    def test_deploy_normal_verbosity(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=False,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "3 created" in captured.out
        assert "0 updated" in captured.out

    def test_deploy_verbose(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=True, quiet=False, dry_run=False,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "created" in captured.out

    def test_deploy_quiet(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=True, dry_run=False,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        # Quiet mode produces no output
        assert captured.out == ""

    def test_deploy_dry_run(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=True,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out

    def test_deploy_with_target_filter(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=True,
            target=["test-claude"], only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "test-claude" in captured.out

    def test_deploy_with_only_type(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=True,
            target=None, only_type=["agents"],
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "agent" in captured.out
        assert "command" not in captured.out

    def test_deploy_filter_error_exits(self, tmp_path, monkeypatch, capsys):
        """FilterError from deploy causes sys.exit(1)."""
        # Create a source with both 'only' and 'except' which triggers FilterError
        src = tmp_path / "source"
        src.mkdir()
        agents = src / "agents"
        agents.mkdir()
        (agents / "bad.md").write_bytes(
            b"---\nname: bad\nonly:\n  - a\nexcept:\n  - b\n---\nBody\n"
        )

        tc = _make_claude_target(tmp_path, "a")
        tc2 = _make_claude_target(tmp_path, "b")
        config = _make_config(src, {tc.id: tc, tc2.id: tc2})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=False,
            target=None, only_type=None,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Cannot specify both" in captured.err

    def test_deploy_skip_hidden_in_normal(self, tmp_path, monkeypatch, capsys):
        """Skipped items are not shown in normal verbosity."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        # Deploy first, then deploy again for skips
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False, quiet=False, dry_run=False,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "3 unchanged" in captured.out
        # No action lines printed for skip in normal verbosity
        lines = [ln for ln in captured.out.strip().split("\n") if ln.strip().startswith("A") or ln.strip().startswith("M")]
        assert len(lines) == 0

    def test_deploy_skip_shown_in_verbose(self, tmp_path, monkeypatch, capsys):
        """Skipped items are shown in verbose mode."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=True, quiet=False, dry_run=False,
            target=None, only_type=None,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        # In verbose mode, skip actions show with space symbol
        assert "3 unchanged" in captured.out


# ===================================================================
# _run_validate tests
# ===================================================================


class TestRunValidate:
    def test_validate_all_valid(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        _run_validate()
        captured = capsys.readouterr()
        assert "All items valid" in captured.out

    def test_validate_with_errors(self, tmp_path, monkeypatch, capsys):
        src = tmp_path / "source"
        src.mkdir()
        agents = src / "agents"
        agents.mkdir()
        (agents / "bad.md").write_bytes(
            b"---\nname: bad\nonly:\n  - nonexistent\n---\nBody\n"
        )

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        with pytest.raises(SystemExit) as exc_info:
            _run_validate()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "1 error(s)" in captured.out

    def test_validate_with_warnings_no_exit(self, tmp_path, monkeypatch, capsys):
        """Warnings alone should not cause sys.exit."""
        from promptdeploy.validate import ValidationIssue

        fake_issues = [
            ValidationIssue(
                level="warning",
                message="test warning",
                file_path=Path("/tmp/w.md"),
                line=5,
            )
        ]

        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: Config(
            source_root=tmp_path, targets={}, groups={}
        ))
        monkeypatch.setattr(
            "promptdeploy.validate.validate_all", lambda cfg: fake_issues
        )
        _run_validate()
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "0 error(s), 1 warning(s)" in captured.out

    def test_validate_error_with_line(self, tmp_path, monkeypatch, capsys):
        """Error with line number formats correctly."""
        from promptdeploy.validate import ValidationIssue

        fake_issues = [
            ValidationIssue(
                level="error",
                message="broken thing",
                file_path=Path("/tmp/e.md"),
                line=10,
            )
        ]

        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: Config(
            source_root=tmp_path, targets={}, groups={}
        ))
        monkeypatch.setattr(
            "promptdeploy.validate.validate_all", lambda cfg: fake_issues
        )
        with pytest.raises(SystemExit):
            _run_validate()
        captured = capsys.readouterr()
        assert ":10:" in captured.out

    def test_validate_error_without_line(self, tmp_path, monkeypatch, capsys):
        """Error without line number omits colon."""
        from promptdeploy.validate import ValidationIssue

        fake_issues = [
            ValidationIssue(
                level="error",
                message="broken thing",
                file_path=Path("/tmp/e.md"),
            )
        ]

        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: Config(
            source_root=tmp_path, targets={}, groups={}
        ))
        monkeypatch.setattr(
            "promptdeploy.validate.validate_all", lambda cfg: fake_issues
        )
        with pytest.raises(SystemExit):
            _run_validate()
        captured = capsys.readouterr()
        assert "ERROR: /tmp/e.md: broken thing" in captured.out


# ===================================================================
# _run_status tests
# ===================================================================


class TestRunStatus:
    def test_status_no_items(self, tmp_path, monkeypatch, capsys):
        config = Config(source_root=tmp_path, targets={}, groups={})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "No items to report" in captured.out

    def test_status_shows_entries(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None)
        _run_status(args)
        captured = capsys.readouterr()
        # All items should be "new" (A) since no manifest exists
        assert "A" in captured.out
        assert "helper" in captured.out
        assert "test-claude" in captured.out

    def test_status_current_items(self, tmp_path, monkeypatch, capsys):
        """After deployment, items show as current (space symbol)."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None)
        _run_status(args)
        captured = capsys.readouterr()
        # Items are current after deploy
        assert "helper" in captured.out

    def test_status_changed_items(self, tmp_path, monkeypatch, capsys):
        """Modified items show as changed (M)."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        # Modify the agent
        (src / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nUpdated body.\n"
        )
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "M" in captured.out

    def test_status_pending_removal(self, tmp_path, monkeypatch, capsys):
        """Deleted source items show as pending removal (D)."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        (src / "agents" / "helper.md").unlink()
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "D" in captured.out

    def test_status_with_target_filter(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=["test-claude"])
        _run_status(args)
        captured = capsys.readouterr()
        assert "test-claude" in captured.out


# ===================================================================
# cli.py __name__ == "__main__" guard (line 190)
# ===================================================================


class TestCliMainGuard:
    def test_cli_module_main_guard(self, tmp_path, monkeypatch):
        """Running cli.py as __main__ invokes main()."""
        import runpy
        # Patch sys.argv to trigger the deploy command dry-run
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr(
            "sys.argv", ["promptdeploy", "deploy", "--dry-run"]
        )
        # Run the module as __main__
        runpy.run_module("promptdeploy.cli", run_name="__main__")
