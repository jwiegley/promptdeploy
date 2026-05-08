"""gptel-prompts target implementation.

Deploys prompts into a directory consumed by ``gptel-prompts.el``. For
``.poet``/``.j2``/``.jinja`` sources, the file is rendered to ``{name}.json``
(an array of role/content turns); the existing JSON handler in
gptel-prompts.el reads this directly so no Jinja expansion is needed in
Emacs. For plain prompts (``.txt``/``.md``/``.org``/``.json``) the file
is copied verbatim.

This target only consumes prompts -- agents, commands, skills, MCP
servers, hooks, and models are silently skipped.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from ..manifest import MANIFEST_FILENAME
from ..poet import POET_EXTENSIONS, parse_poet, render_for_gptel
from .base import Target


class GptelTarget(Target):
    """Deploy prompts into a gptel-prompts directory."""

    @property
    def id(self) -> str:
        return self._id

    def exists(self) -> bool:
        return self._config_path.is_dir()

    def manifest_path(self) -> Path:
        return self._config_path / MANIFEST_FILENAME

    def rsync_includes(self) -> list[str] | None:
        return [
            "*.json",
            "*.txt",
            "*.md",
            "*.org",
            MANIFEST_FILENAME,
        ]

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: Optional[bytes] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        return item_type != "prompt"

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def __init__(self, target_id: str, config_path: Path) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()
        # Records what deploy_prompt actually wrote, keyed by name. The deploy
        # loop reads via deployed_artifact_path() and persists into the
        # manifest so remove_prompt can target exactly that file.
        self._last_deployed: dict[str, Path] = {}
        # Warnings collected during the most recent deploy_prompt call.
        # The deploy loop drains these via consume_warnings().
        self._warnings: list[tuple[str, list[str]]] = []

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        ext = source_path.suffix
        if ext in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            if doc.warnings:
                self._warnings.append((name, list(doc.warnings)))
            rendered = render_for_gptel(doc)
            dest = self._config_path / f"{name}.json"
        else:
            rendered = content
            dest = self._config_path / f"{name}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(rendered)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        # Track the file we just wrote so the manifest can record it.
        self._last_deployed[name] = dest.relative_to(self._config_path)

    def deployed_artifact_path(self, item_type: str, name: str) -> Optional[Path]:
        if item_type != "prompt":
            return None
        return self._last_deployed.get(name)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def deploy_agent(self, name: str, content: bytes) -> None:
        pass

    def deploy_command(self, name: str, content: bytes) -> None:
        pass

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        pass

    def deploy_mcp_server(self, name: str, config: dict) -> None:
        pass

    def deploy_models(self, config: dict) -> None:
        pass

    def deploy_hook(self, name: str, config: dict) -> None:
        pass

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_prompt(self, name: str, target_path: Optional[Path] = None) -> None:
        # When the manifest recorded the exact deployed path, unlink only
        # that file. This avoids destroying user-authored prompts that
        # happen to share the prompt's stem (e.g. an unrelated ``foo.md``).
        if target_path is not None:
            try:
                (self._config_path / target_path).unlink()
            except FileNotFoundError:
                pass
            return
        # Legacy fallback for manifests written before we tracked
        # ``target_path``: probe each known extension. This preserves
        # cleanup of older deployments at the cost of being less precise.
        for ext in (".json", ".txt", ".md", ".org"):
            try:
                (self._config_path / f"{name}{ext}").unlink()
            except FileNotFoundError:
                pass

    def remove_agent(self, name: str) -> None:
        pass

    def remove_command(self, name: str) -> None:
        pass

    def remove_skill(self, name: str) -> None:
        pass

    def remove_mcp_server(self, name: str) -> None:
        pass

    def remove_models(self) -> None:
        pass

    def remove_hook(self, name: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Pre-existing detection
    # ------------------------------------------------------------------

    def item_exists(self, item_type: str, name: str) -> bool:
        if item_type != "prompt":
            return False
        for ext in (".json", ".txt", ".md", ".org"):
            if (self._config_path / f"{name}{ext}").exists():
                return True
        return False
