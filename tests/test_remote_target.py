"""Tests for the RemoteTarget wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from promptdeploy.targets.remote import RemoteTarget

_MOCK_INCLUDES = ["agents/", "agents/**", "settings.json", ".manifest.json"]


@pytest.fixture
def mock_inner() -> MagicMock:
    """Create a mock inner target with all required methods."""
    inner = MagicMock()
    inner.id = "test-target"
    inner.manifest_path.return_value = Path("/staging/.manifest.json")
    inner.item_exists.return_value = True
    inner.rsync_includes.return_value = _MOCK_INCLUDES
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


class TestRemoteTargetProperties:
    def test_id_delegates_to_inner(self, remote_target: RemoteTarget) -> None:
        assert remote_target.id == "test-target"

    def test_rsync_includes_delegates_to_inner(
        self, remote_target: RemoteTarget
    ) -> None:
        assert remote_target.rsync_includes() == _MOCK_INCLUDES


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
            includes=_MOCK_INCLUDES,
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
            includes=_MOCK_INCLUDES,
        )

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

    def test_consume_warnings_when_inner_lacks_method(self, tmp_path: Path) -> None:
        # An inner target that does not provide ``consume_warnings`` at all
        # (e.g. a legacy implementation) should yield an empty list rather
        # than crash.
        class _Bare:
            pass

        staging = tmp_path / "staging-bare"
        staging.mkdir()
        target = RemoteTarget(
            inner=_Bare(),  # type: ignore[arg-type]
            host="h",
            remote_path=Path("/remote"),
            staging_path=staging,
        )
        assert target.consume_warnings() == []

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
