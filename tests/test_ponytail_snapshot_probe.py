"""Adversarial and real-Node tests for the dormant Ponytail snapshot probe."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from promptdeploy import ponytail_snapshot_probe as probe_module
from promptdeploy.bundle_catalog import discover_bundle_items
from promptdeploy.bundle_projection import (
    InstalledTreeEntry,
    installed_tree_sha256,
)
from promptdeploy.bundle_render import (
    BundleRenderContext,
    EmittedHostPath,
    RenderedBundlePlan,
    render_bundle,
)
from promptdeploy.bundles import BundleConfig, BundleSourceBinding
from promptdeploy.ponytail_snapshot_probe import (
    PonytailSnapshotProbeError,
    PonytailSnapshotProbeResult,
    _PonytailSnapshotProbeInput,
    probe_rendered_ponytail_snapshot,
)
from promptdeploy.source import SourceItem

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bundles" / "ponytail.yaml"
RUNTIME_DIGEST = (
    "sha256:46bd65bad6023d631340e3262418866206e95ea5afb38d9bab8dbd567fc32d24"
)


@pytest.fixture(scope="module")
def ponytail_bundle() -> SourceItem:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    bundle = BundleConfig(
        "ponytail",
        MANIFEST,
        BundleSourceBinding(
            "ponytail",
            root.resolve(),
            True,
            None,
            None,
            None,
            "cli",
        ),
    )
    return discover_bundle_items(bundle)[0]


def _context(target_type: str) -> BundleRenderContext:
    if target_type == "codex":
        home = EmittedHostPath("local-target", "posix", "home", ())
        return BundleRenderContext(
            "codex",
            home,
            None,
            home.child(".promptdeploy", "plugin-data", "codex", "ponytail"),
        )
    root = EmittedHostPath("local-target", "posix", "absolute", ("srv", "target"))
    if target_type == "claude":
        return BundleRenderContext("claude", root, root, None)
    return BundleRenderContext(cast(Any, target_type), root, None, None)


def _plan(item: SourceItem, target_type: str) -> RenderedBundlePlan:
    return render_bundle(item, _context(target_type))


def _assert_health_error(
    error: pytest.ExceptionInfo[PonytailSnapshotProbeError],
    kind: str,
    probe: str | None = None,
) -> None:
    assert error.value.kind == kind
    assert error.value.probe == probe
    message = str(error.value)
    assert kind in message
    assert "/Users/" not in message
    assert "/tmp/" not in message


@pytest.mark.parametrize("target_type", ["claude", "codex"])
def test_real_health_sequence_is_exact_isolated_and_cleans_up(
    ponytail_bundle: SourceItem,
    target_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots: list[Path] = []
    original_remove = probe_module._remove_probe_root

    def tracking_remove(root: Path) -> None:
        roots.append(root)
        original_remove(root)

    monkeypatch.setattr(probe_module, "_remove_probe_root", tracking_remove)
    ambient = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": "/must/not/be/read",
        "NODE_OPTIONS": "--require=/must/not/be/loaded.js",
        "NODE_PATH": "/must/not/be/searched",
        "CLAUDE_CONFIG_DIR": "/must/not/be/read/claude",
        "PLUGIN_DATA": "/must/not/be/read/plugin-data",
        "COPILOT_PLUGIN_DATA": "/must/not/be/read/copilot",
        "QODER_SESSION_ID": "must-not-be-read",
        "PONYTAIL_SUBAGENT_MATCHER": "never-match",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)
    result = probe_rendered_ponytail_snapshot(_plan(ponytail_bundle, target_type))
    assert result.surface == target_type
    assert result.runtime_tree_sha256 == RUNTIME_DIGEST
    assert result.probe_node_version
    assert result.probes == probe_module._PROBES
    assert len(roots) == 1
    assert not roots[0].exists()


@pytest.mark.parametrize("target_type", ["droid", "opencode", "gptel"])
def test_health_input_rejects_non_node_targets(
    ponytail_bundle: SourceItem,
    target_type: str,
) -> None:
    with pytest.raises(PonytailSnapshotProbeError) as error:
        _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, target_type))
    _assert_health_error(error, "invalid-plan")


def test_health_input_closes_plan_tree_digest_and_budgets(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    valid = _PonytailSnapshotProbeInput.from_rendered(plan)
    assert valid.runtime_tree_sha256 == RUNTIME_DIGEST

    class PlanSubclass(RenderedBundlePlan):
        pass

    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput.from_rendered(
            PlanSubclass(plan.desired, plan.hook_registration)
        )
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, target_type=cast(Any, "opencode"))
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree=cast(Any, list(valid.runtime_tree)))
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree=())
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree_sha256="sha256:" + "0" * 64)

    entry = valid.runtime_tree[0]

    class EntrySubclass(InstalledTreeEntry):
        pass

    changed_entry = EntrySubclass(
        entry.kind,
        entry.relative_path,
        entry.normalized_mode,
        entry.content,
    )
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree=(changed_entry, *valid.runtime_tree[1:]))

    class StringSubclass(str):
        pass

    forged_entry = object.__new__(InstalledTreeEntry)
    object.__setattr__(forged_entry, "kind", StringSubclass(entry.kind))
    object.__setattr__(forged_entry, "relative_path", entry.relative_path)
    object.__setattr__(forged_entry, "normalized_mode", entry.normalized_mode)
    object.__setattr__(forged_entry, "content", entry.content)
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree=(forged_entry, *valid.runtime_tree[1:]))

    incomplete_entry = object.__new__(InstalledTreeEntry)
    object.__setattr__(incomplete_entry, "kind", "file")
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        replace(valid, runtime_tree=(incomplete_entry, *valid.runtime_tree[1:]))

    monkeypatch.setattr(
        probe_module,
        "installed_tree_sha256",
        lambda _tree: (_ for _ in ()).throw(AttributeError("forged")),
    )
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput("claude", valid.runtime_tree, RUNTIME_DIGEST)
    monkeypatch.undo()

    monkeypatch.setattr(probe_module, "_MAX_TREE_ENTRIES", 0)
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput("claude", valid.runtime_tree, RUNTIME_DIGEST)
    monkeypatch.setattr(probe_module, "_MAX_TREE_ENTRIES", 128)
    monkeypatch.setattr(probe_module, "_MAX_TREE_BYTES", -1)
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput("claude", valid.runtime_tree, RUNTIME_DIGEST)
    monkeypatch.setattr(probe_module, "_MAX_TREE_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(
        probe_module,
        "_REVIEWED_INSTALLED_RUNTIME_SHA256",
        "sha256:" + "0" * 64,
    )
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput(
            "claude",
            valid.runtime_tree,
            "sha256:" + "0" * 64,
        )


def test_health_input_normalizes_invalid_nested_plan(
    ponytail_bundle: SourceItem,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    forged = object.__new__(RenderedBundlePlan)
    object.__setattr__(forged, "desired", plan.desired)
    object.__setattr__(forged, "hook_registration", None)
    with pytest.raises(PonytailSnapshotProbeError) as error:
        _PonytailSnapshotProbeInput.from_rendered(forged)
    _assert_health_error(error, "invalid-plan")

    missing_fields = object.__new__(RenderedBundlePlan)
    with pytest.raises(PonytailSnapshotProbeError) as missing_error:
        _PonytailSnapshotProbeInput.from_rendered(missing_fields)
    _assert_health_error(missing_error, "invalid-plan")


def test_health_input_rejects_validated_plan_without_runtime(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    desired = object.__new__(type(plan.desired))
    object.__setattr__(desired, "target_type", "claude")
    object.__setattr__(desired, "runtime_tree", None)
    object.__setattr__(desired, "runtime_tree_sha256", None)
    forged = object.__new__(RenderedBundlePlan)
    object.__setattr__(forged, "desired", desired)
    object.__setattr__(forged, "hook_registration", plan.hook_registration)
    monkeypatch.setattr(RenderedBundlePlan, "__post_init__", lambda _self: None)
    monkeypatch.setattr(
        probe_module,
        "validate_closed_rendered_bundle",
        lambda value: value,
    )
    with pytest.raises(PonytailSnapshotProbeError, match="invalid-plan"):
        _PonytailSnapshotProbeInput.from_rendered(forged)


def test_health_result_is_closed_and_complete() -> None:
    valid = PonytailSnapshotProbeResult(
        "claude", RUNTIME_DIGEST, "v1.2.3", probe_module._PROBES
    )
    assert valid.probe_node_version == "v1.2.3"
    invalid = (
        {"surface": cast(Any, "opencode")},
        {"runtime_tree_sha256": "bad"},
        {"probe_node_version": cast(Any, "")},
        {"probe_node_version": " v1"},
        {"probe_node_version": "v1\nv2"},
        {"probe_node_version": "v\N{SNOWMAN}"},
        {"probe_node_version": "v" * (probe_module._NODE_VERSION_MAX + 1)},
        {"probes": cast(Any, list(probe_module._PROBES))},
        {"probes": probe_module._PROBES[:-1]},
    )
    for changes in invalid:
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            replace(valid, **changes)


def test_resolve_node_rejects_environment_and_unsafe_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PonytailSnapshotProbeError) as error:
        probe_module._resolve_node(cast(Any, {1: "value"}))
    _assert_health_error(error, "node-launch-failed", "node-version")
    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
        probe_module._resolve_node(cast(Any, {"PATH": 1}))
    with pytest.raises(PonytailSnapshotProbeError, match="node-not-found"):
        probe_module._resolve_node({"PATH": str(tmp_path)})

    monkeypatch.setattr(shutil, "which", lambda *_args, **_kw: "node")
    with pytest.raises(PonytailSnapshotProbeError, match="node-not-found"):
        probe_module._resolve_node({"PATH": str(tmp_path)})

    missing = tmp_path / "missing-node"
    monkeypatch.setattr(
        shutil,
        "which",
        lambda *_args, **_kw: str(missing),
    )
    with pytest.raises(PonytailSnapshotProbeError, match="node-not-found"):
        probe_module._resolve_node({"PATH": str(tmp_path)})

    directory = tmp_path / "node-dir"
    directory.mkdir()
    monkeypatch.setattr(
        shutil,
        "which",
        lambda *_args, **_kw: str(directory),
    )
    with pytest.raises(PonytailSnapshotProbeError, match="node-not-found"):
        probe_module._resolve_node({"PATH": str(tmp_path)})

    executable = tmp_path / "executable"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    link = tmp_path / "node"
    link.symlink_to(executable)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda *_args, **_kw: str(link),
    )
    node, path = probe_module._resolve_node({"PATH": str(tmp_path)})
    assert node == executable
    assert path == str(tmp_path)

    executable.chmod(0o644)
    with pytest.raises(PonytailSnapshotProbeError, match="node-not-found"):
        probe_module._resolve_node({"PATH": str(tmp_path)})


def _python_result(
    tmp_path: Path,
    source: str,
    *,
    stdin: bytes = b"",
    timeout: float = 2.0,
) -> probe_module._ProcessResult:
    return probe_module._run_bounded_process(
        (sys.executable, "-c", source),
        stdin=stdin,
        environment=dict(os.environ),
        cwd=tmp_path,
        timeout=timeout,
        probe="node-version",
    )


def test_bounded_process_success_nonzero_and_broken_stdin(tmp_path: Path) -> None:
    result = _python_result(
        tmp_path,
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data); sys.stderr.buffer.write(b'warn')",
        stdin=b"input",
    )
    assert result == probe_module._ProcessResult(0, b"input", b"warn")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._require_success(result, "node-version")

    nonzero = _python_result(tmp_path, "raise SystemExit(7)")
    assert nonzero.returncode == 7
    with pytest.raises(PonytailSnapshotProbeError, match="node-exit"):
        probe_module._require_success(nonzero, "node-version")

    broken = _python_result(
        tmp_path,
        "import os; os.close(0)",
        stdin=b"x" * probe_module._MAX_STDIN,
    )
    assert broken.returncode == 0


def test_bounded_process_rejects_input_launch_timeout_and_output(
    tmp_path: Path,
) -> None:
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._run_bounded_process(
            (sys.executable, "-c", "pass"),
            stdin=b"x" * (probe_module._MAX_STDIN + 1),
            environment=dict(os.environ),
            cwd=tmp_path,
            timeout=1,
            probe="node-version",
        )
    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
        probe_module._run_bounded_process(
            (str(tmp_path / "missing"),),
            stdin=b"",
            environment=dict(os.environ),
            cwd=tmp_path,
            timeout=1,
            probe="node-version",
        )
    with pytest.raises(PonytailSnapshotProbeError, match="node-timeout"):
        _python_result(tmp_path, "import time; time.sleep(10)", timeout=0.05)

    with pytest.raises(PonytailSnapshotProbeError, match="node-output-limit"):
        probe_module._run_bounded_process(
            (
                sys.executable,
                "-c",
                f"print('x' * {probe_module._NODE_VERSION_MAX})",
            ),
            stdin=b"",
            environment=dict(os.environ),
            cwd=tmp_path,
            timeout=1,
            stdout_limit=probe_module._NODE_VERSION_MAX,
            probe="node-version",
        )

    for stream in ("stdout", "stderr"):
        limit = (
            probe_module._MAX_STDOUT if stream == "stdout" else probe_module._MAX_STDERR
        )
        source = (
            "import sys; "
            f"sys.{stream}.buffer.write(b'x' * {limit + 1}); "
            f"sys.{stream}.flush()"
        )
        with pytest.raises(PonytailSnapshotProbeError, match="node-output-limit"):
            _python_result(tmp_path, source)


def test_concurrent_unbounded_output_is_killed_before_delayed_side_effect(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "pid"
    sentinel = tmp_path / "survived"
    delayed = (
        "import time,pathlib; time.sleep(0.25); "
        f"pathlib.Path({str(sentinel)!r}).write_text('survived')"
    )
    source = (
        "import os,sys,threading; "
        f"open({str(pid_path)!r},'w').write(str(os.getpid())); "
        f"threading.Thread(target=lambda:exec({delayed!r})).start(); "
        "chunk=b'x'*8192; "
        'exec("while True:\\n os.write(1,chunk)\\n os.write(2,chunk)")'
    )
    with pytest.raises(PonytailSnapshotProbeError) as error:
        _python_result(tmp_path, source)
    _assert_health_error(error, "node-output-limit", "node-version")
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    time.sleep(0.3)
    assert not sentinel.exists()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("limit", [True, "1", 0, -1, probe_module._MAX_STDOUT + 1])
def test_bounded_process_rejects_invalid_stream_limits(
    tmp_path: Path,
    stream: str,
    limit: object,
) -> None:
    limits = {
        "stdout_limit": probe_module._MAX_STDOUT,
        "stderr_limit": probe_module._MAX_STDERR,
    }
    limits[f"{stream}_limit"] = cast(Any, limit)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._run_bounded_process(
            (sys.executable, "-c", "pass"),
            stdin=b"",
            environment=dict(os.environ),
            cwd=tmp_path,
            timeout=1,
            probe="node-version",
            **cast(Any, limits),
        )


def test_timeout_kills_term_ignoring_process_group(tmp_path: Path) -> None:
    pid_path = tmp_path / "pid"
    source = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(pid_path)!r},'w').write(str(os.getpid())); "
        "time.sleep(10)"
    )
    with pytest.raises(PonytailSnapshotProbeError, match="node-timeout"):
        _python_result(tmp_path, source, timeout=0.1)
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("inherit_pipes", [True, False])
def test_successful_parent_cannot_leave_process_group_descendants(
    tmp_path: Path,
    inherit_pipes: bool,
) -> None:
    identity_path = tmp_path / "processes.json"
    sentinel = tmp_path / "descendant-survived"
    redirection = (
        "" if inherit_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    )
    child_source = (
        "import pathlib,time; time.sleep(0.25); "
        f"pathlib.Path({str(sentinel)!r}).write_text('survived'); time.sleep(10)"
    )
    source = (
        "import json,os,subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_source!r}]{redirection}); "
        f"open({str(identity_path)!r},'w').write(json.dumps([os.getpid(),child.pid]))"
    )
    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
        _python_result(tmp_path, source)
    _process_group, child = json.loads(identity_path.read_text())
    deadline = time.monotonic() + 1
    while True:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            pytest.fail(f"descendant {child} survived probe cleanup")
        time.sleep(0.01)
    time.sleep(0.3)
    assert not sentinel.exists()


def test_deliberately_detached_descendant_is_outside_probe_contract(
    tmp_path: Path,
) -> None:
    identity_path = tmp_path / "detached-pid"
    sentinel = tmp_path / "detached-survived"
    child_source = (
        "import pathlib,time; time.sleep(0.2); "
        f"pathlib.Path({str(sentinel)!r}).write_text('survived'); time.sleep(10)"
    )
    source = (
        "import subprocess,sys; "
        "child=subprocess.Popen("
        f"[sys.executable,'-c',{child_source!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,start_new_session=True); "
        f"open({str(identity_path)!r},'w').write(str(child.pid))"
    )
    result = _python_result(tmp_path, source)
    assert result.returncode == 0
    child = int(identity_path.read_text())
    try:
        time.sleep(0.3)
        assert sentinel.read_text() == "survived"
        assert "not an OS sandbox" in (probe_module.__doc__ or "")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)
        deadline = time.monotonic() + 1
        while True:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                pytest.fail(f"detached test child {child} could not be cleaned up")
            time.sleep(0.01)


def test_bounded_reader_and_reaper_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_reader = probe_module._read_bounded

    def failing_reader(
        _stream: Any,
        _limit: int,
        state: probe_module._ReaderState,
    ) -> None:
        state.failure = OSError("reader")
        state.overflow.set()

    monkeypatch.setattr(probe_module, "_read_bounded", failing_reader)
    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
        _python_result(tmp_path, "import time; time.sleep(1)")

    class RaisingStream:
        def read(self, _size: int) -> bytes:
            raise OSError("read")

    state = probe_module._ReaderState(bytearray(), threading.Event(), threading.Event())
    original_reader(cast(Any, RaisingStream()), 1, state)
    assert isinstance(state.failure, OSError)
    assert state.overflow.is_set()
    assert state.done.is_set()

    class OversizedStream:
        def read(self, _size: int) -> bytes:
            return b"oversized"

    state = probe_module._ReaderState(bytearray(), threading.Event(), threading.Event())
    original_reader(cast(Any, OversizedStream()), 2, state)
    assert state.content == b"ove"
    assert state.overflow.is_set()
    assert state.done.is_set()

    state = probe_module._ReaderState(
        bytearray(b"xx"), threading.Event(), threading.Event()
    )
    original_reader(cast(Any, OversizedStream()), 1, state)
    assert state.content == b"xx"
    assert state.overflow.is_set()
    assert state.done.is_set()


def test_bounded_writer_handles_partial_short_and_close_failures() -> None:
    class Writer:
        def __init__(
            self,
            writes: list[int | None | BaseException],
            close_failure: BaseException | None = None,
        ) -> None:
            self.writes = iter(writes)
            self.close_failure = close_failure
            self.closed = False

        def write(self, _value: memoryview) -> int | None:
            outcome = next(self.writes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def close(self) -> None:
            self.closed = True
            if self.close_failure is not None:
                raise self.close_failure

    state = probe_module._WriterState(threading.Event())
    writer = Writer([2, 3])
    probe_module._write_bounded(cast(Any, writer), b"value", state)
    assert state.failure is None
    assert state.done.is_set()
    assert writer.closed

    for outcome in (None, 0, -1, 6):
        state = probe_module._WriterState(threading.Event())
        probe_module._write_bounded(cast(Any, Writer([outcome])), b"value", state)
        assert isinstance(state.failure, OSError)
        assert state.done.is_set()

    state = probe_module._WriterState(threading.Event())
    probe_module._write_bounded(
        cast(Any, Writer([5], BrokenPipeError())), b"value", state
    )
    assert state.failure is None
    assert state.done.is_set()

    state = probe_module._WriterState(threading.Event())
    probe_module._write_bounded(
        cast(Any, Writer([5], OSError("close"))), b"value", state
    )
    assert isinstance(state.failure, OSError)
    assert state.done.is_set()

    write_failure = OSError("write")
    state = probe_module._WriterState(threading.Event())
    probe_module._write_bounded(
        cast(Any, Writer([write_failure], OSError("close"))), b"value", state
    )
    assert state.failure is write_failure
    assert state.done.is_set()


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [(BrokenPipeError(), None), (OSError("write"), "node-launch-failed")],
)
def test_bounded_process_normalizes_stdin_pipe_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_error: str | None,
) -> None:
    original_popen = subprocess.Popen

    class FailingStdin:
        closed = False

        def write(self, _value: bytes) -> int:
            raise failure

        def close(self) -> None:
            self.closed = True

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)
        process.stdin = cast(Any, FailingStdin())
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    if expected_error is None:
        result = _python_result(tmp_path, "pass", stdin=b"value")
        assert result.returncode == 0
    else:
        with pytest.raises(PonytailSnapshotProbeError, match=expected_error):
            _python_result(tmp_path, "import time; time.sleep(1)", stdin=b"value")


def test_bounded_process_rejects_stuck_reader_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stuck_reader(
        _stream: Any,
        _limit: int,
        _state: probe_module._ReaderState,
    ) -> None:
        time.sleep(1)

    monkeypatch.setattr(probe_module, "_read_bounded", stuck_reader)
    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed") as error:
        _python_result(tmp_path, "pass")
    assert error.value.__notes__ == ["Ponytail probe process cleanup did not complete"]


@pytest.mark.parametrize("failure", [RuntimeError("start"), KeyboardInterrupt()])
def test_incomplete_process_cleanup_is_attached_to_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    original_start = threading.Thread.start
    original_cleanup = probe_module._cleanup_failed_process
    calls = 0

    def start(thread: threading.Thread) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        original_start(thread)

    def incomplete(
        process: subprocess.Popen[bytes],
        threads: tuple[threading.Thread, ...],
    ) -> bool:
        original_cleanup(process, threads)
        return False

    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(probe_module, "_cleanup_failed_process", incomplete)
    expected = (
        PonytailSnapshotProbeError
        if isinstance(failure, Exception)
        else KeyboardInterrupt
    )
    with pytest.raises(expected) as error:
        _python_result(tmp_path, "import time; time.sleep(10)")
    assert error.value.__notes__ == ["Ponytail probe process cleanup did not complete"]


def test_successful_process_reports_pipe_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = probe_module._close_process_pipes

    def failed_close(process: subprocess.Popen[bytes]) -> bool:
        original_close(process)
        return False

    monkeypatch.setattr(probe_module, "_close_process_pipes", failed_close)
    with pytest.raises(PonytailSnapshotProbeError) as error:
        _python_result(tmp_path, "pass")
    _assert_health_error(error, "process-cleanup-failed", "node-version")


def test_terminate_reaps_already_exited_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
    )
    process.wait()
    calls: list[int] = []

    def missing_group(_pid: int, sent_signal: int) -> None:
        calls.append(sent_signal)
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing_group)
    assert probe_module._terminate_and_reap(process)
    assert calls == [signal.SIGTERM, signal.SIGKILL, 0]


def test_terminate_and_group_checks_handle_permission_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 123

        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self.killed = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self.returncode = -signal.SIGKILL

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is not None:
                return self.returncode
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            raise AssertionError("unbounded wait is forbidden")

    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(PermissionError()),
    )
    process = Process()
    assert not probe_module._terminate_and_reap(cast(Any, process))
    assert process.terminated
    assert process.killed

    already_exited = Process()
    already_exited.returncode = 0
    assert not probe_module._terminate_and_reap(cast(Any, already_exited))
    assert not already_exited.terminated
    assert not already_exited.killed

    assert probe_module._process_group_exists(123)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not probe_module._process_group_exists(123)


def test_join_threads_skips_unstarted_workers() -> None:
    unstarted = threading.Thread(target=lambda: None)
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    assert probe_module._join_threads((unstarted, finished), 0)


def test_process_pipe_cleanup_reports_close_errors() -> None:
    class FailingClose:
        closed = False

        def close(self) -> None:
            raise OSError("close")

    class Process:
        stdin = FailingClose()
        stdout = None
        stderr = None

    assert not probe_module._close_process_pipes(cast(Any, Process()))


@pytest.mark.parametrize("failure", [RuntimeError("start"), KeyboardInterrupt()])
def test_partial_thread_start_is_cleaned_and_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    original_start = threading.Thread.start
    calls = 0

    def start(thread: threading.Thread) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    if isinstance(failure, Exception):
        with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
            _python_result(tmp_path, "import time; time.sleep(10)")
    else:
        with pytest.raises(KeyboardInterrupt) as error:
            _python_result(tmp_path, "import time; time.sleep(10)")
        assert error.value is failure


def _valid_graph(target: str) -> bytes:
    def shape(names: tuple[str, ...], constants: dict[str, str]) -> dict[str, object]:
        return {
            "keys": list(names),
            "types": {name: constants.get(name, "function") for name in names},
        }

    document = {
        "config": shape(
            probe_module._CONFIG_EXPORTS,
            {
                "DEFAULT_MODE": "string",
                "RUNTIME_MODES": "object",
                "VALID_MODES": "object",
            },
        ),
        "instructions": shape(probe_module._INSTRUCTION_EXPORTS, {}),
        "runtime": shape(
            probe_module._RUNTIME_EXPORTS,
            {
                "isCodex": "boolean",
                "isCopilot": "boolean",
                "isQoder": "boolean",
            },
        ),
        "flags": {
            "isCodex": target == "codex",
            "isCopilot": False,
            "isQoder": False,
        },
        "cache": list(probe_module._MODULE_CACHE),
    }
    return json.dumps(document, separators=(",", ":")).encode()


@pytest.mark.parametrize("target", ["claude", "codex"])
def test_module_graph_validator_is_exact(target: str) -> None:
    probe_module._validate_module_graph(
        probe_module._ProcessResult(0, _valid_graph(target), b""),
        cast(Any, target),
    )
    document = json.loads(_valid_graph(target))
    variants = []
    changed = dict(document)
    changed["extra"] = None
    variants.append(changed)
    changed = json.loads(_valid_graph(target))
    changed["config"]["keys"] = []
    variants.append(changed)
    changed = json.loads(_valid_graph(target))
    changed["instructions"]["types"]["getPonytailInstructions"] = "object"
    variants.append(changed)
    changed = json.loads(_valid_graph(target))
    changed["runtime"]["types"]["isCodex"] = "function"
    variants.append(changed)
    changed = json.loads(_valid_graph(target))
    changed["flags"]["isCopilot"] = True
    variants.append(changed)
    changed = json.loads(_valid_graph(target))
    changed["cache"] = []
    variants.append(changed)
    for variant in variants:
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._validate_module_graph(
                probe_module._ProcessResult(
                    0,
                    json.dumps(variant, separators=(",", ":")).encode(),
                    b"",
                ),
                cast(Any, target),
            )


def test_json_and_mapping_validation_is_strict() -> None:
    probe: probe_module.SnapshotProbeName = "relative-module-graph"
    assert probe_module._json_mapping(b'{"a":1}', probe) == {"a": 1}
    for value in (
        b"\xff",
        b"{",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b"[]",
    ):
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._json_mapping(value, probe)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._exact_mapping([], frozenset(), probe)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._exact_mapping({"a": 1}, frozenset(), probe)


def test_node_version_validation_is_strict() -> None:
    assert (
        probe_module._validate_node_version(
            probe_module._ProcessResult(0, b"v1.2.3\n", b"")
        )
        == "v1.2.3"
    )
    invalid = (
        probe_module._ProcessResult(1, b"", b""),
        probe_module._ProcessResult(0, b"v1", b"warning"),
        probe_module._ProcessResult(0, b"not-a-version", b""),
        probe_module._ProcessResult(0, b"", b""),
        probe_module._ProcessResult(0, b"x" * 257, b""),
        probe_module._ProcessResult(0, b"\xff", b""),
        probe_module._ProcessResult(0, b"v1\nv2\n", b""),
        probe_module._ProcessResult(0, b" v1\n", b""),
    )
    for result in invalid:
        with pytest.raises(PonytailSnapshotProbeError):
            probe_module._validate_node_version(result)


def test_native_output_validation_is_target_specific() -> None:
    raw = probe_module._ProcessResult(0, b"context", b"")
    assert (
        probe_module._native_context(
            raw,
            "claude",
            event="SessionStart",
            mode="full",
            probe="canonical-session-start",
        )
        == b"context"
    )
    codex = {
        "systemMessage": "PONYTAIL:FULL",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "context",
        },
    }
    encoded = json.dumps(codex, separators=(",", ":")).encode()
    assert (
        probe_module._native_context(
            probe_module._ProcessResult(0, encoded, b""),
            "codex",
            event="SessionStart",
            mode="full",
            probe="canonical-session-start",
        )
        == b"context"
    )
    claude_subagent = {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": "context",
        }
    }
    assert (
        probe_module._native_context(
            probe_module._ProcessResult(
                0,
                json.dumps(claude_subagent).encode(),
                b"",
            ),
            "claude",
            event="SubagentStart",
            mode="full",
            probe="subagent-event",
        )
        == b"context"
    )

    variants: list[dict[str, Any]] = []
    changed: dict[str, Any] = dict(codex)
    changed["extra"] = None
    variants.append(changed)
    changed = json.loads(encoded)
    changed["systemMessage"] = "OTHER"
    variants.append(changed)
    changed = json.loads(encoded)
    changed["hookSpecificOutput"]["extra"] = None
    variants.append(changed)
    changed = json.loads(encoded)
    changed["hookSpecificOutput"]["hookEventName"] = "Other"
    variants.append(changed)
    changed = json.loads(encoded)
    changed["hookSpecificOutput"]["additionalContext"] = 1
    variants.append(changed)
    for variant in variants:
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._native_context(
                probe_module._ProcessResult(
                    0,
                    json.dumps(variant).encode(),
                    b"",
                ),
                "codex",
                event="SessionStart",
                mode="full",
                probe="canonical-session-start",
            )


def test_context_hash_and_markers_are_required() -> None:
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._validate_context(
            b"wrong",
            mode="full",
            probe="canonical-session-start",
        )
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._validate_context(
            b"wrong",
            mode="lite",
            probe="subagent-event",
        )


def test_run_case_normalizes_runner_failures_and_deadline(tmp_path: Path) -> None:
    kwargs = {
        "argv": ("node",),
        "stdin": b"",
        "environment": {},
        "cwd": tmp_path,
        "per_process_timeout": 2.0,
        "probe": "node-version",
    }

    def should_not_run(*_args: Any, **_kwargs: Any) -> probe_module._ProcessResult:
        raise AssertionError

    with pytest.raises(PonytailSnapshotProbeError, match="node-timeout"):
        probe_module._run_case(
            should_not_run,
            execution_deadline=0,
            **cast(Any, kwargs),
        )

    expected = PonytailSnapshotProbeError("node-output-limit", "node-version")

    def typed_failure(*_args: Any, **_kwargs: Any) -> probe_module._ProcessResult:
        raise expected

    with pytest.raises(PonytailSnapshotProbeError) as propagated:
        probe_module._run_case(
            typed_failure,
            execution_deadline=time.monotonic() + 2,
            **cast(Any, kwargs),
        )
    assert propagated.value is expected

    def os_failure(*_args: Any, **_kwargs: Any) -> probe_module._ProcessResult:
        raise OSError

    with pytest.raises(PonytailSnapshotProbeError, match="node-launch-failed"):
        probe_module._run_case(
            os_failure,
            execution_deadline=time.monotonic() + 2,
            **cast(Any, kwargs),
        )

    def wrong_result(*_args: Any, **_kwargs: Any) -> Any:
        return object()

    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._run_case(
            wrong_result,
            execution_deadline=time.monotonic() + 2,
            **cast(Any, kwargs),
        )

    observed: list[float] = []

    def success(*_args: Any, **call: Any) -> probe_module._ProcessResult:
        observed.append(call["timeout"])
        return probe_module._ProcessResult(0, b"", b"")

    result = probe_module._run_case(
        success,
        execution_deadline=time.monotonic() + 1.5,
        **cast(Any, kwargs),
    )
    assert result.returncode == 0
    assert 0 < observed[0] <= 0.5


def test_materialization_and_runtime_inventory_are_exact(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
) -> None:
    health = _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, "claude"))
    destination = tmp_path / "runtime"
    probe_module._materialize_tree(health.runtime_tree, destination)
    probe_module._assert_materialized_tree(
        destination,
        health.runtime_tree,
        frozenset(),
    )
    assert installed_tree_sha256(health.runtime_tree) == RUNTIME_DIGEST
    not_directory = tmp_path / "not-directory"
    not_directory.write_bytes(b"file")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_materialized_tree(
            not_directory,
            health.runtime_tree,
            frozenset(),
        )


@pytest.mark.parametrize("surface", ["claude", "codex"])
def test_child_environment_is_an_exact_allowlist(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
    surface: str,
) -> None:
    snapshot = _PonytailSnapshotProbeInput.from_rendered(
        _plan(ponytail_bundle, surface)
    )
    root = tmp_path / surface
    root.mkdir()
    layout = probe_module._build_layout(root, snapshot)
    environment = probe_module._child_environment(
        "/trusted/bin",
        layout,
        snapshot,
        layout.healthy_runtime,
    )
    expected = {
        "PATH": "/trusted/bin",
        "HOME": str(layout.home),
        "XDG_CONFIG_HOME": str(layout.xdg),
        "TMPDIR": str(layout.temporary),
        "TMP": str(layout.temporary),
        "TEMP": str(layout.temporary),
        "CLAUDE_PLUGIN_ROOT": str(layout.healthy_runtime),
        "PLUGIN_ROOT": str(layout.healthy_runtime),
        "PONYTAIL_DEFAULT_MODE": "full",
    }
    if surface == "claude":
        expected["CLAUDE_CONFIG_DIR"] = str(layout.claude_profile)
    else:
        expected["PLUGIN_DATA"] = str(layout.plugin_data)
    assert environment == expected


def test_private_state_rejects_scratch_and_profile_mutation(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
) -> None:
    health = _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, "claude"))
    first = tmp_path / "first"
    first.mkdir()
    layout = probe_module._build_layout(first, health)
    (layout.scratch / "unexpected").write_bytes(b"value")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_private_state(
            layout,
            health,
            healthy_mode=None,
            missing_mode=None,
        )

    second = tmp_path / "second"
    second.mkdir()
    layout = probe_module._build_layout(second, health)
    (layout.claude_profile / "settings.json").write_bytes(b"changed")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_private_state(
            layout,
            health,
            healthy_mode=None,
            missing_mode=None,
        )


@pytest.mark.parametrize(
    "tamper",
    ["extra", "missing", "content", "mode", "symlink", "hardlink", "fifo"],
)
def test_runtime_inventory_rejects_every_node_tamper(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
    tamper: str,
) -> None:
    health = _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, "claude"))
    destination = tmp_path / "runtime"
    probe_module._materialize_tree(health.runtime_tree, destination)
    files = [entry for entry in health.runtime_tree if entry.kind == "file"]
    selected = destination / files[0].relative_path
    if tamper == "extra":
        (destination / "extra").write_bytes(b"extra")
    elif tamper == "missing":
        selected.unlink()
    elif tamper == "content":
        selected.write_bytes(b"changed")
    elif tamper == "mode":
        selected.chmod(0o600 if files[0].normalized_mode != 0o600 else 0o644)
    elif tamper == "symlink":
        selected.unlink()
        selected.symlink_to(destination / files[1].relative_path)
    elif tamper == "hardlink":
        selected.unlink()
        os.link(destination / files[1].relative_path, selected)
    else:
        selected.unlink()
        os.mkfifo(selected)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_materialized_tree(
            destination,
            health.runtime_tree,
            frozenset(),
        )


def test_private_inventory_rejects_links_and_special_nodes(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    assert probe_module._private_files(root) == {"nested": None}
    (nested / "value").write_bytes(b"value")
    assert probe_module._private_files(root) == {
        "nested": None,
        "nested/value": b"value",
    }
    (nested / "value").unlink()
    (nested / "value").symlink_to(root)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(root)
    (nested / "value").unlink()
    os.mkfifo(nested / "value")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(root)


def test_private_inventory_rejects_resource_exhaustion_without_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_directory = tmp_path / "not-directory"
    not_directory.write_bytes(b"value")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(not_directory)

    root = tmp_path / "private"
    root.mkdir()
    oversized = root / "oversized"
    oversized.touch()
    with oversized.open("r+b") as stream:
        stream.truncate(probe_module._MAX_PRIVATE_FILE_BYTES + 1)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(root)

    oversized.unlink()
    (root / "entry").write_bytes(b"x")
    monkeypatch.setattr(probe_module, "_MAX_PRIVATE_ENTRIES", 0)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(root)

    monkeypatch.setattr(probe_module, "_MAX_PRIVATE_ENTRIES", 128)
    monkeypatch.setattr(probe_module, "_MAX_PRIVATE_BYTES", 0)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._private_files(root)


def test_bounded_file_reader_rejects_size_links_and_inode_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    first.write_bytes(b"value")
    observed = first.lstat()
    assert probe_module._read_regular_file_bounded(first, observed, 5) == b"value"
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._read_regular_file_bounded(first, observed, 4)

    second = tmp_path / "second"
    second.write_bytes(b"other")
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._read_regular_file_bounded(second, observed, 5)

    link = tmp_path / "link"
    link.symlink_to(first)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._read_regular_file_bounded(link, link.lstat(), 5)

    growing = tmp_path / "growing"
    growing.write_bytes(b"123456")
    growing_stat = list(growing.lstat())
    growing_stat[6] = 5
    stale_size = os.stat_result(growing_stat)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", lambda _descriptor: stale_size)
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._read_regular_file_bounded(growing, stale_size, 5)

    with monkeypatch.context() as patch:
        patch.setattr(
            os,
            "read",
            lambda *_args: (_ for _ in ()).throw(OSError("read")),
        )
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._read_regular_file_bounded(first, observed, 5)

    original_close = os.close
    with monkeypatch.context() as patch:

        def close_then_fail(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError("close")

        patch.setattr(os, "close", close_then_fail)
        with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
            probe_module._read_regular_file_bounded(first, observed, 5)


def test_runtime_inventory_stops_at_entry_budget(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _PonytailSnapshotProbeInput.from_rendered(
        _plan(ponytail_bundle, "claude")
    )
    root_only = tmp_path / "root-only"
    root_only.mkdir()
    monkeypatch.setattr(probe_module, "_MAX_TREE_ENTRIES", 0)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_materialized_tree(
            root_only,
            snapshot.runtime_tree,
            frozenset(),
        )

    root_with_file = tmp_path / "root-with-file"
    root_with_file.mkdir()
    (root_with_file / "file").write_bytes(b"value")
    monkeypatch.setattr(probe_module, "_MAX_TREE_ENTRIES", 1)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_module._assert_materialized_tree(
            root_with_file,
            snapshot.runtime_tree,
            frozenset(),
        )


def test_exclusive_writer_rejects_short_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "write", lambda *_args: 0)
    with pytest.raises(OSError, match="short write"):
        probe_module._write_exclusive(tmp_path / "file", b"value", 0o600)


@pytest.mark.parametrize(
    "invalid_probe",
    [
        "missing-json",
        "missing-legacy",
        "missing-duplicate",
        "missing-trailing",
        "missing-exit",
        "missing-stderr",
        "missing-key",
        "missing-scalar",
        "missing-extra",
        "missing-code",
        "missing-path",
        "review",
        "lite",
    ],
)
def test_probe_sequence_rejects_invalid_negative_and_mode_outputs(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_probe: str,
) -> None:
    health = _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, "claude"))
    root = tmp_path / "probe"
    root.mkdir()
    layout = probe_module._build_layout(root, health)
    monkeypatch.setattr(
        probe_module,
        "_validate_module_graph",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        probe_module,
        "_validate_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        probe_module,
        "_assert_private_state",
        lambda *_args, **_kwargs: None,
    )

    def runner(
        argv: tuple[str, ...],
        *,
        stdin: bytes,
        environment: Mapping[str, str],
        cwd: Path,
        timeout: float,
        stdout_limit: int,
        stderr_limit: int,
        probe: probe_module.SnapshotProbeName,
    ) -> probe_module._ProcessResult:
        del argv, stdin, environment, cwd, timeout, stdout_limit, stderr_limit
        if probe == "node-version":
            return probe_module._ProcessResult(0, b"v1.2.3\n", b"")
        if probe == "missing-canonical-skill":
            if invalid_probe == "missing-json":
                return probe_module._ProcessResult(0, b"", b"")
            if invalid_probe == "missing-legacy":
                return probe_module._ProcessResult(
                    1, b"", b"ENOENT skills/ponytail/SKILL.md"
                )
            if invalid_probe == "missing-duplicate":
                return probe_module._ProcessResult(
                    probe_module._MISSING_SKILL_EXIT,
                    b'{"code":"ENOENT","code":"ENOENT","path":"/wrong"}',
                    b"",
                )
            if invalid_probe == "missing-trailing":
                return probe_module._ProcessResult(
                    probe_module._MISSING_SKILL_EXIT,
                    b'{"code":"ENOENT","path":"/wrong"}\n{}',
                    b"",
                )
            document: dict[str, object] = {
                "code": "ENOENT",
                "path": str(
                    (layout.missing_runtime / probe_module._CANONICAL_SKILL).resolve(
                        strict=False
                    )
                ),
            }
            returncode = probe_module._MISSING_SKILL_EXIT
            stderr = b""
            if invalid_probe == "missing-exit":
                returncode = 1
            elif invalid_probe == "missing-stderr":
                stderr = b"unexpected"
            elif invalid_probe == "missing-key":
                del document["path"]
            elif invalid_probe == "missing-scalar":
                document["code"] = 1
            elif invalid_probe == "missing-extra":
                document["extra"] = None
            elif invalid_probe == "missing-code":
                document["code"] = "EACCES"
            elif invalid_probe == "missing-path":
                document["path"] = "/wrong"
            return probe_module._ProcessResult(
                returncode,
                json.dumps(document, separators=(",", ":")).encode(),
                stderr,
            )
        if probe == "one-shot-review":
            output = (
                b"wrong"
                if invalid_probe == "review"
                else probe_module._REVIEW_CONTEXT.encode()
            )
            return probe_module._ProcessResult(0, output, b"")
        if probe == "state-round-trip":
            output = (
                b"wrong"
                if invalid_probe == "lite"
                else probe_module._LITE_SWITCH_CONTEXT.encode()
            )
            return probe_module._ProcessResult(0, output, b"")
        return probe_module._ProcessResult(0, b"context", b"")

    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract") as error:
        probe_module._probe_snapshot_input(
            health,
            Path(sys.executable),
            os.environ.get("PATH", ""),
            layout,
            runner,
            time.monotonic() + 5,
        )
    expected_probe = {
        "review": "one-shot-review",
        "lite": "state-round-trip",
    }.get(invalid_probe, "missing-canonical-skill")
    assert error.value.probe == expected_probe


def test_successful_probe_sequence_records_exact_order_and_limits(
    ponytail_bundle: SourceItem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = _PonytailSnapshotProbeInput.from_rendered(_plan(ponytail_bundle, "claude"))
    root = tmp_path / "probe"
    root.mkdir()
    layout = probe_module._build_layout(root, health)
    monkeypatch.setattr(probe_module, "_validate_module_graph", lambda *_args: None)
    monkeypatch.setattr(
        probe_module, "_validate_context", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        probe_module, "_assert_private_state", lambda *_args, **_kwargs: None
    )
    calls: list[tuple[probe_module.SnapshotProbeName, int, int, float, bytes]] = []

    def runner(
        argv: tuple[str, ...],
        *,
        stdin: bytes,
        environment: Mapping[str, str],
        cwd: Path,
        timeout: float,
        stdout_limit: int,
        stderr_limit: int,
        probe: probe_module.SnapshotProbeName,
    ) -> probe_module._ProcessResult:
        del argv, environment, cwd
        calls.append((probe, stdout_limit, stderr_limit, timeout, stdin))
        if probe == "node-version":
            return probe_module._ProcessResult(0, b"v1.2.3\n", b"")
        if probe == "missing-canonical-skill":
            document = {
                "code": "ENOENT",
                "path": str(
                    (layout.missing_runtime / probe_module._CANONICAL_SKILL).resolve(
                        strict=False
                    )
                ),
            }
            return probe_module._ProcessResult(
                probe_module._MISSING_SKILL_EXIT,
                json.dumps(document, separators=(",", ":")).encode(),
                b"",
            )
        if probe == "one-shot-review":
            return probe_module._ProcessResult(
                0, probe_module._REVIEW_CONTEXT.encode(), b""
            )
        if probe == "state-round-trip":
            return probe_module._ProcessResult(
                0, probe_module._LITE_SWITCH_CONTEXT.encode(), b""
            )
        if probe == "subagent-event":
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": "context",
                }
            }
            return probe_module._ProcessResult(
                0, json.dumps(output, separators=(",", ":")).encode(), b""
            )
        return probe_module._ProcessResult(0, b"context", b"")

    result = probe_module._probe_snapshot_input(
        health,
        Path(sys.executable),
        os.environ.get("PATH", ""),
        layout,
        runner,
        time.monotonic() + 5,
    )
    assert result.probes == tuple(call[0] for call in calls) == probe_module._PROBES
    assert [call[1] for call in calls] == [
        probe_module._NODE_VERSION_MAX,
        *([probe_module._MAX_STDOUT] * 6),
    ]
    assert all(call[2] == probe_module._MAX_STDERR for call in calls)
    assert 0 < calls[0][3] <= probe_module._NODE_VERSION_TIMEOUT
    assert all(0 < call[3] <= probe_module._HOOK_TIMEOUT for call in calls[1:])
    assert [call[4] for call in calls] == [
        b"",
        b"",
        b"",
        b"",
        b'{"prompt":"/ponytail-review"}',
        b'{"prompt":"/ponytail lite"}',
        b"",
    ]


def test_public_failure_cleanup_preserves_primary_failure(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    node = Path(sys.executable)
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (node, os.environ.get("PATH", "")),
    )

    roots: list[Path] = []
    original_remove = probe_module._remove_probe_root

    def remove(root: Path) -> None:
        roots.append(root)
        original_remove(root)

    monkeypatch.setattr(probe_module, "_remove_probe_root", remove)
    expected = PonytailSnapshotProbeError("probe-contract", "node-version")
    monkeypatch.setattr(
        probe_module,
        "_probe_snapshot_input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(expected),
    )
    with pytest.raises(PonytailSnapshotProbeError) as error:
        probe_rendered_ponytail_snapshot(plan)
    assert error.value is expected
    assert roots and not roots[-1].exists()

    def failed_remove(_root: Path) -> None:
        raise OSError("cleanup")

    monkeypatch.setattr(probe_module, "_remove_probe_root", failed_remove)
    cleanup_primary = PonytailSnapshotProbeError("probe-contract", "node-version")
    monkeypatch.setattr(
        probe_module,
        "_probe_snapshot_input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup_primary),
    )
    with pytest.raises(PonytailSnapshotProbeError) as cleanup_error:
        probe_rendered_ponytail_snapshot(plan)
    assert cleanup_error.value is cleanup_primary
    assert isinstance(cleanup_error.value.__cause__, OSError)
    assert cleanup_error.value.__notes__ == [
        "Ponytail probe temporary cleanup also failed"
    ]


def test_public_materialization_and_success_cleanup_failures(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (Path(sys.executable), ""),
    )
    monkeypatch.setattr(
        probe_module,
        "_build_layout",
        lambda *_args: (_ for _ in ()).throw(OSError("materialize")),
    )
    with pytest.raises(PonytailSnapshotProbeError) as materialization:
        probe_rendered_ponytail_snapshot(plan)
    _assert_health_error(materialization, "materialization-failed")

    monkeypatch.setattr(
        probe_module,
        "_remove_probe_root",
        lambda _root: (_ for _ in ()).throw(OSError("cleanup")),
    )
    with pytest.raises(PonytailSnapshotProbeError) as materialization_with_cleanup:
        probe_rendered_ponytail_snapshot(plan)
    _assert_health_error(materialization_with_cleanup, "materialization-failed")
    assert isinstance(materialization_with_cleanup.value.__cause__, OSError)

    monkeypatch.undo()
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (Path(sys.executable), ""),
    )
    result = PonytailSnapshotProbeResult(
        "claude",
        RUNTIME_DIGEST,
        "v1.2.3",
        probe_module._PROBES,
    )
    monkeypatch.setattr(
        probe_module,
        "_probe_snapshot_input",
        lambda *_args: result,
    )
    monkeypatch.setattr(
        probe_module,
        "_remove_probe_root",
        lambda _root: (_ for _ in ()).throw(OSError("cleanup")),
    )
    with pytest.raises(PonytailSnapshotProbeError) as cleanup:
        probe_rendered_ponytail_snapshot(plan)
    _assert_health_error(cleanup, "temporary-cleanup-failed")


def test_public_materialization_cleanup_and_health_error_paths(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (Path(sys.executable), ""),
    )
    original_build = probe_module._build_layout

    def health_failure(*args: Any) -> probe_module._ProbeLayout:
        original_build(*args)
        raise PonytailSnapshotProbeError("probe-contract")

    monkeypatch.setattr(probe_module, "_build_layout", health_failure)
    with pytest.raises(PonytailSnapshotProbeError, match="probe-contract"):
        probe_rendered_ponytail_snapshot(plan)

    monkeypatch.setattr(
        probe_module,
        "_remove_probe_root",
        lambda _root: (_ for _ in ()).throw(OSError("cleanup")),
    )
    with pytest.raises(PonytailSnapshotProbeError) as cleanup:
        probe_rendered_ponytail_snapshot(plan)
    _assert_health_error(cleanup, "probe-contract")
    assert isinstance(cleanup.value.__cause__, OSError)


def test_public_cleans_root_after_base_exception_during_layout(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (Path(sys.executable), ""),
    )
    roots: list[Path] = []
    failure = KeyboardInterrupt()

    def interrupt(root: Path, _health: _PonytailSnapshotProbeInput) -> Any:
        roots.append(root)
        raise failure

    monkeypatch.setattr(probe_module, "_build_layout", interrupt)
    with pytest.raises(KeyboardInterrupt) as error:
        probe_rendered_ponytail_snapshot(plan)
    assert error.value is failure
    assert roots and not roots[0].exists()


def test_public_default_environment_success_path(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    result = PonytailSnapshotProbeResult(
        "claude",
        RUNTIME_DIGEST,
        "v1.2.3",
        probe_module._PROBES,
    )
    observed: list[Any] = []

    def resolve(environment: Any) -> tuple[Path, str]:
        observed.append(environment)
        return Path(sys.executable), ""

    monkeypatch.setattr(probe_module, "_resolve_node", resolve)
    monkeypatch.setattr(
        probe_module,
        "_probe_snapshot_input",
        lambda *_args: result,
    )
    assert probe_rendered_ponytail_snapshot(plan) is result
    assert observed == [os.environ]


def test_public_api_has_no_runtime_name_or_environment_override(
    ponytail_bundle: SourceItem,
) -> None:
    assert not hasattr(probe_module, "probe_rendered_ponytail_runtime")
    plan = _plan(ponytail_bundle, "claude")
    with pytest.raises(TypeError, match="environment"):
        cast(Any, probe_rendered_ponytail_snapshot)(plan, environment={})


def test_public_rejects_non_posix_and_temp_creation_failure(
    ponytail_bundle: SourceItem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(ponytail_bundle, "claude")
    monkeypatch.setattr(os, "name", "nt")
    with pytest.raises(PonytailSnapshotProbeError, match="unsupported-probe-platform"):
        probe_rendered_ponytail_snapshot(plan)

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        probe_module,
        "_resolve_node",
        lambda _environment: (Path(sys.executable), ""),
    )
    monkeypatch.setattr(
        tempfile,
        "mkdtemp",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("temp")),
    )
    with pytest.raises(PonytailSnapshotProbeError, match="materialization-failed"):
        probe_rendered_ponytail_snapshot(plan)


def test_process_result_requires_exact_values() -> None:
    with pytest.raises(TypeError, match="exact immutable"):
        probe_module._ProcessResult(cast(Any, True), b"", b"")
