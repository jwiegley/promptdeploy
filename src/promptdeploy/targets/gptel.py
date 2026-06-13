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

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

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
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
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
        # Maps prompt name -> artifact filename derived from the source
        # extension by the most recent would_deploy_bytes() call.
        # read_deployed_bytes() prefers this over probing extensions so a
        # user-authored stem-sibling (e.g. a foo.json next to a deployed
        # foo.md) cannot shadow the artifact deploy owns.
        self._expected_artifact: dict[str, str] = {}
        # Warnings collected during the most recent deploy_prompt call.
        # The deploy loop drains these via consume_warnings().
        self._warnings: list[tuple[str, list[str]]] = []

    def _artifact_name(self, name: str, source_path: Path) -> str:
        """Filename this target writes for ``name`` given the source extension."""
        ext = source_path.suffix
        if ext in POET_EXTENSIONS:
            return f"{name}.json"
        return f"{name}{ext}"

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        dest = self._config_path / self._artifact_name(name, source_path)
        if source_path.suffix in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            if doc.warnings:
                self._warnings.append((name, list(doc.warnings)))
            rendered = render_for_gptel(doc)
        else:
            rendered = content
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(rendered)
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        # Track the file we just wrote so the manifest can record it.
        self._last_deployed[name] = dest.relative_to(self._config_path)

    def deployed_artifact_path(self, item_type: str, name: str) -> Path | None:
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

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        pass

    def deploy_models(self, config: dict[str, Any]) -> None:
        pass

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        pass

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        # When the manifest recorded the exact deployed path, unlink only
        # that file. This avoids destroying user-authored prompts that
        # happen to share the prompt's stem (e.g. an unrelated ``foo.md``).
        if target_path is not None:
            with contextlib.suppress(FileNotFoundError):
                (self._config_path / target_path).unlink()
            return
        # Legacy fallback for manifests written before we tracked
        # ``target_path``: probe each known extension. This preserves
        # cleanup of older deployments at the cost of being less precise.
        for ext in (".json", ".txt", ".md", ".org"):
            with contextlib.suppress(FileNotFoundError):
                (self._config_path / f"{name}{ext}").unlink()

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

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        if item_type != "prompt" or source_path is None:
            return None
        # Remember which artifact the source extension maps to so that a
        # following read_deployed_bytes() compares against the file deploy
        # owns rather than a stem-sibling found by extension probing.
        self._expected_artifact[name] = self._artifact_name(name, source_path)
        if source_path.suffix in POET_EXTENSIONS:
            doc = parse_poet(content, source_path=source_path)
            return render_for_gptel(doc)
        return content

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
        if item_type != "prompt":
            return None
        # Prefer the artifact recorded by the most recent
        # would_deploy_bytes() call -- the file deploy owns for this
        # prompt. Probing extensions here would let an unrelated
        # user-authored stem-sibling (e.g. foo.json next to a deployed
        # foo.md) masquerade as the deployed artifact, causing perpetual
        # 'update' churn and defeating silent adoption.
        expected = self._expected_artifact.get(name)
        if expected is not None:
            try:
                return (self._config_path / expected).read_bytes()
            except FileNotFoundError:
                return None
        # Fallback for callers that did not establish an expected artifact:
        # find whichever extension is actually present, mirroring the
        # search order used by ``item_exists``.
        for ext in (".json", ".txt", ".md", ".org"):
            path = self._config_path / f"{name}{ext}"
            try:
                return path.read_bytes()
            except FileNotFoundError:
                continue
        return None
