"""Fail-closed verification for exact promptdeploy items."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .catalog import discover_operation_catalog, select_catalog_for_target
from .config import Config, load_anthropic_default_model
from .deploy import (
    _TYPE_TO_CATEGORY,
    ItemSelector,
    compute_item_hash,
    resolve_item_selectors,
    target_item_matches_source,
)
from .manifest import has_item_changed, load_manifest_strict
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
    items = list(discover_operation_catalog(config))
    selectors = cast(
        set[ItemSelector],
        resolve_item_selectors(items, item_selectors),
    )
    requested = frozenset(selectors)
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
            selection = select_catalog_for_target(
                items,
                requested,
                target=target,
                target_id=target_id,
                config=config,
            )
            applicable.update(selection.applicable_requested)
            if not selection.items:
                continue
            try:
                target.prepare()
                manifest = load_manifest_strict(target.manifest_path())
            except (OSError, ValueError):
                for item in selection.items:
                    failures.append(
                        VerificationFailure(
                            item.item_type,
                            item.name,
                            target_id,
                            "unreadable",
                        )
                    )
                continue

            for item in selection.items:
                try:
                    category = _TYPE_TO_CATEGORY[item.item_type]
                    source_hash = compute_item_hash(item, target, config)
                    if has_item_changed(
                        manifest,
                        category,
                        item.name,
                        source_hash,
                        item.provenance.source,
                    ):
                        reason: VerificationReason = "manifest-mismatch"
                    else:
                        match = target_item_matches_source(target, item)
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
