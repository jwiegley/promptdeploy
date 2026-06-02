"""Pure rendering core for settings.yaml -> per-target settings.json.

No I/O lives here. ``apply_merge_patch``/``generate_merge_patch`` implement
RFC 7386 (JSON Merge Patch); ``render_settings`` composes ``base`` with the
``overrides`` that match a target.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable

from .config import Config


def apply_merge_patch(base: Any, patch: Any) -> Any:
    """Apply an RFC 7386 JSON Merge Patch. Pure; inputs are never mutated."""
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
    setting). Lists are atomic per RFC 7386 — their elements are not inspected.
    """
    if not isinstance(value, dict):
        return value
    return {k: strip_nulls(v) for k, v in value.items() if v is not None}


def render_settings(
    doc: Dict[str, Any], target_id: str, config: Config
) -> Dict[str, Any]:
    """Render the concrete managed settings for one target.

    Starts from ``doc['base']`` and applies every matching ``overrides`` entry as
    a merge patch: group/label overrides first (in file order), then the exact
    ``target_id`` override last (most specific wins). Finally strips
    ``hooks``/``mcpServers`` and any remaining ``null`` values. Returns plain
    dicts only — no ``null`` reaches the caller.
    """
    base = doc.get("base") or {}
    result: Dict[str, Any] = copy.deepcopy(dict(base))

    overrides = doc.get("overrides") or {}
    exact = None
    for key, patch in overrides.items():
        if patch is None:
            continue
        if key == target_id:
            exact = patch
            continue
        if target_id in config.groups.get(key, []):
            result = apply_merge_patch(result, patch)
    if exact is not None:
        result = apply_merge_patch(result, exact)

    result = strip_keys(result, {"hooks", "mcpServers"})
    return strip_nulls(result)
