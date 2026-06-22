"""Tests for the RemoteTarget wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from promptdeploy.targets.base import Target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.remote import RemoteTarget

_MOCK_INCLUDES = ["agents/", "agents/**", "settings.json", ".manifest.json"]
_MOCK_PUSH_INCLUDES = ["agents/", "agents/**", ".manifest.json"]


@pytest.fixture
def mock_inner() -> MagicMock:
    """Create a mock inner target with all required methods."""
    inner = create_autospec(Target, instance=True)
    inner.id = "test-target"
    inner.manifest_path.return_value = Path("/staging/.manifest.json")
    inner.item_exists.return_value = True
    inner.rsync_includes.return_value = _MOCK_INCLUDES
    inner.rsync_push_includes.return_value = _MOCK_PUSH_INCLUDES
    inner.mcp_hash_includes_env = False
    return inner


@pytest.fixture
def remote_target(tmp_path: Path, mock_inner: MagicMock) -> RemoteTarget:
    staging = tmp_path / "staging"
    staging.mkdir()
    return RemoteTarget(
        inner=mock_inner,
        host="user@host",
        remote_path=Path("/remote/target"),
        staging_path=staging,
    )


@pytest.fixture
def remote_mcp_target(tmp_path: Path) -> RemoteTarget:
    """A RemoteTarget wrapping a REAL ClaudeTarget with remote_mcp=True."""
    staging = tmp_path / "staging"
    staging.mkdir()
    inner = ClaudeTarget("rc", staging, manage_mcp=False)
    return RemoteTarget(
        inner=inner,
        host="user@host",
        remote_path=Path("~/.claude"),
        staging_path=staging,
        remote_mcp=True,
    )


class TestRemoteTargetProperties:
    def test_id_delegates_to_inner(self, remote_target: RemoteTarget) -> None:
        assert remote_target.id == "test-target"

    def test_rsync_includes_delegates_to_inner(
        self, remote_target: RemoteTarget
    ) -> None:
        assert remote_target.rsync_includes() == _MOCK_INCLUDES

    def test_prepare_force_deploy_delegates_to_inner(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.prepare_force_deploy("mcp", "srv", {"command": "cmd"})

        mock_inner.prepare_force_deploy.assert_called_once_with(
            "mcp", "srv", {"command": "cmd"}
        )

    def test_prepare_force_deploy_remote_mcp_does_not_delegate(
        self, tmp_path: Path, mock_inner: MagicMock
    ) -> None:
        remote_target = RemoteTarget(
            inner=mock_inner,
            host="user@host",
            remote_path=Path("/remote/target"),
            staging_path=tmp_path / "staging",
            remote_mcp=True,
        )

        remote_target.prepare_force_deploy("mcp", "srv", {"command": "cmd"})

        mock_inner.prepare_force_deploy.assert_not_called()


class TestRemoteTargetLifecycle:
    @patch("promptdeploy.targets.remote.ssh_exists", return_value=True)
    def test_exists_checks_remote(
        self, mock_ssh_exists: MagicMock, remote_target: RemoteTarget
    ) -> None:
        assert remote_target.exists() is True
        mock_ssh_exists.assert_called_once_with("user@host", Path("/remote/target"))

    @patch("promptdeploy.targets.remote.ssh_exists", return_value=False)
    def test_exists_returns_false(
        self, mock_ssh_exists: MagicMock, remote_target: RemoteTarget
    ) -> None:
        assert remote_target.exists() is False

    @patch("promptdeploy.targets.remote.ssh_pull")
    def test_prepare_calls_ssh_pull(
        self, mock_ssh_pull: MagicMock, remote_target: RemoteTarget
    ) -> None:
        remote_target.prepare()
        mock_ssh_pull.assert_called_once_with(
            "user@host",
            Path("/remote/target"),
            remote_target._staging_path,
            verbose=False,
            includes=_MOCK_INCLUDES,
        )

    @patch("promptdeploy.targets.remote.ssh_pull")
    def test_prepare_passes_verbose(
        self, mock_ssh_pull: MagicMock, remote_target: RemoteTarget
    ) -> None:
        remote_target.prepare(verbose=True)
        mock_ssh_pull.assert_called_once_with(
            "user@host",
            Path("/remote/target"),
            remote_target._staging_path,
            verbose=True,
            includes=_MOCK_INCLUDES,
        )

    @patch("promptdeploy.targets.remote.ssh_push")
    def test_finalize_pushes_and_cleans_up(
        self, mock_ssh_push: MagicMock, remote_target: RemoteTarget
    ) -> None:
        assert remote_target._staging_path.exists()
        remote_target.finalize()
        mock_ssh_push.assert_called_once_with(
            "user@host",
            Path("/remote/target"),
            remote_target._staging_path,
            verbose=False,
            includes=_MOCK_PUSH_INCLUDES,
        )
        # staging dir should be removed
        assert not remote_target._staging_path.exists()

    @patch("promptdeploy.targets.remote.ssh_push")
    def test_finalize_passes_verbose(
        self, mock_ssh_push: MagicMock, remote_target: RemoteTarget
    ) -> None:
        remote_target.finalize(verbose=True)
        mock_ssh_push.assert_called_once_with(
            "user@host",
            Path("/remote/target"),
            remote_target._staging_path,
            verbose=True,
            includes=_MOCK_PUSH_INCLUDES,
        )

    @patch("promptdeploy.targets.remote.ssh_push")
    def test_finalize_push_failure_propagates(
        self, mock_ssh_push: MagicMock, remote_target: RemoteTarget
    ) -> None:
        """When ssh_push raises, finalize propagates the error and leaves
        staging in place -- the deploy loop's cleanup() removes it."""
        from promptdeploy.ssh import SSHError

        mock_ssh_push.side_effect = SSHError("rsync push failed")
        with pytest.raises(SSHError, match="rsync push failed"):
            remote_target.finalize()
        assert remote_target._staging_path.exists()

    def test_cleanup_removes_staging_dir(self, remote_target: RemoteTarget) -> None:
        assert remote_target._staging_path.exists()
        remote_target.cleanup()
        assert not remote_target._staging_path.exists()

    def test_cleanup_noop_when_staging_missing(
        self, tmp_path: Path, mock_inner: MagicMock
    ) -> None:
        staging = tmp_path / "nonexistent"
        target = RemoteTarget(
            inner=mock_inner,
            host="user@host",
            remote_path=Path("/remote"),
            staging_path=staging,
        )
        target.cleanup()  # should not raise


