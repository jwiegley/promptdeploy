from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from promptdeploy.config import load_config
from promptdeploy.deploy import item_selected
from promptdeploy.source import SourceDiscovery, SourceItem
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget
from promptdeploy.targets.codex import CodexTarget
from promptdeploy.targets.droid import DroidTarget
from promptdeploy.targets.opencode import OpenCodeTarget

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
}

TYPED_DARWIN_CURRENT_TARGETS = {
    "claude-personal",
    "claude-positron",
    "codex-local",
    "droid",
    "codex-hera",
    "opencode-hera",
    "codex-clio",
    "opencode-clio",
}

TYPED_REMOTE_DARWIN_TARGETS = {
    "codex-hera",
    "opencode-hera",
    "codex-clio",
    "opencode-clio",
}


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


def test_anvil_target_matrix_and_bare_launchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _anvil_items()
    assert set(items) == {"anvil", "anvil-tools"}

    for host in ("hera", "clio", "vulcan", "vps", "andoria-08"):
        assert _selected_targets(items["anvil"], host, monkeypatch) == MAIN_TARGETS

    for host in ("hera", "clio"):
        assert (
            _selected_targets(items["anvil-tools"], host, monkeypatch)
            == TYPED_DARWIN_CURRENT_TARGETS
        )

    for host in ("vulcan", "vps", "andoria-08"):
        assert (
            _selected_targets(items["anvil-tools"], host, monkeypatch)
            == TYPED_REMOTE_DARWIN_TARGETS
        )

    for name, server_id in (("anvil", "anvil"), ("anvil-tools", "emacs-eval")):
        metadata = items[name].metadata
        assert metadata is not None
        assert metadata["command"] == "anvil-mcp"
        assert metadata["args"] == [f"--server-id={server_id}"]
        rendered = json.dumps(metadata, sort_keys=True)
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
    }

    codex = tomllib.loads((codex_home / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["anvil"] == {
        "command": "anvil-mcp",
        "args": ["--server-id=anvil"],
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
    }
