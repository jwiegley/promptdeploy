"""SSH transport for remote target deployment via rsync."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import Any

# SSH options that prevent interactive hangs:
# - BatchMode=yes: fail immediately instead of prompting for passwords/keys
# - ConnectTimeout=10: don't wait forever for unreachable hosts
# - StrictHostKeyChecking=yes: fail closed -- never auto-accept an unknown
#   host key. Remote MCP deploys transport real secrets over this channel, so
#   a spoofed first connection must NOT be silently trusted; the host must be
#   in known_hosts (seed it out-of-band before the first remote deploy).
_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
]

# rsync uses -e to pass SSH options
_RSYNC_SSH = ["ssh", *_SSH_OPTS]
_REMOTE_OPERATION_TIMEOUT_SECONDS = 30
_REMOTE_FINGERPRINT_TIMEOUT_SECONDS = 30


class SSHError(Exception):
    """Raised when an SSH or rsync operation fails."""


def _validate_host(host: object) -> str:
    """Reject option-like or transport-ambiguous SSH destinations."""
    if (
        not isinstance(host, str)
        or not host
        or not host.isprintable()
        or any(character.isspace() for character in host)
        or host.startswith("-")
        or "/" in host
        or "\\" in host
    ):
        raise SSHError("Invalid SSH host value")

    parts = host.split("@")
    if len(parts) > 2:
        raise SSHError("Invalid SSH host value")
    if len(parts) == 2:
        user, destination = parts
        if not user or any(character in user for character in ":[]"):
            raise SSHError("Invalid SSH host value")
    else:
        destination = parts[0]

    if not destination or destination.startswith("-"):
        raise SSHError("Invalid SSH host value")
    if ":" in destination:
        if not (
            destination.startswith("[")
            and destination.endswith("]")
            and destination.count("[") == 1
            and destination.count("]") == 1
            and ":" in destination[1:-1]
        ):
            raise SSHError("Invalid SSH host value")
    elif "[" in destination or "]" in destination:
        raise SSHError("Invalid SSH host value")
    return host


def _check_tools() -> None:
    """Verify that rsync and ssh are available on PATH."""
    for tool in ("rsync", "ssh"):
        if shutil.which(tool) is None:
            raise SSHError(
                f"'{tool}' not found on PATH; required for remote deployment"
            )


def _quote_remote_path(remote_path: Path) -> str:
    """Quote a remote path for use on an ssh command line.

    ssh joins its arguments with spaces and hands the result to the remote
    shell, so paths containing spaces or shell metacharacters must be
    quoted. A leading ``~`` or ``~/`` is kept outside the quotes so the
    remote shell still performs home-directory expansion (deploy.yaml
    remote paths rely on this).
    """
    s = str(remote_path)
    if s == "~":
        return s
    if s.startswith("~/"):
        return "~/" + shlex.quote(s[2:])
    return shlex.quote(s)


def ssh_exists(host: str, remote_path: Path) -> bool:
    """Check if a directory exists on a remote host.

    Raises SSHError if the host is unreachable (returncode 255).
    Returns False only when the host is reachable but the directory
    does not exist.
    """
    host = _validate_host(host)
    _check_tools()
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "test", "-d", _quote_remote_path(remote_path)],
            capture_output=True,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"SSH existence check on {host} timed out") from exc
    if result.returncode == 255:
        stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
        raise SSHError(
            f"SSH connection to {host} failed: {stderr}. "
            "If this is a new host, add its key to known_hosts first "
            "(StrictHostKeyChecking=yes does not auto-accept unknown keys)."
        )
    return result.returncode == 0


def _rsync_filter_args(includes: Sequence[str] | None) -> list[str]:
    """Build rsync include/exclude filter arguments."""
    if not includes:
        return []
    args: list[str] = []
    for pattern in includes:
        args.extend(["--include", pattern])
    args.extend(["--exclude", "*"])
    return args


def ssh_pull(
    host: str,
    remote_path: Path,
    local_path: Path,
    *,
    verbose: bool = False,
    includes: Sequence[str] | None = None,
) -> None:
    """Sync a remote directory to a local staging directory.

    If the remote directory does not exist, the local path is left empty
    (treated as a fresh deployment target).
    """
    host = _validate_host(host)
    _check_tools()
    local_path.mkdir(parents=True, exist_ok=True)

    if not ssh_exists(host, remote_path):
        return

    # Trailing slash on source means "contents of", so local_path gets
    # the contents of remote_path rather than a nested subdirectory.
    src = f"{host}:{remote_path}/"
    cmd = [
        "rsync",
        "-az",
        "--delete",
        *(["-v"] if verbose else []),
        *_rsync_filter_args(includes),
        "-e",
        " ".join(_RSYNC_SSH),
        "--",
        src,
        str(local_path) + "/",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=None if verbose else subprocess.PIPE,
            stderr=sys.stderr if verbose else subprocess.PIPE,
            text=True,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"rsync pull from {host} timed out") from exc
    if result.returncode != 0:
        stderr = result.stderr or ""
        raise SSHError(f"rsync pull from {host}:{remote_path} failed: {stderr}")


def ssh_push(
    host: str,
    remote_path: Path,
    local_path: Path,
    *,
    verbose: bool = False,
    includes: Sequence[str] | None = None,
) -> None:
    """Sync a local staging directory to a remote directory.

    Creates the remote parent directory if it does not exist.
    """
    host = _validate_host(host)
    _check_tools()
    # Ensure remote parent directory exists
    parent = remote_path.parent
    try:
        mkdir_result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "mkdir", "-p", _quote_remote_path(parent)],
            capture_output=True,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"Remote directory creation on {host} timed out") from exc
    if mkdir_result.returncode != 0:
        stderr = (
            mkdir_result.stderr.decode(errors="replace").strip()
            if mkdir_result.stderr
            else ""
        )
        raise SSHError(
            f"Failed to create remote directory {parent} on {host}: {stderr}"
        )

    src = str(local_path) + "/"
    dst = f"{host}:{remote_path}/"
    cmd = [
        "rsync",
        "-az",
        "--delete",
        *(["-v"] if verbose else []),
        *_rsync_filter_args(includes),
        "-e",
        " ".join(_RSYNC_SSH),
        "--",
        src,
        dst,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=None if verbose else subprocess.PIPE,
            stderr=sys.stderr if verbose else subprocess.PIPE,
            text=True,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"rsync push to {host} timed out") from exc
    if result.returncode != 0:
        stderr = result.stderr or ""
        raise SSHError(f"rsync push to {host}:{remote_path} failed: {stderr}")


# NOTE on this template's design:
#   * SECURITY: the entire program body after _fail() is wrapped in one outer
#     try/except BaseException -> _fail("unexpected error during merge"). This
#     makes "no secret-bearing value ever reaches stderr" a STRUCTURAL property,
#     independent of the remote interpreter's default traceback formatter. The
#     base64-decoded ops live in locals `ops`/`op`/`entry`; without this guard a
#     KeyError/ValueError raised in the merge loop or path-expansion could emit a
#     traceback that some interpreters (e.g. -X dev, custom excepthook) render
#     with locals. _fail prints ONLY fixed strings.
#   * The $PAYLOAD_B64 and $TARGET_PATH_REPR placeholders are filled by
#     build_claude_merge_script via string.Template.substitute. target_path is
#     injected as a repr() literal; ops only as base64.
_REMOTE_MERGE_TEMPLATE = Template(
    """\
