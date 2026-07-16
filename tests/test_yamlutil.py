"""Strict YAML loader tests."""

import pytest
import yaml

from promptdeploy.yamlutil import load_unique_yaml


def test_unique_yaml_loads_nested_mapping() -> None:
    assert load_unique_yaml("outer:\n  inner: value\n") == {"outer": {"inner": "value"}}


def test_unique_yaml_preserves_standard_merge_override_precedence() -> None:
    assert load_unique_yaml(
        "defaults: &defaults\n"
        "  path: /default\n"
        "  mode: safe\n"
        "selected:\n"
        "  <<: *defaults\n"
        "  path: /override\n"
    ) == {
        "defaults": {"path": "/default", "mode": "safe"},
        "selected": {"path": "/override", "mode": "safe"},
    }


def test_unique_yaml_preserves_merge_sequence_precedence() -> None:
    loaded = load_unique_yaml(
        "first: &first {one: 1, shared: first}\n"
        "second: &second {two: 2, shared: second}\n"
        "selected:\n"
        "  <<: [*first, *second]\n"
        "  shared: explicit\n"
    )
    assert isinstance(loaded, dict)
    assert loaded["selected"] == {"one": 1, "two": 2, "shared": "explicit"}


def test_unique_yaml_allows_repeated_source_in_merge_sequence() -> None:
    assert load_unique_yaml(
        "base: &base {one: 1}\nselected:\n  <<: [*base, *base]\n"
    ) == {"base": {"one": 1}, "selected": {"one": 1}}


def test_unique_yaml_leaves_invalid_merge_type_to_standard_diagnostic() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="mapping or list"):
        load_unique_yaml("selected:\n  <<: invalid\n")


def test_unique_yaml_rejects_duplicate_literal_merge_keys() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key <<"):
        load_unique_yaml(
            "first: &first {one: 1}\n"
            "second: &second {two: 2}\n"
            "selected:\n"
            "  <<: *first\n"
            "  <<: *second\n"
        )


@pytest.mark.parametrize(
    "document",
    [
        "selected: {<<: &defaults {path: /one, path: /two}}\n",
        ("selected:\n  <<: [&first {one: 1}, &second {path: /one, path: /two}]\n"),
    ],
)
def test_unique_yaml_rejects_duplicates_inside_inline_merge_sources(
    document: str,
) -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
        load_unique_yaml(document)


def test_unique_yaml_rejects_unhashable_mapping_key() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="unhashable"):
        load_unique_yaml("? [one, two]\n: value\n")


def test_unique_yaml_rejects_unhashable_key_from_merge_expansion() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="unhashable"):
        load_unique_yaml("selected:\n  <<: &source\n    ? [one, two]\n    : value\n")
