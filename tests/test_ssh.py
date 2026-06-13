"""Tests for promptdeploy SSH transport functions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import call, patch

import pytest

from promptdeploy.ssh import (
    _RSYNC_SSH,
    _SSH_OPTS,
    SSHError,
    _check_tools,
    _quote_remote_path,
    _rsync_filter_args,
    ssh_exists,
    ssh_pull,
    ssh_push,
)


class TestQuoteRemotePath:
    def test_plain_path_unchanged(self) -> None:
        assert _quote_remote_path(Path("/remote/path")) == "/remote/path"

    def test_path_with_space_quoted(self) -> None:
        assert _quote_remote_path(Path("/remote/my dir")) == "'/remote/my dir'"

    def test_tilde_slash_kept_outside_quotes(self) -> None:
        # The leading ~/ must stay unquoted so the remote shell expands it.
        assert _quote_remote_path(Path("~/my dir")) == "~/'my dir'"

    def test_bare_tilde_unquoted(self) -> None:
        assert _quote_remote_path(Path("~")) == "~"

    def test_tilde_path_without_specials(self) -> None:
        assert _quote_remote_path(Path("~/plain")) == "~/plain"


class TestCheckTools:
    def test_raises_when_rsync_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda tool: None if tool == "rsync" else "/usr/bin/ssh"
        )
        with pytest.raises(SSHError, match="'rsync' not found on PATH"):
            _check_tools()

    def test_raises_when_ssh_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda tool: None if tool == "ssh" else "/usr/bin/rsync"
        )
        with pytest.raises(SSHError, match="'ssh' not found on PATH"):
            _check_tools()

    def test_succeeds_when_both_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        _check_tools()  # should not raise


class TestRsyncFilterArgs:
    def test_returns_empty_when_no_includes(self) -> None:
        assert _rsync_filter_args(None) == []

    def test_returns_empty_for_empty_list(self) -> None:
        assert _rsync_filter_args([]) == []

    def test_builds_include_exclude_flags(self) -> None:
        result = _rsync_filter_args(["agents/", "agents/**", "settings.json"])
        assert result == [
            "--include",
            "agents/",
            "--include",
            "agents/**",
            "--include",
            "settings.json",
            "--exclude",
            "*",
        ]


class TestSSHExists:
    def test_returns_true_when_dir_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        with patch("promptdeploy.ssh.subprocess.run", return_value=result) as mock_run:
            assert ssh_exists("user@host", Path("/remote/path")) is True
        mock_run.assert_called_once_with(
            ["ssh", *_SSH_OPTS, "user@host", "test", "-d", "/remote/path"],
            capture_output=True,
        )

    def test_returns_false_when_dir_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=1
        )
        with patch("promptdeploy.ssh.subprocess.run", return_value=result):
            assert ssh_exists("user@host", Path("/remote/path")) is False

    def test_raises_on_connection_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=255, stderr=b"Could not resolve hostname"
        )
        with (
            patch("promptdeploy.ssh.subprocess.run", return_value=result),
            pytest.raises(SSHError, match="SSH connection to .* failed"),
        ):
            ssh_exists("user@host", Path("/remote/path"))

    def test_quotes_path_with_spaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remote paths are shell-quoted in the ssh command line (B32)."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        with patch("promptdeploy.ssh.subprocess.run", return_value=result) as mock_run:
            assert ssh_exists("user@host", Path("/remote/my dir")) is True
        mock_run.assert_called_once_with(
            ["ssh", *_SSH_OPTS, "user@host", "test", "-d", "'/remote/my dir'"],
            capture_output=True,
        )


class TestSSHPull:
    def test_creates_local_dir_and_syncs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"

        exists_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [exists_result, rsync_result]
            ssh_pull("user@host", Path("/remote/path"), local)

        assert local.exists()
        assert mock_run.call_count == 2
        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                " ".join(_RSYNC_SSH),
                "user@host:/remote/path/",
                str(local) + "/",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_verbose_adds_v_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"

        exists_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [exists_result, rsync_result]
            ssh_pull("user@host", Path("/remote/path"), local, verbose=True)

        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "-v",
                "-e",
                " ".join(_RSYNC_SSH),
                "user@host:/remote/path/",
                str(local) + "/",
            ],
            stdout=None,
            stderr=sys.stderr,
            text=True,
        )

    def test_includes_adds_filter_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        includes = ["agents/", "agents/**", "settings.json"]

        exists_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [exists_result, rsync_result]
            ssh_pull("user@host", Path("/remote/path"), local, includes=includes)

        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "--include",
                "agents/",
                "--include",
                "agents/**",
                "--include",
                "settings.json",
                "--exclude",
                "*",
                "-e",
                " ".join(_RSYNC_SSH),
                "user@host:/remote/path/",
                str(local) + "/",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_skips_rsync_when_remote_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"

        exists_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=1
        )

        with patch(
            "promptdeploy.ssh.subprocess.run", return_value=exists_result
        ) as mock_run:
            ssh_pull("user@host", Path("/remote/path"), local)

        assert local.exists()
        assert mock_run.call_count == 1

    def test_raises_on_rsync_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"

        exists_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="connection refused"
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [exists_result, rsync_result]
            with pytest.raises(
                SSHError, match="rsync pull.*failed.*connection refused"
            ):
                ssh_pull("user@host", Path("/remote/path"), local)


class TestSSHPush:
    def test_creates_remote_parent_and_syncs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [mkdir_result, rsync_result]
            ssh_push("user@host", Path("/remote/path"), local)

        assert mock_run.call_count == 2
        mkdir_call = mock_run.call_args_list[0]
        assert mkdir_call == call(
            ["ssh", *_SSH_OPTS, "user@host", "mkdir", "-p", "/remote"],
            capture_output=True,
        )
        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                " ".join(_RSYNC_SSH),
                str(local) + "/",
                "user@host:/remote/path/",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_verbose_adds_v_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [mkdir_result, rsync_result]
            ssh_push("user@host", Path("/remote/path"), local, verbose=True)

        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "-v",
                "-e",
                " ".join(_RSYNC_SSH),
                str(local) + "/",
                "user@host:/remote/path/",
            ],
            stdout=None,
            stderr=sys.stderr,
            text=True,
        )

    def test_includes_adds_filter_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()
        includes = ["agents/", "agents/**"]

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [mkdir_result, rsync_result]
            ssh_push("user@host", Path("/remote/path"), local, includes=includes)

        rsync_call = mock_run.call_args_list[1]
        assert rsync_call == call(
            [
                "rsync",
                "-az",
                "--delete",
                "--include",
                "agents/",
                "--include",
                "agents/**",
                "--exclude",
                "*",
                "-e",
                " ".join(_RSYNC_SSH),
                str(local) + "/",
                "user@host:/remote/path/",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_raises_on_rsync_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [mkdir_result, rsync_result]
            with pytest.raises(SSHError, match="rsync push.*failed.*permission denied"):
                ssh_push("user@host", Path("/remote/path"), local)

    def test_raises_on_mkdir_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed remote mkdir is surfaced instead of silently ignored (B32)."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=1, stderr=b"mkdir: permission denied"
        )

        with (
            patch("promptdeploy.ssh.subprocess.run", return_value=mkdir_result),
            pytest.raises(SSHError, match="permission denied"),
        ):
            ssh_push("user@host", Path("/remote/path"), local)

    def test_mkdir_quotes_tilde_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mkdir path is quoted but a leading ~/ stays unquoted (B32)."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        local = tmp_path / "staging"
        local.mkdir()

        mkdir_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        rsync_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("promptdeploy.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [mkdir_result, rsync_result]
            ssh_push("user@host", Path("~/my dir/sub"), local)

        mkdir_call = mock_run.call_args_list[0]
        assert mkdir_call == call(
            ["ssh", *_SSH_OPTS, "user@host", "mkdir", "-p", "~/'my dir'"],
            capture_output=True,
        )