import base64, json, os, sys, tempfile

def _fail(msg):
    # NEVER print the payload, ops, or any entry value here.
    sys.stderr.write("promptdeploy remote MCP merge failed: " + msg + "\\n")
    sys.exit(1)

try:
    try:
        raw = base64.b64decode("$PAYLOAD_B64")
        ops = json.loads(raw.decode("utf-8"))
    except Exception:
        _fail("could not decode operations payload")

    path = os.path.expanduser($TARGET_PATH_REPR)

    data = {}
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            text = blob.decode("utf-8")
            if text.strip():
                data = json.loads(text)
        if not isinstance(data, dict):
            _fail("existing .claude.json is not a JSON object")
    except json.JSONDecodeError:
        _fail("existing .claude.json is not valid JSON; fix or remove it on the remote")
    except UnicodeDecodeError:
        _fail("existing .claude.json is not valid UTF-8; fix or remove it remotely")
    except OSError:
        _fail("could not read existing .claude.json")

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers

    for op in ops:
        name = op["name"]
        if op["action"] == "pop":
            servers.pop(name, None)
        else:
            servers[name] = op["entry"]

    if not servers:
        data.pop("mcpServers", None)

    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        _fail("could not write .claude.json atomically")
except SystemExit:
    raise
except BaseException:
    _fail("unexpected error during merge")
"""
)


def build_claude_merge_script(ops: Sequence[dict[str, Any]], target_path: str) -> str:
    """Render the python3 program that surgically merges MCP ops into a remote
    .claude.json.

    ``ops`` is a list of ``{"action": "set"|"pop", "name": str, "entry":
    dict|None}``. It is embedded as ``base64(json(ops))`` INSIDE the returned
    source so the secret-bearing payload is never an argv token and never
    appears in ``ps``/``/proc/<pid>/cmdline``; the program decodes it from its
    own text. ``target_path`` is the remote .claude.json path (e.g.
    ``"~/.claude/.claude.json"``); ``~`` is expanded on the remote via
    ``os.path.expanduser`` inside the program.

    The program loads the file (or ``{}`` if missing/blank), sets/pops
    ``mcpServers[name]`` per op, and writes atomically (``mkstemp`` 0600 in the
    same dir + ``os.replace``). Its entire body is wrapped in an outer
    try/except so any error prints ONLY a fixed diagnostic to stderr (never the
    payload, ops, or any entry value) and exits non-zero; stdout stays empty on
    success.
    """
    payload = base64.b64encode(
        json.dumps(list(ops), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return _REMOTE_MERGE_TEMPLATE.substitute(
        PAYLOAD_B64=payload,
        TARGET_PATH_REPR=repr(target_path),
    )


_REMOTE_MCP_FINGERPRINT_TEMPLATE = Template(
    """\
