"""Fail-closed verification for exact promptdeploy items."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .config import Config, load_anthropic_default_model
from .deploy import (
    _TYPE_TO_CATEGORY,
    ItemSelector,
    compute_item_hash,
    item_selected,
    resolve_item_selectors,
)
from .manifest import has_changed, load_manifest_strict
from .source import SourceDiscovery, SourceItem
from .targets import create_target

VerificationReason = Literal[
    "manifest-mismatch",
    "mismatch",
    "unprovable",
    "unreadable",
    "no-applicable-target",
]


@dataclass(frozen=True)
class VerificationFailure:
    """One exact item that could not be proved current on one target."""

    item_type: str
    name: str
    target_id: str
    reason: VerificationReason


def _selected_items(
    items: list[SourceItem],
    selectors: set[ItemSelector],
) -> list[SourceItem]:
    return [item for item in items if (item.item_type, item.name) in selectors]


def verify_items(
    config: Config,
    *,
    target_ids: list[str],
    item_selectors: list[ItemSelector],
    local_host: str | None = None,
) -> list[VerificationFailure]:
    """Strictly prove every selected, applicable item on fresh target readers."""
    if not item_selectors:
        raise ValueError("verification requires at least one exact item selector")
    items = list(SourceDiscovery(config.source_root).discover_all())
    selectors = cast(
        set[ItemSelector],
        resolve_item_selectors(items, item_selectors),
    )
    selected_items = _selected_items(items, selectors)
    applicable: set[ItemSelector] = set()
    failures: list[VerificationFailure] = []
    global_model = load_anthropic_default_model(config.source_root / "models.yaml")

    for target_id in target_ids:
        target = create_target(
            config.targets[target_id],
            global_model=global_model,
            local_host=local_host,
        )
        try:
            try:
                target.prepare()
                manifest = load_manifest_strict(target.manifest_path())
            except (OSError, ValueError):
                for item in selected_items:
                    if item_selected(item, target, target_id, config):
                        selector = (item.item_type, item.name)
                        applicable.add(selector)
                        failures.append(
                            VerificationFailure(
                                item.item_type,
                                item.name,
                                target_id,
                                "unreadable",
                            )
                        )
                continue

            for item in selected_items:
                if not item_selected(item, target, target_id, config):
                    continue
                selector = (item.item_type, item.name)
                applicable.add(selector)
                try:
                    category = _TYPE_TO_CATEGORY[item.item_type]
                    source_hash = compute_item_hash(item, target, config)
                    if has_changed(manifest, category, item.name, source_hash):
                        reason: VerificationReason = "manifest-mismatch"
                    else:
                        match = target.item_matches_source(
                            item.item_type,
                            item.name,
                            item.content,
                            item.metadata,
                            source_path=item.path,
                        )
                        if match is True:
                            continue
                        reason = "mismatch" if match is False else "unprovable"
                except (OSError, ValueError):
                    reason = "unreadable"
                failures.append(
                    VerificationFailure(
                        item.item_type,
                        item.name,
                        target_id,
                        reason,
                    )
                )
        finally:
            target.cleanup()

    for item_type, name in sorted(selectors - applicable):
        failures.append(
            VerificationFailure(
                item_type,
                name,
                "<none>",
                "no-applicable-target",
            )
        )
    return failures
