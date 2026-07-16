"""Dormant semantic conformance probes for rendered Ponytail snapshots.

The harness deliberately has no deployed-filesystem authority. It accepts only
the retained installed bytes in a validated :class:`RenderedBundlePlan`, makes
private temporary copies, and returns a local diagnostic summary in memory.

The installed snapshot and ambient Node executable are trusted inputs, not
hostile code. Stream sizes and sequential child execution are bounded, and
ordinary same-group descendants are cleaned up, but this is not an OS sandbox:
code that deliberately starts a detached session can escape process-group
supervision. Local validation, bounded materialization, inventory, and cleanup
are synchronous and are not preempted by the child-execution budget.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, cast

from .bundle_projection import (
    InstalledTreeEntry,
    InstalledTreeSnapshot,
    installed_tree_sha256,
    validate_closed_rendered_bundle,
)
from .bundle_render import RenderedBundlePlan
from .bundles import BundleSchemaError

SnapshotSurface = Literal["claude", "codex"]
SnapshotProbeName = Literal[
    "node-version",
    "relative-module-graph",
    "canonical-session-start",
    "missing-canonical-skill",
    "one-shot-review",
    "state-round-trip",
    "subagent-event",
]
SnapshotFailureKind = Literal[
    "invalid-plan",
    "unsupported-probe-platform",
    "materialization-failed",
    "node-not-found",
    "node-launch-failed",
    "node-timeout",
    "node-output-limit",
    "node-exit",
    "probe-contract",
    "process-cleanup-failed",
    "temporary-cleanup-failed",
]

_PROBES: tuple[SnapshotProbeName, ...] = (
    "node-version",
    "relative-module-graph",
    "canonical-session-start",
    "missing-canonical-skill",
    "one-shot-review",
    "state-round-trip",
    "subagent-event",
)
_NODE_VERSION = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_NODE_VERSION_MAX = 256
_MAX_TREE_ENTRIES = 128
_MAX_TREE_BYTES = 4 * 1024 * 1024
_MAX_STDIN = 8 * 1024
_MAX_STDOUT = 64 * 1024
_MAX_STDERR = 64 * 1024
_MAX_PRIVATE_ENTRIES = 128
_MAX_PRIVATE_FILE_BYTES = 64 * 1024
_MAX_PRIVATE_BYTES = 256 * 1024
_NODE_VERSION_TIMEOUT = 2.0
_HOOK_TIMEOUT = 5.0
_PROCESS_EXECUTION_BUDGET = 30.0
_PROCESS_CLEANUP_RESERVE = 1.0
_TERMINATE_GRACE = 0.25
_PIPE_DRAIN_GRACE = 0.1
_READ_CHUNK = 8192

_CANONICAL_SKILL = "skills/ponytail/SKILL.md"
_CANONICAL_RULE = b"The shortest path to done is the right path."
_OLD_FALLBACK = b"The best code is the code never written"
_REVIEWED_INSTALLED_RUNTIME_SHA256 = (
    "sha256:46bd65bad6023d631340e3262418866206e95ea5afb38d9bab8dbd567fc32d24"
)
_FULL_CONTEXT_SIZE = 5252
_FULL_CONTEXT_SHA256 = (
    "da4fb09cff2f6726691ce6591cebc38c95597d79da132e49c6fa2665c4e8a3ff"
)
_LITE_CONTEXT_SIZE = 5225
_LITE_CONTEXT_SHA256 = (
    "ea09a138c7aad46645e7ad1e60b4c552638314e689cdca1d27c2ba42fc2380eb"
)
_REVIEW_CONTEXT = (
    "PONYTAIL MODE ACTIVE — level: review. Behavior defined by /ponytail-review skill."
)
_LITE_SWITCH_CONTEXT = "PONYTAIL MODE CHANGED — level: lite"
_SETTINGS_BYTES = b'{"statusLine":{"type":"command","command":"true"}}\n'

_CONFIG_EXPORTS = (
    "DEFAULT_MODE",
    "RUNTIME_MODES",
    "VALID_MODES",
    "getClaudeDir",
    "getConfigDir",
    "getConfigPath",
    "getDefaultMode",
    "getHideStatus",
    "getQuietStartup",
    "isDeactivationCommand",
    "isShellSafe",
    "normalizeConfigMode",
    "normalizeMode",
    "normalizePersistedMode",
    "writeDefaultMode",
)
_INSTRUCTION_EXPORTS = ("filterSkillBodyForMode", "getPonytailInstructions")
_RUNTIME_EXPORTS = (
    "clearMode",
    "isCodex",
    "isCopilot",
    "isQoder",
    "readMode",
    "setMode",
    "writeHookOutput",
)
_MODULE_CACHE = (
    "hooks/ponytail-config.js",
    "hooks/ponytail-instructions.js",
    "hooks/ponytail-runtime.js",
)
_MODULE_GRAPH_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
const root = fs.realpathSync(process.argv[1]);
function inspect(relative) {
  const value = require(path.join(root, relative));
  const keys = Object.keys(value).sort();
  const types = {};
  for (const key of keys) types[key] = typeof value[key];
  return { keys, types, value };
}
const config = inspect('hooks/ponytail-config.js');
const instructions = inspect('hooks/ponytail-instructions.js');
const runtime = inspect('hooks/ponytail-runtime.js');
const cache = Object.keys(require.cache)
  .filter(file => file.startsWith(root + path.sep))
  .map(file => path.relative(root, file).split(path.sep).join('/'))
  .sort();
process.stdout.write(JSON.stringify({
  config: { keys: config.keys, types: config.types },
  instructions: { keys: instructions.keys, types: instructions.types },
  runtime: { keys: runtime.keys, types: runtime.types },
  flags: {
    isCodex: runtime.value.isCodex,
    isCopilot: runtime.value.isCopilot,
    isQoder: runtime.value.isQoder,
  },
  cache,
}));
""".strip()
_MISSING_SKILL_EXIT = 73
_MISSING_SKILL_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
try {
  require(path.resolve(process.argv[1]));
} catch (error) {
  const errorPath = error && typeof error.path === 'string' ? error.path : null;
  const result = {
    code: error && typeof error.code === 'string' ? error.code : null,
    path: errorPath === null
      ? null
      : path.join(fs.realpathSync(path.dirname(errorPath)), path.basename(errorPath)),
  };
  process.stdout.write(JSON.stringify(result));
  process.exitCode = 73;
}
""".strip()


class PonytailSnapshotProbeError(BundleSchemaError):
    """One sanitized, typed failure from the dormant snapshot probe."""

    kind: SnapshotFailureKind
    probe: SnapshotProbeName | None

    def __init__(
        self,
        kind: SnapshotFailureKind,
        probe: SnapshotProbeName | None = None,
    ) -> None:
        self.kind = kind
        self.probe = probe
        location = f" during {probe}" if probe is not None else ""
        super().__init__(f"Ponytail rendered-snapshot probe failed{location}: {kind}")


def _is_probe_node_version(value: object) -> bool:
    if type(value) is not str or not value or value.strip() != value:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return (
        len(encoded) <= _NODE_VERSION_MAX
        and len(value.splitlines()) == 1
        and _NODE_VERSION.fullmatch(value) is not None
    )


def _validate_snapshot_tree(
    tree: InstalledTreeSnapshot,
    expected_digest: str,
) -> None:
    if type(tree) is not tuple or not tree:
        raise PonytailSnapshotProbeError("invalid-plan")
    if (
        type(expected_digest) is not str
        or expected_digest != _REVIEWED_INSTALLED_RUNTIME_SHA256
    ):
        raise PonytailSnapshotProbeError("invalid-plan")
    total = 0
    for entry in tree:
        if type(entry) is not InstalledTreeEntry:
            raise PonytailSnapshotProbeError("invalid-plan")
        if (
            type(entry.kind) is not str
            or type(entry.relative_path) is not str
            or type(entry.normalized_mode) is not int
            or (entry.content is not None and type(entry.content) is not bytes)
        ):
            raise PonytailSnapshotProbeError("invalid-plan")
        InstalledTreeEntry.__post_init__(entry)
        if entry.content is not None:
            total += len(entry.content)
    if len(tree) > _MAX_TREE_ENTRIES or total > _MAX_TREE_BYTES:
        raise PonytailSnapshotProbeError("invalid-plan")
    if installed_tree_sha256(tree) != expected_digest:
        raise PonytailSnapshotProbeError("invalid-plan")


@dataclass(frozen=True, slots=True)
class _PonytailSnapshotProbeInput:
    """Closed retained bytes accepted by the effectful snapshot executor."""

    target_type: SnapshotSurface
    runtime_tree: InstalledTreeSnapshot
    runtime_tree_sha256: str

    def __post_init__(self) -> None:
        try:
            if type(self.target_type) is not str or self.target_type not in {
                "claude",
                "codex",
            }:
                raise PonytailSnapshotProbeError("invalid-plan")
            _validate_snapshot_tree(self.runtime_tree, self.runtime_tree_sha256)
        except PonytailSnapshotProbeError:
            raise
        except (AttributeError, BundleSchemaError, TypeError, ValueError) as exc:
            raise PonytailSnapshotProbeError("invalid-plan") from exc

    @staticmethod
    def from_rendered(plan: RenderedBundlePlan) -> _PonytailSnapshotProbeInput:
        """Capture only the validated installed runtime, never a host path."""
        if type(plan) is not RenderedBundlePlan:
            raise PonytailSnapshotProbeError("invalid-plan")
        try:
            RenderedBundlePlan.__post_init__(plan)
            validate_closed_rendered_bundle(plan.desired)
            if plan.desired.target_type not in {"claude", "codex"}:
                raise PonytailSnapshotProbeError("invalid-plan")
            tree = plan.desired.runtime_tree
            digest = plan.desired.runtime_tree_sha256
            if tree is None or digest is None:
                raise PonytailSnapshotProbeError("invalid-plan")
            return _PonytailSnapshotProbeInput(
                cast(SnapshotSurface, plan.desired.target_type),
                tree,
                digest,
            )
        except PonytailSnapshotProbeError:
            raise
        except (AttributeError, BundleSchemaError, TypeError, ValueError) as exc:
            raise PonytailSnapshotProbeError("invalid-plan") from exc


@dataclass(frozen=True, slots=True)
class PonytailSnapshotProbeResult:
    """Local snapshot summary only; this makes no deployed-target claim."""

    surface: SnapshotSurface
    runtime_tree_sha256: str
    probe_node_version: str
    probes: tuple[SnapshotProbeName, ...]

    def __post_init__(self) -> None:
        if type(self.surface) is not str or self.surface not in {
            "claude",
            "codex",
        }:
            raise PonytailSnapshotProbeError("probe-contract")
        if (
            type(self.runtime_tree_sha256) is not str
            or self.runtime_tree_sha256 != _REVIEWED_INSTALLED_RUNTIME_SHA256
            or not _is_probe_node_version(self.probe_node_version)
            or type(self.probes) is not tuple
            or not all(type(probe) is str for probe in self.probes)
            or self.probes != _PROBES
        ):
            raise PonytailSnapshotProbeError("probe-contract")


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if (
            type(self.returncode) is not int
            or type(self.stdout) is not bytes
            or type(self.stderr) is not bytes
        ):
            raise TypeError("process results require exact immutable values")


class _ProcessRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        stdin: bytes,
        environment: Mapping[str, str],
        cwd: Path,
        timeout: float,
        stdout_limit: int,
        stderr_limit: int,
        probe: SnapshotProbeName,
    ) -> _ProcessResult: ...


@dataclass(slots=True)
class _ReaderState:
    content: bytearray
    overflow: threading.Event
    done: threading.Event
    failure: BaseException | None = None


@dataclass(slots=True)
class _WriterState:
    done: threading.Event
    failure: BaseException | None = None


def _read_bounded(stream: BinaryIO, limit: int, state: _ReaderState) -> None:
    try:
        while True:
            remaining = limit + 1 - len(state.content)
            if remaining <= 0:
                state.overflow.set()
                return
            chunk = stream.read(min(_READ_CHUNK, remaining))
            if not chunk:
                return
            state.content.extend(chunk[:remaining])
            if len(state.content) > limit:
                state.overflow.set()
                return
    except BaseException as exc:
        state.failure = exc
        state.overflow.set()
    finally:
        state.done.set()


def _write_bounded(stream: BinaryIO, content: bytes, state: _WriterState) -> None:
    try:
        remaining = memoryview(content)
        while remaining:
            written = stream.write(remaining)
            if written is None or written <= 0 or written > len(remaining):
                raise OSError("short process stdin write")
            remaining = remaining[written:]
    except BrokenPipeError:
        pass
    except BaseException as exc:
        state.failure = exc
    finally:
        try:
            stream.close()
        except BrokenPipeError:
            pass
        except BaseException as exc:
            if state.failure is None:
                state.failure = exc
        state.done.set()


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> bool:
    signaled = False
    try:
        os.killpg(process.pid, signal.SIGTERM)
        signaled = True
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.terminate()
            signaled = True
    if signaled and process.poll() is None:
        deadline = time.monotonic() + _TERMINATE_GRACE
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_TERMINATE_GRACE)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_TERMINATE_GRACE)
    return process.poll() is not None and not _process_group_exists(process.pid)


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM cannot prove absence. Fail closed even though Darwin may also
        # report it transiently for a group that has just vanished.
        return True
    return True


def _close_process_pipes(process: subprocess.Popen[bytes]) -> bool:
    closed = True
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except (OSError, ValueError):
                closed = False
    return closed


def _join_threads(
    threads: tuple[threading.Thread, ...],
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    for thread in threads:
        if thread.ident is None:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(timeout=remaining)
    return not any(thread.ident is not None and thread.is_alive() for thread in threads)


def _cleanup_failed_process(
    process: subprocess.Popen[bytes],
    threads: tuple[threading.Thread, ...],
) -> bool:
    reaped = _terminate_and_reap(process)
    pipes_closed = _close_process_pipes(process)
    workers_stopped = _join_threads(threads, _TERMINATE_GRACE)
    return reaped and pipes_closed and workers_stopped


def _run_bounded_process(
    argv: tuple[str, ...],
    *,
    stdin: bytes,
    environment: Mapping[str, str],
    cwd: Path,
    timeout: float,
    stdout_limit: int = _MAX_STDOUT,
    stderr_limit: int = _MAX_STDERR,
    probe: SnapshotProbeName,
) -> _ProcessResult:
    if (
        type(stdin) is not bytes
        or len(stdin) > _MAX_STDIN
        or type(stdout_limit) is not int
        or not 0 < stdout_limit <= _MAX_STDOUT
        or type(stderr_limit) is not int
        or not 0 < stderr_limit <= _MAX_STDERR
    ):
        raise PonytailSnapshotProbeError("probe-contract", probe)
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PonytailSnapshotProbeError("node-launch-failed", probe) from exc

    assert process.stdout is not None
    assert process.stderr is not None
    assert process.stdin is not None
    stdout = _ReaderState(bytearray(), threading.Event(), threading.Event())
    stderr = _ReaderState(bytearray(), threading.Event(), threading.Event())
    writer = _WriterState(threading.Event())
    stdout_thread = threading.Thread(
        target=_read_bounded,
        args=(process.stdout, stdout_limit, stdout),
        name="ponytail-snapshot-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_bounded,
        args=(process.stderr, stderr_limit, stderr),
        name="ponytail-snapshot-stderr",
        daemon=True,
    )
    stdin_thread = threading.Thread(
        target=_write_bounded,
        args=(process.stdin, stdin, writer),
        name="ponytail-snapshot-stdin",
        daemon=True,
    )
    threads = (stdin_thread, stdout_thread, stderr_thread)
    deadline = time.monotonic() + timeout
    cleanup_checked = False
    cleanup_ok = True
    try:
        stdin_thread.start()
        stdout_thread.start()
        stderr_thread.start()
        drain_deadline: float | None = None
        failure: SnapshotFailureKind | None = None
        while True:
            if (
                writer.failure is not None
                or stdout.failure is not None
                or stderr.failure is not None
            ):
                failure = "node-launch-failed"
                break
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                failure = "node-output-limit"
                break

            returncode = process.poll()
            now = time.monotonic()
            if returncode is not None:
                if _process_group_exists(process.pid):
                    failure = "node-launch-failed"
                    break
                if all(state.done.is_set() for state in (writer, stdout, stderr)):
                    break
                if drain_deadline is None:
                    drain_deadline = min(deadline, now + _PIPE_DRAIN_GRACE)
                if now >= drain_deadline:
                    failure = "node-launch-failed"
                    break
            if now >= deadline:
                failure = "node-timeout"
                break
            time.sleep(min(0.01, deadline - now))

        if failure is not None:
            cleanup_ok = _cleanup_failed_process(process, threads)
            cleanup_checked = True
            raise PonytailSnapshotProbeError(failure, probe)
        return _ProcessResult(
            process.wait(),
            bytes(stdout.content),
            bytes(stderr.content),
        )
    except BaseException as exc:
        if not cleanup_checked:
            cleanup_ok = _cleanup_failed_process(process, threads)
            cleanup_checked = True
        if isinstance(exc, PonytailSnapshotProbeError):
            if not cleanup_ok:
                exc.add_note("Ponytail probe process cleanup did not complete")
            raise
        if isinstance(exc, Exception):
            normalized = PonytailSnapshotProbeError("node-launch-failed", probe)
            if not cleanup_ok:
                normalized.add_note("Ponytail probe process cleanup did not complete")
            raise normalized from exc
        if not cleanup_ok:
            exc.add_note("Ponytail probe process cleanup did not complete")
        raise
    finally:
        if not cleanup_checked:
            pipes_closed = _close_process_pipes(process)
            workers_stopped = _join_threads(threads, _TERMINATE_GRACE)
            if not pipes_closed or not workers_stopped:
                raise PonytailSnapshotProbeError("process-cleanup-failed", probe)


@dataclass(frozen=True, slots=True)
class _ProbeLayout:
    root: Path
    healthy_runtime: Path
    missing_runtime: Path
    scratch: Path
    home: Path
    xdg: Path
    temporary: Path
    claude_profile: Path
    plugin_data: Path
    missing_claude_profile: Path
    missing_plugin_data: Path


def _write_exclusive(path: Path, content: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _materialize_tree(tree: InstalledTreeSnapshot, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    directories = [entry for entry in tree if entry.kind == "directory"]
    files = [entry for entry in tree if entry.kind == "file"]
    for entry in sorted(
        directories,
        key=lambda value: (value.relative_path.count("/"), value.relative_path),
    ):
        if entry.relative_path == ".":
            continue
        (destination / entry.relative_path).mkdir(mode=0o700)
    for entry in files:
        assert entry.content is not None
        _write_exclusive(
            destination / entry.relative_path,
            entry.content,
            entry.normalized_mode,
        )
    for entry in sorted(
        directories,
        key=lambda value: (value.relative_path.count("/"), value.relative_path),
        reverse=True,
    ):
        path = (
            destination
            if entry.relative_path == "."
            else destination / entry.relative_path
        )
        path.chmod(entry.normalized_mode, follow_symlinks=False)


def _read_regular_file_bounded(
    path: Path,
    observed: os.stat_result,
    limit: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PonytailSnapshotProbeError("probe-contract") from exc
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino)
            or current.st_size < 0
            or current.st_size > limit
        ):
            raise PonytailSnapshotProbeError("probe-contract")
        content = bytearray()
        while len(content) <= limit:
            chunk = os.read(
                descriptor,
                min(_READ_CHUNK, limit + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > limit or len(content) != current.st_size:
            raise PonytailSnapshotProbeError("probe-contract")
        return bytes(content)
    except OSError as exc:
        raise PonytailSnapshotProbeError("probe-contract") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            raise PonytailSnapshotProbeError("probe-contract") from exc


def _build_layout(root: Path, health: _PonytailSnapshotProbeInput) -> _ProbeLayout:
    layout = _ProbeLayout(
        root=root,
        healthy_runtime=root / "healthy-runtime",
        missing_runtime=root / "missing-runtime",
        scratch=root / "scratch",
        home=root / "home",
        xdg=root / "xdg",
        temporary=root / "tmp",
        claude_profile=root / "claude-profile",
        plugin_data=root / "plugin-data",
        missing_claude_profile=root / "missing-claude-profile",
        missing_plugin_data=root / "missing-plugin-data",
    )
    _materialize_tree(health.runtime_tree, layout.healthy_runtime)
    _materialize_tree(health.runtime_tree, layout.missing_runtime)
    (layout.missing_runtime / _CANONICAL_SKILL).unlink()
    for path in (
        layout.scratch,
        layout.home,
        layout.xdg,
        layout.temporary,
        layout.claude_profile,
        layout.plugin_data,
        layout.missing_claude_profile,
        layout.missing_plugin_data,
    ):
        path.mkdir(mode=0o700)
    _write_exclusive(layout.claude_profile / "settings.json", _SETTINGS_BYTES, 0o600)
    _write_exclusive(
        layout.missing_claude_profile / "settings.json", _SETTINGS_BYTES, 0o600
    )
    _assert_materialized_tree(
        layout.healthy_runtime,
        health.runtime_tree,
        frozenset(),
    )
    _assert_materialized_tree(
        layout.missing_runtime,
        health.runtime_tree,
        frozenset({_CANONICAL_SKILL}),
    )
    return layout


def _child_environment(
    base_path: str,
    layout: _ProbeLayout,
    health: _PonytailSnapshotProbeInput,
    runtime: Path,
    *,
    missing: bool = False,
) -> dict[str, str]:
    claude_profile = layout.missing_claude_profile if missing else layout.claude_profile
    plugin_data = layout.missing_plugin_data if missing else layout.plugin_data
    environment = {
        "PATH": base_path,
        "HOME": str(layout.home),
        "XDG_CONFIG_HOME": str(layout.xdg),
        "TMPDIR": str(layout.temporary),
        "TMP": str(layout.temporary),
        "TEMP": str(layout.temporary),
        "CLAUDE_PLUGIN_ROOT": str(runtime),
        "PLUGIN_ROOT": str(runtime),
        "PONYTAIL_DEFAULT_MODE": "full",
    }
    if health.target_type == "claude":
        environment["CLAUDE_CONFIG_DIR"] = str(claude_profile)
    else:
        environment["PLUGIN_DATA"] = str(plugin_data)
    return environment


def _expected_runtime_paths(
    tree: InstalledTreeSnapshot,
    omitted: frozenset[str],
) -> dict[str, InstalledTreeEntry]:
    return {
        entry.relative_path: entry
        for entry in tree
        if entry.relative_path not in omitted
    }


def _assert_materialized_tree(
    root: Path,
    tree: InstalledTreeSnapshot,
    omitted: frozenset[str],
) -> None:
    expected = _expected_runtime_paths(tree, omitted)
    observed: dict[str, os.stat_result] = {}
    stack = [(root, ".")]
    while stack:
        directory, relative = stack.pop()
        directory_stat = directory.lstat()
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise PonytailSnapshotProbeError("probe-contract")
        observed[relative] = directory_stat
        if len(observed) > _MAX_TREE_ENTRIES:
            raise PonytailSnapshotProbeError("probe-contract")
        for child in os.scandir(directory):
            child_relative = (
                child.name if relative == "." else f"{relative}/{child.name}"
            )
            child_path = directory / child.name
            child_stat = child_path.lstat()
            if stat.S_ISLNK(child_stat.st_mode):
                raise PonytailSnapshotProbeError("probe-contract")
            if stat.S_ISDIR(child_stat.st_mode):
                stack.append((child_path, child_relative))
            elif not stat.S_ISREG(child_stat.st_mode) or child_stat.st_nlink != 1:
                raise PonytailSnapshotProbeError("probe-contract")
            else:
                observed[child_relative] = child_stat
                if len(observed) > _MAX_TREE_ENTRIES:
                    raise PonytailSnapshotProbeError("probe-contract")
    if observed.keys() != expected.keys():
        raise PonytailSnapshotProbeError("probe-contract")
    for relative, entry in expected.items():
        path = root if relative == "." else root / relative
        value = observed[relative]
        if stat.S_IMODE(value.st_mode) != entry.normalized_mode:
            raise PonytailSnapshotProbeError("probe-contract")
        if entry.kind == "file":
            assert entry.content is not None
            if (
                value.st_size != len(entry.content)
                or _read_regular_file_bounded(
                    path,
                    value,
                    len(entry.content),
                )
                != entry.content
            ):
                raise PonytailSnapshotProbeError("probe-contract")


def _private_files(root: Path) -> dict[str, bytes | None]:
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise PonytailSnapshotProbeError("probe-contract")
    observed: dict[str, bytes | None] = {}
    total = 0
    stack = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        for child in os.scandir(directory):
            relative = f"{prefix}/{child.name}" if prefix else child.name
            path = directory / child.name
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode):
                raise PonytailSnapshotProbeError("probe-contract")
            if stat.S_ISDIR(value.st_mode):
                observed[relative] = None
                stack.append((path, relative))
            elif stat.S_ISREG(value.st_mode) and value.st_nlink == 1:
                remaining = _MAX_PRIVATE_BYTES - total
                if value.st_size > _MAX_PRIVATE_FILE_BYTES or value.st_size > remaining:
                    raise PonytailSnapshotProbeError("probe-contract")
                content = _read_regular_file_bounded(
                    path,
                    value,
                    min(_MAX_PRIVATE_FILE_BYTES, remaining),
                )
                total += len(content)
                observed[relative] = content
            else:
                raise PonytailSnapshotProbeError("probe-contract")
            if len(observed) > _MAX_PRIVATE_ENTRIES:
                raise PonytailSnapshotProbeError("probe-contract")
    return observed


def _state_path(
    layout: _ProbeLayout, target: SnapshotSurface, *, missing: bool
) -> Path:
    if target == "claude":
        root = layout.missing_claude_profile if missing else layout.claude_profile
    else:
        root = layout.missing_plugin_data if missing else layout.plugin_data
    return root / ".ponytail-active"


def _assert_private_state(
    layout: _ProbeLayout,
    health: _PonytailSnapshotProbeInput,
    *,
    healthy_mode: bytes | None,
    missing_mode: bytes | None,
) -> None:
    for path in (layout.scratch, layout.home, layout.xdg, layout.temporary):
        if _private_files(path):
            raise PonytailSnapshotProbeError("probe-contract")
    expected_profiles: dict[Path, dict[str, bytes | None]] = {
        layout.claude_profile: {"settings.json": _SETTINGS_BYTES},
        layout.missing_claude_profile: {"settings.json": _SETTINGS_BYTES},
        layout.plugin_data: {},
        layout.missing_plugin_data: {},
    }
    if healthy_mode is not None:
        state = _state_path(layout, health.target_type, missing=False)
        expected_profiles[state.parent][state.name] = healthy_mode
    if missing_mode is not None:
        state = _state_path(layout, health.target_type, missing=True)
        expected_profiles[state.parent][state.name] = missing_mode
    for root, expected in expected_profiles.items():
        if _private_files(root) != expected:
            raise PonytailSnapshotProbeError("probe-contract")
    _assert_materialized_tree(
        layout.healthy_runtime,
        health.runtime_tree,
        frozenset(),
    )
    _assert_materialized_tree(
        layout.missing_runtime,
        health.runtime_tree,
        frozenset({_CANONICAL_SKILL}),
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _json_mapping(value: bytes, probe: SnapshotProbeName) -> dict[str, object]:
    try:
        decoded = value.decode("utf-8")
        parsed: object = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PonytailSnapshotProbeError("probe-contract", probe) from exc
    if type(parsed) is not dict:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    return cast(dict[str, object], parsed)


def _exact_mapping(
    value: object,
    keys: frozenset[str],
    probe: SnapshotProbeName,
) -> dict[str, object]:
    if type(value) is not dict or value.keys() != keys:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    return cast(dict[str, object], value)


def _require_success(result: _ProcessResult, probe: SnapshotProbeName) -> None:
    if result.returncode != 0:
        raise PonytailSnapshotProbeError("node-exit", probe)
    if result.stderr:
        raise PonytailSnapshotProbeError("probe-contract", probe)


def _run_case(
    runner: _ProcessRunner,
    argv: tuple[str, ...],
    *,
    stdin: bytes,
    environment: Mapping[str, str],
    cwd: Path,
    per_process_timeout: float,
    execution_deadline: float,
    stdout_limit: int = _MAX_STDOUT,
    stderr_limit: int = _MAX_STDERR,
    probe: SnapshotProbeName,
) -> _ProcessResult:
    remaining = execution_deadline - time.monotonic() - _PROCESS_CLEANUP_RESERVE
    if remaining <= 0:
        raise PonytailSnapshotProbeError("node-timeout", probe)
    try:
        result = runner(
            argv,
            stdin=stdin,
            environment=environment,
            cwd=cwd,
            timeout=min(per_process_timeout, remaining),
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            probe=probe,
        )
    except PonytailSnapshotProbeError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise PonytailSnapshotProbeError("node-launch-failed", probe) from exc
    if type(result) is not _ProcessResult:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    return result


def _validate_node_version(result: _ProcessResult) -> str:
    probe: SnapshotProbeName = "node-version"
    _require_success(result, probe)
    if not result.stdout or len(result.stdout) > _NODE_VERSION_MAX:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    try:
        text = result.stdout.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PonytailSnapshotProbeError("probe-contract", probe) from exc
    lines = text.splitlines()
    if len(lines) != 1 or not _is_probe_node_version(lines[0]):
        raise PonytailSnapshotProbeError("probe-contract", probe)
    return lines[0]


def _validate_export_shape(
    value: object,
    expected: tuple[str, ...],
    constants: Mapping[str, str],
    probe: SnapshotProbeName,
) -> None:
    shape = _exact_mapping(value, frozenset({"keys", "types"}), probe)
    if shape["keys"] != list(expected):
        raise PonytailSnapshotProbeError("probe-contract", probe)
    types = _exact_mapping(shape["types"], frozenset(expected), probe)
    for name in expected:
        expected_type = constants.get(name, "function")
        if types[name] != expected_type:
            raise PonytailSnapshotProbeError("probe-contract", probe)


def _validate_module_graph(result: _ProcessResult, target: SnapshotSurface) -> None:
    probe: SnapshotProbeName = "relative-module-graph"
    _require_success(result, probe)
    document = _json_mapping(result.stdout, probe)
    if document.keys() != {
        "config",
        "instructions",
        "runtime",
        "flags",
        "cache",
    }:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    _validate_export_shape(
        document["config"],
        _CONFIG_EXPORTS,
        {
            "DEFAULT_MODE": "string",
            "RUNTIME_MODES": "object",
            "VALID_MODES": "object",
        },
        probe,
    )
    _validate_export_shape(
        document["instructions"],
        _INSTRUCTION_EXPORTS,
        {},
        probe,
    )
    _validate_export_shape(
        document["runtime"],
        _RUNTIME_EXPORTS,
        {"isCodex": "boolean", "isCopilot": "boolean", "isQoder": "boolean"},
        probe,
    )
    flags = _exact_mapping(
        document["flags"],
        frozenset({"isCodex", "isCopilot", "isQoder"}),
        probe,
    )
    if flags != {
        "isCodex": target == "codex",
        "isCopilot": False,
        "isQoder": False,
    } or document["cache"] != list(_MODULE_CACHE):
        raise PonytailSnapshotProbeError("probe-contract", probe)


def _native_context(
    result: _ProcessResult,
    target: SnapshotSurface,
    *,
    event: str,
    mode: str,
    probe: SnapshotProbeName,
) -> bytes:
    _require_success(result, probe)
    if target == "claude" and event != "SubagentStart":
        return result.stdout
    document = _json_mapping(result.stdout, probe)
    expected_keys = (
        frozenset({"hookSpecificOutput"})
        if target == "claude"
        else frozenset({"systemMessage", "hookSpecificOutput"})
    )
    if document.keys() != expected_keys:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    if target == "codex" and document["systemMessage"] != f"PONYTAIL:{mode.upper()}":
        raise PonytailSnapshotProbeError("probe-contract", probe)
    hook = _exact_mapping(
        document["hookSpecificOutput"],
        frozenset({"hookEventName", "additionalContext"}),
        probe,
    )
    if hook["hookEventName"] != event or type(hook["additionalContext"]) is not str:
        raise PonytailSnapshotProbeError("probe-contract", probe)
    return hook["additionalContext"].encode("utf-8")


def _validate_context(
    context: bytes,
    *,
    mode: Literal["full", "lite"],
    probe: SnapshotProbeName,
) -> None:
    expected_size, expected_sha = (
        (_FULL_CONTEXT_SIZE, _FULL_CONTEXT_SHA256)
        if mode == "full"
        else (_LITE_CONTEXT_SIZE, _LITE_CONTEXT_SHA256)
    )
    if (
        len(context) != expected_size
        or hashlib.sha256(context).hexdigest() != expected_sha
        or not context.startswith(f"PONYTAIL MODE ACTIVE — level: {mode}".encode())
        or _CANONICAL_RULE not in context
        or b"STATUSLINE SETUP NEEDED" in context
        or _OLD_FALLBACK in context
    ):
        raise PonytailSnapshotProbeError("probe-contract", probe)


def _resolve_node(environment: Mapping[str, str]) -> tuple[Path, str]:
    copied: dict[str, str] = {}
    for key, value in environment.items():
        if type(key) is not str or type(value) is not str:
            raise PonytailSnapshotProbeError("node-launch-failed", "node-version")
        copied[key] = value
    path_value = copied.get("PATH", "")
    candidate = shutil.which("node", path=path_value)
    if candidate is None:
        raise PonytailSnapshotProbeError("node-not-found", "node-version")
    try:
        unresolved = Path(candidate)
        if not unresolved.is_absolute():
            raise OSError("relative executable")
        node = unresolved.resolve(strict=True)
        node_stat = node.stat()
    except OSError as exc:
        raise PonytailSnapshotProbeError("node-not-found", "node-version") from exc
    if not stat.S_ISREG(node_stat.st_mode) or not os.access(node, os.X_OK):
        raise PonytailSnapshotProbeError("node-not-found", "node-version")
    return node, path_value


def _probe_snapshot_input(
    health: _PonytailSnapshotProbeInput,
    node: Path,
    base_path: str,
    layout: _ProbeLayout,
    runner: _ProcessRunner,
    execution_deadline: float,
) -> PonytailSnapshotProbeResult:
    environment = _child_environment(
        base_path,
        layout,
        health,
        layout.healthy_runtime,
    )
    version_result = _run_case(
        runner,
        (str(node), "--version"),
        stdin=b"",
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_NODE_VERSION_TIMEOUT,
        execution_deadline=execution_deadline,
        stdout_limit=_NODE_VERSION_MAX,
        probe="node-version",
    )
    node_version = _validate_node_version(version_result)

    graph_result = _run_case(
        runner,
        (str(node), "-e", _MODULE_GRAPH_SCRIPT, str(layout.healthy_runtime)),
        stdin=b"",
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="relative-module-graph",
    )
    _validate_module_graph(graph_result, health.target_type)
    _assert_private_state(
        layout,
        health,
        healthy_mode=None,
        missing_mode=None,
    )

    activation = _run_case(
        runner,
        (str(node), str(layout.healthy_runtime / "hooks/ponytail-activate.js")),
        stdin=b"",
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="canonical-session-start",
    )
    full_context = _native_context(
        activation,
        health.target_type,
        event="SessionStart",
        mode="full",
        probe="canonical-session-start",
    )
    _validate_context(
        full_context,
        mode="full",
        probe="canonical-session-start",
    )
    _assert_private_state(
        layout,
        health,
        healthy_mode=b"full",
        missing_mode=None,
    )

    missing_environment = _child_environment(
        base_path,
        layout,
        health,
        layout.missing_runtime,
        missing=True,
    )
    missing = _run_case(
        runner,
        (
            str(node),
            "-e",
            _MISSING_SKILL_SCRIPT,
            str(layout.missing_runtime / "hooks/ponytail-activate.js"),
        ),
        stdin=b"",
        environment=missing_environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="missing-canonical-skill",
    )
    missing_document = _json_mapping(missing.stdout, "missing-canonical-skill")
    if (
        missing.returncode != _MISSING_SKILL_EXIT
        or missing.stderr
        or missing_document.keys() != {"code", "path"}
        or missing_document["code"] != "ENOENT"
        or missing_document["path"]
        != str((layout.missing_runtime / _CANONICAL_SKILL).resolve(strict=False))
    ):
        raise PonytailSnapshotProbeError("probe-contract", "missing-canonical-skill")
    _assert_private_state(
        layout,
        health,
        healthy_mode=b"full",
        missing_mode=b"full",
    )

    review = _run_case(
        runner,
        (str(node), str(layout.healthy_runtime / "hooks/ponytail-mode-tracker.js")),
        stdin=b'{"prompt":"/ponytail-review"}',
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="one-shot-review",
    )
    review_context = _native_context(
        review,
        health.target_type,
        event="UserPromptSubmit",
        mode="review",
        probe="one-shot-review",
    )
    if review_context != _REVIEW_CONTEXT.encode("utf-8"):
        raise PonytailSnapshotProbeError("probe-contract", "one-shot-review")
    _assert_private_state(
        layout,
        health,
        healthy_mode=b"full",
        missing_mode=b"full",
    )

    lite = _run_case(
        runner,
        (str(node), str(layout.healthy_runtime / "hooks/ponytail-mode-tracker.js")),
        stdin=b'{"prompt":"/ponytail lite"}',
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="state-round-trip",
    )
    lite_context = _native_context(
        lite,
        health.target_type,
        event="UserPromptSubmit",
        mode="lite",
        probe="state-round-trip",
    )
    if lite_context != _LITE_SWITCH_CONTEXT.encode("utf-8"):
        raise PonytailSnapshotProbeError("probe-contract", "state-round-trip")
    _assert_private_state(
        layout,
        health,
        healthy_mode=b"lite",
        missing_mode=b"full",
    )

    subagent = _run_case(
        runner,
        (str(node), str(layout.healthy_runtime / "hooks/ponytail-subagent.js")),
        stdin=b"",
        environment=environment,
        cwd=layout.scratch,
        per_process_timeout=_HOOK_TIMEOUT,
        execution_deadline=execution_deadline,
        probe="subagent-event",
    )
    subagent_context = _native_context(
        subagent,
        health.target_type,
        event="SubagentStart",
        mode="lite",
        probe="subagent-event",
    )
    _validate_context(
        subagent_context,
        mode="lite",
        probe="subagent-event",
    )
    _assert_private_state(
        layout,
        health,
        healthy_mode=b"lite",
        missing_mode=b"full",
    )
    return PonytailSnapshotProbeResult(
        health.target_type,
        health.runtime_tree_sha256,
        node_version,
        _PROBES,
    )


def _remove_probe_root(root: Path) -> None:
    shutil.rmtree(root)


def probe_rendered_ponytail_snapshot(
    plan: RenderedBundlePlan,
) -> PonytailSnapshotProbeResult:
    """Run local conformance probes against one pinned rendered snapshot.

    This does not inspect deployed paths, hook registration, target Node,
    target trust state, transport, reachability, or Windows behavior. It is a
    conformance check for a trusted pinned snapshot and trusted local Node, not
    a sandbox for hostile code or deliberately detached descendants.
    """
    if os.name != "posix":
        raise PonytailSnapshotProbeError("unsupported-probe-platform")
    execution_deadline = time.monotonic() + _PROCESS_EXECUTION_BUDGET
    health = _PonytailSnapshotProbeInput.from_rendered(plan)
    node, base_path = _resolve_node(os.environ)
    root: Path | None = None
    result: PonytailSnapshotProbeResult | None = None
    primary: BaseException | None = None
    try:
        try:
            root = Path(tempfile.mkdtemp(prefix="promptdeploy-ponytail-snapshot-"))
            root.chmod(0o700)
            layout = _build_layout(root, health)
        except OSError as exc:
            raise PonytailSnapshotProbeError("materialization-failed") from exc
        result = _probe_snapshot_input(
            health,
            node,
            base_path,
            layout,
            _run_bounded_process,
            execution_deadline,
        )
    except BaseException as exc:
        primary = exc

    if root is not None:
        try:
            _remove_probe_root(root)
        except OSError as cleanup_exc:
            if primary is not None:
                primary.add_note("Ponytail probe temporary cleanup also failed")
                raise primary.with_traceback(primary.__traceback__) from cleanup_exc
            raise PonytailSnapshotProbeError(
                "temporary-cleanup-failed"
            ) from cleanup_exc
    if primary is not None:
        raise primary.with_traceback(primary.__traceback__)
    assert result is not None
    return result
