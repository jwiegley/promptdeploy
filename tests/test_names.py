from __future__ import annotations

import pytest

from promptdeploy.names import require_canonical_item_name


@pytest.mark.parametrize(
    "name",
    ["alpha", "alpha.beta", "name:with:colons", "internal space", "日本語"],
)
def test_canonical_path_item_names_are_accepted(name: str) -> None:
    assert require_canonical_item_name("skill", name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        " leading",
        "trailing ",
        ".",
        "..",
        "../victim",
        "a/b",
        "a\\b",
        "/tmp/victim",
        "nul\0byte",
        "line\nbreak",
        "tab\tname",
        "delete\x7fkey",
        "next-line\x85key",
        "bidi\u202ekey",
    ],
)
def test_unsafe_path_item_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="Unsafe skill name"):
        require_canonical_item_name("skill", name)


def test_non_path_items_keep_existing_name_grammar() -> None:
    assert require_canonical_item_name("mcp", "server/name") == "server/name"
    with pytest.raises(ValueError, match="non-empty string"):
        require_canonical_item_name("mcp", None)
