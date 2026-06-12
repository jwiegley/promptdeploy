"""Pure rendering core for settings.yaml -> per-target settings.json.

No I/O lives here. ``apply_merge_patch``/``generate_merge_patch`` implement
RFC 7396 (JSON Merge Patch); ``render_settings`` composes ``base`` with the
``overrides`` that match a target.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable

from .config import Config

# Top-level settings.json keys that promptdeploy manages through dedicated
# item types rather than through settings.yaml: hooks/mcpServers from
# hooks/*.yaml and mcp/*.yaml, extraKnownMarketplaces/enabledPlugins from
# marketplaces/*.yaml. They are stripped from rendered settings so settings.yaml
# never fights those item types over the same keys. settings_sync reuses this set.
MANAGED_ELSEWHERE = frozenset(
    {"hooks", "mcpServers", "extraKnownMarketplaces", "enabledPlugins"}
)


def apply_merge_patch(base: Any, patch: Any) -> Any:
    """Apply an RFC 7396 JSON Merge Patch. Pure; inputs are never mutated."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result: Dict[str, Any] = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = apply_merge_patch(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def generate_merge_patch(
    base: Dict[str, Any], target: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the minimal patch ``P`` with ``apply_merge_patch(base, P) == target``.

    ``base`` and ``target`` are both dicts (the settings domain). Keys dropped
    in ``target`` become ``None``; nested dicts recurse; everything else is
    replaced by the ``target`` value.

    Limitation: RFC 7396 uses ``null`` to mean *delete*, so a merge patch
    cannot express setting a key to an explicit ``null``. If ``target``
    contains ``None`` values, the patch encodes them as deletions and the
    round-trip converges on the null-stripped target instead. Callers must
    feed null-free targets (``render_settings`` and ``read_live_settings``
    both strip nulls for this reason).
    """
    patch: Dict[str, Any] = {}
    for key in base:
        if key not in target:
            patch[key] = None
    for key, tval in target.items():
        if key not in base:
            patch[key] = copy.deepcopy(tval)
            continue
        bval = base[key]
        if bval == tval:
            continue
        if isinstance(bval, dict) and isinstance(tval, dict):
            patch[key] = generate_merge_patch(bval, tval)
        else:
            patch[key] = copy.deepcopy(tval)
    return patch


def strip_keys(d: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Return a shallow copy of ``d`` without the named top-level keys."""
    drop = set(keys)
    return {k: v for k, v in d.items() if k not in drop}


def strip_nulls(value: Any) -> Any:
    """Recursively drop ``None`` values from dicts.

    Empty dicts are preserved (e.g. ``extraKnownMarketplaces: {}`` is a valid
    setting). Lists are atomic per RFC 7396 — their elements are not inspected.
    """
    if not isinstance(value, dict):
        return value
    return {k: strip_nulls(v) for k, v in value.items() if v is not None}


def _apply_group_overrides(
    doc: Dict[str, Any], target_id: str, config: Config
) -> Dict[str, Any]:
    """Return ``base`` with every matching group/label override applied.

    Overrides apply as merge patches in file order; the exact ``target_id``
    entry is NOT applied. Nothing is stripped — this is the raw intermediate.
    """
    base = doc.get("base") or {}
    result: Dict[str, Any] = copy.deepcopy(dict(base))
    overrides = doc.get("overrides") or {}
    for key, patch in overrides.items():
        if patch is None or key == target_id:
            continue
        if target_id in config.groups.get(key, []):
            result = apply_merge_patch(result, patch)
    return result


def render_settings(
    doc: Dict[str, Any], target_id: str, config: Config
) -> Dict[str, Any]:
    """Render the concrete managed settings for one target.

    Starts from ``doc['base']`` and applies every matching ``overrides`` entry as
    a merge patch: group/label overrides first (in file order), then the exact
    ``target_id`` override last (most specific wins). Finally strips the
    :data:`MANAGED_ELSEWHERE` keys (``hooks``/``mcpServers``/
    ``extraKnownMarketplaces``/``enabledPlugins``) and any remaining ``null``
    values. Returns plain dicts only — no ``null`` reaches the caller.
    """
    result = _apply_group_overrides(doc, target_id, config)
    exact = (doc.get("overrides") or {}).get(target_id)
    if exact is not None:
        result = apply_merge_patch(result, exact)

    result = strip_keys(result, MANAGED_ELSEWHERE)
    rendered: Dict[str, Any] = strip_nulls(result)
    return rendered


def render_pre_exact(
    doc: Dict[str, Any], target_id: str, config: Config
) -> Dict[str, Any]:
    """Render one target's settings as if its exact override did not exist.

    Same pipeline as :func:`render_settings` minus the final exact
    ``target_id`` patch: ``base`` + matching group/label overrides in file
    order, then the :data:`MANAGED_ELSEWHERE`/null strip. ``settings
    reconcile --apply`` generates its drift patch against this intermediate
    so the exact override it writes composes with group overrides instead of
    fighting them (the exact override is reconcile's output, not its input).
    """
    result = _apply_group_overrides(doc, target_id, config)
    result = strip_keys(result, MANAGED_ELSEWHERE)
    rendered: Dict[str, Any] = strip_nulls(result)
    return rendered
