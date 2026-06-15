"""Tests for promptdeploy SSH transport functions."""

from __future__ import annotations

import base64
import json
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
    build_claude_merge_script,
    ssh_exists,
    ssh_pull,
    ssh_push,
    ssh_stdin,
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
            pytest.raises(SSHError, match=r"SSH connection to .* failed"),
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
                SSHError, match=r"rsync pull.*failed.*connection refused"
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
            with pytest.raises(
                SSHError, match=r"rsync push.*failed.*permission denied"
            ):
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


class TestSSHOptsFailClosed:
    def test_strict_host_key_checking_is_yes(self) -> None:
        """Resolved decision #4: host-key trust fails closed (no accept-new)."""
        assert "StrictHostKeyChecking=yes" in _SSH_OPTS
        assert "StrictHostKeyChecking=accept-new" not in _SSH_OPTS


_SET_OPS = [
    {"action": "set", "name": "srv", "entry": {"command": "npx", "args": ["x"]}}
]


class TestBuildClaudeMergeScript:
    def test_build_merge_script_embeds_base64_ops(self) -> None:
        script = build_claude_merge_script(_SET_OPS, "~/.claude/.claude.json")
        expected_b64 = base64.b64encode(
            json.dumps(list(_SET_OPS), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        assert f'base64.b64decode("{expected_b64}")' in script
        assert "import base64, json" in script
        assert "os.replace" in script

    def test_build_merge_script_compiles(self) -> None:
        script = build_claude_merge_script(_SET_OPS, "~/.claude/.claude.json")
        compile(script, "<remote>", "exec")  # must not raise

    def test_build_merge_script_no_secret_in_text(self) -> None:
        ops = [
            {
                "action": "set",
                "name": "srv",
                "entry": {"env": {"TOK": "super-secret-token"}},
            }
        ]
        script = build_claude_merge_script(ops, "~/.claude/.claude.json")
        assert "super-secret-token" not in script

    def test_build_merge_script_target_path_repr(self) -> None:
        script = build_claude_merge_script(_SET_OPS, "~/.claude/.claude.json")
        assert "os.path.expanduser('~/.claude/.claude.json')" in script

    def test_build_merge_script_adversarial_value_roundtrips(self) -> None:
        adversarial = 'a"b{c}d\\e${X}f'
        ops = [{"action": "set", "name": "srv", "entry": {"env": {"K": adversarial}}}]
        script = build_claude_merge_script(ops, "~/.claude/.claude.json")
        # Extract the base64 literal and round-trip it.
        marker = 'base64.b64decode("'
        start = script.index(marker) + len(marker)
        end = script.index('"', start)
        decoded = json.loads(base64.b64decode(script[start:end]).decode("utf-8"))
        assert decoded[0]["entry"]["env"]["K"] == adversarial


def _run_remote_program(script: str) -> subprocess.CompletedProcess[bytes]:
    """Execute the rendered remote merge program in a child interpreter."""
    return subprocess.run(
        [sys.executable, "-"],
        input=script.encode("utf-8"),
        capture_output=True,
    )


class TestRemoteProgramBehavior:
    def test_remote_program_set_preserves_siblings(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        target.write_text(
            json.dumps({"oauth": {"x": 1}, "mcpServers": {"old": {"command": "o"}}})
        )
        ops = [{"action": "set", "name": "new", "entry": {"command": "n"}}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode == 0, result.stderr
        data = json.loads(target.read_text())
        assert data["oauth"] == {"x": 1}
        assert data["mcpServers"]["old"] == {"command": "o"}
        assert data["mcpServers"]["new"] == {"command": "n"}

    def test_remote_program_pop_to_empty_drops_key(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        target.write_text(json.dumps({"mcpServers": {"only": {"command": "o"}}}))
        ops = [{"action": "pop", "name": "only", "entry": None}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode == 0, result.stderr
        data = json.loads(target.read_text())
        assert "mcpServers" not in data

    def test_remote_program_missing_file_creates(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        ops = [{"action": "set", "name": "srv", "entry": {"command": "c"}}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode == 0, result.stderr
        data = json.loads(target.read_text())
        assert data == {"mcpServers": {"srv": {"command": "c"}}}

    def test_remote_program_blank_file_treated_empty(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        target.write_text("   \n  ")
        ops = [{"action": "set", "name": "srv", "entry": {"command": "c"}}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode == 0, result.stderr
        data = json.loads(target.read_text())
        assert data == {"mcpServers": {"srv": {"command": "c"}}}

    def test_remote_program_invalid_json_aborts_no_clobber(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / ".claude.json"
        target.write_text("not json {")
        ops = [{"action": "set", "name": "srv", "entry": {"command": "c"}}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode != 0
        assert b"not valid JSON" in result.stderr
        assert b"command" not in result.stderr
        assert target.read_text() == "not json {"

    def test_remote_program_non_object_aborts(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        target.write_text(json.dumps([1, 2, 3]))
        ops = [{"action": "set", "name": "srv", "entry": {"command": "c"}}]
        result = _run_remote_program(build_claude_merge_script(ops, str(target)))
        assert result.returncode != 0
        assert b"not a JSON object" in result.stderr
        assert json.loads(target.read_text()) == [1, 2, 3]

    def test_remote_program_malformed_op_fails_no_secret(self, tmp_path: Path) -> None:
        target = tmp_path / ".claude.json"
        # Hand-build a payload missing "name" but carrying a sentinel secret.
        ops = [{"action": "set", "entry": {"env": {"K": "SENTINEL-SECRET"}}}]
        script = build_claude_merge_script(ops, str(target))
        result = _run_remote_program(script)
        assert result.returncode != 0
        assert result.stderr.strip() == (
            b"promptdeploy remote MCP merge failed: unexpected error during merge"
        )
        assert b"SENTINEL-SECRET" not in result.stderr


_SCRIPT = "print('hi')\n"


def _completed(returncode: int, stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stderr=stderr, stdout=b""
    )


class TestSSHStdin:
    def test_ssh_stdin_success_pipes_script_via_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        with patch(
            "promptdeploy.ssh.subprocess.run", return_value=_completed(0)
        ) as mock_run:
            ssh_stdin("user@host", _SCRIPT)  # returns None, no raise
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["ssh", *_SSH_OPTS, "user@host", "python3", "-"]
        assert kwargs["input"] == _SCRIPT.encode("utf-8")
        assert kwargs["capture_output"] is True
        assert "text" not in kwargs

    def test_ssh_stdin_connection_failure_255(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        with (
            patch(
                "promptdeploy.ssh.subprocess.run",
                return_value=_completed(255, b"connection refused"),
            ),
            pytest.raises(SSHError, match=r"SSH connection to .* failed"),
        ):
            ssh_stdin("user@host", _SCRIPT)

    def test_ssh_stdin_python3_missing_127(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        with (
            patch(
                "promptdeploy.ssh.subprocess.run",
                return_value=_completed(127, b"python3: command not found"),
            ),
            pytest.raises(SSHError, match=r"python3 not found on"),
        ):
            ssh_stdin("user@host", _SCRIPT)

    def test_ssh_stdin_remote_failure_other_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        with (
            patch(
                "promptdeploy.ssh.subprocess.run",
                return_value=_completed(1, b"could not write .claude.json atomically"),
            ),
            pytest.raises(SSHError, match=r"Remote MCP merge on .* failed \(exit 1\)"),
        ):
            ssh_stdin("user@host", _SCRIPT)

    def test_ssh_stdin_failure_no_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        with (
            patch("promptdeploy.ssh.subprocess.run", return_value=_completed(1, b"")),
            pytest.raises(SSHError),
        ):
            ssh_stdin("user@host", _SCRIPT)

    def test_ssh_stdin_secret_absent_from_argv_present_in_stdin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        ops = [
            {
                "action": "set",
                "name": "srv",
                "entry": {"env": {"K": "SECRET-SENTINEL-XYZ"}},
            }
        ]
        script = build_claude_merge_script(ops, "~/.claude/.claude.json")
        expected_b64 = base64.b64encode(
            json.dumps(list(ops), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        with patch(
            "promptdeploy.ssh.subprocess.run", return_value=_completed(0)
        ) as mock_run:
            ssh_stdin("user@host", script)
        args, kwargs = mock_run.call_args
        assert "SECRET-SENTINEL-XYZ" not in repr(args)
        for token in args[0]:
            assert "SECRET-SENTINEL-XYZ" not in token
        assert expected_b64.encode("ascii") in kwargs["input"]

    def test_ssh_stdin_tools_missing_raises_before_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda tool: None if tool == "ssh" else "/usr/bin/rsync"
        )
        with (
            patch("promptdeploy.ssh.subprocess.run") as mock_run,
            pytest.raises(SSHError),
        ):
            ssh_stdin("user@host", _SCRIPT)
        mock_run.assert_not_called()

    def test_ssh_stdin_error_message_never_contains_script(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        ops = [
            {
                "action": "set",
                "name": "srv",
                "entry": {"env": {"K": "SECRET-SENTINEL-XYZ"}},
            }
        ]
        script = build_claude_merge_script(ops, "~/.claude/.claude.json")
        b64 = base64.b64encode(
            json.dumps(list(ops), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        for rc in (255, 127, 1):
            with (
                patch(
                    "promptdeploy.ssh.subprocess.run",
                    return_value=_completed(rc, b"benign remote stderr"),
                ),
                pytest.raises(SSHError) as exc_info,
            ):
                ssh_stdin("user@host", script)
            assert b64 not in str(exc_info.value)
