"""Semantic goldens for the dormant managed Ponytail runtime snapshots."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from promptdeploy.bundle_catalog import discover_bundle_items
from promptdeploy.bundles import BundleConfig, BundleSourceBinding
from promptdeploy.imported_tree import ImportedTreeSnapshot

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bundles" / "ponytail.yaml"


@pytest.fixture(scope="module")
def ponytail_root() -> Path:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    return root.resolve()


@pytest.fixture(scope="module")
def claude_runtime(ponytail_root: Path) -> ImportedTreeSnapshot:
    bundle = BundleConfig(
        "ponytail",
        MANIFEST,
        BundleSourceBinding(
            "ponytail",
            ponytail_root,
            True,
            None,
            None,
            None,
            "cli",
        ),
    )
    support = discover_bundle_items(bundle)[0]
    return next(
        payload.imported_tree
        for payload in support.bundle_payloads
        if payload.name == "claude-codex-runtime-v1"
    )


def _materialize(snapshot: ImportedTreeSnapshot, destination: Path) -> None:
    directories = [entry for entry in snapshot.entries if entry.kind == "directory"]
    files = [entry for entry in snapshot.entries if entry.kind != "directory"]
    for entry in sorted(
        directories,
        key=lambda item: (item.relative_path.count("/"), item.relative_path),
    ):
        path = (
            destination
            if entry.relative_path == "."
            else destination / entry.relative_path
        )
        path.mkdir(exist_ok=True)
        path.chmod(entry.normalized_mode)
    for entry in files:
        assert entry.content is not None
        path = destination / entry.relative_path
        path.write_bytes(entry.content)
        path.chmod(entry.normalized_mode)


def _node_environment(tmp_path: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CLAUDE_CONFIG_DIR",
            "COPILOT_PLUGIN_DATA",
            "PLUGIN_DATA",
            "QODER_SESSION_ID",
            "XDG_CONFIG_HOME",
        }
    }
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    return env


def test_strict_runtime_reads_canonical_skill_and_fails_when_missing(
    claude_runtime: ImportedTreeSnapshot,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _materialize(claude_runtime, runtime)
    node = shutil.which("node")
    assert node is not None
    env = _node_environment(tmp_path)
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude")
    env["PONYTAIL_DEFAULT_MODE"] = "full"
    command = [node, str(runtime / "hooks" / "ponytail-activate.js")]

    healthy = subprocess.run(command, env=env, capture_output=True, check=False)
    assert healthy.returncode == 0
    assert b"PONYTAIL MODE ACTIVE" in healthy.stdout
    assert b"## The ladder" in healthy.stdout

    (runtime / "skills" / "ponytail" / "SKILL.md").unlink()
    missing = subprocess.run(command, env=env, capture_output=True, check=False)
    assert missing.returncode != 0
    assert b"PONYTAIL MODE ACTIVE" not in missing.stdout
    assert b"The best code is the code never written" not in missing.stdout


@pytest.mark.parametrize("ambient", [None, "lite", "full", "ultra", "off"])
def test_review_is_one_shot_and_normal_switches_still_persist(
    claude_runtime: ImportedTreeSnapshot,
    tmp_path: Path,
    ambient: str | None,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _materialize(claude_runtime, runtime)
    node = shutil.which("node")
    assert node is not None
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    state = plugin_data / ".ponytail-active"
    if ambient is not None:
        state.write_text(ambient)
    env = _node_environment(tmp_path)
    env["PLUGIN_DATA"] = str(plugin_data)
    command = [node, str(runtime / "hooks" / "ponytail-mode-tracker.js")]

    review = subprocess.run(
        command,
        input=json.dumps({"prompt": "/ponytail-review"}).encode(),
        env=env,
        capture_output=True,
        check=False,
    )
    assert review.returncode == 0
    output = json.loads(review.stdout)
    assert output["systemMessage"] == "PONYTAIL:REVIEW"
    assert "level: review" in output["hookSpecificOutput"]["additionalContext"]
    if ambient is None:
        assert not state.exists()
    else:
        assert state.read_text() == ambient

    switched = subprocess.run(
        command,
        input=json.dumps({"prompt": "/ponytail lite"}).encode(),
        env=env,
        capture_output=True,
        check=False,
    )
    assert switched.returncode == 0
    assert state.read_text() == "lite"


@pytest.mark.parametrize(
    ("matcher", "payload", "injects"),
    [
        ("^general$", b'{"agent_type":"general"}', True),
        ("^general$", b'{"agent_type":"explore"}', False),
        ("[", b'{"agent_type":"explore"}', True),
        ("^general$", b"{", True),
    ],
)
def test_subagent_matcher_is_scoped_and_fails_open(
    claude_runtime: ImportedTreeSnapshot,
    tmp_path: Path,
    matcher: str,
    payload: bytes,
    injects: bool,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _materialize(claude_runtime, runtime)
    node = shutil.which("node")
    assert node is not None
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    (plugin_data / ".ponytail-active").write_text("full")
    env = _node_environment(tmp_path)
    env["PLUGIN_DATA"] = str(plugin_data)
    env["PONYTAIL_SUBAGENT_MATCHER"] = matcher

    result = subprocess.run(
        [node, str(runtime / "hooks" / "ponytail-subagent.js")],
        input=payload,
        env=env,
        capture_output=True,
        check=False,
        timeout=2,
    )
    assert result.returncode == 0
    assert not result.stderr
    if not injects:
        assert not result.stdout
        return
    output = json.loads(result.stdout)
    assert output["systemMessage"] == "PONYTAIL:FULL"
    hook = output["hookSpecificOutput"]
    assert hook["hookEventName"] == "SubagentStart"
    assert "PONYTAIL MODE ACTIVE — level: full" in hook["additionalContext"]
