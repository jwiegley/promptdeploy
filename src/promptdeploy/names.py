"""Canonical naming rules for items that become filesystem artifacts."""

import unicodedata

PATH_ITEM_TYPES = frozenset({"agent", "command", "skill", "prompt"})


def require_canonical_item_name(item_type: str, name: object) -> str:
    """Return NAME when it is a safe leaf, otherwise fail without normalizing it."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"Unsafe {item_type} name: expected a non-empty string")
    if item_type not in PATH_ITEM_TYPES:
        return name
    if (
        name != name.strip()
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in name)
    ):
        raise ValueError(
            f"Unsafe {item_type} name: expected one canonical path component"
        )
    return name
