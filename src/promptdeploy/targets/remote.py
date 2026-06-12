"""Remote SSH target wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from ..ssh import ssh_exists, ssh_pull, ssh_push
from .base import Target


class RemoteTarget(Target):
    """Wraps an inner target, syncing to/from a remote host via rsync over SSH.

    The inner target operates on a local staging directory. ``prepare()``
    pulls the current remote state into staging, and ``finalize()`` pushes
    staging back to the remote.
    """

    def __init__(
        self,
        inner: Target,
        host: str,
        remote_path: Path,
        staging_path: Path,
    ) -> None:
        self._inner = inner
        self._host = host
        self._remote_path = remote_path
        self._staging_path = staging_path

    @property
    def id(self) -> str:
        return self._inner.id

    def exists(self) -> bool:
        return ssh_exists(self._host, self._remote_path)

    def rsync_includes(self) -> list[str] | None:
        return self._inner.rsync_includes()

    def prepare(self, *, verbose: bool = False) -> None:
        ssh_pull(
            self._host,
            self._remote_path,
            self._staging_path,
            verbose=verbose,
            includes=self._inner.rsync_includes(),
        )

    def finalize(self, *, verbose: bool = False) -> None:
        ssh_push(
            self._host,
            self._remote_path,
            self._staging_path,
            verbose=verbose,
            includes=self._inner.rsync_includes(),
        )
        self._cleanup_staging()

    def cleanup(self) -> None:
        self._cleanup_staging()

    def _cleanup_staging(self) -> None:
        if self._staging_path.exists():
            shutil.rmtree(self._staging_path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Delegated Target methods
    # ------------------------------------------------------------------

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: Optional[bytes] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        return self._inner.should_skip(item_type, name, content, metadata)

    def content_fingerprint(self, item_type: str) -> Optional[str]:
        return self._inner.content_fingerprint(item_type)

    def deploy_agent(self, name: str, content: bytes) -> None:
        self._inner.deploy_agent(name, content)

    def deploy_command(self, name: str, content: bytes) -> None:
        self._inner.deploy_command(name, content)

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        self._inner.deploy_skill(name, source_dir)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        self._inner.deploy_mcp_server(name, config)

    def deploy_models(self, config: dict[str, Any]) -> None:
        self._inner.deploy_models(config)

    def deploy_hook(self, name: str, config: dict[str, Any]) -> None:
        self._inner.deploy_hook(name, config)

    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None:
        self._inner.deploy_prompt(name, content, source_path)

    def remove_agent(self, name: str) -> None:
        self._inner.remove_agent(name)

    def remove_command(self, name: str) -> None:
        self._inner.remove_command(name)

    def remove_skill(self, name: str) -> None:
        self._inner.remove_skill(name)

    def remove_mcp_server(self, name: str) -> None:
        self._inner.remove_mcp_server(name)

    def remove_models(self) -> None:
        self._inner.remove_models()

    def remove_hook(self, name: str) -> None:
        self._inner.remove_hook(name)

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        self._inner.deploy_marketplace(name, config)

    def remove_marketplace(self, name: str) -> None:
        self._inner.remove_marketplace(name)

    def remove_prompt(self, name: str, target_path: Optional[Path] = None) -> None:
        self._inner.remove_prompt(name, target_path)

    def deployed_artifact_path(self, item_type: str, name: str) -> Optional[Path]:
        return self._inner.deployed_artifact_path(item_type, name)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        return self._inner.consume_warnings()

    def item_exists(self, item_type: str, name: str) -> bool:
        return self._inner.item_exists(item_type, name)

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Optional[Path] = None,
    ) -> Optional[bytes]:
        return self._inner.would_deploy_bytes(item_type, name, content, source_path)

    def read_deployed_bytes(self, item_type: str, name: str) -> Optional[bytes]:
        return self._inner.read_deployed_bytes(item_type, name)

    def deploy_settings(
        self, rendered: dict[str, Any], previous_keys: list[str]
    ) -> None:
        self._inner.deploy_settings(rendered, previous_keys)

    def remove_settings(self, previous_keys: list[str]) -> None:
        self._inner.remove_settings(previous_keys)

    def read_settings_json(self) -> dict[str, Any]:
        return self._inner.read_settings_json()

    def manifest_path(self) -> Path:
        return self._inner.manifest_path()
