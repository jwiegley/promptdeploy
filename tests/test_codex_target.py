"""Tests for the OpenAI Codex target implementation."""

from __future__ import annotations

import json
import os
import stat
import tomllib
from pathlib import Path

import pytest

from promptdeploy.envsubst import EnvVarError
from promptdeploy.frontmatter import FrontmatterError, parse_frontmatter
from promptdeploy.manifest import MANIFEST_FILENAME
from promptdeploy.targets.codex import CodexConfigError, CodexTarget


def _make_target(tmp_path: Path) -> CodexTarget:
    tmp_path.mkdir(exist_ok=True)
    return CodexTarget("codex", tmp_path)


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text("utf-8"))


def test_constructor_accepts_codex_home_path(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    target = CodexTarget("codex", codex_home)

    assert target.id == "codex"
    assert target.exists()
    assert target.manifest_path() == codex_home / MANIFEST_FILENAME
    assert target.deployed_artifact_path("agent", "a") == Path(".codex/agents/a.toml")
    pull_includes = target.rsync_includes() or []
    push_includes = target.rsync_push_includes() or []
    assert ".agents/skills/**" in pull_includes
    assert ".codex/agents/**" in pull_includes
    assert ".codex/config.toml" in pull_includes
    assert f".codex/{MANIFEST_FILENAME}" in pull_includes
    assert ".codex/**" not in pull_includes
    assert ".agents/skills/**" in push_includes
    assert ".codex/agents/**" in push_includes
    assert ".codex/prompts/**" not in push_includes
    assert ".codex/config.toml" in push_includes
    assert f".codex/{MANIFEST_FILENAME}" in push_includes
    assert ".codex/**" not in push_includes
    assert not any("sessions" in pattern for pattern in push_includes)


def test_exists_false_for_missing_home(tmp_path: Path):
    target = CodexTarget("missing", tmp_path / "missing")
    assert not target.exists()


def test_should_skip_settings_marketplaces_and_non_codex_models(tmp_path: Path):
    target = _make_target(tmp_path)

    assert target.should_skip("settings", "settings")
    assert target.should_skip("marketplace", "m")
    assert not target.should_skip("agent", "a")
    assert target.should_skip("models", "models", metadata={})
    assert target.should_skip(
        "models",
        "models",
        metadata={"providers": {"p": {"display_name": "P"}}},
    )
    assert not target.should_skip(
        "models",
        "models",
        metadata={"providers": {"p": {"display_name": "P", "codex": {}}}},
    )
    assert target.content_fingerprint("agent") == "codex-agent-v2"
    assert target.content_fingerprint("mcp") == "codex-mcp-v4"
    assert target.content_fingerprint("models") == "codex-target-v1"
    assert target.content_fingerprint("command") == "codex-command-skill-v3"
    assert target.content_fingerprint("skill") is None
    assert target.mcp_hash_includes_env is True


def test_deploy_agent_writes_custom_agent_toml_and_warning(tmp_path: Path):
    target = _make_target(tmp_path)
    content = (
        b"---\n"
        b"name: reviewer\n"
        b"description: Review code.\n"
        b"version: v1\n"
        b"only: [other]\n"
        b"tools: Read, Bash\n"
        b"model: gpt-5.5\n"
        b"mcp_servers:\n"
        b"  context7:\n"
        b"    enabled: true\n"
        b"---\n"
        b"Review like an owner.\n"
    )

    target.deploy_agent("reviewer", content)

    path = tmp_path / ".codex" / "agents" / "reviewer.toml"
    data = _read_toml(path)
    assert data["name"] == "reviewer"
    assert data["description"] == "Review code."
    assert data["developer_instructions"] == "Review like an owner.\n"
    assert data["model"] == "gpt-5.5"
    assert data["mcp_servers"]["context7"]["enabled"] is True
    assert "only" not in data
    assert "tools" not in data
    assert "version" not in data
    assert target.consume_warnings() == [
        (
            "reviewer",
            ["frontmatter field 'tools' has no Codex custom-agent equivalent; dropped"],
        ),
        (
            "reviewer",
            [
                "frontmatter field 'version' has no Codex custom-agent equivalent; "
                "dropped"
            ],
        ),
    ]
    assert target.item_exists("agent", "reviewer")
    assert target.read_deployed_bytes("agent", "reviewer") == path.read_bytes()
    assert target.would_deploy_bytes("agent", "reviewer", content) == path.read_bytes()
    assert target.consume_warnings() == []


def test_deploy_agent_without_frontmatter_gets_defaults(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_agent("plain", b"Plain instructions.\n")
    data = _read_toml(tmp_path / ".codex" / "agents" / "plain.toml")
    assert data == {
        "name": "plain",
        "description": "Custom Codex agent 'plain'.",
        "developer_instructions": "Plain instructions.\n",
    }


def test_deploy_agent_rejects_non_utf8_body(tmp_path: Path):
    target = _make_target(tmp_path)
    with pytest.raises(FrontmatterError, match="not valid UTF-8"):
        target.deploy_agent("bad", b"---\nname: bad\n---\n\xff")


def test_remove_agent_noops_when_missing(tmp_path: Path):
    target = _make_target(tmp_path)
    target.remove_agent("missing")
    target.deploy_agent("a", b"body")
    target.remove_agent("a")
    assert not target.item_exists("agent", "a")
    assert target.read_deployed_bytes("agent", "a") is None


def test_deploy_command_as_generated_skill(tmp_path: Path):
    target = _make_target(tmp_path)
    content = (
        b"---\n"
        b"name: fix\n"
        b"description: Fix command.\n"
        b"argument-hint: [files, or branch]\n"
        b"allowed-tools: Read\n"
        b"disable-model-invocation: true\n"
        b"droid_deploy: skill\n"
        b"except: [x]\n"
        b"---\n"
        b"Fix $ARGUMENTS.\n"
    )

    target.deploy_command("fix", content)

    dest = tmp_path / ".agents" / "skills" / "command-fix" / "SKILL.md"
    metadata, body = parse_frontmatter(dest.read_bytes())
    assert metadata == {"name": "command-fix", "description": "Fix command."}
    assert b"promptdeploy command 'fix'" in body
    assert b"Fix $ARGUMENTS." in body

    assert not (tmp_path / ".codex" / "prompts" / "fix.md").exists()
    assert target.item_exists("command", "fix")
    assert target.deployed_artifact_path("command", "fix") == Path(
        ".agents/skills/command-fix"
    )
    assert target.read_deployed_bytes("command", "fix") is None
    assert target.would_deploy_bytes("command", "fix", content) is None


def test_deploy_command_overwrites_symlink(tmp_path: Path):
    target = _make_target(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    dest = tmp_path / ".agents" / "skills" / "command-fix"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(real)

    target.deploy_command("fix", b"body")

    assert dest.is_dir()
    assert not dest.is_symlink()
    assert (dest / "SKILL.md").exists()
    assert real.exists()


def test_deploy_command_overwrites_existing_directory(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_command("fix", b"old")
    target.deploy_command("fix", b"new")
    dest = tmp_path / ".agents" / "skills" / "command-fix" / "SKILL.md"
    assert b"new" in dest.read_bytes()
    assert not (tmp_path / ".codex" / "prompts" / "fix.md").exists()


def test_remove_command_removes_directory_and_symlink(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_command("fix", b"body")
    target.remove_command("fix")
    assert not (tmp_path / ".agents" / "skills" / "command-fix").exists()

    real = tmp_path / "real"
    real.mkdir()
    dest = tmp_path / ".agents" / "skills" / "command-fix"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(real)
    target.remove_command("fix")
    assert not dest.exists()
    assert not dest.is_symlink()
    assert real.exists()


def test_remove_command_noops_when_missing(tmp_path: Path):
    target = _make_target(tmp_path)
    target.remove_command("missing")


def test_deploy_prompt_as_generated_skill_with_warnings(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "demo.poet"
    content = b"- role: system\n  content: 'Hello {{ missing }}'\n"
    src.write_bytes(content)

    target.deploy_prompt("demo", content, src)

    dest = tmp_path / ".agents" / "skills" / "prompt-demo" / "SKILL.md"
    metadata, body = parse_frontmatter(dest.read_bytes())
    assert metadata == {
        "name": "prompt-demo",
        "description": "Promptdeploy rendered prompt 'demo'.",
    }
    assert b"<instructions>" in body
    assert target.consume_warnings() == [
        ("demo", ["Undefined Jinja variable: missing"])
    ]
    assert target.item_exists("prompt", "demo")
    assert target.deployed_artifact_path("prompt", "demo") == Path(
        ".agents/skills/prompt-demo"
    )
    target.remove_prompt("demo", target_path=Path("ignored"))
    assert not dest.exists()


def test_deploy_plain_prompt_as_generated_skill(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "plain.md"
    target.deploy_prompt("plain", b"Plain.\n", src)
    body = (tmp_path / ".agents" / "skills" / "prompt-plain" / "SKILL.md").read_bytes()
    assert b"Plain." in body


def test_deploy_poet_prompt_without_warnings(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "demo.poet"
    content = b"- role: system\n  content: Hello\n"
    src.write_bytes(content)
    target.deploy_prompt("demo", content, src)
    assert target.consume_warnings() == []


def test_deploy_skill_copies_and_transforms(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_bytes(b"---\nname: skill\nonly: [x]\n---\nSkill body.\n")
    (src / "helper.py").write_text("print('hi')")

    target.deploy_skill("skill", src)

    dest = tmp_path / ".agents" / "skills" / "skill"
    metadata, body = parse_frontmatter((dest / "SKILL.md").read_bytes())
    assert metadata == {"name": "skill"}
    assert body == b"Skill body.\n"
    assert (dest / "helper.py").read_text() == "print('hi')"
    assert target.item_exists("skill", "skill")
    assert target.deployed_artifact_path("skill", "skill") == Path(
        ".agents/skills/skill"
    )


def test_deploy_skill_without_skill_md(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "helper.py").write_text("print('hi')")
    target.deploy_skill("bare", src)
    assert (tmp_path / ".agents" / "skills" / "bare" / "helper.py").exists()
    assert not (tmp_path / ".agents" / "skills" / "bare" / "SKILL.md").exists()


def test_deploy_skill_overwrites_existing_and_symlink(tmp_path: Path):
    target = _make_target(tmp_path)
    src1 = tmp_path / "src1"
    src1.mkdir()
    (src1 / "SKILL.md").write_bytes(b"one")
    src2 = tmp_path / "src2"
    src2.mkdir()
    (src2 / "SKILL.md").write_bytes(b"two")

    target.deploy_skill("skill", src1)
    target.deploy_skill("skill", src2)
    skill_md = tmp_path / ".agents" / "skills" / "skill" / "SKILL.md"
    assert skill_md.read_bytes() == b"two"

    real = tmp_path / "real"
    real.mkdir()
    dest = tmp_path / ".agents" / "skills" / "link-skill"
    dest.symlink_to(real)
    target.deploy_skill("link-skill", src1)
    assert not dest.is_symlink()


def test_remove_skill_removes_directory_and_symlink(tmp_path: Path):
    target = _make_target(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_bytes(b"body")
    target.deploy_skill("skill", src)
    target.remove_skill("skill")
    assert not target.item_exists("skill", "skill")

    real = tmp_path / "real"
    real.mkdir()
    dest = tmp_path / ".agents" / "skills" / "skill"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(real)
    target.remove_skill("skill")
    assert real.exists()


def test_deploy_mcp_server_maps_codex_fields_and_preserves_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LOCAL_TOKEN", "local-token")
    monkeypatch.setenv("OTHER_TOKEN", "other-token")
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('model = "gpt-5.5"\n')

    target.deploy_mcp_server(
        "context7",
        {
            "name": "context7",
            "description": "docs",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
            "env": {
                "LOCAL_TOKEN": "${LOCAL_TOKEN}",
                "RENAMED": "${OTHER_TOKEN}",
                "STATIC": "x",
            },
            "headers": {
                "Authorization": "Bearer ${AUTH_TOKEN}",
                "X-Region": "us",
                "X-Env": "${HEADER_TOKEN}",
            },
            "enabled": True,
            "enabled_tools": ["lookup"],
            "type": "http",
        },
    )

    data = _read_toml(config_path)
    assert data["model"] == "gpt-5.5"
    server = data["mcp_servers"]["context7"]
    assert server["command"] == "npx"
    assert server["args"] == ["-y", "@upstash/context7-mcp"]
    assert server["env"] == {
        "LOCAL_TOKEN": "local-token",
        "RENAMED": "other-token",
        "STATIC": "x",
    }
    assert "env_vars" not in server
    assert server["bearer_token_env_var"] == "AUTH_TOKEN"
    assert server["http_headers"] == {"X-Region": "us"}
    assert server["env_http_headers"] == {"X-Env": "HEADER_TOKEN"}
    assert "type" not in server
    assert "enabled" not in server
    assert target.consume_warnings() == []
    assert target.item_exists("mcp", "context7")


def test_deploy_mcp_server_preserves_existing_codex_specific_fields(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server(
        "srv",
        {
            "command": "cmd",
            "env_vars": [{"name": "REMOTE_TOKEN", "source": "remote"}],
            "env": {"PORT": 123},
            "headers": {"X-Number": 5},
            "http_headers": {"X.Dot": "static"},
            "env_http_headers": {"X-Existing": "EXISTING"},
        },
    )

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert server["env_vars"] == [{"name": "REMOTE_TOKEN", "source": "remote"}]
    assert server["env"] == {"PORT": 123}
    assert server["http_headers"] == {"X.Dot": "static", "X-Number": 5}
    assert server["env_http_headers"] == {"X-Existing": "EXISTING"}


def test_deploy_mcp_server_applies_codex_overrides(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server(
        "srv",
        {
            "command": "/etc/profiles/per-user/johnw/bin/server",
            "args": ["--old"],
            "codex": {
                "command": "server",
                "args": ["--new"],
                "env_vars": ["TOKEN"],
            },
        },
    )

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert server == {
        "command": "server",
        "args": ["--new"],
        "env_vars": ["TOKEN"],
    }


def test_deploy_mcp_omits_empty_env_and_header_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TOKEN", "token-value")
    target = _make_target(tmp_path)
    target.deploy_mcp_server(
        "env-only",
        {
            "command": "cmd",
            "env": {"TOKEN": "${TOKEN}"},
            "headers": {"X-Env": "${HEADER_TOKEN}"},
        },
    )
    target.deploy_mcp_server(
        "header-only",
        {"command": "cmd", "headers": {"X-Region": "us"}},
    )

    data = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]
    assert data["env-only"]["env"] == {"TOKEN": "token-value"}
    assert "env_vars" not in data["env-only"]
    assert data["env-only"]["env_http_headers"] == {"X-Env": "HEADER_TOKEN"}
    assert "http_headers" not in data["env-only"]
    assert data["header-only"]["http_headers"] == {"X-Region": "us"}
    assert "env_http_headers" not in data["header-only"]


def test_deploy_mcp_strict_expands_missing_env_refs(tmp_path: Path):
    target = _make_target(tmp_path)
    with pytest.raises(EnvVarError, match=r"MISSING.*mcp\.srv\.env\.TOKEN"):
        target.deploy_mcp_server(
            "srv",
            {"command": "cmd", "env": {"TOKEN": "${MISSING}"}},
        )


def test_deploy_mcp_url_strict_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Codex performs no env expansion anywhere in config.toml -- a url is
    # used literally -- so a URL-borne secret must be baked at deploy time.
    monkeypatch.setenv("REF_KEY", "secret-ref")
    target = _make_target(tmp_path)
    target.deploy_mcp_server(
        "srv", {"url": "https://api.example.com/mcp?apiKey=${REF_KEY}"}
    )

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert server["url"] == "https://api.example.com/mcp?apiKey=secret-ref"


def test_deploy_mcp_url_without_refs_unchanged(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server("srv", {"url": "https://api.example.com/mcp"})

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert server["url"] == "https://api.example.com/mcp"


def test_deploy_mcp_url_missing_env_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MISSING_URL_KEY", raising=False)
    target = _make_target(tmp_path)
    with pytest.raises(EnvVarError, match=r"MISSING_URL_KEY.*mcp\.srv\.url"):
        target.deploy_mcp_server(
            "srv", {"url": "https://x/mcp?apiKey=${MISSING_URL_KEY}"}
        )


def test_deploy_mcp_preserves_literal_string_env_value(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server(
        "srv",
        {"command": "cmd", "env": {"MODE": "literal"}},
    )

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert server["env"] == {"MODE": "literal"}


def test_deploy_mcp_omits_empty_env_map(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server("srv", {"command": "cmd", "env": {}})

    server = _read_toml(tmp_path / ".codex" / "config.toml")["mcp_servers"]["srv"]
    assert "env" not in server


def test_mcp_item_exists_false_when_config_missing_or_table_absent(tmp_path: Path):
    target = _make_target(tmp_path)
    assert not target.item_exists("mcp", "srv")
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[model_providers.proxy]\nname = "Proxy"\n')
    target.deploy_mcp_server("srv", {"command": "cmd"})
    assert target.item_exists("mcp", "srv")


def test_deploy_mcp_disabled_removes_managed_block(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_mcp_server("srv", {"command": "cmd"})
    target.deploy_mcp_server("srv", {"enabled": False, "command": "cmd"})
    assert "mcp_servers" not in _read_toml(tmp_path / ".codex" / "config.toml")


def test_remove_mcp_preserves_unmanaged_config(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[mcp_servers.manual]\ncommand = "x"\n')
    assert target.item_exists("mcp", "manual")
    target.remove_mcp_server("manual")
    assert _read_toml(config_path)["mcp_servers"]["manual"]["command"] == "x"


def test_deploy_mcp_rejects_invalid_toml(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("[bad\n")

    with pytest.raises(CodexConfigError, match="Cannot parse TOML"):
        target.item_exists("mcp", "x")


def test_deploy_mcp_rejects_invalid_unmanaged_toml(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("[bad\n")

    with pytest.raises(CodexConfigError, match="Cannot parse TOML"):
        target.deploy_mcp_server("srv", {"command": "cmd"})


def test_deploy_mcp_rejects_unmanaged_duplicate(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[mcp_servers.srv]\ncommand = "manual"\n')

    with pytest.raises(CodexConfigError, match="unmanaged table"):
        target.deploy_mcp_server("srv", {"command": "cmd"})


def test_prepare_force_deploy_mcp_removes_unmanaged_duplicate(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "[mcp_servers.srv]\n"
        'command = "manual"\n'
        "\n"
        "[mcp_servers.srv.env]\n"
        'TOKEN = "manual"\n'
        "\n"
        "[mcp_servers.keep]\n"
        'command = "keep"\n'
    )

    target.prepare_force_deploy("mcp", "srv", {})
    target.deploy_mcp_server("srv", {"command": "cmd"})

    data = _read_toml(config_path)
    assert data["mcp_servers"]["srv"] == {"command": "cmd"}
    assert data["mcp_servers"]["keep"] == {"command": "keep"}


def test_prepare_force_deploy_mcp_preserves_managed_blocks(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "# >>> promptdeploy codex mcp managed\n"
        "[mcp_servers.managed]\n"
        'command = "managed"\n'
        "# <<< promptdeploy codex mcp managed\n"
        "\n"
        "[mcp_servers.srv]\n"
        'command = "manual"\n'
    )

    target.prepare_force_deploy("mcp", "srv", {})

    text = config_path.read_text("utf-8")
    assert "# >>> promptdeploy codex mcp managed" in text
    assert "[mcp_servers.managed]" in text
    assert "[mcp_servers.srv]" not in text


def test_prepare_force_deploy_missing_config_noops(tmp_path: Path):
    target = _make_target(tmp_path)

    target.prepare_force_deploy("mcp", "srv", {})
    target.prepare_force_deploy("models", "models", {"providers": []})
    target.prepare_force_deploy("agent", "helper", {})

    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_prepare_force_deploy_models_removes_codex_provider_duplicate(
    tmp_path: Path,
):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "[model_providers.proxy]\n"
        'name = "Manual"\n'
        "\n"
        "[model_providers.keep]\n"
        'name = "Keep"\n'
    )

    target.prepare_force_deploy(
        "models",
        "models",
        {
            "providers": {
                "proxy": {"codex": {}},
                "ignored": {},
                "invalid": [],
            }
        },
    )

    data = _read_toml(config_path)
    assert "proxy" not in data["model_providers"]
    assert data["model_providers"]["keep"] == {"name": "Keep"}


def test_prepare_force_deploy_mcp_removes_array_table_duplicate(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "[[mcp_servers.srv]]\n"
        'command = "manual"\n'
        "\n"
        "[mcp_servers.keep]\n"
        'command = "keep"\n'
    )

    target.prepare_force_deploy("mcp", "srv", {})
    target.deploy_mcp_server("srv", {"command": "cmd"})

    data = _read_toml(config_path)
    assert data["mcp_servers"]["srv"] == {"command": "cmd"}
    assert data["mcp_servers"]["keep"] == {"command": "keep"}


def test_prepare_force_deploy_does_not_mask_invalid_toml(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[bad\n\n[mcp_servers.srv]\ncommand = "manual"\n')

    target.prepare_force_deploy("mcp", "srv", {})

    with pytest.raises(CodexConfigError, match="Cannot parse TOML"):
        target.deploy_mcp_server("srv", {"command": "cmd"})


def test_prepare_force_deploy_preserves_unclosed_managed_block(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = (
        "# >>> promptdeploy codex mcp managed\n"
        "[mcp_servers.managed]\n"
        'command = "managed"\n'
    )
    config_path.write_text(original)

    target.prepare_force_deploy("mcp", "srv", {})

    assert config_path.read_text("utf-8") == original


def test_toml_probe_path_handles_defensive_mixed_shapes():
    assert CodexTarget._find_toml_probe_path(
        {"first": {}, "second": {"__probe": True}}, "__probe", []
    ) == ["second"]
    assert CodexTarget._find_toml_probe_path(
        {"array": [{}, {"__probe": True}]}, "__probe", []
    ) == ["array"]
    assert CodexTarget._find_toml_probe_path({"array": [{}]}, "__probe", []) is None
    assert CodexTarget._find_toml_probe_path("plain", "__probe", []) is None


def test_deploy_models_writes_codex_providers_and_removes_stale(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_models(
        {
            "providers": {
                "proxy": {
                    "display_name": "Proxy",
                    "base_url": "https://proxy.example/v1",
                    "api_key": "${PROXY_KEY}",
                    "droid": {},
                    "codex": {
                        "name": "Proxy API",
                        "query_params": {"api-version": "2026-01-01"},
                        "auth": {"command": "get-token", "args": ["--codex"]},
                    },
                    "models": {"gpt-x": {}},
                },
                "ignored": {
                    "display_name": "Ignored",
                    "base_url": "https://ignored.example/v1",
                    "api_key": "${IGNORED_KEY}",
                    "models": {"x": {}},
                },
            }
        }
    )

    config_path = tmp_path / ".codex" / "config.toml"
    data = _read_toml(config_path)
    provider = data["model_providers"]["proxy"]
    assert provider["name"] == "Proxy API"
    assert provider["base_url"] == "https://proxy.example/v1"
    assert provider["env_key"] == "PROXY_KEY"
    assert provider["wire_api"] == "responses"
    assert provider["query_params"] == {"api-version": "2026-01-01"}
    assert provider["auth"] == {"command": "get-token", "args": ["--codex"]}
    assert "ignored" not in data["model_providers"]
    assert target.item_exists("models", "models")

    target.deploy_models({"providers": {}})
    assert "model_providers" not in _read_toml(config_path)


def test_remove_models_removes_unclosed_managed_blocks(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "# >>> promptdeploy codex model_provider stale\n"
        "[model_providers.stale]\n"
        'name = "Stale"\n'
    )

    target.remove_models()
    target.remove_models()

    assert config_path.read_text() == ""


def test_remove_models_preserves_unmanaged_config_lines(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('model = "gpt-5.5"\n')

    target.remove_models()

    assert config_path.read_text() == 'model = "gpt-5.5"\n'


def test_deploy_models_with_non_mapping_providers_noops(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_models({"providers": []})
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_deploy_models_preserves_codex_overrides(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_models(
        {
            "providers": {
                "explicit": {
                    "display_name": "Explicit",
                    "base_url": "https://provider.example/v1",
                    "api_key": "literal",
                    "codex": {
                        "base_url": "https://codex.example/v1",
                        "env_key": "EXPLICIT_KEY",
                        "wire_api": "chat",
                    },
                    "models": {"x": {}},
                },
                "literal-key": {
                    "display_name": "Literal",
                    "base_url": "https://literal.example/v1",
                    "api_key": "literal",
                    "codex": {},
                    "models": {"x": {}},
                },
            }
        }
    )
    providers = _read_toml(tmp_path / ".codex" / "config.toml")["model_providers"]
    provider = providers["explicit"]
    assert provider["base_url"] == "https://codex.example/v1"
    assert provider["env_key"] == "EXPLICIT_KEY"
    assert provider["wire_api"] == "chat"
    assert providers["literal-key"]["base_url"] == "https://literal.example/v1"
    assert "env_key" not in providers["literal-key"]


def test_deploy_models_rejects_unmanaged_duplicate(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[model_providers.proxy]\nname = "Manual"\n')

    with pytest.raises(CodexConfigError, match="unmanaged table"):
        target.deploy_models(
            {
                "providers": {
                    "proxy": {
                        "display_name": "Proxy",
                        "base_url": "https://proxy.example/v1",
                        "codex": {},
                        "models": {"x": {}},
                    }
                }
            }
        )


def test_deploy_models_duplicate_leaves_existing_managed_blocks(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_models(
        {
            "providers": {
                "stale": {
                    "display_name": "Stale",
                    "base_url": "https://stale.example/v1",
                    "codex": {},
                    "models": {"x": {}},
                }
            }
        }
    )
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.write_text(
        config_path.read_text() + '\n[model_providers.proxy]\nname = "Manual"\n'
    )

    with pytest.raises(CodexConfigError, match="unmanaged table"):
        target.deploy_models(
            {
                "providers": {
                    "proxy": {
                        "display_name": "Proxy",
                        "base_url": "https://proxy.example/v1",
                        "codex": {},
                        "models": {"x": {}},
                    }
                }
            }
        )

    text = config_path.read_text()
    assert "# >>> promptdeploy codex model_provider stale" in text
    assert '[model_providers.proxy]\nname = "Manual"' in text


def test_deploy_models_ignores_malformed_provider_entries(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_models({"providers": {"bad": [], "plain": {"codex": []}}})
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_deploy_models_rejects_unrepresentable_toml_value(tmp_path: Path):
    target = _make_target(tmp_path)
    with pytest.raises(CodexConfigError, match="null"):
        target.deploy_models(
            {
                "providers": {
                    "p": {
                        "display_name": "P",
                        "base_url": "https://p.example/v1",
                        "codex": {"name": None},
                        "models": {"x": {}},
                    }
                }
            }
        )


def test_deploy_hook_merges_tags_replaces_and_removes(tmp_path: Path):
    target = _make_target(tmp_path)
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"matcher": "manual", "hooks": []}],
                    "PreToolUse": [{"_source": "git-ai", "hooks": []}],
                }
            }
        )
    )

    target.deploy_hook(
        "git-ai",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "check"}],
                    },
                    "bad-entry",
                ],
                "Stop": "not-a-list",
            }
        },
    )

    data = json.loads(hooks_path.read_text())
    assert data["hooks"]["Stop"] == [{"matcher": "manual", "hooks": []}]
    assert data["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "check"}],
            "_source": "git-ai",
        }
    ]
    assert target.item_exists("hook", "git-ai")

    target.deploy_hook("git-ai", {"hooks": {}})
    data = json.loads(hooks_path.read_text())
    assert "PreToolUse" not in data["hooks"]
    assert data["hooks"]["Stop"] == [{"matcher": "manual", "hooks": []}]

    target.remove_hook("missing")
    target.remove_hook("git-ai")
    assert json.loads(hooks_path.read_text())["hooks"]["Stop"]


def test_deploy_hook_preserves_existing_event_list_and_non_mapping_config(
    tmp_path: Path,
):
    target = _make_target(tmp_path)
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": "manual", "hooks": []}]}})
    )

    target.deploy_hook(
        "new",
        {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}},
    )
    target.deploy_hook("ignored", {"hooks": []})

    entries = json.loads(hooks_path.read_text())["hooks"]["PreToolUse"]
    assert entries[0] == {"matcher": "manual", "hooks": []}
    assert entries[1]["_source"] == "new"


def test_deploy_hook_without_codex_notify_preserves_config_toml(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = '[mcp_servers.srv]\ncommand = "srv"\n\n'
    config_path.write_text(original)

    target.deploy_hook(
        "plain",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "plain"}]}]}},
    )

    assert config_path.read_text() == original


def test_deploy_hook_creates_and_prunes_empty_hooks(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_hook("empty", {"hooks": {}})
    assert json.loads((tmp_path / ".codex" / "hooks.json").read_text()) == {}
    target.remove_hook("empty")


def test_deploy_hook_manages_codex_notify_at_toml_root(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[mcp_servers.srv]\ncommand = "srv"\n')

    target.deploy_hook(
        "agent-deck-codex",
        {
            "codex": {"notify": ["agent-deck", "codex-notify"]},
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "agent-deck"}]}]
            },
        },
    )

    text = config_path.read_text()
    assert text.startswith("# >>> promptdeploy codex notify agent-deck-codex\n")
    data = tomllib.loads(text)
    assert data["notify"] == ["agent-deck", "codex-notify"]
    assert data["mcp_servers"]["srv"]["command"] == "srv"
    assert target.item_exists("hook", "agent-deck-codex")

    target.deploy_hook("agent-deck-codex", {"hooks": {}})

    assert "notify" not in tomllib.loads(config_path.read_text())


def test_deploy_hook_adopts_agent_deck_codex_notify_install(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "# BEGIN AGENTDECK CODEX NOTIFY\n"
        'notify = ["agent-deck", "codex-notify"]\n'
        "# END AGENTDECK CODEX NOTIFY\n\n"
        "[model_providers.openai]\n"
        'name = "OpenAI"\n'
    )

    target.deploy_hook(
        "agent-deck-codex",
        {"codex": {"notify": ["agent-deck", "codex-notify"]}},
    )

    text = config_path.read_text()
    assert "# BEGIN AGENTDECK CODEX NOTIFY" not in text
    assert text.count("notify = ") == 1
    assert tomllib.loads(text)["notify"] == ["agent-deck", "codex-notify"]


def test_deploy_hook_rejects_unmanaged_codex_notify(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('notify = ["custom-notify"]\n')

    with pytest.raises(CodexConfigError, match="unmanaged 'notify'"):
        target.deploy_hook(
            "agent-deck-codex",
            {"codex": {"notify": ["agent-deck", "codex-notify"]}},
        )


def test_deploy_hook_reports_toml_parse_errors_for_codex_notify(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("broken = [\n")

    with pytest.raises(CodexConfigError, match="Cannot parse TOML"):
        target.deploy_hook(
            "agent-deck-codex",
            {"codex": {"notify": ["agent-deck", "codex-notify"]}},
        )


def test_deploy_hook_rejects_second_managed_codex_notify(tmp_path: Path):
    target = _make_target(tmp_path)

    target.deploy_hook("first", {"codex": {"notify": ["first"]}})

    with pytest.raises(CodexConfigError, match="managed by hook 'first'"):
        target.deploy_hook("second", {"codex": {"notify": ["second"]}})


def test_agent_deck_codex_notify_cleanup_variants():
    assert (
        CodexTarget._remove_agent_deck_notify_marker_blocks(
            '# BEGIN AGENTDECK CODEX NOTIFY\nnotify = ["agent-deck", "codex-notify"]\n'
        )
        == ""
    )
    assert CodexTarget._remove_agent_deck_legacy_notify_table(
        "[notify]\n"
        "\n"
        'program = ["agent-deck", "codex-notify"]\n'
        "[model_providers.openai]\n"
        'name = "OpenAI"\n'
    ) == ('[model_providers.openai]\nname = "OpenAI"\n')
    assert CodexTarget._remove_agent_deck_legacy_notify_table(
        '[notify]\nprogram = ["other"]\n'
    ) == ('[notify]\nprogram = ["other"]\n')


def test_remove_hook_noops_on_missing_or_malformed_hooks(tmp_path: Path):
    target = _make_target(tmp_path)
    target.remove_hook("x")
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(json.dumps({"hooks": "bad"}))
    target.remove_hook("x")
    assert json.loads(hooks_path.read_text()) == {"hooks": "bad"}
    assert not target.item_exists("hook", "x")

    hooks_path.write_text(json.dumps({"hooks": {"Stop": "bad"}}))
    target.remove_hook("x")
    assert json.loads(hooks_path.read_text()) == {"hooks": {"Stop": "bad"}}


def test_remove_hook_keeps_non_mapping_entries(tmp_path: Path):
    target = _make_target(tmp_path)
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps({"hooks": {"Stop": ["manual", {"_source": "generated"}]}})
    )

    target.remove_hook("generated")

    assert json.loads(hooks_path.read_text()) == {"hooks": {"Stop": ["manual"]}}


def test_hooks_json_errors_are_clear(tmp_path: Path):
    target = _make_target(tmp_path)
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text("[")
    with pytest.raises(CodexConfigError, match="Cannot parse JSON"):
        target.deploy_hook("x", {"hooks": {}})
    hooks_path.write_text("[]")
    with pytest.raises(CodexConfigError, match="Top level"):
        target.item_exists("hook", "x")


def test_config_file_mode_is_preserved(tmp_path: Path):
    target = _make_target(tmp_path)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("")
    os.chmod(config_path, 0o640)

    target.deploy_mcp_server("srv", {"command": "cmd"})

    assert stat.S_IMODE(os.stat(config_path).st_mode) == 0o640


def test_malformed_managed_blocks_are_removed(tmp_path: Path):
    text = '# >>> promptdeploy codex mcp srv\n[mcp_servers.srv]\ncommand = "x"\n'
    assert CodexTarget._remove_managed_block_from_text(text, "mcp", "srv") == ""
    assert CodexTarget._remove_managed_blocks_from_text(text) == ""
    assert (
        CodexTarget._remove_managed_blocks_from_text(
            "# >>> promptdeploy codex mcp srv\n"
            "[mcp_servers.srv]\n"
            "# <<< promptdeploy codex model_provider srv\n"
        )
        == ""
    )


def test_toml_value_and_key_helpers_cover_scalar_variants():
    assert CodexTarget._render_toml_document({"section": {"key": "value"}}) == (
        b'[section]\nkey = "value"\n'
    )
    assert CodexTarget._toml_value(False) == "false"
    assert CodexTarget._toml_value(3) == "3"
    assert CodexTarget._toml_value(1.5) == "1.5"
    assert CodexTarget._toml_value({}) == "{  }"
    assert CodexTarget._toml_value({"X.Dot": "v"}) == '{ "X.Dot" = "v" }'
    assert CodexTarget._toml_key("X.Dot") == '"X.Dot"'
    with pytest.raises(CodexConfigError, match="type object"):
        CodexTarget._toml_value(object())


def test_write_bytes_cleans_tmp_on_replace_failure(tmp_path: Path, monkeypatch):
    target = _make_target(tmp_path)

    def boom(_src: str, _dst: Path) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="replace failed"):
        target.deploy_agent("a", b"body")
    leftovers = list((tmp_path / ".codex" / "agents").glob("*.tmp"))
    assert leftovers == []


def test_write_text_cleans_tmp_on_replace_failure(tmp_path: Path, monkeypatch):
    target = _make_target(tmp_path)

    def boom(_src: str, _dst: Path) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="replace failed"):
        target.deploy_mcp_server("srv", {"command": "cmd"})
    leftovers = list((tmp_path / ".codex").glob("*.tmp"))
    assert leftovers == []


def test_base_noop_methods_do_not_create_files(tmp_path: Path):
    target = _make_target(tmp_path)
    target.deploy_settings({"model": "gpt-5.5"}, [])
    target.remove_settings(["model"])
    target.deploy_marketplace("m", {})
    target.remove_marketplace("m")
    assert not (tmp_path / ".codex" / "settings.json").exists()


def test_unknown_items_do_not_exist_and_no_bytes(tmp_path: Path):
    target = _make_target(tmp_path)
    assert not target.item_exists("marketplace", "m")
    assert target.would_deploy_bytes("skill", "s", b"body") is None
    assert target.read_deployed_bytes("skill", "s") is None
    assert target.deployed_artifact_path("mcp", "srv") is None
