"""Tests for the CLI entry point and all _run_* functions."""

import argparse
from pathlib import Path

import pytest
import yaml

from promptdeploy.cli import (
    _build_parser,
    _load_config_or_exit,
    _run_deploy,
    _run_list,
    _run_status,
    _run_validate,
    main,
)
from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import deploy

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
        monkeypatch.setattr("sys.argv", ["promptdeploy", "deploy", "--dry-run"])
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
        assert (
            "no managed items" in captured.out
            or "not installed" in captured.out
            or captured.out != ""
        )

    def test_main_no_command_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["promptdeploy"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2  # argparse required subcommand


# ===================================================================
# _run_deploy tests
# ===================================================================


class TestRunDeployLoadsDotenv:
    def test_load_dotenv_called_with_source_root(self, tmp_path, monkeypatch, capsys):
        """_run_deploy calls load_dotenv with config.source_root / '.env'."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        calls: list = []
        monkeypatch.setattr(
            "promptdeploy.envsubst.load_dotenv",
            lambda path: calls.append(path),
        )

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        assert len(calls) == 1
        assert calls[0] == src / ".env"


class TestRunDeploy:
    def test_deploy_normal_verbosity(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
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
            verbose=True,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
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
            verbose=False,
            quiet=True,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
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
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
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
            verbose=False,
            quiet=False,
            dry_run=True,
            target=["test-claude"],
            only_type=None,
            target_root=None,
            force=False,
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
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=["agents"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "agent" in captured.out
        assert "command" not in captured.out

    def test_deploy_with_only_type_hooks(self, tmp_path, monkeypatch, capsys):
        """--only-type hooks is a valid choice."""
        src = tmp_path / "source"
        src.mkdir()
        hooks_dir = src / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  Stop:\n    - matcher: ''\n"
            b"      hooks:\n        - command: echo\n          type: command\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=["hooks"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "hook" in captured.out

    def test_deploy_with_only_type_prompts(self, tmp_path, monkeypatch, capsys):
        """--only-type prompts is a valid choice."""
        src = tmp_path / "source"
        src.mkdir()
        prompts_dir = src / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "demo.poet").write_bytes(b"- role: system\n  content: x\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=["prompts"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "prompt" in captured.out

    def test_deploy_with_only_type_settings(self, tmp_path, monkeypatch, capsys):
        """--only-type settings is a valid choice."""
        src = tmp_path / "source"
        src.mkdir()
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=["settings"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "settings" in captured.out

    def test_deploy_with_only_type_marketplaces(self, tmp_path, monkeypatch, capsys):
        """--only-type marketplaces is a valid choice."""
        src = tmp_path / "source"
        src.mkdir()
        mk = src / "marketplaces"
        mk.mkdir()
        (mk / "acme.yaml").write_bytes(
            b"name: acme\nsource:\n  source: github\n  repo: a/b\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=["marketplaces"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "marketplace" in captured.out

    def test_deploy_surfaces_poet_warnings(self, tmp_path, monkeypatch, capsys):
        """A .poet prompt with an undefined Jinja variable should produce a
        warning visible on stderr during deploy (not just validate)."""
        src = tmp_path / "source"
        src.mkdir()
        prompts_dir = src / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "warny.poet").write_bytes(
            b"- role: system\n  content: 'hi {{ missing }}'\n"
        )
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=["prompts"],
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "Undefined Jinja variable: missing" in captured.err
        assert "warny" in captured.err

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
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Cannot specify both" in captured.err

    def test_deploy_envvar_error_exits(self, tmp_path, monkeypatch, capsys):
        """EnvVarError from deploy is reported and causes sys.exit(1)."""
        from promptdeploy.envsubst import EnvVarError

        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        def boom(*_args, **_kwargs):
            raise EnvVarError("MISSING_THING not set")

        monkeypatch.setattr("promptdeploy.deploy.deploy", boom)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "MISSING_THING" in captured.err

    def test_cli_ssherror_clean_exit(self, tmp_path, monkeypatch, capsys):
        """SSHError from deploy is reported and causes sys.exit(1)."""
        from promptdeploy.ssh import SSHError

        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        def boom(*_args, **_kwargs):
            raise SSHError("Remote MCP merge on host failed")

        monkeypatch.setattr("promptdeploy.deploy.deploy", boom)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Remote MCP merge" in captured.err

    def test_deploy_skip_hidden_in_normal(self, tmp_path, monkeypatch, capsys):
        """Skipped items are not shown in normal verbosity."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        # Deploy first, then deploy again for skips
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "3 unchanged" in captured.out
        # No action lines printed for skip in normal verbosity
        lines = [
            ln
            for ln in captured.out.strip().split("\n")
            if ln.strip().startswith("A") or ln.strip().startswith("M")
        ]
        assert len(lines) == 0

    def test_deploy_pre_existing_shown(self, tmp_path, monkeypatch, capsys):
        """Pre-existing items show with P symbol."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Place a pre-existing agent file
        agents_dir = tc.path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "helper.md").write_text("pre-existing")

        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "P" in captured.out
        assert "1 pre-existing" in captured.out

    def test_deploy_skip_shown_in_verbose(self, tmp_path, monkeypatch, capsys):
        """Skipped items are shown in verbose mode."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=True,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        # In verbose mode, skip actions show with space symbol
        assert "3 unchanged" in captured.out

    def test_deploy_force_overwrites_unchanged(self, tmp_path, monkeypatch, capsys):
        """--force causes unchanged items to be redeployed."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=True,
        )
        _run_deploy(args)
        captured = capsys.readouterr()
        assert "3 updated" in captured.out
        assert "0 unchanged" in captured.out


class TestUnknownTargetArg:
    """An unknown --target value exits 1 with a message, not a traceback (B2)."""

    def test_deploy_unknown_target_exits(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=["nope"],
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        assert "Unknown target: nope" in capsys.readouterr().err

    def test_status_unknown_target_exits(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=["nope"], target_root=None)
        with pytest.raises(SystemExit) as exc_info:
            _run_status(args)
        assert exc_info.value.code == 1
        assert "Unknown target: nope" in capsys.readouterr().err

    def test_list_unknown_target_exits(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=["nope"], target_root=None)
        with pytest.raises(SystemExit) as exc_info:
            _run_list(args)
        assert exc_info.value.code == 1
        assert "Unknown target: nope" in capsys.readouterr().err


class TestLoadConfigErrorExits:
    """A ValueError from load_config (bad deploy.yaml) exits cleanly (B3/B4)."""

    def test_validate_bad_deploy_yaml_exits(self, monkeypatch, capsys):
        def boom(*_args, **_kwargs):
            raise ValueError("Top level of deploy.yaml must be a mapping")

        monkeypatch.setattr("promptdeploy.cli.load_config", boom)
        with pytest.raises(SystemExit) as exc_info:
            _run_validate()
        assert exc_info.value.code == 1
        assert "must be a mapping" in capsys.readouterr().err


class TestCorruptSettingsJson:
    def test_deploy_corrupt_settings_json_exits(self, tmp_path, monkeypatch, capsys):
        """A corrupt settings.json on a claude target exits 1 naming the file."""
        src = tmp_path / "source"
        src.mkdir()
        hooks_dir = src / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  Stop:\n    - matcher: ''\n      hooks:\n"
            b"        - command: echo\n          type: command\n"
        )
        tc = _make_claude_target(tmp_path)
        (tc.path / "settings.json").write_text("{not json")
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        assert "settings.json" in capsys.readouterr().err


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
            )
        ]

        monkeypatch.setattr(
            "promptdeploy.cli.load_config",
            lambda *a, **kw: Config(source_root=tmp_path, targets={}, groups={}),
        )
        monkeypatch.setattr(
            "promptdeploy.validate.validate_all", lambda cfg: fake_issues
        )
        _run_validate()
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "0 error(s), 1 warning(s)" in captured.out

    def test_validate_error_format(self, tmp_path, monkeypatch, capsys):
        """Errors print as 'ERROR: <path>: <message>'."""
        from promptdeploy.validate import ValidationIssue

        fake_issues = [
            ValidationIssue(
                level="error",
                message="broken thing",
                file_path=Path("/tmp/e.md"),
            )
        ]

        monkeypatch.setattr(
            "promptdeploy.cli.load_config",
            lambda *a, **kw: Config(source_root=tmp_path, targets={}, groups={}),
        )
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

        args = argparse.Namespace(target=None, target_root=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "No items to report" in captured.out

    def test_status_shows_entries(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None, target_root=None)
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

        args = argparse.Namespace(target=None, target_root=None)
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

        args = argparse.Namespace(target=None, target_root=None)
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

        args = argparse.Namespace(target=None, target_root=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "D" in captured.out

    def test_status_with_target_filter(self, tmp_path, monkeypatch, capsys):
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=["test-claude"], target_root=None)
        _run_status(args)
        captured = capsys.readouterr()
        assert "test-claude" in captured.out

    def test_status_sanitizes_remote_error(self, tmp_path, monkeypatch, capsys):
        from promptdeploy.ssh import SSHError

        config = Config(source_root=tmp_path, targets={}, groups={})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr(
            "promptdeploy.status.get_status",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                SSHError("SECRET-SENTINEL from remote stderr")
            ),
        )

        with pytest.raises(SystemExit) as raised:
            _run_status(argparse.Namespace(target=None, target_root=None))

        assert raised.value.code == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == "ERROR: remote status failed"
        assert "SECRET-SENTINEL" not in captured.out + captured.err

    def test_status_reports_missing_env_without_a_value(
        self, tmp_path, monkeypatch, capsys
    ):
        from promptdeploy.envsubst import EnvVarError

        config = Config(source_root=tmp_path, targets={}, groups={})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        monkeypatch.setattr(
            "promptdeploy.status.get_status",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                EnvVarError("Environment variable ANVIL_TOKEN is not set")
            ),
        )

        with pytest.raises(SystemExit) as raised:
            _run_status(argparse.Namespace(target=None, target_root=None))

        assert raised.value.code == 1
        captured = capsys.readouterr()
        assert "ANVIL_TOKEN" in captured.err


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
        # Patch at the config module level so runpy re-imports pick it up
        monkeypatch.setattr("promptdeploy.config.load_config", lambda *a, **kw: config)
        monkeypatch.setattr("sys.argv", ["promptdeploy", "deploy", "--dry-run"])
        # Run the module as __main__
        runpy.run_module("promptdeploy.cli", run_name="__main__")


# ===================================================================
# --target-root argument: argument parsing tests
# ===================================================================


class TestTargetRootArgParsing:
    """Verify target and bundle arguments on the production parser."""

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        return _build_parser().parse_args(argv)

    def test_deploy_accepts_target_root(self, tmp_path):
        args = self._parse(["deploy", "--target-root", str(tmp_path)])
        assert args.target_root == tmp_path

    def test_deploy_target_root_defaults_to_none(self):
        args = self._parse(["deploy"])
        assert args.target_root is None

    def test_status_accepts_target_root(self, tmp_path):
        args = self._parse(["status", "--target-root", str(tmp_path)])
        assert args.target_root == tmp_path

    def test_status_target_root_defaults_to_none(self):
        args = self._parse(["status"])
        assert args.target_root is None

    def test_list_accepts_target_root(self, tmp_path):
        args = self._parse(["list", "--target-root", str(tmp_path)])
        assert args.target_root == tmp_path

    def test_list_target_root_defaults_to_none(self):
        args = self._parse(["list"])
        assert args.target_root is None

    def test_verify_accepts_target_root(self, tmp_path):
        args = self._parse(
            [
                "verify",
                "--only-item",
                "skill:ponytail",
                "--target-root",
                str(tmp_path),
            ]
        )
        assert args.target_root == tmp_path

    def test_verify_target_root_defaults_to_none(self):
        args = self._parse(["verify", "--only-item", "skill:ponytail"])
        assert args.target_root is None

    def test_global_bundle_binding_flags(self, tmp_path):
        config = tmp_path / "deploy.yaml"
        descriptor = tmp_path / "bindings.json"
        first = tmp_path / "first"
        second = tmp_path / "second"
        args = self._parse(
            [
                "--config",
                str(config),
                "--bundle-bindings-file",
                str(descriptor),
                "--bundle-source",
                f"ponytail={first}",
                "--bundle-source",
                f"other={second}",
                "--require-immutable-bundles",
                "validate",
            ]
        )
        assert args.config == config
        assert args.bundle_bindings_file == descriptor
        assert args.bundle_source == [
            f"ponytail={first}",
            f"other={second}",
        ]
        assert args.require_immutable_bundles


def test_load_config_forwards_explicit_bundle_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    expected = Config(source_root=tmp_path, targets={}, groups={})

    def fake_load_config(**kwargs: object) -> Config:
        observed.update(kwargs)
        return expected

    monkeypatch.setattr("promptdeploy.cli.load_config", fake_load_config)
    descriptor = tmp_path / "bindings.json"
    config_path = tmp_path / "deploy.yaml"
    args = argparse.Namespace(
        config=config_path,
        bundle_bindings_file=descriptor,
        bundle_source=["ponytail=/absolute/source"],
        require_immutable_bundles=True,
    )

    assert _load_config_or_exit(args) is expected
    assert observed == {
        "config_path": config_path,
        "bundle_bindings_file": descriptor,
        "bundle_source_overrides": ("ponytail=/absolute/source",),
        "require_immutable_bundles": True,
    }


# ===================================================================
# --target-root: _run_* integration tests
# ===================================================================


class TestTargetRootDeploy:
    """Verify _run_deploy remaps paths when --target-root is set."""

    def test_deploy_target_root_redirects_files(self, tmp_path, monkeypatch, capsys):
        """Files are written under target-root/target-id, not the original path."""
        src = _make_source(tmp_path)
        original_target_dir = tmp_path / "original-target"
        original_target_dir.mkdir()
        tc = TargetConfig(id="test-claude", type="claude", path=original_target_dir)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        preview_root = tmp_path / "preview"
        preview_root.mkdir()

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=False,
        )
        _run_deploy(args)
        capsys.readouterr()  # discard output

        expected_agents_dir = preview_root / "test-claude" / "agents"
        assert expected_agents_dir.exists(), f"{expected_agents_dir} should exist"
        assert (expected_agents_dir / "helper.md").exists()

        # Original path should be untouched
        assert not (original_target_dir / "agents").exists()

    def test_deploy_target_root_uses_resolved_path(self, tmp_path, monkeypatch, capsys):
        """target_root is resolved to an absolute path before remapping."""
        src = _make_source(tmp_path)
        tc = TargetConfig(id="tgt", type="claude", path=tmp_path / "orig")
        (tmp_path / "orig").mkdir()
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        preview_root = tmp_path / "preview"
        preview_root.mkdir()

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=False,
        )
        _run_deploy(args)
        capsys.readouterr()

        assert (preview_root / "tgt" / "agents" / "helper.md").exists()

    def test_deploy_target_root_dry_run(self, tmp_path, monkeypatch, capsys):
        """--target-root combined with --dry-run does not write files."""
        src = _make_source(tmp_path)
        tc = TargetConfig(id="tgt", type="claude", path=tmp_path / "orig")
        (tmp_path / "orig").mkdir()
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        preview_root = tmp_path / "preview"
        preview_root.mkdir()

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=False,
        )
        _run_deploy(args)
        captured = capsys.readouterr()

        assert "[dry-run]" in captured.out
        # No actual files written
        assert not (preview_root / "tgt").exists()

    def test_deploy_target_root_rejects_symlink_escape(
        self, tmp_path, monkeypatch, capsys
    ):
        src = _make_source(tmp_path)
        live = tmp_path / "live-target"
        live.mkdir()
        sentinel = live / "sentinel"
        sentinel.write_text("untouched")
        tc = TargetConfig(id="tgt", type="claude", path=live)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        preview_root = tmp_path / "preview"
        preview_root.mkdir()
        (preview_root / "tgt").symlink_to(live, target_is_directory=True)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=False,
        )

        with pytest.raises(SystemExit) as raised:
            _run_deploy(args)

        assert raised.value.code == 1
        assert sentinel.read_text() == "untouched"
        assert not (live / "agents").exists()
        assert "is a symlink" in capsys.readouterr().err

    def test_deploy_target_root_rejects_symlinked_root(
        self, tmp_path, monkeypatch, capsys
    ):
        src = _make_source(tmp_path)
        live = tmp_path / "live-target"
        live.mkdir()
        sentinel = live / "sentinel"
        sentinel.write_text("untouched")
        tc = TargetConfig(id="tgt", type="droid", path=tmp_path / "original")
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        preview_root = tmp_path / "preview"
        preview_root.symlink_to(live, target_is_directory=True)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=True,
        )

        with pytest.raises(SystemExit) as raised:
            _run_deploy(args)

        assert raised.value.code == 1
        assert sentinel.read_text() == "untouched"
        assert not (live / "droids").exists()
        assert "must not be a symlink" in capsys.readouterr().err

    def test_deploy_target_root_reports_unknown_home_cleanly(
        self, tmp_path, monkeypatch, capsys
    ):
        src = _make_source(tmp_path)
        tc = TargetConfig(id="tgt", type="claude", path=tmp_path / "original")
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=Path("~promptdeploy_no_such_user_98f31/preview"),
            force=False,
        )

        with pytest.raises(SystemExit) as raised:
            _run_deploy(args)

        assert raised.value.code == 1
        assert "unknown home directory" in capsys.readouterr().err

    def test_deploy_target_root_rejects_symlinked_parent(
        self, tmp_path, monkeypatch, capsys
    ):
        src = _make_source(tmp_path)
        live = tmp_path / "live-target"
        live.mkdir()
        sentinel = live / "sentinel"
        sentinel.write_text("untouched")
        lexical_parent = tmp_path / "preview-parent"
        lexical_parent.symlink_to(live, target_is_directory=True)
        tc = TargetConfig(id="tgt", type="droid", path=tmp_path / "original")
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=lexical_parent / "preview",
            force=True,
        )

        with pytest.raises(SystemExit) as raised:
            _run_deploy(args)

        assert raised.value.code == 1
        assert sentinel.read_text() == "untouched"
        assert not (live / "preview").exists()
        assert "parent must not be a symlink" in capsys.readouterr().err


class TestTargetRootStatus:
    """Verify _run_status uses remapped paths when --target-root is set."""

    def test_status_target_root_reads_from_remapped_path(
        self, tmp_path, monkeypatch, capsys
    ):
        """Status reports items as new when target-root dir has no manifest."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        preview_root = tmp_path / "preview"

        args = argparse.Namespace(target=None, target_root=preview_root)
        _run_status(args)
        captured = capsys.readouterr()

        # preview dir doesn't exist → items show as new (A)
        assert "A" in captured.out


class TestTargetRootList:
    """Verify _run_list uses remapped paths when --target-root is set."""

    def test_list_target_root_shows_not_installed_for_missing_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        """With --target-root pointing at an empty dir, target shows not installed."""
        src = _make_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        preview_root = tmp_path / "preview"
        # Don't create preview_root subdirs — they won't exist

        args = argparse.Namespace(target=None, target_root=preview_root)
        _run_list(args)
        captured = capsys.readouterr()

        assert "not installed" in captured.out

    def test_list_target_root_shows_deployed_items(self, tmp_path, monkeypatch, capsys):
        """After deploying with target-root, list reads from the remapped location."""
        src = _make_source(tmp_path)
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        tc = TargetConfig(id="test-claude", type="claude", path=original_dir)
        config = _make_config(src, {tc.id: tc})

        preview_root = tmp_path / "preview"
        preview_root.mkdir()

        # Deploy using target_root so files land in preview/test-claude/
        deploy_args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            only_type=None,
            target_root=preview_root,
            force=False,
        )
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)
        _run_deploy(deploy_args)
        capsys.readouterr()

        # Now list using the same target_root
        list_args = argparse.Namespace(target=None, target_root=preview_root)
        _run_list(list_args)
        captured = capsys.readouterr()

        assert "test-claude:" in captured.out
        assert "helper" in captured.out


