from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig, load_config
from promptdeploy.deploy import deploy, item_selected
from promptdeploy.source import SourceDiscovery, SourceItem
from promptdeploy.ssh import mcp_entry_fingerprint
from promptdeploy.status import get_status
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.opencode import OpenCodeTarget
from promptdeploy.targets.remote import RemoteTarget

ROOT = Path(__file__).resolve().parents[1]

MAIN_TARGETS = {
    "claude-personal",
    "claude-positron",
    "codex-local",
    "codex-hera",
    "droid",
    "opencode-hera",
    "codex-clio",
    "opencode-clio",
    "claude-vulcan",
    "opencode-vulcan",
    "claude-vps",
    "claude-andoria",
    "codex-andoria",
    "opencode-andoria-08",
    "claude-andoria-t2",
    "opencode-andoria-t2",
    "claude-delphi-3bd4",
    "opencode-delphi-3bd4",
    "claude-gpu-server",
    "opencode-gpu-server",
}

FLEET_HOST_TARGETS = {
    "hera": {"codex-hera", "opencode-hera"},
    "clio": {"codex-clio", "opencode-clio"},
    "vulcan": {"claude-vulcan", "opencode-vulcan"},
    "vps": {"claude-vps"},
    "andoria-08": {
        "claude-andoria",
        "codex-andoria",
        "opencode-andoria-08",
    },
    "andoria-t2": {"claude-andoria-t2", "opencode-andoria-t2"},
    "delphi-3bd4": {"claude-delphi-3bd4", "opencode-delphi-3bd4"},
    "gpu-server": {"claude-gpu-server", "opencode-gpu-server"},
}
SHARED_CLAUDE_TARGETS = (
    "claude-andoria",
    "claude-andoria-t2",
    "claude-delphi-3bd4",
    "claude-gpu-server",
)
SHARED_OPENCODE_TARGETS = (
    "opencode-andoria-08",
    "opencode-andoria-t2",
    "opencode-delphi-3bd4",
    "opencode-gpu-server",
)


def _anvil_items() -> dict[str, SourceItem]:
    return {
        item.name: item
        for item in SourceDiscovery(ROOT).discover_mcp_servers()
        if item.name in {"anvil", "anvil-tools"}
    }


def _selected_targets(
    item: SourceItem,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
) -> set[str]:
    monkeypatch.setenv("PROMPTDEPLOY_HOST", host)
    config = load_config(ROOT / "deploy.yaml")
    selected: set[str] = set()
    for target_id, target_config in config.targets.items():
        target = create_target(target_config)
        try:
            if item_selected(item, target, target_id, config):
                selected.add(target_id)
        finally:
            target.cleanup()
    return selected


def test_anvil_target_matrix_and_retired_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _anvil_items()
    assert set(items) == {"anvil", "anvil-tools"}

    active = {
        name
        for name, item in items.items()
        if (item.metadata or {}).get("enabled", True)
    }
    assert active == {"anvil"}

    monkeypatch.setenv("PROMPTDEPLOY_HOST", "outside-fleet")
    config = load_config(ROOT / "deploy.yaml")
    assert {
        host: set(config.groups[host]) for host in FLEET_HOST_TARGETS
    } == FLEET_HOST_TARGETS

    shared_claude = [config.targets[target_id] for target_id in SHARED_CLAUDE_TARGETS]
    assert len({target.path for target in shared_claude}) == 1
    assert {tuple(target.labels) for target in shared_claude} == {
        ("claude", "positron", "remote")
    }

    shared_opencode = [
        config.targets[target_id] for target_id in SHARED_OPENCODE_TARGETS
    ]
    assert len({target.path for target in shared_opencode}) == 1
    assert {tuple(target.labels) for target in shared_opencode} == {
        ("positron", "remote")
    }

    for host in (
        "hera",
        "clio",
        "vulcan",
        "vps",
        "andoria-08",
        "andoria-t2",
        "delphi-3bd4",
        "gpu-server",
    ):
        assert _selected_targets(items["anvil"], host, monkeypatch) == MAIN_TARGETS
        assert (
            _selected_targets(items["anvil-tools"], host, monkeypatch) == MAIN_TARGETS
        )

    primary = items["anvil"].metadata
    retired = items["anvil-tools"].metadata
    assert primary is not None
    assert retired is not None
    assert set(primary["only"]) == MAIN_TARGETS
    assert set(retired["only"]) == MAIN_TARGETS
    assert primary["command"] == "anvil-mcp"
    assert primary["args"] == ["--server-id=anvil"]
    assert primary["claude"] == {"timeout": 330000}
    assert primary["codex"] == {
        "startup_timeout_sec": 330,
        "tool_timeout_sec": 330,
    }
    assert primary["opencode"] == {"timeout": 330000}
    assert primary["enabled"] is True
    assert retired["command"] == "anvil-mcp"
    assert retired["args"] == ["--server-id=emacs-eval"]
    assert retired["enabled"] is False

    rendered = json.dumps(items, default=lambda item: item.metadata, sort_keys=True)
    assert "/Users/johnw" not in rendered
    assert "/tmp/johnw-emacs/server" not in rendered
    assert "/nix/store/" not in rendered


