"""Side-effect-free construction and selection of deployable operations."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from .bundle_catalog import (
    compose_catalog,
    dependency_closure_for_target,
    item_applies_to_target_type,
)
from .config import Config
from .filters import should_deploy_to
from .source import ItemIdentity, SourceDiscovery, SourceItem
from .targets.base import Target

_SHARED_SLASH_ITEM_TYPES = frozenset({"command", "skill", "prompt"})
_FULL_SLASH_TARGET_TYPES = frozenset({"claude", "codex", "opencode"})


@dataclass(frozen=True, slots=True)
class TargetCatalogSelection:
    """One target's requested and dependency-closed catalog selection."""

    requested: frozenset[ItemIdentity]
    applicable_requested: frozenset[ItemIdentity]
    closed: frozenset[ItemIdentity]
    items: tuple[SourceItem, ...]


def source_label(item: SourceItem) -> str:
    """Return the logical source label used by filters and diagnostics."""
    source = item.provenance.source
    if source is not None:
        return f"{source.bundle}:{source.path}"
    return f"primary:{item.provenance.primary_path or item.path}"


def catalog_item_applies(
    item: SourceItem,
    target_id: str,
    target_type: str,
    config: Config,
) -> bool:
    """Return static applicability for catalog collision preflight.

    This predicate deliberately models only target-type declarations,
    source filters, and the shared command/skill/prompt namespace. Runtime
    selection uses :func:`item_selected` with the concrete target instead.
    """
    if not item_applies_to_target_type(item, target_type):
        return False
    if not should_deploy_to(
        target_id,
        item.metadata,
        config,
        source_label(item),
        filetags=item.filetags,
    ):
        return False
    if item.item_type not in _SHARED_SLASH_ITEM_TYPES:
        return True
    if target_type in _FULL_SLASH_TARGET_TYPES:
        return True
    if target_type == "droid":
        if item.item_type in {"skill", "prompt"}:
            return True
        return (item.metadata or {}).get("droid_deploy") == "skill"
    if target_type == "gptel":
        return item.item_type == "prompt"
    return False


def discover_operation_catalog(config: Config) -> tuple[SourceItem, ...]:
    """Strictly discover and preflight one immutable operation catalog."""
    primary = tuple(SourceDiscovery(config.source_root).discover_all())
    return compose_catalog(
        primary,
        config.bundles,
        configured_target_types={
            target_id: target.type for target_id, target in config.targets.items()
        },
        applies=lambda item, target_id, target_type: catalog_item_applies(
            item, target_id, target_type, config
        ),
    )


def item_selected(
    item: SourceItem,
    target: Target,
    target_id: str,
    config: Config,
) -> bool:
    """Return whether one catalog item applies to a concrete target."""
    if not item_applies_to_target_type(item, config.targets[target_id].type):
        return False
    if not should_deploy_to(
        target_id,
        item.metadata,
        config,
        source_label(item),
        filetags=item.filetags,
    ):
        return False
    return not target.should_skip(
        item.item_type, item.name, item.content, item.metadata
    )


def select_catalog_for_target(
    items: Sequence[SourceItem],
    requested: Collection[ItemIdentity],
    *,
    target: Target,
    target_id: str,
    config: Config,
) -> TargetCatalogSelection:
    """Select applicable requests and close their dependencies per target."""
    requested_set = frozenset(requested)
    index = {(item.item_type, item.name): item for item in items}
    selected_cache: dict[ItemIdentity, bool] = {}

    def requested_filter(item: SourceItem) -> bool:
        identity = (item.item_type, item.name)
        if identity not in selected_cache:
            selected_cache[identity] = item_selected(item, target, target_id, config)
        return selected_cache[identity]

    closed = dependency_closure_for_target(
        requested_set,
        items,
        target_type=config.targets[target_id].type,
        requested_filter=requested_filter,
    )
    applicable_requested = frozenset(
        identity for identity in requested_set if requested_filter(index[identity])
    )
    return TargetCatalogSelection(
        requested=requested_set,
        applicable_requested=applicable_requested,
        closed=closed,
        items=tuple(item for item in items if (item.item_type, item.name) in closed),
    )