# ===================================================================
# settings init / reconcile subcommands
# ===================================================================


def test_settings_init_and_reconcile_cli(tmp_path, monkeypatch, capsys):
    import json

    from promptdeploy import cli

    # Source tree with deploy.yaml pointing at two local claude targets.
    src = tmp_path / "src"
    src.mkdir()
    p = src.parent / "claude-personal"
    p.mkdir()
    q = src.parent / "claude-positron"
    q.mkdir()
    (p / "settings.json").write_text(json.dumps({"effortLevel": "low"}))
    (q / "settings.json").write_text(json.dumps({"effortLevel": "high"}))
    (src / "deploy.yaml").write_text(
        "source_root: .\n"
        "targets:\n"
        f"  claude-personal:\n    type: claude\n    path: {p}\n    labels: [claude]\n"
        f"  claude-positron:\n    type: claude\n    path: {q}\n    labels: [claude]\n"
    )
    monkeypatch.chdir(src)

    monkeypatch.setattr(
        "sys.argv",
        ["promptdeploy", "settings", "init", "--from", "claude-personal"],
    )
    cli.main()
    doc_text = (src / "settings.yaml").read_text()
    assert "effortLevel: low" in doc_text
    assert "claude-positron" in doc_text  # override captured

    # Reconcile (report-only) must not raise and should print a diff or "clean".
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "reconcile"])
    cli.main()
    capsys.readouterr()  # drained; no exception is the contract


