"""OpenCode target implementation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ..frontmatter import transform_for_target
from ..manifest import MANIFEST_FILENAME
from .base import Target

# Keys that are deployment metadata, not part of the MCP server config.
_MCP_STRIP_KEYS = frozenset(
    {"name", "description", "scope", "enabled", "only", "except"}
)


class OpenCodeTarget(Target):
    """Deploy prompts and MCP servers into an OpenCode configuration directory."""

    def __init__(self, target_id: str, config_path: Path) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()

    @property
    def id(self) -> str:
        return self._id

    def exists(self) -> bool:
        return self._config_path.is_dir()

    def manifest_path(self) -> Path:
        return self._config_path / MANIFEST_FILENAME

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._config_path / "agents" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(transform_for_target(content, self._id))

    def deploy_command(self, name: str, content: bytes) -> None:
        dest = self._config_path / "commands" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(transform_for_target(content, self._id))

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        dest = self._config_path / "skills" / name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir.resolve(), dest, symlinks=False)
        skill_md = dest / "SKILL.md"
        if skill_md.exists():
            skill_md.write_bytes(transform_for_target(skill_md.read_bytes(), self._id))

    def deploy_hook(self, name: str, config: dict) -> None:
        pass

    def remove_hook(self, name: str) -> None:
        pass

    def deploy_models(self, config: dict) -> None:
        from ..envsubst import expand_env_vars

        oc_path = self._opencode_path()
        data = self._load_json(oc_path)

        providers: dict = {}

        for prov_key, prov in config.get("providers", {}).items():
            oc_cfg = prov.get("opencode", {})
            if not oc_cfg:
                continue

            api_key = expand_env_vars(prov.get("api_key", ""))
            base_url = prov.get("base_url", "")

            provider_entry: dict = {
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

            models_dict: dict = {}
            for model_id, model in prov.get("models", {}).items():
                if model is None:
                    model = {}
                model_entry: dict = {
                    "name": model.get("display_name", model_id),
                }
                # Add limits if specified
                context_limit = model.get("context_limit")
                output_limit = model.get("output_limit")
                if context_limit is not None or output_limit is not None:
                    limit: dict = {}
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

    def deploy_mcp_server(self, name: str, config: dict) -> None:
        oc_path = self._opencode_path()
        data = self._load_json(oc_path)

        # Disabled servers are not written at all.
        if not config.get("enabled", True):
            data.get("mcp", {}).pop(name, None)
        else:
            oc_config: dict = {}
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
            # "environment" key, not "env".
            env = config.get("env")
            if env:
                oc_config["environment"] = dict(env)
            # Copy any remaining non-metadata, non-handled keys.
            for k, v in config.items():
                if k not in _MCP_STRIP_KEYS and k not in (
                    "command",
                    "args",
                    "env",
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
        if item_type == "command":
            return (self._config_path / "commands" / f"{name}.md").exists()
        if item_type == "skill":
            dest = self._config_path / "skills" / name
            return dest.exists() or dest.is_symlink()
        return False

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
    def _load_json(path: Path) -> dict:
        if not path.exists():
            return {}
        return json.loads(path.read_text("utf-8"))

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
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
