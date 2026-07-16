"""Canonical naming rules for items that become filesystem artifacts."""

import re
import unicodedata

PATH_ITEM_TYPES = frozenset({"agent", "bundle", "command", "skill", "prompt"})
_BUNDLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


def require_canonical_item_name(item_type: str, name: object) -> str:
    """Return NAME when it is a safe leaf, otherwise fail without normalizing it."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"Unsafe {item_type} name: expected a non-empty string")
    if item_type == "bundle":
        if _BUNDLE_NAME.fullmatch(name) is None:
            raise ValueError(
                "Unsafe bundle name: expected one lowercase canonical path component"
            )
        return name
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