def _write_settings_deploy_yaml(tmp_path, monkeypatch, *, with_settings_yaml: bool):
    """Create a src/ tree with deploy.yaml (one local claude target) and chdir into it.

    Mirrors the inline setup in test_settings_init_and_reconcile_cli.
    """
    import json

    src = tmp_path / "src"
    src.mkdir()
    p = src.parent / "claude-personal"
    p.mkdir()
    (p / "settings.json").write_text(json.dumps({"effortLevel": "low"}))
    (src / "deploy.yaml").write_text(
        "source_root: .\n"
        "targets:\n"
        f"  claude-personal:\n    type: claude\n    path: {p}\n    labels: [claude]\n"
    )
    if with_settings_yaml:
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
    monkeypatch.chdir(src)
    return src


def test_settings_init_bad_target_exits(tmp_path, monkeypatch, capsys):
    # `--target nope` -> expand_target_arg raises ValueError (a CAUGHT type)
    # inside _run_settings_init's try -> ERROR printed + exit 1.
    import pytest

    from promptdeploy import cli

    _write_settings_deploy_yaml(tmp_path, monkeypatch, with_settings_yaml=False)
    monkeypatch.setattr(
        "sys.argv", ["promptdeploy", "settings", "init", "--target", "nope"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "ERROR" in capsys.readouterr().err


def test_settings_reconcile_missing_yaml_exits(tmp_path, monkeypatch, capsys):
    # Valid deploy.yaml with one claude target but NO settings.yaml ->
    # reconcile_settings raises FileNotFoundError (a CAUGHT type) -> exit 1,
    # message mentions `init`.
    import pytest

    from promptdeploy import cli

    _write_settings_deploy_yaml(tmp_path, monkeypatch, with_settings_yaml=False)
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "reconcile"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "init" in capsys.readouterr().err  # FileNotFoundError message mentions init


def test_settings_reconcile_prints_diffs_report_and_apply(
    tmp_path, monkeypatch, capsys
):
    # Host has drift (autoUpdates) absent from settings.yaml -> reconcile reports
    # a diff. Covers the diff-printing loop and BOTH `if args.apply` arms.
    import json

    from promptdeploy import cli

    src = tmp_path / "src"
    src.mkdir()
    p = src.parent / "claude-personal"
    p.mkdir()
    (p / "settings.json").write_text(
        json.dumps({"effortLevel": "low", "autoUpdates": False})
    )
    (src / "deploy.yaml").write_text(
        "source_root: .\n"
        "targets:\n"
        f"  claude-personal:\n    type: claude\n    path: {p}\n    labels: [claude]\n"
    )
    (src / "settings.yaml").write_text("base:\n  model: opus\n")
    monkeypatch.chdir(src)

    # Report-only: prints '+' (host-only) and '-' (rendered-only) diff lines, then
    # the "Re-run with --apply" footer.
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "reconcile"])
    cli.main()
    out = capsys.readouterr().out
    assert "autoUpdates" in out
    assert "Re-run with --apply" in out

    # Apply: prints the same diffs and the "Applied host drift" footer.
    monkeypatch.setattr(
        "sys.argv", ["promptdeploy", "settings", "reconcile", "--apply"]
    )
    cli.main()
    out = capsys.readouterr().out
    assert "Applied host drift into overrides." in out


class TestFrontmatterErrorExits:
    """A malformed agent must produce a clean error naming the file, not a
    traceback, from both deploy and status."""

    def _make_bad_source(self, tmp_path: Path) -> Path:
        src = tmp_path / "source"
        src.mkdir()
        agents = src / "agents"
        agents.mkdir()
        (agents / "bad.md").write_bytes(b"---\ninvalid: yaml: [broken\n---\nBody\n")
        return src

    def test_deploy_frontmatter_error_exits(self, tmp_path, monkeypatch, capsys):
        src = self._make_bad_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=True,
            target=None,
            only_type=None,
            target_root=None,
            force=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "bad.md" in captured.err

    def test_status_frontmatter_error_exits(self, tmp_path, monkeypatch, capsys):
        src = self._make_bad_source(tmp_path)
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        monkeypatch.setattr("promptdeploy.cli.load_config", lambda *a, **kw: config)

        args = argparse.Namespace(target=None, target_root=None)
        with pytest.raises(SystemExit) as exc_info:
            _run_status(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "bad.md" in captured.err
