"""Claude Code target implementation."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..frontmatter import transform_for_target
from ..manifest import MANIFEST_FILENAME
from .base import (
    ANVIL_MCP_NAMES,
    Target,
    install_skill_tree_atomically,
    transformed_skill_tree_matches,
)

# Keys that are deployment metadata, not part of the Claude MCP server config.
_MCP_STRIP_KEYS = frozenset(
    {
        "name",
        "description",
        "scope",
        "enabled",
        "only",
        "except",
        "claude",
        "codex",
        "opencode",
    }
)


class JsonConfigError(ValueError):
    """Raised when an existing target JSON config file cannot be parsed.

    Subclasses ``ValueError`` so callers that already handle config-shaped
    errors (e.g. the ``settings`` subcommands) catch it without changes.
    """


class ClaudeTarget(Target):
    """Deploy prompts and MCP servers into a Claude Code configuration directory."""

    def __init__(
        self,
        target_id: str,
        config_path: Path,
        *,
        model: str | None = None,
        manage_mcp: bool = True,
        expand_secrets: bool = True,
    ) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()
        self._model = model
        self._injected = {"model": model} if model else None
        # MCP servers deploy into .claude.json, which is machine-specific and
        # never rsynced; remote claude targets therefore cannot manage MCP.
        self._manage_mcp = manage_mcp
        # False only for --target-root previews: ${VAR} in MCP
        # env/headers/url is then written verbatim so secrets are never
        # expanded into the user-chosen preview directory.
        self._expand_secrets = expand_secrets
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
            "settings.json",
            MANIFEST_FILENAME,
        ]

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._config_path / "agents" / f"{name}.md"
        self._write_bytes(dest, transform_for_target(content, inject=self._injected))

    def deploy_command(self, name: str, content: bytes) -> None:
        dest = self._config_path / "commands" / f"{name}.md"
        self._write_bytes(dest, transform_for_target(content))

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
        self._write_bytes(dest, rendered)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        install_skill_tree_atomically(
            source_dir,
            self._config_path / "skills" / name,
            lambda contents: transform_for_target(contents, inject=self._injected),
        )

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if item_type == "mcp":
            return not self._manage_mcp
        return item_type == "models"

    def content_fingerprint(self, item_type: str) -> str | None:
        if self._injected is not None and item_type in ("agent", "skill"):
            return f"model={self._model}"
        if item_type == "mcp":
            # Bump when the deployed .claude.json entry format changes (e.g.
            # the URL-server "type" field or env expansion policy) so existing
            # deployments refresh even though the source YAML is unchanged.
            # v6: Codex and OpenCode overrides are stripped while Claude's
            # native per-server timeout remains in the rendered entry.
            return "claude-mcp-entry-v6"
        return None

    @property
    def mcp_hash_includes_env(self) -> bool:
        return True

    def deploy_models(self, config: dict[str, Any]) -> None:
        pass  # Claude Code does not support custom models

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        settings = self._load_json(self._settings_path())
        hooks_config = config.get("hooks", {})
        settings_hooks = self._ensure_dict(settings, "hooks")

        # Remove ALL existing entries from this hook group across every
        # event type -- not just the ones in the new config.  This handles
        # the case where a hook group previously contributed to an event
        # type that is no longer present in the updated YAML.  Non-dict
        # entries (hand-edits) cannot carry a _source tag and are kept.
        empty_event_types = []
        for event_type, entries in settings_hooks.items():
            if not isinstance(entries, list):
                continue
            filtered = [
                e
                for e in entries
                if not (isinstance(e, dict) and e.get("_source") == name)
            ]
            if filtered:
                settings_hooks[event_type] = filtered
            else:
                empty_event_types.append(event_type)
        for event_type in empty_event_types:
            del settings_hooks[event_type]

        # Add new entries with _source tag.  De-duplicate only against
        # entries this group may own: untagged entries (e.g. installed by
        # hand) and entries tagged with this group's own name.  Entries
        # owned by OTHER groups are never touched, even when their content
        # is identical -- otherwise deploying group B would steal group A's
        # entry and a later removal of B would delete a hook A still claims.
        for event_type, matchers in hooks_config.items():
            event_list = settings_hooks.get(event_type)
            if not isinstance(event_list, list):
                event_list = []
                settings_hooks[event_type] = event_list
            for entry in matchers:
                new_entry = dict(entry)
                new_entry["_source"] = name
                content = {k: v for k, v in new_entry.items() if k != "_source"}
                event_list[:] = [
                    e
                    for e in event_list
                    if not (
                        isinstance(e, dict)
                        and e.get("_source") in (None, name)
                        and {k: v for k, v in e.items() if k != "_source"} == content
                    )
                ]
                event_list.append(new_entry)

        if not settings_hooks:
            settings.pop("hooks", None)

        self._save_json(self._settings_path(), settings)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        # MCP servers deploy into .claude.json (user scope) -- the surface
        # Claude Code actually reads. It never reads settings.json's
        # mcpServers. ${VAR} references in env/headers/url are expanded at
        # deploy time so `claude /doctor` does not depend on launcher
        # environment -- except under a --target-root preview
        # (expand_secrets=False), which writes ${VAR} verbatim so secrets
        # never land in the preview directory. Only the named server key is
        # touched; every other key in the app-owned file is preserved.
        path = self._claude_json_path()
        data = self._load_json(path)

        if not config.get("enabled", True):
            servers = data.get("mcpServers")
            if isinstance(servers, dict):
                servers.pop(name, None)
        else:
            self._ensure_dict(data, "mcpServers")[name] = self._claude_mcp_entry(
                config, name=name, expand_secrets=self._expand_secrets
            )

        self._save_json(path, data)

    @staticmethod
    def _claude_mcp_entry(
        config: dict[str, Any],
        *,
        name: str | None = None,
        expand_secrets: bool = True,
    ) -> dict[str, Any]:
        """Build the ``.claude.json`` ``mcpServers`` entry for a server.

        Strips deployment metadata (:data:`_MCP_STRIP_KEYS`). With
        ``expand_secrets`` (the local deploy path), ``${VAR}`` references in
        ``env``/``headers`` values and in a string ``url`` are strict-expanded
        -- a URL can carry a secret in a query parameter -- so the deployed
        config never depends on the launcher environment. Baking is a
        deliberate policy decision even though Claude Code runtime-expands
        ``${VAR}``/``${VAR:-default}`` in these fields of ``.claude.json``
        itself: at runtime an unset reference is left as literal text (a
        broken server when ``claude`` launches without the repo ``.env``),
        whereas the deploy-time bake fails loudly (``EnvVarError``) and
        matches the remote path, which passes ``expand_secrets=False`` and
        applies its own expansion (see
        ``RemoteTarget._expand_entry_secrets``). ``--target-root`` previews
        also pass ``expand_secrets=False`` (via the constructor flag) but
        apply no expansion at all: secrets must never be baked into the
        user-chosen preview directory. URL-transport servers get an
        explicit ``type`` (default ``http``) because Claude Code treats a
        ``type``-less entry as stdio and rejects it for the missing
        ``command`` ("command: expected string, received undefined").
        An explicit ``type`` in the source (e.g. ``sse``) is preserved.
        """
        from ..envsubst import expand_env_vars_strict

        config = ClaudeTarget._apply_claude_mcp_overrides(config)
        entry = {k: v for k, v in config.items() if k not in _MCP_STRIP_KEYS}
        if expand_secrets:
            for field in ("env", "headers"):
                value = entry.get(field)
                if isinstance(value, dict):
                    entry[field] = {
                        k: (
                            expand_env_vars_strict(
                                v,
                                context=(
                                    f"mcp.{name}.{field}.{k}"
                                    if name
                                    else f"mcp.{field}.{k}"
                                ),
                            )
                            if isinstance(v, str)
                            else v
                        )
                        for k, v in value.items()
                    }
            # Deliberate policy: Claude Code would runtime-expand ${VAR} in
            # url too, but an unset variable survives to runtime as literal
            # text (a broken server when `claude` launches without .env), so
            # bake at deploy time like env/headers above and the remote path.
            url = entry.get("url")
            if isinstance(url, str):
                entry["url"] = expand_env_vars_strict(
                    url, context=f"mcp.{name}.url" if name else "mcp.url"
                )
        if "url" in entry and "type" not in entry:
            entry["type"] = "http"
        return entry

    @staticmethod
    def _apply_claude_mcp_overrides(config: dict[str, Any]) -> dict[str, Any]:
        override = config.get("claude")
        if not isinstance(override, dict):
            return config
        merged = {k: v for k, v in config.items() if k != "claude"}
        merged.update(override)
        return merged

    @staticmethod
    def _strip_marketplace(settings: dict[str, Any], name: str) -> None:
        """Remove a marketplace's ownership from a settings dict in place.

        Pops ``extraKnownMarketplaces[name]`` and every ``enabledPlugins`` key
        whose marketplace part equals ``name``. Marketplace ownership is
        self-tagged in the ``<plugin>@<marketplace>`` key, so match the part
        after the final ``@`` exactly -- a marketplace named ``official`` must
        not claim a plugin keyed ``x@plugins-official``.
        """
        markets = settings.get("extraKnownMarketplaces")
        if isinstance(markets, dict):
            markets.pop(name, None)
        plugins = settings.get("enabledPlugins")
        if isinstance(plugins, dict):
            for key in [k for k in plugins if k.rsplit("@", 1)[-1] == name]:
                del plugins[key]
        if isinstance(markets, dict) and not markets:
            settings.pop("extraKnownMarketplaces", None)
        if isinstance(plugins, dict) and not plugins:
            settings.pop("enabledPlugins", None)

    @staticmethod
    def _ensure_dict(settings: dict[str, Any], key: str) -> dict[str, Any]:
        """Return ``settings[key]`` as a dict, replacing a non-dict value.

        ``dict.setdefault`` leaves an existing non-dict value (e.g. a string
        written by a hand-edit or a TUI) untouched, so a subsequent item
        assignment would raise. Coerce such values to a fresh dict instead.
        """
        value = settings.get(key)
        if not isinstance(value, dict):
            value = {}
            settings[key] = value
        return value

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        path = self._settings_path()
        settings = self._load_json(path)
        self._strip_marketplace(settings, name)
        if config.get("enabled", True):
            source = config.get("source")
            if isinstance(source, dict):
                entry: dict[str, Any] = {"source": dict(source)}
                if "autoUpdate" in config:
                    entry["autoUpdate"] = bool(config["autoUpdate"])
                self._ensure_dict(settings, "extraKnownMarketplaces")[name] = entry
            plugins = config.get("plugins")
            if isinstance(plugins, dict):
                for plugin, val in plugins.items():
                    self._ensure_dict(settings, "enabledPlugins")[
                        f"{plugin}@{name}"
                    ] = bool(val)
        self._save_json(path, settings)

    def remove_marketplace(self, name: str) -> None:
        path = self._settings_path()
        if not path.exists():
            return
        settings = self._load_json(path)
        self._strip_marketplace(settings, name)
        self._save_json(path, settings)

    def deploy_settings(
        self, rendered: dict[str, Any], previous_keys: list[str]
    ) -> None:
        path = self._settings_path()
        settings = self._load_json(path)
        for key in previous_keys:
            if key not in rendered:
                settings.pop(key, None)
        for key, value in rendered.items():
            settings[key] = value
        self._save_json(path, settings)

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_agent(self, name: str) -> None:
        self._remove_file(self._config_path / "agents" / f"{name}.md")

    def remove_command(self, name: str) -> None:
        self._remove_file(self._config_path / "commands" / f"{name}.md")

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        # Claude prompts always land at ``commands/{name}.md``; the manifest
        # ``target_path`` is informational and ignored here.
        self._remove_file(self._config_path / "commands" / f"{name}.md")

    def remove_skill(self, name: str) -> None:
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    def remove_models(self) -> None:
        pass  # Claude Code does not support custom models

    def remove_hook(self, name: str) -> None:
        path = self._settings_path()
        if not path.exists():
            return
        settings = self._load_json(path)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return
        empty_event_types = []
        for event_type, entries in hooks.items():
            filtered = [e for e in entries if e.get("_source") != name]
            if filtered:
                hooks[event_type] = filtered
            else:
                empty_event_types.append(event_type)
        for event_type in empty_event_types:
            del hooks[event_type]
        if not hooks:
            del settings["hooks"]
        self._save_json(path, settings)

    def remove_mcp_server(self, name: str) -> None:
        path = self._claude_json_path()
        if not path.exists():
            return
        data = self._load_json(path)
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            servers.pop(name, None)
        self._save_json(path, data)

    def remove_settings(self, previous_keys: list[str]) -> None:
        path = self._settings_path()
        if not path.exists():
            return
        settings = self._load_json(path)
        for key in previous_keys:
            settings.pop(key, None)
        self._save_json(path, settings)

    def read_settings_json(self) -> dict[str, Any]:
        return self._load_json(self._settings_path())

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
        if item_type == "hook":
            settings = self._load_json(self._settings_path())
            hooks = settings.get("hooks")
            if not isinstance(hooks, dict):
                return False
            return any(
                e.get("_source") == name for entries in hooks.values() for e in entries
            )
        if item_type == "mcp":
            data = self._load_json(self._claude_json_path())
            servers = data.get("mcpServers")
            return isinstance(servers, dict) and name in servers
        if item_type == "marketplace":
            settings = self._load_json(self._settings_path())
            if name in settings.get("extraKnownMarketplaces", {}):
                return True
            return any(
                key.rsplit("@", 1)[-1] == name
                for key in settings.get("enabledPlugins", {})
            )
        return False

    def item_matches_source(
        self,
        item_type: str,
        name: str,
        content: bytes,
        metadata: dict[str, Any] | None,
        source_path: Path | None = None,
    ) -> bool | None:
        if item_type == "skill":
            if source_path is None:
                return None
            return transformed_skill_tree_matches(
                source_path.parent,
                self._config_path / "skills" / name,
                lambda contents: transform_for_target(contents, inject=self._injected),
            )
        if item_type != "mcp" or name not in ANVIL_MCP_NAMES or metadata is None:
            return None

        missing = object()
        data = self._load_json(self._claude_json_path())
        servers = data.get("mcpServers")
        actual = servers.get(name, missing) if isinstance(servers, dict) else missing
        if not metadata.get("enabled", True):
            return actual is missing
        expected = self._claude_mcp_entry(metadata, name=name)
        return actual == expected

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        if item_type == "agent":
            return transform_for_target(content, inject=self._injected)
        if item_type == "command":
            return transform_for_target(content)
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

    def _settings_path(self) -> Path:
        return self._config_path / "settings.json"

    def _claude_json_path(self) -> Path:
        return self._config_path / ".claude.json"

    @staticmethod
    def _remove_file(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    @staticmethod
    def _write_bytes(path: Path, data: bytes) -> None:
        """Atomically write ``data`` to ``path`` via temp file + os.replace."""
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
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data: dict[str, Any] = json.loads(path.read_text("utf-8"))
            return data
        except json.JSONDecodeError as exc:
            raise JsonConfigError(
                f"Cannot parse JSON in {path}: {exc}. "
                f"Fix or remove the file, then re-run."
            ) from exc

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
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