def test_primary_anvil_renders_for_all_client_formats(tmp_path: Path) -> None:
    metadata = _anvil_items()["anvil"].metadata
    assert metadata is not None

    claude_dir = tmp_path / "claude"
    codex_home = tmp_path / "codex"
    droid_dir = tmp_path / "droid"
    opencode_dir = tmp_path / "opencode"
    for directory in (claude_dir, codex_home, droid_dir, opencode_dir):
        directory.mkdir()

    ClaudeTarget("claude", claude_dir).deploy_mcp_server("anvil", metadata)
    CodexTarget("codex", codex_home).deploy_mcp_server("anvil", metadata)
    DroidTarget("droid", droid_dir).deploy_mcp_server("anvil", metadata)
    OpenCodeTarget("opencode", opencode_dir).deploy_mcp_server("anvil", metadata)

    claude = json.loads((claude_dir / ".claude.json").read_text())
    assert claude["mcpServers"]["anvil"] == {
        "command": "anvil-mcp",
        "args": ["--server-id=anvil"],
        "timeout": 330000,
    }

    codex = tomllib.loads((codex_home / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["anvil"] == {
        "command": "anvil-mcp",
        "args": ["--server-id=anvil"],
        "startup_timeout_sec": 330,
        "tool_timeout_sec": 330,
    }

    droid = json.loads((droid_dir / "mcp.json").read_text())
    assert droid["mcpServers"]["anvil"] == {
        "type": "stdio",
        "command": "anvil-mcp",
        "args": ["--server-id=anvil"],
        "disabled": False,
    }

    opencode = json.loads((opencode_dir / "opencode.json").read_text())
    assert opencode["mcp"]["anvil"] == {
        "type": "local",
        "command": ["anvil-mcp", "--server-id=anvil"],
        "timeout": 330000,
    }


def test_non_mcp_items_use_default_matching(tmp_path: Path) -> None:
    metadata = _anvil_items()["anvil"].metadata
    assert metadata is not None

    targets = (
        CodexTarget("codex", tmp_path / "codex"),
        OpenCodeTarget("opencode", tmp_path / "opencode"),
    )
    for target in targets:
        assert target.item_matches_source("skill", "anvil", b"", metadata) is None


def test_anvil_mcp_renderer_fingerprints_are_versioned(tmp_path: Path) -> None:
    assert ClaudeTarget("claude", tmp_path / "claude").content_fingerprint("mcp") == (
        "claude-mcp-entry-v6"
    )
    assert CodexTarget("codex", tmp_path / "codex").content_fingerprint("mcp") == (
        "codex-mcp-v6"
    )
    assert DroidTarget("droid", tmp_path / "droid").content_fingerprint("mcp") == (
        "droid-mcp-v2"
    )
    assert (
        OpenCodeTarget("opencode", tmp_path / "opencode").content_fingerprint("mcp")
        == "opencode-mcp-v4"
    )


ANVIL_RECOVERY_POLICY_MARKERS = (
    "An operation timeout is not a transport failure.",
    "perform exactly one bounded read-only liveness reprobe.",
    "`dispatched: false`",
    "Never replay a mutating request",
    "Do not disable Anvil for the rest of the session.",
    "after ten minutes, whichever comes first.",
)


def _assert_anvil_recovery_policy(path: Path) -> None:
    rendered = " ".join(path.read_text().split())
    for marker in ANVIL_RECOVERY_POLICY_MARKERS:
        assert marker in rendered


def test_anvil_recovery_policy_renders_for_codex_and_claude(
    tmp_path: Path,
) -> None:
    source = ROOT / "skills" / "anvil"
    _assert_anvil_recovery_policy(source / "SKILL.md")

    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    ClaudeTarget("claude", claude_root).deploy_skill("anvil", source)
    CodexTarget("codex", codex_root).deploy_skill("anvil", source)

    _assert_anvil_recovery_policy(claude_root / "skills" / "anvil" / "SKILL.md")
    _assert_anvil_recovery_policy(
        codex_root / ".agents" / "skills" / "anvil" / "SKILL.md"
    )


CLIENT_TYPES = ("claude", "codex", "droid", "opencode")


def _write_mcp_source(
    source_root: Path,
    *,
    name: str,
    server_id: str,
    enabled: bool,
    deadlines: bool = False,
) -> None:
    mcp_dir = source_root / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"name: {name}",
        "command: anvil-mcp",
        "args:",
        f'  - "--server-id={server_id}"',
    ]
    if deadlines:
        lines.extend(
            [
                "claude:",
                "  timeout: 330000",
                "codex:",
                "  startup_timeout_sec: 330",
                "  tool_timeout_sec: 330",
                "opencode:",
                "  timeout: 330000",
            ]
        )
    lines.extend([f"enabled: {str(enabled).lower()}", ""])
    (mcp_dir / f"{name}.yaml").write_text("\n".join(lines))


def _client_config(
    tmp_path: Path,
    target_type: str,
    *,
    name: str = "anvil",
    server_id: str = "anvil",
    enabled: bool = True,
    deadlines: bool = False,
) -> tuple[Config, TargetConfig]:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    target_root.mkdir()
    _write_mcp_source(
        source_root,
        name=name,
        server_id=server_id,
        enabled=enabled,
        deadlines=deadlines,
    )
    target_config = TargetConfig(
        id="client",
        type=target_type,
        path=target_root,
        labels=[],
    )
    return (
        Config(
            source_root=source_root,
            targets={"client": target_config},
            groups={},
        ),
        target_config,
    )


def _client_json_path(target_config: TargetConfig) -> Path:
    paths = {
        "claude": target_config.path / ".claude.json",
        "droid": target_config.path / "mcp.json",
        "opencode": target_config.path / "opencode.json",
    }
    return paths[target_config.type]


def _seed_sentinel(target_config: TargetConfig) -> None:
    if target_config.type == "codex":
        path = target_config.path / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('sentinel = "keep"\n')
        return
    _client_json_path(target_config).write_text(json.dumps({"sentinel": "keep"}))


def _assert_sentinel_preserved(target_config: TargetConfig) -> None:
    if target_config.type == "codex":
        data = tomllib.loads(
            (target_config.path / ".codex" / "config.toml").read_text()
        )
    else:
        data = json.loads(_client_json_path(target_config).read_text())
    assert data["sentinel"] == "keep"


def _read_mcp_entry(target_config: TargetConfig, name: str) -> object:
    if target_config.type == "codex":
        path = target_config.path / ".codex" / "config.toml"
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        servers = data.get("mcp_servers")
    else:
        path = _client_json_path(target_config)
        data = json.loads(path.read_text()) if path.exists() else {}
        root_key = "mcp" if target_config.type == "opencode" else "mcpServers"
        servers = data.get(root_key)
    return servers.get(name) if isinstance(servers, dict) else None


def _expected_mcp_entry(
    target_type: str,
    server_id: str,
    *,
    deadlines: bool = False,
) -> dict[str, object]:
    if target_type == "droid":
        return {
            "type": "stdio",
            "command": "anvil-mcp",
            "args": [f"--server-id={server_id}"],
            "disabled": False,
        }
    if target_type == "opencode":
        opencode_entry: dict[str, object] = {
            "type": "local",
            "command": ["anvil-mcp", f"--server-id={server_id}"],
        }
        if deadlines:
            opencode_entry["timeout"] = 330000
        return opencode_entry
    entry: dict[str, object] = {
        "command": "anvil-mcp",
        "args": [f"--server-id={server_id}"],
    }
    if deadlines and target_type == "claude":
        entry["timeout"] = 330000
    if deadlines and target_type == "codex":
        entry.update(startup_timeout_sec=330, tool_timeout_sec=330)
    return entry


def _make_mcp_entry_stale(target_config: TargetConfig, name: str) -> None:
    if target_config.type == "codex":
        path = target_config.path / ".codex" / "config.toml"
        text = path.read_text()
        path.write_text(
            text.replace(
                'command = "anvil-mcp"',
                'command = "legacy-anvil"',
                1,
            )
        )
        return

    path = _client_json_path(target_config)
    data = json.loads(path.read_text())
    root_key = "mcp" if target_config.type == "opencode" else "mcpServers"
    entry = data[root_key][name]
    entry["command"] = (
        ["legacy-anvil"] if target_config.type == "opencode" else "legacy-anvil"
    )
    path.write_text(json.dumps(data))


def _make_deadlines_stale(target_config: TargetConfig) -> None:
    if target_config.type == "codex":
        path = target_config.path / ".codex" / "config.toml"
        text = path.read_text()
        text = text.replace("startup_timeout_sec = 330", "startup_timeout_sec = 60")
        text = text.replace("tool_timeout_sec = 330", "tool_timeout_sec = 60")
        path.write_text(text)
        return

    path = _client_json_path(target_config)
    data = json.loads(path.read_text())
    root_key = "mcp" if target_config.type == "opencode" else "mcpServers"
    data[root_key]["anvil"]["timeout"] = 60000
    path.write_text(json.dumps(data))


def _remove_mcp_entry(target_config: TargetConfig, name: str) -> None:
    target = create_target(target_config)
    try:
        target.remove_mcp_server(name)
    finally:
        target.cleanup()


def _mcp_status(config: Config, name: str) -> str:
    matches = [
        entry.state
        for entry in get_status(config)
        if entry.item_type == "mcp" and entry.name == name
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("target_type", CLIENT_TYPES)
@pytest.mark.parametrize("drift", ("stale", "missing"))
def test_anvil_named_entry_drift_is_repaired(
    tmp_path: Path,
    target_type: str,
    drift: str,
) -> None:
    config, target_config = _client_config(tmp_path, target_type)
    _seed_sentinel(target_config)

    assert [
        action.action
        for action in deploy(config)
        if action.item_type == "mcp" and action.name == "anvil"
    ] == ["create"]
    assert _mcp_status(config, "anvil") == "current"

    if drift == "stale":
        _make_mcp_entry_stale(target_config, "anvil")
    else:
        _remove_mcp_entry(target_config, "anvil")

    assert _mcp_status(config, "anvil") == "changed"
    assert [
        action.action
        for action in deploy(config)
        if action.item_type == "mcp" and action.name == "anvil"
    ] == ["update"]
    assert _read_mcp_entry(target_config, "anvil") == _expected_mcp_entry(
        target_type, "anvil"
    )
    _assert_sentinel_preserved(target_config)
    assert _mcp_status(config, "anvil") == "current"


@pytest.mark.parametrize("target_type", ("claude", "codex", "opencode"))
def test_anvil_deadline_drift_is_reported_and_repaired(
    tmp_path: Path,
    target_type: str,
) -> None:
    config, target_config = _client_config(
        tmp_path,
        target_type,
        deadlines=True,
    )
    _seed_sentinel(target_config)

    deploy(config)
    assert _read_mcp_entry(target_config, "anvil") == _expected_mcp_entry(
        target_type,
        "anvil",
        deadlines=True,
    )
    assert _mcp_status(config, "anvil") == "current"

    _make_deadlines_stale(target_config)
    assert _mcp_status(config, "anvil") == "changed"
    assert [
        action.action
        for action in deploy(config)
        if action.item_type == "mcp" and action.name == "anvil"
    ] == ["update"]
    assert _read_mcp_entry(target_config, "anvil") == _expected_mcp_entry(
        target_type,
        "anvil",
        deadlines=True,
    )
    _assert_sentinel_preserved(target_config)
    assert _mcp_status(config, "anvil") == "current"


@pytest.mark.parametrize("target_type", CLIENT_TYPES)
def test_retired_anvil_tools_is_removed_and_stays_absent(
    tmp_path: Path,
    target_type: str,
) -> None:
    config, target_config = _client_config(
        tmp_path,
        target_type,
        name="anvil-tools",
        server_id="emacs-eval",
    )
    _seed_sentinel(target_config)
    deploy(config)
    assert _read_mcp_entry(target_config, "anvil-tools") == _expected_mcp_entry(
        target_type, "emacs-eval"
    )

    _write_mcp_source(
        config.source_root,
        name="anvil-tools",
        server_id="emacs-eval",
        enabled=False,
    )
    assert [
        action.action
        for action in deploy(config)
        if action.item_type == "mcp" and action.name == "anvil-tools"
    ] == ["update"]
    assert _read_mcp_entry(target_config, "anvil-tools") is None
    _assert_sentinel_preserved(target_config)
    assert _mcp_status(config, "anvil-tools") == "current"

    assert [
        action.action
        for action in deploy(config)
        if action.item_type == "mcp" and action.name == "anvil-tools"
    ] == ["skip"]
    assert _read_mcp_entry(target_config, "anvil-tools") is None


def test_remote_claude_compares_only_anvil_fingerprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    target = RemoteTarget(
        ClaudeTarget("remote", staging, manage_mcp=False),
        "remote.example",
        Path("~/.claude"),
        staging,
        remote_mcp=True,
    )
    primary = _anvil_items()["anvil"].metadata
    retired = _anvil_items()["anvil-tools"].metadata
    assert primary is not None
    assert retired is not None

    expected = ClaudeTarget._claude_mcp_entry(
        primary,
        name="anvil",
        expand_secrets=False,
    )
    assert expected == {
        "command": "anvil-mcp",
        "args": ["--server-id=anvil"],
        "timeout": 330000,
    }
    expected_digest = mcp_entry_fingerprint(expected)
    assert expected_digest == (
        "sha256:467612b2b1e415dd4d528933f94a6b3e4e7b3b2927bb28ac7d201732ef1abc31"
    )
    assert expected_digest != mcp_entry_fingerprint(
        {"command": "anvil-mcp", "args": ["--server-id=anvil"]}
    )
    observed: list[tuple[str, str, str]] = []

    def probe(host: str, target_path: str, name: str) -> str | None:
        observed.append((host, target_path, name))
        return expected_digest

    monkeypatch.setattr(
        "promptdeploy.targets.remote.ssh_remote_mcp_fingerprint",
        probe,
    )
    assert target.item_matches_source("mcp", "anvil", b"", primary) is True
    assert observed == [("remote.example", "~/.claude/.claude.json", "anvil")]

    monkeypatch.setattr(
        "promptdeploy.targets.remote.ssh_remote_mcp_fingerprint",
        lambda *_args: None,
    )
    assert target.item_matches_source("mcp", "anvil", b"", primary) is False
    assert target.item_matches_source("mcp", "anvil-tools", b"", retired) is True

    def unexpected_probe(*_args: object) -> str | None:
        raise AssertionError("unrelated MCP entry must not be probed")

    monkeypatch.setattr(
        "promptdeploy.targets.remote.ssh_remote_mcp_fingerprint",
        unexpected_probe,
    )
    unrelated = {"command": "secret-bearing-server", "enabled": True}
    assert target.item_matches_source("mcp", "context7", b"", unrelated) is None
    assert target.item_matches_source("skill", "anvil", b"", primary) is None
