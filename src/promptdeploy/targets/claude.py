"""Claude Code target implementation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..frontmatter import transform_for_target
from ..manifest import MANIFEST_FILENAME
from .base import Target

# Keys that are deployment metadata, not part of the Claude MCP server config.
_MCP_STRIP_KEYS = frozenset(
    {"name", "description", "scope", "enabled", "only", "except"}
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
        model: Optional[str] = None,
    ) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()
        self._model = model
        self._injected = {"model": model} if model else None
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
        dest = self._config_path / "skills" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Stage the full copy (including the SKILL.md transform) in a temp
        # directory next to the destination, then swap it into place so a
        # failure mid-copy never leaves a partially-written skill behind.
        tmp_dir = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".promptdeploy-"))
        try:
            staged = tmp_dir / "skill"
            # Resolve symlinks so the deployed copy is self-contained.
            shutil.copytree(source_dir.resolve(), staged, symlinks=False)
            # Transform the SKILL.md inside the staged copy.
            skill_md = staged / "SKILL.md"
            if skill_md.exists():
                self._write_bytes(
                    skill_md,
                    transform_for_target(skill_md.read_bytes(), inject=self._injected),
                )
            if dest.is_symlink():
                dest.unlink()
            elif dest.exists():
                shutil.rmtree(dest)
            os.replace(staged, dest)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: Optional[bytes] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        return item_type == "models"

    def content_fingerprint(self, item_type: str) -> Optional[str]:
        if self._injected is not None and item_type in ("agent", "skill"):
            return f"model={self._model}"
        return None

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
        settings = self._load_json(self._settings_path())

        if not config.get("enabled", True):
            settings.get("mcpServers", {}).pop(name, None)
        else:
            claude_config = {
                k: v for k, v in config.items() if k not in _MCP_STRIP_KEYS
            }
            settings.setdefault("mcpServers", {})[name] = claude_config

        self._save_json(self._settings_path(), settings)

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

    def remove_prompt(self, name: str, target_path: Optional[Path] = None) -> None:
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
        path = self._settings_path()
        if not path.exists():
            return
        settings = self._load_json(path)
        settings.get("mcpServers", {}).pop(name, None)
        self._save_json(path, settings)

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
            settings = self._load_json(self._settings_path())
            return name in settings.get("mcpServers", {})
        if item_type == "marketplace":
            settings = self._load_json(self._settings_path())
            if name in settings.get("extraKnownMarketplaces", {}):
                return True
            return any(
                key.rsplit("@", 1)[-1] == name
                for key in settings.get("enabledPlugins", {})
            )
        return False

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Optional[Path] = None,
    ) -> Optional[bytes]:
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

    def read_deployed_bytes(self, item_type: str, name: str) -> Optional[bytes]:
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

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

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
            try:
                os.unlink(tmp)
            except OSError:
                pass
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
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
