"""Strict exact verification and no-SSH CLI regressions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from promptdeploy import cli
from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import deploy
from promptdeploy.manifest import MANIFEST_FILENAME, load_manifest, save_manifest
from promptdeploy.targets import create_target
from promptdeploy.verify import verify_items

SELECTORS = [
    ("mcp", "anvil"),
    ("mcp", "anvil-tools"),
    ("skill", "anvil"),
]


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "anvil.yaml").write_text(
        "name: anvil\ncommand: anvil-mcp\nargs:\n  - --server-id=anvil\nenabled: true\n"
    )
    (root / "mcp" / "anvil-tools.yaml").write_text(
        "name: anvil-tools\n"
        "command: anvil-mcp\n"
        "args:\n"
        "  - --server-id=emacs-eval\n"
        "enabled: false\n"
    )
    skill = root / "skills" / "anvil"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(
        b"---\nname: anvil\ndescription: Anvil.\n---\nUse Anvil.\n"
    )
    (skill / "references" / "tools.md").write_bytes(b"tools\n")
    return root


def _config(tmp_path: Path, target_type: str = "claude") -> Config:
    root = _source(tmp_path)
    target_path = tmp_path / f"{target_type}-target"
    return Config(
        source_root=root,
        targets={
            "local": TargetConfig(
                id="local",
                type=target_type,
                path=target_path,
            )
        },
        groups={},
    )


def _skill_destination(config: Config) -> Path:
    target = config.targets["local"]
    if target.type == "codex":
        return target.path / ".agents" / "skills" / "anvil"
    return target.path / "skills" / "anvil"


@pytest.mark.parametrize("target_type", ["claude", "codex", "droid", "opencode"])
def test_exact_force_and_strict_verify_all_client_types(
    target_type: str, tmp_path: Path
) -> None:
    config = _config(tmp_path, target_type)
    deploy(config, item_selectors=SELECTORS, force=True)

    assert (
        verify_items(
            config,
            target_ids=["local"],
            item_selectors=SELECTORS,
        )
        == []
    )


@pytest.mark.parametrize(
    ("drift", "expected_reason"),
    [
        ("manifest", "manifest-mismatch"),
        ("primary", "mismatch"),
        ("tombstone", "mismatch"),
        ("skill", "mismatch"),
    ],
)
def test_verify_reports_selected_drift_only(
    drift: str, expected_reason: str, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    deploy(config, item_selectors=SELECTORS, force=True)
    target_config = config.targets["local"]

    if drift == "manifest":
        manifest = load_manifest(target_config.path / MANIFEST_FILENAME)
        manifest.items["mcp_servers"]["anvil"].source_hash = "sha256:stale"
        save_manifest(manifest, target_config.path / MANIFEST_FILENAME)
    elif drift == "primary":
        create_target(target_config).deploy_mcp_server(
            "anvil", {"command": "wrong", "enabled": True}
        )
    elif drift == "tombstone":
        create_target(target_config).deploy_mcp_server(
            "anvil-tools", {"command": "resurrected", "enabled": True}
        )
    else:
        (_skill_destination(config) / "references" / "tools.md").write_bytes(b"drift\n")

    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=SELECTORS,
    )

    assert any(failure.reason == expected_reason for failure in failures)
    assert all(failure.name in {"anvil", "anvil-tools"} for failure in failures)


def test_verify_does_not_inspect_unselected_secret_sibling(tmp_path: Path) -> None:
    config = _config(tmp_path)
    deploy(config, item_selectors=SELECTORS, force=True)
    target_path = config.targets["local"].path
    config_path = target_path / ".claude.json"
    data = json.loads(config_path.read_text())
    data["mcpServers"]["secret-sibling"] = {
        "command": "stale",
        "env": {"TOKEN": "do-not-report-this"},
    }
    config_path.write_text(json.dumps(data))

    assert (
        verify_items(
            config,
            target_ids=["local"],
            item_selectors=SELECTORS,
        )
        == []
    )


def test_verify_unprovable_item_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    agents = config.source_root / "agents"
    agents.mkdir()
    (agents / "helper.md").write_bytes(b"---\nname: helper\n---\nBody.\n")
    deploy(config, item_selectors=[("agent", "helper")])

    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=[("agent", "helper")],
    )

    assert [(failure.name, failure.reason) for failure in failures] == [
        ("helper", "unprovable")
    ]


def test_verify_prepare_failure_is_unreadable_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    cleaned: list[bool] = []

    fake = SimpleNamespace(
        prepare=lambda: (_ for _ in ()).throw(PermissionError("injected")),
        cleanup=lambda: cleaned.append(True),
        should_skip=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr("promptdeploy.verify.create_target", lambda *_a, **_k: fake)

    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=[("mcp", "anvil")],
    )

    assert [(failure.reason, failure.target_id) for failure in failures] == [
        ("unreadable", "local")
    ]
    assert cleaned == [True]


def test_verify_prepare_failure_on_inapplicable_item_reports_no_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    cleaned: list[bool] = []
    fake = SimpleNamespace(
        prepare=lambda: (_ for _ in ()).throw(PermissionError("injected")),
        cleanup=lambda: cleaned.append(True),
        should_skip=lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr("promptdeploy.verify.create_target", lambda *_a, **_k: fake)

    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=[("mcp", "anvil")],
    )

    assert [(failure.target_id, failure.reason) for failure in failures] == [
        ("<none>", "no-applicable-target")
    ]
    assert cleaned == [True]


def test_verify_match_exception_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    deploy(config, item_selectors=[("mcp", "anvil")])
    target = create_target(config.targets["local"])

    def unreadable(*_args, **_kwargs):
        raise ValueError("injected")

    monkeypatch.setattr(target, "item_matches_source", unreadable)
    monkeypatch.setattr("promptdeploy.verify.create_target", lambda *_a, **_k: target)
    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=[("mcp", "anvil")],
    )
    assert [failure.reason for failure in failures] == ["unreadable"]


def test_verify_requires_known_applicable_selectors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        verify_items(config, target_ids=["local"], item_selectors=[])
    with pytest.raises(ValueError, match="Unknown source item selector"):
        verify_items(
            config,
            target_ids=["local"],
            item_selectors=[("mcp", "missing")],
        )

    config.groups["other"] = []
    (config.source_root / "mcp" / "anvil.yaml").write_text(
        "name: anvil\ncommand: anvil-mcp\nonly: [other]\n"
    )
    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=[("mcp", "anvil")],
    )
    assert [(failure.target_id, failure.reason) for failure in failures] == [
        ("<none>", "no-applicable-target")
    ]


def test_local_only_selection_filters_before_any_target_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    config = Config(
        source_root=source,
        targets={
            "hostless": TargetConfig("hostless", "claude", tmp_path / "hostless"),
            "current": TargetConfig(
                "current", "claude", tmp_path / "current", host="hera"
            ),
            "remote": TargetConfig("remote", "claude", Path("/remote"), host="vulcan"),
        },
        groups={"mixed": ["hostless", "current", "remote"]},
    )
    monkeypatch.setenv("PROMPTDEPLOY_HOST", "hera")

    target_ids, runtime_host = cli._select_operation_targets(
        config,
        argparse.Namespace(target=["mixed"], local_only=True),
    )

    assert target_ids == ["hostless", "current"]
    assert runtime_host == "hera"
    with pytest.raises(ValueError, match="No selected targets"):
        cli._select_operation_targets(
            config,
            argparse.Namespace(target=["remote"], local_only=True),
        )
    assert cli._select_operation_targets(
        config,
        argparse.Namespace(target=["remote"], local_only=False),
    ) == (["remote"], None)


def test_create_target_local_host_is_a_second_no_ssh_guard(tmp_path: Path) -> None:
    local = TargetConfig("local", "claude", tmp_path / "local", host="hera")
    remote = TargetConfig("remote", "claude", Path("/remote"), host="vulcan")
    assert create_target(local, local_host="hera").id == "local"
    with pytest.raises(ValueError, match="remote from runtime host"):
        create_target(remote, local_host="hera")


@pytest.mark.parametrize("command", ["deploy", "verify"])
def test_source_dotenv_cannot_spoof_local_only_host_identity(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    (source / ".env").write_text("PROMPTDEPLOY_HOST=vulcan\nDOTENV_SENTINEL=loaded\n")
    target_path = tmp_path / "must-not-be-written"
    target = TargetConfig(
        "vulcan",
        "claude",
        target_path,
        host="vulcan",
    )
    config = Config(source_root=source, targets={"vulcan": target}, groups={})
    monkeypatch.delenv("PROMPTDEPLOY_HOST", raising=False)
    monkeypatch.delenv("DOTENV_SENTINEL", raising=False)
    monkeypatch.setattr(
        "promptdeploy.config.current_host",
        lambda: os.environ.get("PROMPTDEPLOY_HOST", "hera"),
    )
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("operation must not reach target construction")

    if command == "deploy":
        monkeypatch.setattr("promptdeploy.deploy.deploy", forbidden)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=["vulcan"],
            local_only=True,
            only_type=None,
            only_item=None,
            target_root=None,
            force=False,
        )
        operation = cli._run_deploy
    else:
        monkeypatch.setattr("promptdeploy.verify.verify_items", forbidden)
        args = argparse.Namespace(
            target=["vulcan"],
            local_only=True,
            only_item=["mcp:anvil"],
        )
        operation = cli._run_verify

    with pytest.raises(SystemExit) as raised:
        operation(args)
    assert raised.value.code == 1
    assert "PROMPTDEPLOY_HOST" not in os.environ
    assert os.environ["DOTENV_SENTINEL"] == "loaded"
    assert not target_path.exists()


@pytest.mark.parametrize("command", ["deploy", "verify"])
def test_source_dotenv_cannot_spoof_ordinary_host_identity(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    source = config.source_root
    (source / ".env").write_text("PROMPTDEPLOY_HOST=vulcan\n")
    monkeypatch.delenv("PROMPTDEPLOY_HOST", raising=False)
    monkeypatch.setattr(
        "promptdeploy.config.current_host",
        lambda: os.environ.get("PROMPTDEPLOY_HOST", "hera"),
    )
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)
    observed: list[str] = []

    if command == "deploy":

        def fake_operation(*_args, **_kwargs):
            observed.append(os.environ.get("PROMPTDEPLOY_HOST", "hera"))
            return []

        monkeypatch.setattr("promptdeploy.deploy.deploy", fake_operation)
        args = argparse.Namespace(
            verbose=False,
            quiet=False,
            dry_run=False,
            target=None,
            local_only=False,
            only_type=None,
            only_item=None,
            target_root=None,
            force=False,
        )
        cli._run_deploy(args)
    else:

        def fake_operation(*_args, **_kwargs):
            observed.append(os.environ.get("PROMPTDEPLOY_HOST", "hera"))
            return []

        monkeypatch.setattr("promptdeploy.verify.verify_items", fake_operation)
        args = argparse.Namespace(
            target=None,
            local_only=False,
            only_item=["mcp:anvil"],
        )
        cli._run_verify(args)

    assert observed == ["hera"]
    assert "PROMPTDEPLOY_HOST" not in os.environ


def test_source_dotenv_preserves_exported_host_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("PROMPTDEPLOY_HOST=vulcan\n")
    monkeypatch.setenv("PROMPTDEPLOY_HOST", "hera")

    cli._load_source_dotenv(dotenv)

    assert os.environ["PROMPTDEPLOY_HOST"] == "hera"


def test_run_verify_cli_success_and_secret_free_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    deploy(config, item_selectors=SELECTORS, force=True)
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)
    args = argparse.Namespace(
        target=None,
        local_only=False,
        only_item=["mcp:anvil", "mcp:anvil-tools", "skill:anvil"],
    )

    cli._run_verify(args)
    assert "Verified 3 exact item selector" in capsys.readouterr().out

    create_target(config.targets["local"]).deploy_mcp_server(
        "anvil", {"command": "wrong", "env": {"TOKEN": "secret-sentinel"}}
    )
    with pytest.raises(SystemExit) as raised:
        cli._run_verify(args)
    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert "mismatch" in captured.err
    assert "secret-sentinel" not in captured.err


def test_run_verify_cli_reports_setup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)
    args = argparse.Namespace(
        target=None,
        local_only=False,
        only_item=["invalid"],
    )
    with pytest.raises(SystemExit) as raised:
        cli._run_verify(args)
    assert raised.value.code == 1
    assert "verification could not run" in capsys.readouterr().err

    (config.source_root / "mcp" / "anvil.yaml").write_text(
        "name: anvil\ncommand: anvil-mcp\nonly: [undefined-environment]\n"
    )
    args.only_item = ["mcp:anvil"]
    with pytest.raises(SystemExit) as filtered:
        cli._run_verify(args)
    assert filtered.value.code == 1
    assert "Invalid environment ID" in capsys.readouterr().err


def test_run_verify_cli_sanitizes_remote_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from promptdeploy.ssh import SSHError

    config = _config(tmp_path)
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)

    def fail(*_args, **_kwargs):
        raise SSHError("SECRET-SENTINEL from remote stderr")

    monkeypatch.setattr("promptdeploy.verify.verify_items", fail)
    args = argparse.Namespace(
        target=None,
        local_only=False,
        only_item=["mcp:anvil"],
    )
    with pytest.raises(SystemExit) as raised:
        cli._run_verify(args)
    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: remote verification failed"
    assert "SECRET-SENTINEL" not in captured.out + captured.err


def test_run_verify_cli_reports_missing_env_without_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from promptdeploy.envsubst import EnvVarError

    config = _config(tmp_path)
    monkeypatch.setattr("promptdeploy.cli.load_config", lambda: config)

    def fail(*_args, **_kwargs):
        raise EnvVarError("Environment variable ANVIL_TOKEN is not set")

    monkeypatch.setattr("promptdeploy.verify.verify_items", fail)
    args = argparse.Namespace(
        target=None,
        local_only=False,
        only_item=["mcp:anvil"],
    )
    with pytest.raises(SystemExit) as raised:
        cli._run_verify(args)
    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert "verification could not run" in captured.err
    assert "ANVIL_TOKEN" in captured.err


@pytest.mark.parametrize(
    "manifest_mutation",
    [
        lambda data: data.update({"version": 2}),
        lambda data: data.update({"unknown": True}),
    ],
)
def test_verify_treats_unknown_manifest_schema_as_unreadable(
    manifest_mutation, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    deploy(config, item_selectors=SELECTORS, force=True)
    manifest_path = config.targets["local"].path / ".prompt-deploy-manifest.json"
    data = json.loads(manifest_path.read_text())
    manifest_mutation(data)
    manifest_path.write_text(json.dumps(data))

    failures = verify_items(
        config,
        target_ids=["local"],
        item_selectors=SELECTORS,
    )

    assert failures
    assert {failure.reason for failure in failures} == {"unreadable"}


def test_main_parser_requires_exact_verify_items_and_excludes_type_mix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["promptdeploy", "verify"])
    with pytest.raises(SystemExit) as missing:
        cli.main()
    assert missing.value.code == 2

    monkeypatch.setattr(
        "sys.argv",
        [
            "promptdeploy",
            "deploy",
            "--only-type",
            "mcp",
            "--only-item",
            "mcp:anvil",
        ],
    )
    with pytest.raises(SystemExit) as mixed:
        cli.main()
    assert mixed.value.code == 2


def test_main_dispatches_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "promptdeploy.cli._run_verify", lambda args: seen.append(args.only_item)
    )
    monkeypatch.setattr(
        "sys.argv",
        ["promptdeploy", "verify", "--only-item", "mcp:anvil"],
    )
    cli.main()
    assert seen == [["mcp:anvil"]]
