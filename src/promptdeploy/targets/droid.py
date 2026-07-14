"""Droid target implementation."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..frontmatter import (
    parse_frontmatter,
    serialize_frontmatter,
    strip_deployment_fields,
    transform_for_target,
)
from ..manifest import MANIFEST_FILENAME
from .base import (
    ANVIL_MCP_NAMES,
    Target,
    install_skill_tree_atomically,
    transformed_skill_tree_matches,
)

# Keys that are deployment metadata, not part of the MCP server config.
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


class DroidTarget(Target):
    """Deploy prompts and MCP servers into a Droid configuration directory."""

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
            "droids/",
            "droids/**",
            "skills/",
            "skills/**",
            "settings.json",
            "mcp.json",
            MANIFEST_FILENAME,
        ]

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if item_type == "settings":
            return True
        if item_type == "hook":
            return True
        if item_type == "marketplace":
            return True
        if item_type == "command":
            if content is not None:
                fm, _ = parse_frontmatter(content)
                if fm and fm.get("droid_deploy") == "skill":
                    return False
            return True
        return False

    def content_fingerprint(self, item_type: str) -> str | None:
        if item_type == "mcp":
            # v2: Claude, Codex, and OpenCode override metadata are stripped.
            return "droid-mcp-v2"
        return None

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._config_path / "droids" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(transform_for_target(content))

    def deploy_command(self, name: str, content: bytes) -> None:
        # Commands are skipped by default on Droid. However, if
        # frontmatter contains droid_deploy: 'skill', wrap as a skill.
        metadata, body = parse_frontmatter(content)
        if metadata and metadata.get("droid_deploy") == "skill":
            dest = self._config_path / "skills" / name
            # Never write through a pre-existing symlink: replace it so the
            # deploy cannot modify an unrelated directory it points at.
            if dest.is_symlink():
                dest.unlink()
            dest.mkdir(parents=True, exist_ok=True)
            # droid_deploy is deployment metadata, not part of the skill.
            cleaned = strip_deployment_fields(metadata)
            cleaned.pop("droid_deploy", None)
            skill_md = dest / "SKILL.md"
            skill_md.write_bytes(serialize_frontmatter(cleaned, body))
            return
        # Otherwise: skip silently.

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        from ..poet import POET_EXTENSIONS, parse_plain, parse_poet, render_for_command

        if source_path.suffix in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            if doc.warnings:
                self._warnings.append((name, list(doc.warnings)))
        else:
            doc = parse_plain(content)
        rendered = render_for_command(doc)
        dest = self._config_path / "skills" / name
        # Never write through a pre-existing symlink: replace it so the
        # deploy cannot modify an unrelated directory it points at.
        if dest.is_symlink():
            dest.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        skill_md = dest / "SKILL.md"
        skill_md.write_bytes(rendered)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        install_skill_tree_atomically(
            source_dir,
            self._config_path / "skills" / name,
            transform_for_target,
        )

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        pass

    def remove_hook(self, name: str) -> None:
        pass

    def deploy_models(self, config: dict[str, Any]) -> None:
        from ..envsubst import expand_env_vars

        settings_path = self._config_path / "settings.json"
        data = self._load_json(settings_path)

        custom_models = []
        index = 0

        for prov_key, prov in config.get("providers", {}).items():
            display_prefix = prov.get("display_name", prov_key)
            base_url = prov.get("base_url", "")
            api_key = expand_env_vars(prov.get("api_key", ""))
            droid_cfg = prov.get("droid", {})
            provider_type = droid_cfg.get(
                "provider_type", "generic-chat-completion-api"
            )
            no_image_support = droid_cfg.get("no_image_support", False)
            extra_args = droid_cfg.get("extra_args")
            extra_headers = droid_cfg.get("extra_headers")

            for model_id, model in prov.get("models", {}).items():
                if model is None:
                    model = {}
                display_name = (
                    f"[{display_prefix}] {model.get('display_name', model_id)}"
                )
                slug = display_name.replace(" ", "-")
                entry: dict[str, Any] = {
                    "apiKey": api_key,
                    "baseUrl": base_url,
                    "displayName": display_name,
                    "id": f"custom:{slug}-{index}",
                    "index": index,
                    "model": model_id,
                    "noImageSupport": no_image_support,
                    "provider": provider_type,
                }
                max_output = model.get("max_output_tokens")
                if max_output is not None:
                    entry["maxOutputTokens"] = max_output
                if extra_args is not None:
                    entry["extraArgs"] = dict(extra_args)
                if extra_headers is not None:
                    entry["extraHeaders"] = dict(extra_headers)

                custom_models.append(entry)
                index += 1

        data["customModels"] = custom_models
        self._save_json(settings_path, data)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        mcp_path = self._mcp_path()
        data = self._load_json(mcp_path)

        if not config.get("enabled", True):
            data.get("mcpServers", {}).pop(name, None)
        else:
            data.setdefault("mcpServers", {})[name] = self._droid_mcp_entry(config)

        self._save_json(mcp_path, data)

    @staticmethod
    def _droid_mcp_entry(config: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {}
        # Determine type based on presence of url vs command.
        if "url" in config:
            entry["type"] = "http"
        else:
            entry["type"] = "stdio"
        # Copy non-metadata keys.
        for key, value in config.items():
            if key not in _MCP_STRIP_KEYS and key != "url":
                entry[key] = value
            elif key == "url":
                # Verbatim on purpose: Droid itself expands
                # ${VAR}/${VAR:-default} in url (and env/headers) at
                # load time; an unset variable leaves the placeholder in
                # place with a warning at server start.
                entry["url"] = value
        # Disabled servers never reach this renderer: ``enabled: false``
        # removes the entry from mcp.json entirely.
        entry["disabled"] = False
        return entry

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_agent(self, name: str) -> None:
        self._remove_file(self._config_path / "droids" / f"{name}.md")

    def remove_command(self, name: str) -> None:
        # Commands could have been deployed as skills.
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    def remove_skill(self, name: str) -> None:
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        # Droid prompts deploy as a ``skills/{name}/`` directory; ``target_path``
        # is informational and not needed here.
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    def remove_models(self) -> None:
        settings_path = self._config_path / "settings.json"
        if not settings_path.exists():
            return
        data = self._load_json(settings_path)
        data.pop("customModels", None)
        self._save_json(settings_path, data)

    def remove_mcp_server(self, name: str) -> None:
        path = self._mcp_path()
        if not path.exists():
            return
        data = self._load_json(path)
        data.get("mcpServers", {}).pop(name, None)
        self._save_json(path, data)

    # ------------------------------------------------------------------
    # Pre-existing detection
    # ------------------------------------------------------------------

    def item_exists(self, item_type: str, name: str) -> bool:
        if item_type == "agent":
            return (self._config_path / "droids" / f"{name}.md").exists()
        if item_type in ("command", "skill", "prompt"):
            dest = self._config_path / "skills" / name
            return dest.exists() or dest.is_symlink()
        if item_type == "mcp":
            data = self._load_json(self._mcp_path())
            return name in data.get("mcpServers", {})
        if item_type == "models":
            data = self._load_json(self._config_path / "settings.json")
            return bool(data.get("customModels"))
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
                transform_for_target,
            )
        if item_type != "mcp" or name not in ANVIL_MCP_NAMES or metadata is None:
            return None

        missing = object()
        data = self._load_json(self._mcp_path())
        servers = data.get("mcpServers")
        actual = servers.get(name, missing) if isinstance(servers, dict) else missing
        if not metadata.get("enabled", True):
            return actual is missing
        expected = self._droid_mcp_entry(metadata)
        return actual == expected

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        # Droid writes a single ``.md`` file only for agents -- commands are
        # either skipped entirely or wrapped as a ``skills/{name}/SKILL.md``
        # directory artifact, and prompts also become skill directories.
        # Both directory artifacts fall outside the single-file adoption path.
        if item_type == "agent":
            return transform_for_target(content)
        return None

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
        if item_type != "agent":
            return None
        path = self._config_path / "droids" / f"{name}.md"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mcp_path(self) -> Path:
        return self._config_path / "mcp.json"

    @staticmethod
    def _remove_file(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

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
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
