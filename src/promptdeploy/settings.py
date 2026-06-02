"""Pure rendering core for settings.yaml -> per-target settings.json.

No I/O lives here. ``apply_merge_patch``/``generate_merge_patch`` implement
RFC 7386 (JSON Merge Patch); ``render_settings`` composes ``base`` with the
``overrides`` that match a target.
"""

from __future__ import annotations

import copy
from typing import Any, Dict


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
