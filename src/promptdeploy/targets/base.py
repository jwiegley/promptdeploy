from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


class Target(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def exists(self) -> bool: ...

    def prepare(self, *, verbose: bool = False) -> None:
        """Called before any deploy/read operations. No-op for local targets."""

    def finalize(self, *, verbose: bool = False) -> None:
        """Called after all deploy operations complete. No-op for local targets."""

    def cleanup(self) -> None:
        """Called to release resources (e.g. temp dirs) without pushing changes."""

    def deploy_settings(
        self, rendered: dict[str, Any], previous_keys: list[str]
    ) -> None:
        """Merge rendered Claude settings into the target's settings.json.

        Default no-op so non-Claude targets need no changes.
        """

    def remove_settings(self, previous_keys: list[str]) -> None:
        """Remove previously-managed settings keys. No-op by default."""

    def deploy_marketplace(self, name: str, config: dict[str, Any]) -> None:
        """Merge a Claude marketplace + its enabled plugins into settings.json.

        Default no-op so non-Claude targets need no changes.
        """

    def remove_marketplace(self, name: str) -> None:
        """Remove a marketplace and its enabled plugins. No-op by default."""

    def read_settings_json(self) -> dict[str, Any]:
        """Return the target's current settings.json as a dict.

        Returns ``{}`` when the target has no Claude settings file (the default
        for non-Claude targets).
        """
        return {}

    def rsync_includes(self) -> list[str] | None:
        """Return rsync include patterns for managed paths.

        When non-None, only these paths are synced to/from the remote.
        Returning None (the default) syncs the entire directory.
        """
        return None

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: Optional[bytes] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Return True if this target would no-op the deploy for this item.

        When True, the deploy loop will not call the deploy method and will
        not record the item in the manifest -- ensuring idempotency for
        items that a target silently ignores.
        """
        return False

    def content_fingerprint(self, item_type: str) -> Optional[str]:
        """Return a string describing target-side transform inputs, or None.

        The deploy loop folds this value into the manifest hash so that a
        config change which alters deployed bytes (e.g. a flipped injected
        model) invalidates the cache even when source bytes are unchanged.
        Default: no target-side transforms.
        """
        return None

    @abstractmethod
    def deploy_agent(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_command(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_skill(self, name: str, source_dir: Path) -> None: ...

    @abstractmethod
    def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_models(self, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_hook(self, name: str, config: dict[str, Any]) -> None: ...

    @abstractmethod
    def deploy_prompt(self, name: str, content: bytes, source_path: Path) -> None: ...

    @abstractmethod
    def remove_agent(self, name: str) -> None: ...

    @abstractmethod
    def remove_command(self, name: str) -> None: ...

    @abstractmethod
    def remove_skill(self, name: str) -> None: ...

    @abstractmethod
    def remove_mcp_server(self, name: str) -> None: ...

    @abstractmethod
    def remove_models(self) -> None: ...

    @abstractmethod
    def remove_hook(self, name: str) -> None: ...

    @abstractmethod
    def remove_prompt(self, name: str, target_path: Optional[Path] = None) -> None:
        """Remove a deployed prompt by ``name``.

        ``target_path`` is the relative path that was recorded in the manifest
        when the prompt was last deployed. When provided, targets should
        prefer it as the authoritative location to unlink so that stale
        prompts cannot collide with unrelated user-authored files. When
        ``None`` (e.g. legacy manifests written before path tracking), the
        target may fall back to its previous heuristic.
        """
        ...

    def deployed_artifact_path(self, item_type: str, name: str) -> Optional[Path]:
        """Return the relative path the most recent deploy wrote, if any.

        The deploy loop calls this after a successful deploy and stores the
        result in the manifest. The path is relative to the target's root.
        Default: returns ``None`` so existing targets opt in incrementally.
        """
        return None

    def consume_warnings(self) -> list[tuple[str, list[str]]]:
        """Drain and return warnings collected during the last batch of deploys.

        Returns a list of ``(item_name, [warning, ...])`` pairs. Targets that
        render templated prompts (``.poet``/``.j2``/``.jinja``) collect any
        warnings emitted by :func:`promptdeploy.poet.parse_poet` and surface
        them here so the deploy loop can print them. Default: nothing to
        report.
        """
        return []

    @abstractmethod
    def item_exists(self, item_type: str, name: str) -> bool:
        """Check if an item already exists at the deploy target path.

        Used to detect pre-existing items that were not deployed by
        promptdeploy and should not be overwritten or removed.
        """
        ...

    def would_deploy_bytes(
        self,
        item_type: str,
        name: str,
        content: bytes,
        source_path: Optional[Path] = None,
    ) -> Optional[bytes]:
        """Return the bytes this target would write for a single-file artifact.

        Used by the deploy loop to decide whether a pre-existing on-disk
        file is byte-identical to what we would write -- if so, the item
        is silently adopted into the manifest rather than reported as
        pre-existing on every deploy.

        Returns ``None`` for items that are not single-file artifacts
        (e.g. skill directories, MCP/hook entries merged into JSON).
        """
        return None

    def read_deployed_bytes(self, item_type: str, name: str) -> Optional[bytes]:
        """Read the bytes currently on disk for a single-file artifact.

        Mirrors :meth:`would_deploy_bytes` so the deploy loop can compare
        on-disk content to what it would write. Returns ``None`` when the
        item is not a single-file artifact or no file is present.
        """
        return None

    @abstractmethod
    def manifest_path(self) -> Path: ...
