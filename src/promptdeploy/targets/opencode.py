"""OpenCode target implementation."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..frontmatter import (
    parse_frontmatter,
    serialize_frontmatter,
    strip_deployment_fields,
)
from ..manifest import MANIFEST_FILENAME
from .base import Target

# Keys that are deployment metadata, not part of the MCP server config.
_MCP_STRIP_KEYS = frozenset(
    {"name", "description", "scope", "enabled", "only", "except"}
)

# Frontmatter fields that use Claude Code format and are incompatible
# with OpenCode's schema.  These are stripped after being converted
# (where possible) to OpenCode equivalents.
_CLAUDE_ONLY_FIELDS = frozenset({"model"})

# Known OpenCode built-in tool names (lowercase).
_OPENCODE_TOOL_NAMES = frozenset(
    {
        "bash",
        "edit",
        "glob",
        "grep",
        "list",
        "lsp",
        "patch",
        "read",
        "skill",
        "todoread",
        "todowrite",
        "webfetch",
        "websearch",
        "write",
        "task",
        "question",
    }
)


# Claude Code names MCP tools ``mcp__<server>__<tool>``; OpenCode flattens
# the same tool to ``<server>_<tool>``.
_MCP_TOOL_RE = re.compile(r"^mcp__([^_].*?)__(.+)$")


def _convert_claude_tools(
    tools_value: object, warnings: list[str] | None = None
) -> dict[str, bool]:
    """Convert a Claude Code ``tools`` value to an OpenCode tools object.

    Accepts either a comma-separated string or a YAML list of strings.
    Claude Code format examples::

        "Read, Grep, Glob, Bash(grep:*), Bash(wc:*)"
        ["mcp__perplexity__perplexity_search_web", "WebFetch"]

    OpenCode expects a mapping of tool names to booleans::

        {"read": true, "bash": true, "edit": false, ...}

    A Claude ``tools`` field is an *allowlist*: only the listed tools are
    available.  OpenCode instead enables every tool unless it is mapped to
    ``false``, so each listed tool is emitted as ``true`` and every OpenCode
    built-in NOT listed is emitted as ``false`` -- otherwise a restricted
    agent would silently gain the full toolset on OpenCode.

    Built-in tool names are lowercased and de-duplicated; ``Bash(...)``
    variants collapse to a single ``bash: true`` entry (the argument
    restriction cannot be expressed, which is recorded in ``warnings``).
    MCP tools ``mcp__<server>__<tool>`` translate to OpenCode's flattened
    ``<server>_<tool>`` name.  Anything else cannot be translated: it is
    dropped with a warning.  Returns an empty dict for non-string/non-list
    inputs and for inputs with no tokens at all.
    """
    if isinstance(tools_value, str):
        tokens: list[str] = re.split(r"\s*,\s*", tools_value.strip())
    elif isinstance(tools_value, list):
        tokens = [str(t).strip() for t in tools_value]
    else:
        return {}
    tokens = [t for t in tokens if t]
    if not tokens:
        return {}

    result: dict[str, bool] = {}
    for token in tokens:
        mcp_match = _MCP_TOOL_RE.match(token)
        if mcp_match is not None:
            server, tool = mcp_match.groups()
            result[f"{server}_{tool}"] = True
            continue
        # Strip Bash(...) qualifiers -> "bash"
        base = re.sub(r"\(.*\)$", "", token).strip().lower()
        if base != token.lower() and warnings is not None:
            warnings.append(
                f"tools entry '{token}': argument restriction cannot be "
                f"expressed on OpenCode; widened to '{base}'"
            )
        if base in _OPENCODE_TOOL_NAMES:
            result[base] = True
        elif warnings is not None:
            warnings.append(
                f"tools entry '{token}' has no OpenCode equivalent; "
                f"dropped from the allowlist"
            )
    # Preserve allowlist semantics: disable every built-in that was not
    # explicitly listed (sorted for deterministic output).
    for builtin in sorted(_OPENCODE_TOOL_NAMES):
        result.setdefault(builtin, False)
    return result


def _transform_for_opencode(content: bytes, warnings: list[str] | None = None) -> bytes:
    """Transform frontmatter for OpenCode targets.

    In addition to stripping deployment fields (``only``/``except``), this:

    * Converts a string- or list-valued ``tools`` field (a Claude Code
      allowlist) to an OpenCode-compatible object with boolean values --
      listed tools become ``true``, unlisted built-ins ``false``, and MCP
      tools are translated to OpenCode's ``<server>_<tool>`` names.  When
      nothing converts (e.g. an empty list), the field is removed so
      OpenCode's schema validator does not reject it.  Restrictions that
      cannot be translated are reported via ``warnings``.
    * Removes ``model`` (Claude Code uses short aliases like ``sonnet``
      which are not valid OpenCode model identifiers).
    """
    metadata, body = parse_frontmatter(content)
    if metadata is None:
        return content

    cleaned = strip_deployment_fields(metadata)

    # Convert tools: string|list -> tools: {name: bool, ...}
    tools_val = cleaned.get("tools")
    if isinstance(tools_val, (str, list)):
        converted = _convert_claude_tools(tools_val, warnings)
        if converted:
            cleaned["tools"] = converted
        else:
            # No recognisable tools; remove the field entirely.
            del cleaned["tools"]

    # Strip Claude-only fields that have no OpenCode equivalent.
    for field in _CLAUDE_ONLY_FIELDS:
        cleaned.pop(field, None)

    return serialize_frontmatter(cleaned, body)


class OpenCodeTarget(Target):
    """Deploy prompts and MCP servers into an OpenCode configuration directory."""

    def __init__(self, target_id: str, config_path: Path) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()
        # Warnings collected during the most recent deploy_prompt calls;
        # drained via consume_warnings() by the deploy loop.
        self._warnings: list[tuple[str, list[str]]] = []

    @property
    def id(self) -> str:
        return self._id

    def exists(self) -> bool:
        return self._config_path.is_dir()

    def manifest_path(self) -> Path:
        return self._config_path / MANIFEST_FILENAME

    def rsync_includes(self) -> list[str] | None:
        return [
            "agents/",
            "agents/**",
            "commands/",
            "commands/**",
            "skills/",
            "skills/**",
            "opencode.json",
            MANIFEST_FILENAME,
        ]

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return item_type in ("hook", "settings", "marketplace")

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._config_path / "agents" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._transform_collecting_warnings(name, content))

    def deploy_command(self, name: str, content: bytes) -> None:
        dest = self._config_path / "commands" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._transform_collecting_warnings(name, content))

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        from ..poet import POET_EXTENSIONS, parse_plain, parse_poet, render_for_command

        if source_path.suffix in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            if doc.warnings:
                self._warnings.append((name, list(doc.warnings)))
        else:
            doc = parse_plain(content)
        rendered = render_for_command(doc)
        dest = self._config_path / "commands" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(rendered)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def _transform_collecting_warnings(self, name: str, content: bytes) -> bytes:
        """Run the OpenCode transform, queueing any warnings under ``name``."""
        warnings: list[str] = []
        transformed = _transform_for_opencode(content, warnings)
        if warnings:
            self._warnings.append((name, warnings))
        return transformed

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir.resolve(), dest, symlinks=False)
        skill_md = dest / "SKILL.md"
        if skill_md.exists():
            skill_md.write_bytes(
                self._transform_collecting_warnings(name, skill_md.read_bytes())
            )

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        pass

    def remove_hook(self, name: str) -> None:
        pass

    def deploy_models(self, config: dict[str, Any]) -> None:
        from ..envsubst import expand_env_vars_strict

        oc_path = self._opencode_path()
        data = self._load_json(oc_path)

        providers: dict[str, Any] = {}

        for prov_key, prov in config.get("providers", {}).items():
            oc_cfg = prov.get("opencode", {})
            if not oc_cfg:
                continue

            api_key = expand_env_vars_strict(
                prov.get("api_key", ""),
                context=f"models.providers.{prov_key}.api_key",
            )
            base_url = prov.get("base_url", "")

            provider_entry: dict[str, Any] = {
                "npm": oc_cfg.get("npm", "@ai-sdk/openai-compatible"),
                "name": oc_cfg.get("name", prov.get("display_name", prov_key)),
                "options": {
                    "baseURL": base_url,
                    "apiKey": api_key,
                },
            }
            # Add timeout option if specified
            timeout = oc_cfg.get("timeout")
            if timeout is not None:
                provider_entry["options"]["timeout"] = timeout

            models_dict: dict[str, Any] = {}
            for model_id, model in prov.get("models", {}).items():
                if model is None:
                    model = {}
                model_entry: dict[str, Any] = {
                    "name": model.get("display_name", model_id),
                }
                # Add limits if specified
                context_limit = model.get("context_limit")
                output_limit = model.get("output_limit")
                if context_limit is not None or output_limit is not None:
                    limit: dict[str, Any] = {}
                    if context_limit is not None:
                        limit["context"] = context_limit
                    if output_limit is not None:
                        limit["output"] = output_limit
                    model_entry["limit"] = limit
                models_dict[model_id] = model_entry

            if models_dict:
                provider_entry["models"] = models_dict
                providers[prov_key] = provider_entry

        data["provider"] = providers
        self._save_json(oc_path, data)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        from ..envsubst import expand_env_vars_strict

        oc_path = self._opencode_path()
        data = self._load_json(oc_path)

        # Disabled servers are not written at all.
        if not config.get("enabled", True):
            data.get("mcp", {}).pop(name, None)
        else:
            oc_config: dict[str, Any] = {}
            # Determine type based on presence of url vs command.
            cmd = config.get("command")
            args = config.get("args", [])
            if "url" in config:
                oc_config["type"] = "remote"
            elif cmd is not None:
                oc_config["type"] = "local"
            # command is an array: command + args combined.
            if cmd is not None:
                oc_config["command"] = [cmd] + list(args)
            # "environment" key, not "env".  Expand ${VAR} at deploy time
            # since OpenCode runs from a directory that won't have these
            # vars set; an unresolved reference raises EnvVarError.
            env = config.get("env")
            if env:
                oc_config["environment"] = {
                    k: (
                        expand_env_vars_strict(v, context=f"mcp.{name}.env.{k}")
                        if isinstance(v, str)
                        else v
                    )
                    for k, v in env.items()
                }
            # HTTP headers get the same strict deploy-time expansion as
            # env values, and for the same reason: OpenCode will not
            # expand ${VAR} at runtime.
            headers = config.get("headers")
            if headers:
                oc_config["headers"] = {
                    k: (
                        expand_env_vars_strict(v, context=f"mcp.{name}.headers.{k}")
                        if isinstance(v, str)
                        else v
                    )
                    for k, v in headers.items()
                }
            # Copy any remaining non-metadata, non-handled keys.
            for k, v in config.items():
                if k not in _MCP_STRIP_KEYS and k not in (
                    "command",
                    "args",
                    "env",
                    "headers",
                ):
                    oc_config[k] = v
            data.setdefault("mcp", {})[name] = oc_config

        self._save_json(oc_path, data)

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_agent(self, name: str) -> None:
        self._remove_file(self._config_path / "agents" / f"{name}.md")

    def remove_command(self, name: str) -> None:
        self._remove_file(self._config_path / "commands" / f"{name}.md")

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        # OpenCode prompts always land at ``commands/{name}.md``; the manifest
        # ``target_path`` is informational and ignored here.
        self._remove_file(self._config_path / "commands" / f"{name}.md")

    def remove_skill(self, name: str) -> None:
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    def remove_models(self) -> None:
        path = self._opencode_path()
        if not path.exists():
            return
        data = self._load_json(path)
        data.pop("provider", None)
        self._save_json(path, data)

    def remove_mcp_server(self, name: str) -> None:
        path = self._opencode_path()
        if not path.exists():
            return
        data = self._load_json(path)
        data.get("mcp", {}).pop(name, None)
        self._save_json(path, data)

    # ------------------------------------------------------------------
    # Pre-existing detection
    # ------------------------------------------------------------------

    def item_exists(self, item_type: str, name: str) -> bool:
        if item_type == "agent":
            return (self._config_path / "agents" / f"{name}.md").exists()
        if item_type in ("command", "prompt"):
            return (self._config_path / "commands" / f"{name}.md").exists()
        if item_type == "skill":
            dest = self._config_path / "skills" / name
            return dest.exists() or dest.is_symlink()
        if item_type == "mcp":
            data = self._load_json(self._opencode_path())
            return name in data.get("mcp", {})
        if item_type == "models":
            data = self._load_json(self._opencode_path())
            return bool(data.get("provider"))
        return False

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        if item_type in ("agent", "command"):
            return _transform_for_opencode(content)
        if item_type == "prompt":
            from ..poet import (
                POET_EXTENSIONS,
                parse_plain,
                parse_poet,
                render_for_command,
            )

            if source_path is not None and source_path.suffix in POET_EXTENSIONS:
                doc = parse_poet(content, source_path=source_path)
            else:
                doc = parse_plain(content)
            return render_for_command(doc)
        return None

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
        if item_type == "agent":
            path = self._config_path / "agents" / f"{name}.md"
        elif item_type in ("command", "prompt"):
            path = self._config_path / "commands" / f"{name}.md"
        else:
            return None
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _opencode_path(self) -> Path:
        return self._config_path / "opencode.json"

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        data: dict[str, Any] = json.loads(path.read_text("utf-8"))
        return data

    @staticmethod
    def _save_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
