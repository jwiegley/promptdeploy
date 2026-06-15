"""OpenAI Codex target implementation."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from ..frontmatter import (
    parse_frontmatter,
    serialize_frontmatter,
    strip_deployment_fields,
    transform_for_target,
)
from ..manifest import MANIFEST_FILENAME
from .base import Target

_MCP_STRIP_KEYS = frozenset(
    {"name", "description", "scope", "enabled", "only", "except"}
)
_MODEL_STRIP_KEYS = frozenset(
    {
        "models",
        "overrides",
        "only",
        "except",
        "droid",
        "opencode",
        "codex",
        "base_url",
    }
)
_AGENT_DROP_KEYS = frozenset({"tools"})
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_BEARER_ENV_REF_RE = re.compile(r"^Bearer\s+\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_BEGIN_PREFIX = "# >>> promptdeploy codex "
_END_PREFIX = "# <<< promptdeploy codex "


class CodexConfigError(ValueError):
    """Raised when an existing Codex config file cannot be parsed."""


class CodexTarget(Target):
    """Deploy promptdeploy content into an OpenAI Codex local configuration."""

    def __init__(self, target_id: str, config_path: Path) -> None:
        self._id = target_id
        raw_path = config_path.expanduser().resolve()
        if raw_path.name == ".codex":
            self._home_path = raw_path.parent
            self._codex_path = raw_path
        else:
            self._home_path = raw_path
            self._codex_path = raw_path / ".codex"
        self._skills_path = self._home_path / ".agents" / "skills"
        self._warnings: list[tuple[str, list[str]]] = []

    @property
    def id(self) -> str:
        return self._id

    def exists(self) -> bool:
        return self._home_path.is_dir()

    def manifest_path(self) -> Path:
        return self._codex_path / MANIFEST_FILENAME

    def rsync_includes(self) -> list[str] | None:
        return [
            ".agents/",
            ".agents/skills/",
            ".agents/skills/**",
            ".codex/",
            ".codex/**",
        ]

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if item_type in ("settings", "marketplace"):
            return True
        if item_type == "models":
            providers = (metadata or {}).get("providers")
            if not isinstance(providers, dict):
                return True
            return not any(
                isinstance(provider, dict) and isinstance(provider.get("codex"), dict)
                for provider in providers.values()
            )
        return False

    def content_fingerprint(self, item_type: str) -> str | None:
        if item_type in ("agent", "mcp", "models", "hook"):
            return "codex-target-v1"
        if item_type in ("command", "prompt"):
            return "codex-command-skill-v1"
        return None

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._codex_path / "agents" / f"{name}.toml"
        self._write_bytes(dest, self._agent_toml(name, content, collect_warnings=True))

    def deploy_command(self, name: str, content: bytes) -> None:
        self._write_generated_skill(
            self._command_skill_dir(name),
            self._command_skill_bytes(name, content),
        )

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        from ..poet import POET_EXTENSIONS, parse_plain, parse_poet, render_for_command

        if source_path.suffix in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            if doc.warnings:
                self._warnings.append((name, list(doc.warnings)))
        else:
            doc = parse_plain(content)
        rendered = render_for_command(doc)
        self._write_generated_skill(
            self._prompt_skill_dir(name),
            self._generated_skill_bytes(
                skill_name=self._prompt_skill_name(name),
                description=f"Promptdeploy rendered prompt '{name}'.",
                body=rendered,
                source_kind="prompt",
                source_name=name,
            ),
        )

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        dest = self._skills_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".promptdeploy-"))
        try:
            staged = tmp_dir / "skill"
            shutil.copytree(source_dir.resolve(), staged, symlinks=False)
            skill_md = staged / "SKILL.md"
            if skill_md.exists():
                self._write_bytes(skill_md, transform_for_target(skill_md.read_bytes()))
            if dest.is_symlink():
                dest.unlink()
            elif dest.exists():
                shutil.rmtree(dest)
            os.replace(staged, dest)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        if not config.get("enabled", True):
            self.remove_mcp_server(name)
            return
        if self._unmanaged_table_exists(["mcp_servers", name]):
            raise CodexConfigError(
                f"Cannot deploy Codex MCP server '{name}': an unmanaged table "
                f"with that name already exists in {self._config_path()}"
            )
        entry = self._codex_mcp_entry(name, config)
        block = self._render_toml_table(["mcp_servers", name], entry)
        self._replace_managed_block("mcp", name, block)

    def deploy_models(self, config: dict[str, Any]) -> None:
        providers = config.get("providers")
        if not isinstance(providers, dict):
            self._remove_managed_blocks_by_kind("model_provider")
            return

        managed: list[tuple[str, dict[str, Any]]] = []
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            codex_config = provider.get("codex")
            if not isinstance(codex_config, dict):
                continue
            if self._unmanaged_table_exists(["model_providers", str(provider_id)]):
                raise CodexConfigError(
                    f"Cannot deploy Codex model provider '{provider_id}': "
                    "an unmanaged table with that name already exists in "
                    f"{self._config_path()}"
                )
            entry = self._codex_model_provider_entry(str(provider_id), provider)
            managed.append((str(provider_id), entry))

        self._remove_managed_blocks_by_kind("model_provider")
        for provider_id, entry in managed:
            block = self._render_toml_table(["model_providers", provider_id], entry)
            self._append_managed_block("model_provider", provider_id, block)

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        path = self._hooks_path()
        data = self._load_json(path)
        hooks = self._ensure_dict(data, "hooks")
        self._strip_hook_source(hooks, name)

        hooks_config = config.get("hooks")
        if isinstance(hooks_config, dict):
            for event_type, entries in hooks_config.items():
                if not isinstance(entries, list):
                    continue
                event_entries = hooks.get(event_type)
                if not isinstance(event_entries, list):
                    event_entries = []
                    hooks[event_type] = event_entries
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    tagged = dict(entry)
                    tagged["_source"] = name
                    event_entries.append(tagged)

        self._prune_empty_hooks(data)
        self._save_json(path, data)

    def deploy_settings(
        self, rendered: dict[str, Any], previous_keys: list[str]
    ) -> None:
        pass

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        pass

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_agent(self, name: str) -> None:
        self._remove_file(self._codex_path / "agents" / f"{name}.toml")

    def remove_command(self, name: str) -> None:
        self._remove_tree(self._command_skill_dir(name))

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        self._remove_tree(self._prompt_skill_dir(name))

    def remove_skill(self, name: str) -> None:
        self._remove_tree(self._skills_path / name)

    def remove_mcp_server(self, name: str) -> None:
        self._remove_managed_block("mcp", name)

    def remove_models(self) -> None:
        self._remove_managed_blocks_by_kind("model_provider")

    def remove_hook(self, name: str) -> None:
        path = self._hooks_path()
        if not path.exists():
            return
        data = self._load_json(path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return
        self._strip_hook_source(hooks, name)
        self._prune_empty_hooks(data)
        self._save_json(path, data)

    def remove_settings(self, previous_keys: list[str]) -> None:
        pass

    def remove_marketplace(self, name: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Pre-existing detection
    # ------------------------------------------------------------------

    def item_exists(self, item_type: str, name: str) -> bool:
        if item_type == "agent":
            return (self._codex_path / "agents" / f"{name}.toml").exists()
        if item_type == "command":
            return self._command_skill_dir(name).exists()
        if item_type == "prompt":
            return self._prompt_skill_dir(name).exists()
        if item_type == "skill":
            dest = self._skills_path / name
            return dest.exists() or dest.is_symlink()
        if item_type == "mcp":
            servers = self._parsed_config().get("mcp_servers")
            return isinstance(servers, dict) and name in servers
        if item_type == "models":
            return self._has_managed_block_kind("model_provider")
        if item_type == "hook":
            data = self._load_json(self._hooks_path())
            hooks = data.get("hooks")
            if not isinstance(hooks, dict):
                return False
            return any(
                isinstance(entry, dict) and entry.get("_source") == name
                for entries in hooks.values()
                if isinstance(entries, list)
                for entry in entries
            )
        return False

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        if item_type == "agent":
            return self._agent_toml(name, content, collect_warnings=False)
        return None

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
        if item_type != "agent":
            return None
        path = self._codex_path / "agents" / f"{name}.toml"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def deployed_artifact_path(self, item_type: str, name: str) -> Path | None:
        if item_type == "agent":
            return Path(".codex") / "agents" / f"{name}.toml"
        if item_type == "command":
            return Path(".agents") / "skills" / self._command_skill_dir_name(name)
        if item_type == "prompt":
            return Path(".agents") / "skills" / self._prompt_skill_dir_name(name)
        if item_type == "skill":
            return Path(".agents") / "skills" / name
        return None

    # ------------------------------------------------------------------
    # Transform helpers
    # ------------------------------------------------------------------

    def _agent_toml(
        self, name: str, content: bytes, *, collect_warnings: bool
    ) -> bytes:
        metadata, body = parse_frontmatter(content)
        cleaned = strip_deployment_fields(metadata or {})
        for key in _AGENT_DROP_KEYS:
            if key in cleaned:
                cleaned.pop(key, None)
                if collect_warnings:
                    self._warnings.append(
                        (
                            name,
                            [
                                f"frontmatter field '{key}' has no Codex "
                                "custom-agent equivalent; dropped"
                            ],
                        )
                    )
        entry: dict[str, Any] = {}
        entry["name"] = str(cleaned.pop("name", name))
        description = cleaned.pop("description", None)
        if isinstance(description, str) and description.strip():
            entry["description"] = description
        else:
            entry["description"] = f"Custom Codex agent '{name}'."
        entry["developer_instructions"] = body.decode("utf-8")
        entry.update(cleaned)
        return self._render_toml_document(entry)

    def _command_skill_bytes(self, name: str, content: bytes) -> bytes:
        metadata, body = parse_frontmatter(content)
        cleaned = strip_deployment_fields(metadata or {})
        description = cleaned.get("description")
        if not isinstance(description, str) or not description.strip():
            description = f"Promptdeploy command '{name}'."
        return self._generated_skill_bytes(
            skill_name=self._command_skill_name(name),
            description=description,
            body=body,
            source_kind="command",
            source_name=name,
        )

    def _generated_skill_bytes(
        self,
        *,
        skill_name: str,
        description: str,
        body: bytes,
        source_kind: str,
        source_name: str,
    ) -> bytes:
        metadata = {"name": skill_name, "description": description}
        prefix = (
            f"Use this skill for the promptdeploy {source_kind} '{source_name}'.\n\n"
            "Treat the user's current request as the arguments for the prompt below. "
            "If the prompt contains `$ARGUMENTS`, interpret it as those arguments.\n\n"
            "Prompt:\n\n"
        ).encode()
        return serialize_frontmatter(metadata, prefix + body)

    @staticmethod
    def _command_skill_name(name: str) -> str:
        return f"command-{name}"

    @staticmethod
    def _prompt_skill_name(name: str) -> str:
        return f"prompt-{name}"

    @classmethod
    def _command_skill_dir_name(cls, name: str) -> str:
        return cls._command_skill_name(name)

    @classmethod
    def _prompt_skill_dir_name(cls, name: str) -> str:
        return cls._prompt_skill_name(name)

    def _command_skill_dir(self, name: str) -> Path:
        return self._skills_path / self._command_skill_dir_name(name)

    def _prompt_skill_dir(self, name: str) -> Path:
        return self._skills_path / self._prompt_skill_dir_name(name)

    def _write_generated_skill(self, dest: Path, content: bytes) -> None:
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        self._write_bytes(dest / "SKILL.md", content)

    def _codex_mcp_entry(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {}
        env_vars: list[Any] = list(config.get("env_vars", []))
        for key, value in config.items():
            if key in _MCP_STRIP_KEYS or key in {"headers", "type"}:
                continue
            if key == "env" and isinstance(value, dict):
                env: dict[str, Any] = {}
                for env_key, env_value in value.items():
                    if isinstance(env_value, str):
                        match = _ENV_REF_RE.fullmatch(env_value)
                        if match and match.group(1) == env_key:
                            env_vars.append(env_key)
                            continue
                        if "${" in env_value:
                            self._warnings.append(
                                (
                                    name,
                                    [
                                        f"env.{env_key} contains an embedded "
                                        "variable reference; Codex cannot express "
                                        "that without writing a literal value"
                                    ],
                                )
                            )
                    env[env_key] = env_value
                if env:
                    entry["env"] = env
                continue
            if key != "env_vars":
                entry[key] = value
        if env_vars:
            entry["env_vars"] = env_vars

        headers = config.get("headers")
        if isinstance(headers, dict):
            http_headers: dict[str, Any] = dict(entry.get("http_headers", {}))
            env_http_headers: dict[str, str] = dict(entry.get("env_http_headers", {}))
            for header, value in headers.items():
                if not isinstance(value, str):
                    http_headers[header] = value
                    continue
                bearer = _BEARER_ENV_REF_RE.fullmatch(value)
                if header.lower() == "authorization" and bearer is not None:
                    entry["bearer_token_env_var"] = bearer.group(1)
                    continue
                match = _ENV_REF_RE.fullmatch(value)
                if match is not None:
                    env_http_headers[header] = match.group(1)
                else:
                    http_headers[header] = value
            if http_headers:
                entry["http_headers"] = http_headers
            if env_http_headers:
                entry["env_http_headers"] = env_http_headers
        return entry

    @staticmethod
    def _codex_model_provider_entry(
        provider_id: str, provider: dict[str, Any]
    ) -> dict[str, Any]:
        codex_config = provider.get("codex")
        assert isinstance(codex_config, dict)
        entry = {
            k: v
            for k, v in provider.items()
            if k not in _MODEL_STRIP_KEYS and k not in {"api_key", "display_name"}
        }
        entry.update(codex_config)
        entry.setdefault("name", provider.get("display_name", provider_id))
        if "base_url" not in entry and "base_url" in provider:
            entry["base_url"] = provider["base_url"]
        api_key = provider.get("api_key")
        if "env_key" not in entry and isinstance(api_key, str):
            match = _ENV_REF_RE.fullmatch(api_key)
            if match is not None:
                entry["env_key"] = match.group(1)
        entry.setdefault("wire_api", "responses")
        return entry

    # ------------------------------------------------------------------
    # TOML helpers
    # ------------------------------------------------------------------

    def _config_path(self) -> Path:
        return self._codex_path / "config.toml"

    def _parsed_config(self) -> dict[str, Any]:
        path = self._config_path()
        if not path.exists():
            return {}
        text = path.read_text("utf-8")
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise CodexConfigError(
                f"Cannot parse TOML in {path}: {exc}. Fix or remove the file, "
                "then re-run."
            ) from exc
        return data

    def _config_text(self) -> str:
        path = self._config_path()
        if not path.exists():
            return ""
        return path.read_text("utf-8")

    def _replace_managed_block(self, kind: str, name: str, block: str) -> None:
        text = self._remove_managed_block_from_text(self._config_text(), kind, name)
        self._write_config_text(self._append_block_to_text(text, kind, name, block))

    def _append_managed_block(self, kind: str, name: str, block: str) -> None:
        text = self._config_text()
        self._write_config_text(self._append_block_to_text(text, kind, name, block))

    def _remove_managed_block(self, kind: str, name: str) -> None:
        self._write_config_text(
            self._remove_managed_block_from_text(self._config_text(), kind, name)
        )

    def _remove_managed_blocks_by_kind(self, kind: str) -> None:
        if not self._config_path().exists():
            return
        lines = self._config_text().splitlines(keepends=True)
        kept: list[str] = []
        i = 0
        while i < len(lines):
            marker_kind, _marker_name = self._parse_begin_marker(lines[i])
            if marker_kind == kind:
                i += 1
                while i < len(lines) and self._parse_end_marker(lines[i]) != kind:
                    i += 1
                if i < len(lines):
                    i += 1
                continue
            kept.append(lines[i])
            i += 1
        self._write_config_text("".join(kept).rstrip() + ("\n" if kept else ""))

    def _has_managed_block_kind(self, kind: str) -> bool:
        return any(
            self._parse_begin_marker(line)[0] == kind
            for line in self._config_text().splitlines()
        )

    def _unmanaged_table_exists(self, table_path: list[str]) -> bool:
        text = self._config_text()
        text_without_managed = self._remove_managed_blocks_from_text(text)
        if not text_without_managed.strip():
            return False
        try:
            data = tomllib.loads(text_without_managed)
        except tomllib.TOMLDecodeError as exc:
            raise CodexConfigError(
                f"Cannot parse TOML in {self._config_path()}: {exc}. "
                "Fix or remove the file, then re-run."
            ) from exc
        current: object = data
        for part in table_path:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    @classmethod
    def _remove_managed_block_from_text(cls, text: str, kind: str, name: str) -> str:
        lines = text.splitlines(keepends=True)
        kept: list[str] = []
        i = 0
        while i < len(lines):
            if cls._parse_begin_marker(lines[i]) == (kind, name):
                i += 1
                while i < len(lines) and cls._parse_end_marker(lines[i]) != kind:
                    i += 1
                if i < len(lines):
                    i += 1
                continue
            kept.append(lines[i])
            i += 1
        return "".join(kept).rstrip() + ("\n" if kept else "")

    @classmethod
    def _remove_managed_blocks_from_text(cls, text: str) -> str:
        lines = text.splitlines(keepends=True)
        kept: list[str] = []
        i = 0
        while i < len(lines):
            marker_kind, _marker_name = cls._parse_begin_marker(lines[i])
            if marker_kind is not None:
                i += 1
                while i < len(lines) and cls._parse_end_marker(lines[i]) is None:
                    i += 1
                if i < len(lines):
                    i += 1
                continue
            kept.append(lines[i])
            i += 1
        return "".join(kept)

    @staticmethod
    def _append_block_to_text(text: str, kind: str, name: str, block: str) -> str:
        text = text.rstrip()
        prefix = f"{text}\n\n" if text else ""
        return (
            f"{prefix}{_BEGIN_PREFIX}{kind} {name}\n"
            f"{block.rstrip()}\n"
            f"{_END_PREFIX}{kind} {name}\n"
        )

    @staticmethod
    def _parse_begin_marker(line: str) -> tuple[str | None, str | None]:
        stripped = line.strip()
        if not stripped.startswith(_BEGIN_PREFIX):
            return None, None
        rest = stripped[len(_BEGIN_PREFIX) :]
        kind, _, name = rest.partition(" ")
        return (kind or None), (name or None)

    @staticmethod
    def _parse_end_marker(line: str) -> str | None:
        stripped = line.strip()
        if not stripped.startswith(_END_PREFIX):
            return None
        rest = stripped[len(_END_PREFIX) :]
        kind, _, _name = rest.partition(" ")
        return kind or None

    def _write_config_text(self, text: str) -> None:
        self._write_text(self._config_path(), text)

    @classmethod
    def _render_toml_document(cls, data: dict[str, Any]) -> bytes:
        lines = cls._render_toml_sections([], data)
        return ("\n".join(lines) + "\n").encode()

    @classmethod
    def _render_toml_table(cls, table_path: list[str], data: dict[str, Any]) -> str:
        return "\n".join(cls._render_toml_sections(table_path, data)) + "\n"

    @classmethod
    def _render_toml_sections(
        cls, table_path: list[str], data: dict[str, Any]
    ) -> list[str]:
        lines: list[str] = []
        if table_path:
            rendered_path = ".".join(cls._toml_key(part) for part in table_path)
            lines.append(f"[{rendered_path}]")
        for key, value in data.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{cls._toml_key(str(key))} = {cls._toml_value(value)}")
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            nested_lines = cls._render_toml_sections([*table_path, str(key)], value)
            if lines and nested_lines:
                lines.append("")
            lines.extend(nested_lines)
        return lines

    @classmethod
    def _toml_value(cls, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, list):
            return "[" + ", ".join(cls._toml_value(v) for v in value) + "]"
        if isinstance(value, dict):
            inner = ", ".join(
                f"{cls._toml_key(str(k))} = {cls._toml_value(v)}"
                for k, v in value.items()
            )
            return "{ " + inner + " }"
        if value is None:
            raise CodexConfigError("TOML cannot represent null values")
        raise CodexConfigError(
            f"TOML cannot represent value of type {type(value).__name__}"
        )

    @staticmethod
    def _toml_key(key: str) -> str:
        if _BARE_TOML_KEY_RE.fullmatch(key):
            return key
        return json.dumps(key)

    # ------------------------------------------------------------------
    # JSON and filesystem helpers
    # ------------------------------------------------------------------

    def _hooks_path(self) -> Path:
        return self._codex_path / "hooks.json"

    @staticmethod
    def _ensure_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
        value = data.get(key)
        if not isinstance(value, dict):
            value = {}
            data[key] = value
        return value

    @staticmethod
    def _strip_hook_source(hooks: dict[str, Any], name: str) -> None:
        empty_event_types: list[str] = []
        for event_type, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            filtered = [
                entry
                for entry in entries
                if not (isinstance(entry, dict) and entry.get("_source") == name)
            ]
            if filtered:
                hooks[event_type] = filtered
            else:
                empty_event_types.append(event_type)
        for event_type in empty_event_types:
            del hooks[event_type]

    @staticmethod
    def _prune_empty_hooks(data: dict[str, Any]) -> None:
        hooks = data.get("hooks")
        if isinstance(hooks, dict) and not hooks:
            data.pop("hooks", None)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise CodexConfigError(
                f"Cannot parse JSON in {path}: {exc}. Fix or remove the file, "
                "then re-run."
            ) from exc
        if not isinstance(data, dict):
            raise CodexConfigError(f"Top level of {path} must be a JSON object")
        return data

    @classmethod
    def _save_json(cls, path: Path, data: dict[str, Any]) -> None:
        cls._write_text(path, json.dumps(data, indent=2) + "\n")

    @staticmethod
    def _remove_file(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    @classmethod
    def _write_bytes(cls, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            mode = stat.S_IMODE(os.stat(path).st_mode)
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