import hashlib, json, os, sys

def _fail(msg):
    # NEVER print configuration values here.
    sys.stderr.write("promptdeploy remote MCP fingerprint failed: " + msg + "\\n")
    sys.exit(1)

try:
    path = os.path.expanduser($TARGET_PATH_REPR)
    name = $NAME_REPR

    try:
        if not os.path.exists(path):
            sys.stdout.write("missing\\n")
            sys.exit(0)
        with open(path, "rb") as f:
            blob = f.read()
        text = blob.decode("utf-8")
        data = json.loads(text) if text.strip() else {}
        if not isinstance(data, dict):
            _fail("existing .claude.json is not a JSON object")
    except json.JSONDecodeError:
        _fail("existing .claude.json is not valid JSON")
    except UnicodeDecodeError:
        _fail("existing .claude.json is not valid UTF-8")
    except OSError:
        _fail("could not read existing .claude.json")

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        sys.stdout.write("missing\\n")
        sys.exit(0)

    encoded = json.dumps(
        servers[name],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    sys.stdout.write("sha256:" + hashlib.sha256(encoded).hexdigest() + "\\n")
except SystemExit:
    raise
except BaseException:
    _fail("unexpected error during fingerprint")
"""
)


def mcp_entry_fingerprint(entry: object) -> str:
    """Return the canonical SHA-256 used by the remote named-entry probe."""
    encoded = json.dumps(
        entry,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_claude_mcp_fingerprint_script(name: str, target_path: str) -> str:
    """Build a read-only probe that emits only `missing` or a SHA-256.

    The remote program parses the app-owned JSON but never emits the named
    entry or any unrelated value. Both substitutions are Python string
    literals, not shell arguments.
    """
    return _REMOTE_MCP_FINGERPRINT_TEMPLATE.substitute(
        NAME_REPR=repr(name),
        TARGET_PATH_REPR=repr(target_path),
    )


def ssh_remote_mcp_fingerprint(
    host: str,
    target_path: str,
    name: str,
) -> str | None:
    """Return the remote named MCP entry fingerprint, or None when absent.

    The SSH argv contains no configuration data. The probe travels over stdin
    and its stdout is accepted only when it is exactly `missing` or the
    canonical SHA-256 shape; unexpected output is rejected without echoing it.
    """
    host = _validate_host(host)
    _check_tools()
    script = build_claude_mcp_fingerprint_script(name, target_path)
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "python3", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=_REMOTE_FINGERPRINT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"Remote MCP fingerprint on {host} timed out") from exc
    stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
    if result.returncode == 255:
        raise SSHError(f"SSH connection to {host} failed: {stderr}")
    if result.returncode == 127:
        raise SSHError(
            f"python3 not found on {host} (exit 127): {stderr}. "
            "The remote MCP fingerprint requires python3 on the "
            "non-interactive PATH."
        )
    if result.returncode != 0:
        raise SSHError(
            f"Remote MCP fingerprint on {host} failed "
            f"(exit {result.returncode}): {stderr}"
        )

    try:
        output = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SSHError(
            f"Remote MCP fingerprint on {host} returned invalid output"
        ) from exc
    if output == "missing":
        return None
    if re.fullmatch(r"sha256:[0-9a-f]{64}", output):
        return output
    raise SSHError(f"Remote MCP fingerprint on {host} returned invalid output")


def ssh_stdin(host: str, script: str) -> None:
    """Run ``python3 -`` on ``host``, piping ``script`` (which embeds the
    payload) via STDIN.

    SECURITY INVARIANT: never interpolate ``script`` into any message, log, or
    exception -- it embeds the base64 secret payload. The remote process argv
    is exactly ``["ssh", *_SSH_OPTS, host, "python3", "-"]``, so a secret
    embedded in ``script`` never appears in ``ps``/``/proc/<pid>/cmdline``. The
    program is written to the child's stdin (``input=script.encode()``), NOT
    passed as an argument.

    Raises ``SSHError`` naming ``host`` on any non-zero exit. returncode 255 is
    a connection failure; 127 is missing python3; any other non-zero is a
    remote failure (the program's ``_fail`` diagnostic). Only the remote stderr
    + host are interpolated into the message; this is SAFE because the merge
    program never prints the payload/ops/entries.
    """
    host = _validate_host(host)
    _check_tools()
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "python3", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"Remote MCP merge on {host} timed out") from exc
    if result.returncode == 0:
        return
    stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
    if result.returncode == 255:
        raise SSHError(f"SSH connection to {host} failed: {stderr}")
    if result.returncode == 127:
        raise SSHError(
            f"python3 not found on {host} (exit 127): {stderr}. "
            "The remote MCP merge requires python3 on the non-interactive PATH."
        )
    raise SSHError(
        f"Remote MCP merge on {host} failed (exit {result.returncode}): {stderr}"
    )