class TestRemoteTargetDelegation:
    def test_deploy_agent(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.deploy_agent("agent1", b"content")
        mock_inner.deploy_agent.assert_called_once_with("agent1", b"content")

    def test_deploy_command(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.deploy_command("cmd1", b"content")
        mock_inner.deploy_command.assert_called_once_with("cmd1", b"content")

    def test_deploy_skill(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        source = Path("/source/skill")
        remote_target.deploy_skill("skill1", source)
        mock_inner.deploy_skill.assert_called_once_with("skill1", source)

    def test_deploy_mcp_server(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        cfg = {"command": "npx", "args": ["server"]}
        remote_target.deploy_mcp_server("mcp1", cfg)
        mock_inner.deploy_mcp_server.assert_called_once_with("mcp1", cfg)

    def test_deploy_models(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        cfg: dict = {"providers": []}
        remote_target.deploy_models(cfg)
        mock_inner.deploy_models.assert_called_once_with(cfg)

    def test_deploy_hook(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        cfg = {"event": "PreToolUse"}
        remote_target.deploy_hook("hook1", cfg)
        mock_inner.deploy_hook.assert_called_once_with("hook1", cfg)

    def test_remove_agent(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_agent("agent1")
        mock_inner.remove_agent.assert_called_once_with("agent1")

    def test_remove_command(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_command("cmd1")
        mock_inner.remove_command.assert_called_once_with("cmd1")

    def test_remove_skill(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_skill("skill1")
        mock_inner.remove_skill.assert_called_once_with("skill1")

    def test_remove_mcp_server(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_mcp_server("mcp1")
        mock_inner.remove_mcp_server.assert_called_once_with("mcp1")

    def test_remove_models(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_models()
        mock_inner.remove_models.assert_called_once()

    def test_remove_hook(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_hook("hook1")
        mock_inner.remove_hook.assert_called_once_with("hook1")

    def test_deploy_prompt(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        src = Path("/source/demo.poet")
        remote_target.deploy_prompt("demo", b"body", src)
        mock_inner.deploy_prompt.assert_called_once_with("demo", b"body", src)

    def test_remove_prompt(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_prompt("demo")
        mock_inner.remove_prompt.assert_called_once_with("demo", None)

    def test_remove_prompt_with_target_path(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        target_path = Path("demo.json")
        remote_target.remove_prompt("demo", target_path)
        mock_inner.remove_prompt.assert_called_once_with("demo", target_path)

    def test_deployed_artifact_path_delegates(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.deployed_artifact_path.return_value = Path("demo.json")
        result = remote_target.deployed_artifact_path("prompt", "demo")
        assert result == Path("demo.json")
        mock_inner.deployed_artifact_path.assert_called_once_with("prompt", "demo")

    def test_consume_warnings_delegates_to_inner(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.consume_warnings.return_value = [("demo", ["bad var"])]
        assert remote_target.consume_warnings() == [("demo", ["bad var"])]
        mock_inner.consume_warnings.assert_called_once_with()

    def test_should_skip(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.should_skip.return_value = True
        result = remote_target.should_skip("hook", "hook1", b"content", {"key": "val"})
        assert result is True
        mock_inner.should_skip.assert_called_once_with(
            "hook", "hook1", b"content", {"key": "val"}
        )

    def test_content_fingerprint_delegates_to_inner(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.content_fingerprint.return_value = "model=claude-opus-4-7"
        result = remote_target.content_fingerprint("agent")
        assert result == "model=claude-opus-4-7"
        mock_inner.content_fingerprint.assert_called_once_with("agent")

    def test_item_exists(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        result = remote_target.item_exists("agent", "agent1")
        assert result is True
        mock_inner.item_exists.assert_called_once_with("agent", "agent1")

    def test_manifest_path(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        result = remote_target.manifest_path()
        assert result == Path("/staging/.manifest.json")
        mock_inner.manifest_path.assert_called_once()

    def test_would_deploy_bytes_delegates_to_inner(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.would_deploy_bytes.return_value = b"x"
        result = remote_target.would_deploy_bytes(
            "agent", "a", b"content", source_path=Path("/src/a.md")
        )
        assert result == b"x"
        mock_inner.would_deploy_bytes.assert_called_once_with(
            "agent", "a", b"content", Path("/src/a.md")
        )

    def test_read_deployed_bytes_delegates_to_inner(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.read_deployed_bytes.return_value = b"y"
        result = remote_target.read_deployed_bytes("agent", "a")
        assert result == b"y"
        mock_inner.read_deployed_bytes.assert_called_once_with("agent", "a")


def _make_remote(inner):
    from pathlib import Path

    from promptdeploy.targets.remote import RemoteTarget

    return RemoteTarget(
        inner=inner,
        host="user@host",
        remote_path=Path("/remote/target"),
        staging_path=Path("/staging"),
    )


def test_read_settings_json_delegates_to_inner():
    from unittest.mock import MagicMock

    inner = MagicMock()
    inner.read_settings_json.return_value = {"model": "x"}
    remote = _make_remote(inner)
    assert remote.read_settings_json() == {"model": "x"}
    inner.read_settings_json.assert_called_once_with()


def test_deploy_settings_delegates_to_inner():
    from unittest.mock import MagicMock

    inner = MagicMock()
    remote = _make_remote(inner)
    remote.deploy_settings({"model": "x"}, ["model"])
    inner.deploy_settings.assert_called_once_with({"model": "x"}, ["model"])


def test_remove_settings_delegates_to_inner():
    from unittest.mock import MagicMock

    inner = MagicMock()
    remote = _make_remote(inner)
    remote.remove_settings(["model", "env"])
    inner.remove_settings.assert_called_once_with(["model", "env"])


def test_deploy_marketplace_delegates_to_inner():
    from unittest.mock import MagicMock

    inner = MagicMock()
    remote = _make_remote(inner)
    cfg = {"source": {"source": "github", "repo": "a/b"}}
    remote.deploy_marketplace("acme", cfg)
    inner.deploy_marketplace.assert_called_once_with("acme", cfg)


def test_remove_marketplace_delegates_to_inner():
    from unittest.mock import MagicMock

    inner = MagicMock()
    remote = _make_remote(inner)
    remote.remove_marketplace("acme")
    inner.remove_marketplace.assert_called_once_with("acme")


# ----------------------------------------------------------------------
# Remote MCP (remote_mcp=True): accumulate / flush / hash semantics
# ----------------------------------------------------------------------


class TestRemoteMcpShouldSkip:
    def test_should_skip_mcp_false_when_remote_mcp(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        assert remote_mcp_target.should_skip("mcp", "x") is False

    def test_should_skip_mcp_delegates_when_not_remote_mcp(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.should_skip.return_value = True
        assert remote_target.should_skip("mcp", "x") is True
        mock_inner.should_skip.assert_called_once_with("mcp", "x", None, None)

    def test_should_skip_nonmcp_delegates(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        # agent is not skipped by ClaudeTarget, so this stays False via delegate.
        assert remote_mcp_target.should_skip("agent", "x") is False


class TestRemoteMcpDeployServer:
    def test_deploy_mcp_server_accumulates_set_op(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server(
            "srv", {"name": "srv", "command": "npx", "args": ["s"]}
        )
        assert remote_mcp_target._mcp_ops == [
            {"action": "set", "name": "srv", "entry": {"command": "npx", "args": ["s"]}}
        ]
        assert "srv" in remote_mcp_target._mcp_seen
        assert not (remote_mcp_target._staging_path / ".claude.json").exists()

    def test_deploy_mcp_server_url_gets_type_http_in_entry(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "url": "https://x"})
        assert remote_mcp_target._mcp_ops[0]["entry"]["type"] == "http"

    def test_deploy_mcp_server_env_headers_strict_expanded(
        self, remote_mcp_target: RemoteTarget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOK", "secret123")
        remote_mcp_target.deploy_mcp_server(
            "srv",
            {
                "name": "srv",
                "url": "https://x",
                "env": {"K": "${TOK}"},
                "headers": {"Authorization": "Bearer ${TOK}"},
            },
        )
        entry = remote_mcp_target._mcp_ops[0]["entry"]
        assert entry["env"]["K"] == "secret123"
        assert entry["headers"]["Authorization"] == "Bearer secret123"

    def test_deploy_mcp_server_expands_remote_secrets_once(
        self, remote_mcp_target: RemoteTarget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOK", "literal ${INNER}")
        monkeypatch.delenv("INNER", raising=False)
        remote_mcp_target.deploy_mcp_server(
            "srv",
            {
                "name": "srv",
                "command": "c",
                "env": {"K": "${TOK}"},
            },
        )
        entry = remote_mcp_target._mcp_ops[0]["entry"]
        assert entry["env"]["K"] == "literal ${INNER}"

    def test_deploy_mcp_server_missing_env_raises(
        self, remote_mcp_target: RemoteTarget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from promptdeploy.envsubst import EnvVarError

        monkeypatch.delenv("ABSENT", raising=False)
        with pytest.raises(EnvVarError):
            remote_mcp_target.deploy_mcp_server(
                "srv", {"name": "srv", "command": "c", "env": {"K": "${ABSENT}"}}
            )
        assert remote_mcp_target._mcp_ops == []
        assert remote_mcp_target._mcp_seen == set()

    def test_deploy_mcp_server_disabled_queues_pop(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server(
            "srv", {"name": "srv", "command": "c", "enabled": False}
        )
        assert remote_mcp_target._mcp_ops == [
            {"action": "pop", "name": "srv", "entry": None}
        ]

    def test_deploy_mcp_server_non_string_env_value_passes_through(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server(
            "srv", {"name": "srv", "command": "c", "env": {"PORT": 8080}}
        )
        assert remote_mcp_target._mcp_ops[0]["entry"]["env"]["PORT"] == 8080

    def test_deploy_mcp_server_no_env_headers(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        entry = remote_mcp_target._mcp_ops[0]["entry"]
        assert "env" not in entry
        assert "headers" not in entry

    def test_deploy_mcp_server_delegates_when_not_remote_mcp(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        cfg = {"command": "npx"}
        remote_target.deploy_mcp_server("srv", cfg)
        mock_inner.deploy_mcp_server.assert_called_once_with("srv", cfg)
        assert remote_target._mcp_ops == []


class TestRemoteMcpRemoveServer:
    def test_remove_mcp_server_accumulates_pop(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.remove_mcp_server("srv")
        assert remote_mcp_target._mcp_ops == [
            {"action": "pop", "name": "srv", "entry": None}
        ]
        assert "srv" in remote_mcp_target._mcp_seen

    def test_remove_mcp_server_delegates_when_not_remote_mcp(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        remote_target.remove_mcp_server("srv")
        mock_inner.remove_mcp_server.assert_called_once_with("srv")


class TestRemoteMcpItemExists:
    def test_item_exists_mcp_seen_returns_true(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        assert remote_mcp_target.item_exists("mcp", "srv") is True

    def test_item_exists_mcp_reads_manifest(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        from promptdeploy.manifest import (
            Manifest,
            ManifestItem,
            save_manifest,
        )

        save_manifest(
            Manifest(
                items={"mcp_servers": {"srv": ManifestItem(source_hash="sha256:x")}}
            ),
            remote_mcp_target.manifest_path(),
        )
        assert remote_mcp_target.item_exists("mcp", "srv") is True

    def test_item_exists_mcp_absent_manifest_false(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        assert remote_mcp_target.item_exists("mcp", "nope") is False

    def test_item_exists_mcp_manifest_memoized(
        self, remote_mcp_target: RemoteTarget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from promptdeploy.manifest import Manifest

        calls = {"n": 0}

        def fake_load(_path: Path) -> Manifest:
            calls["n"] += 1
            return Manifest()

        monkeypatch.setattr("promptdeploy.manifest.load_manifest", fake_load)
        assert remote_mcp_target.item_exists("mcp", "a") is False
        assert remote_mcp_target.item_exists("mcp", "b") is False
        assert calls["n"] == 1

    def test_item_exists_nonmcp_delegates(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        # No agents/x.md in staging -> ClaudeTarget reports False.
        assert remote_mcp_target.item_exists("agent", "x") is False

    def test_item_exists_mcp_delegates_when_not_remote_mcp(
        self, remote_target: RemoteTarget, mock_inner: MagicMock
    ) -> None:
        mock_inner.item_exists.return_value = True
        assert remote_target.item_exists("mcp", "srv") is True
        mock_inner.item_exists.assert_called_once_with("mcp", "srv")


class TestRemoteMcpHashProperty:
    def test_remote_mcp_hash_property_true(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        assert remote_mcp_target.remote_mcp_hash is True
        assert remote_mcp_target.mcp_hash_includes_env is True

    def test_remote_mcp_hash_property_false_when_not_remote_mcp(
        self, remote_target: RemoteTarget
    ) -> None:
        assert remote_target.remote_mcp_hash is False
        assert remote_target.mcp_hash_includes_env is False

    def test_base_target_remote_mcp_hash_default_false(self, tmp_path: Path) -> None:
        target = ClaudeTarget("c", tmp_path)
        assert target.remote_mcp_hash is False
        assert target.mcp_hash_includes_env is True


class TestRemoteMcpFlush:
    def test_flush_remote_mcp_calls_ssh_stdin_with_built_script(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        from promptdeploy.ssh import build_claude_merge_script

        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        with patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin:
            remote_mcp_target.flush_remote_mcp()
        expected = build_claude_merge_script(
            remote_mcp_target._mcp_ops, "~/.claude/.claude.json"
        )
        mock_stdin.assert_called_once_with("user@host", expected)

    def test_flush_remote_mcp_noop_when_no_ops(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        with patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin:
            remote_mcp_target.flush_remote_mcp()
        mock_stdin.assert_not_called()

    def test_flush_remote_mcp_noop_when_not_remote_mcp(
        self, remote_target: RemoteTarget
    ) -> None:
        remote_target._mcp_ops = [{"action": "pop", "name": "x", "entry": None}]
        with patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin:
            remote_target.flush_remote_mcp()
        mock_stdin.assert_not_called()

    def test_flush_remote_mcp_propagates_ssherror(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        from promptdeploy.ssh import SSHError

        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        with (
            patch(
                "promptdeploy.targets.remote.ssh_stdin",
                side_effect=SSHError("boom"),
            ),
            pytest.raises(SSHError, match="boom"),
        ):
            remote_mcp_target.flush_remote_mcp()
        assert remote_mcp_target._mcp_ops != []


class TestRemoteMcpFinalizeCleanup:
    def test_finalize_pushes_and_clears_ops(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        remote_mcp_target._mcp_manifest_names = {"old"}
        with (
            patch("promptdeploy.targets.remote.ssh_push") as mock_push,
            patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin,
        ):
            remote_mcp_target.finalize()
        mock_push.assert_called_once()
        mock_stdin.assert_not_called()
        assert remote_mcp_target._mcp_ops == []
        assert remote_mcp_target._mcp_seen == set()
        assert remote_mcp_target._mcp_manifest_names is None
        assert not remote_mcp_target._staging_path.exists()

    def test_finalize_push_failure_propagates_no_clear(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        from promptdeploy.ssh import SSHError

        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        with (
            patch(
                "promptdeploy.targets.remote.ssh_push",
                side_effect=SSHError("push fail"),
            ),
            pytest.raises(SSHError, match="push fail"),
        ):
            remote_mcp_target.finalize()
        assert remote_mcp_target._mcp_ops != []
        assert remote_mcp_target._staging_path.exists()

    def test_cleanup_discards_ops_no_ssh(self, remote_mcp_target: RemoteTarget) -> None:
        remote_mcp_target.deploy_mcp_server("srv", {"name": "srv", "command": "c"})
        remote_mcp_target._mcp_manifest_names = {"old"}
        with (
            patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin,
            patch("promptdeploy.targets.remote.ssh_push") as mock_push,
        ):
            remote_mcp_target.cleanup()
        mock_stdin.assert_not_called()
        mock_push.assert_not_called()
        assert remote_mcp_target._mcp_ops == []
        assert remote_mcp_target._mcp_seen == set()
        assert remote_mcp_target._mcp_manifest_names is None
        assert not remote_mcp_target._staging_path.exists()


class TestRemoteMcpGuardrails:
    def test_rsync_includes_excludes_claude_json(
        self, remote_mcp_target: RemoteTarget
    ) -> None:
        includes = remote_mcp_target.rsync_includes() or []
        assert ".claude.json" not in includes

    def test_target_root_previews_remote_mcp_as_local_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from promptdeploy.config import (
            Config,
            TargetConfig,
            remap_targets_to_root,
        )
        from promptdeploy.targets import create_target

        target_dir = tmp_path / "preview"
        tc = TargetConfig(
            id="rc", type="claude", path=tmp_path / "live", host="user@remotehost"
        )
        config = Config(source_root=tmp_path / "src", targets={tc.id: tc}, groups={})
        remapped = remap_targets_to_root(config, target_dir)
        target = create_target(remapped.targets["rc"])
        assert isinstance(target, ClaudeTarget)
        assert not isinstance(target, RemoteTarget)
        assert target.remote_mcp_hash is False
        assert target.mcp_hash_includes_env is True
        monkeypatch.setenv("TOK", "value")
        # ${VAR} is expanded locally, no ssh_stdin.
        with patch("promptdeploy.targets.remote.ssh_stdin") as mock_stdin:
            target.deploy_mcp_server(
                "srv", {"name": "srv", "command": "c", "env": {"K": "${TOK}"}}
            )
        mock_stdin.assert_not_called()
        claude_json = json.loads((target._config_path / ".claude.json").read_text())
        assert claude_json["mcpServers"]["srv"]["env"]["K"] == "value"
