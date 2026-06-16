"""Remote SSH target wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..ssh import (
    build_claude_merge_script,
    ssh_exists,
    ssh_pull,
    ssh_push,
    ssh_stdin,
)
from .base import Target
from .claude import ClaudeTarget


class RemoteTarget(Target):
    """Wraps an inner target, syncing to/from a remote host via rsync over SSH.

    The inner target operates on a local staging directory. ``prepare()``
    pulls the current remote state into staging, and ``finalize()`` pushes
    staging back to the remote.

    When ``remote_mcp`` is True (claude inner only), MCP servers are NOT
    written into the staging ``.claude.json`` (which is machine-specific and
    never rsynced). Instead each set/pop is accumulated and flushed -- before
    the manifest is saved -- as a single surgical merge into the remote
    ``<remote_path>/.claude.json`` over SSH stdin (see :meth:`flush_remote_mcp`).
    """

    def __init__(
        self,
        inner: Target,
        host: str,
        remote_path: Path,
        staging_path: Path,
        *,
        remote_mcp: bool = False,
    ) -> None:
        self._inner = inner
        self._host = host
        self._remote_path = remote_path
        self._staging_path = staging_path
        self._remote_mcp = remote_mcp
        # Accumulated surgical MCP merge ops, flushed by flush_remote_mcp()
        # BEFORE save_manifest in the deploy loop. Each op is one of:
        #   {"action": "set", "name": str, "entry": dict}
        #   {"action": "pop", "name": str, "entry": None}
        self._mcp_ops: list[dict[str, Any]] = []
        # Names whose set/pop op was queued this run. NOTE: not consulted during
        # the normal deploy loop ordering (item_exists for a name runs BEFORE
        # its op is queued); it exists only so a direct
        # deploy_mcp_server->item_exists call (e.g. in tests) reports True.
        # Manifest is the authoritative source.
        self._mcp_seen: set[str] = set()
        # Memoized staging-manifest mcp names (one parse per deploy). None =
        # unread.
        self._mcp_manifest_names: set[str] | None = None

    @property
    def id(self) -> str:
        return self._inner.id

    def exists(self) -> bool:
        return ssh_exists(self._host, self._remote_path)

    def rsync_includes(self) -> list[str] | None:
        return self._inner.rsync_includes()

    @property
    def remote_mcp_hash(self) -> bool:
        return self._remote_mcp

    @property
    def mcp_hash_includes_env(self) -> bool:
        return self._remote_mcp or self._inner.mcp_hash_includes_env

    def prepare(self, *, verbose: bool = False) -> None:
        ssh_pull(
            self._host,
            self._remote_path,
            self._staging_path,
            verbose=verbose,
            includes=self._inner.rsync_includes(),
        )

    def flush_remote_mcp(self) -> None:
        """Apply accumulated MCP ops to the remote .claude.json over SSH stdin.

        No-op when not a remote_mcp target or when no ops were queued (so no
        SSH connection is opened). Builds the merge program with the ops
        embedded as base64 and pipes it to ``python3 -`` on the host. Raises
        SSHError on failure; the caller (deploy loop) has NOT yet saved the
        manifest, so the next run retries. Does NOT clear ``_mcp_ops`` (cleared
        only in finalize/cleanup), so a flush-success followed by a
        finalize/push failure does not re-queue here. The remote ``~`` in
        remote_path is expanded inside the program via os.path.expanduser; no
        shell quoting is involved (the path is a repr() literal inside the
        piped program, not a shell word).
        """
        if not self._remote_mcp or not self._mcp_ops:
            return
        target_path = f"{self._remote_path}/.claude.json"
        script = build_claude_merge_script(self._mcp_ops, target_path)
        ssh_stdin(self._host, script)

    def finalize(self, *, verbose: bool = False) -> None:
        ssh_push(
            self._host,
            self._remote_path,
            self._staging_path,
            verbose=verbose,
            includes=self._inner.rsync_push_includes(),
        )
        self._mcp_ops.clear()
        self._mcp_seen.clear()
        self._mcp_manifest_names = None
        self._cleanup_staging()

    def cleanup(self) -> None:
        self._mcp_ops.clear()
        self._mcp_seen.clear()
        self._mcp_manifest_names = None
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
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if self._remote_mcp and item_type == "mcp":
            return False
        return self._inner.should_skip(item_type, name, content, metadata)

    def content_fingerprint(self, item_type: str) -> str | None:
        return self._inner.content_fingerprint(item_type)

    def deploy_agent(self, name: str, content: bytes) -> None:
        self._inner.deploy_agent(name, content)

    def deploy_command(self, name: str, content: bytes) -> None:
        self._inner.deploy_command(name, content)

    def deploy_skill(self, name: str, source_dir: Path) -> None:
        self._inner.deploy_skill(name, source_dir)

    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
        if not self._remote_mcp:
            self._inner.deploy_mcp_server(name, config)
            return
        if not config.get("enabled", True):
            # Disabled => removal on the remote (mirrors local pop).
            self._mcp_ops.append({"action": "pop", "name": name, "entry": None})
            self._mcp_seen.add(name)
            return
        entry = ClaudeTarget._claude_mcp_entry(config, name=name, expand_secrets=False)
        entry = self._expand_entry_secrets(name, entry)  # may raise EnvVarError
        self._mcp_ops.append({"action": "set", "name": name, "entry": entry})
        self._mcp_seen.add(name)

    @staticmethod
    def _expand_entry_secrets(name: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Strict-expand ${VAR} in the entry's env/headers values for the
        remote bake.

        A missing variable raises EnvVarError (envsubst), which cli.py catches
        -> exit 1, so we never ship an empty secret to the remote .claude.json.
        Only env and headers carry secrets per the MCP schema; ${VAR} in any
        other field (command, args, url, type, ...) is out of schema contract
        and passes through VERBATIM -- and, unlike local where runtime never
        expands non-env/headers fields either, it will NOT be expanded on the
        remote. Returns a NEW dict; the source entry is not mutated.
        """
        from ..envsubst import expand_env_vars_strict

        out = dict(entry)
        for key, ctx in (("env", "env"), ("headers", "headers")):
            block = entry.get(key)
            if isinstance(block, dict):
                out[key] = {
                    k: (
                        expand_env_vars_strict(v, context=f"mcp.{name}.{ctx}.{k}")
                        if isinstance(v, str)
                        else v
                    )
                    for k, v in block.items()
                }
        return out

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
        if not self._remote_mcp:
            self._inner.remove_mcp_server(name)
            return
        self._mcp_ops.append({"action": "pop", "name": name, "entry": None})
        self._mcp_seen.add(name)

    def remove_models(self) -> None:
        self._inner.remove_models()

    def remove_hook(self, name: str) -> None:
        self._inner.remove_hook(name)

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        self._inner.deploy_marketplace(name, config)

    def remove_marketplace(self, name: str) -> None:
        self._inner.remove_marketplace(name)

    def remove_prompt(self, name: str, target_path: Path | None = None) -> None:
        self._inner.remove_prompt(name, target_path)

    def deployed_artifact_path(self, item_type: str, name: str) -> Path | None:
        return self._inner.deployed_artifact_path(item_type, name)

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        return self._inner.consume_warnings()

    def item_exists(self, item_type: str, name: str) -> bool:
        if self._remote_mcp and item_type == "mcp":
            if name in self._mcp_seen:
                return True
            return name in self._remote_mcp_manifest_names()
        return self._inner.item_exists(item_type, name)

    def _remote_mcp_manifest_names(self) -> set[str]:
        """mcp_servers names from the staging manifest pulled by prepare().

        Memoized (one parse per deploy). load_manifest tolerates a missing file
        (returns empty Manifest), so a first-ever deploy reports no names and
        every server classifies as ``create``. Reads the SAME staging manifest
        file the deploy loop loads (target.manifest_path()), so item_exists and
        the loop's is_update can never disagree in the single-threaded loop.
        Reset to None in finalize/cleanup.
        """
        if self._mcp_manifest_names is None:
            from ..manifest import load_manifest

            manifest = load_manifest(self._inner.manifest_path())
            self._mcp_manifest_names = set(manifest.items.get("mcp_servers", {}))
        return self._mcp_manifest_names

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Path | None = None,
    ) -> bytes | None:
        return self._inner.would_deploy_bytes(item_type, name, content, source_path)

    def read_deployed_bytes(self, item_type: str, name: str) -> bytes | None:
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
