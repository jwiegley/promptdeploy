"""Strict YAML loading shared by checked configuration surfaces."""

from __future__ import annotations

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver


class UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys at every depth."""


_MERGE_KEY = object()


def _direct_mapping_key(
    loader: UniqueKeySafeLoader,
    node: yaml.Node,
) -> object:
    """Construct one literal key without expanding YAML merge mappings."""
    if node.tag == "tag:yaml.org,2002:merge":
        return _MERGE_KEY
    return loader.construct_object(node, deep=False)  # type: ignore[no-untyped-call]


def _check_literal_keys(
    loader: UniqueKeySafeLoader,
    node: yaml.MappingNode,
) -> None:
    """Reject duplicates written in one mapping, before merge expansion."""
    direct_keys: dict[object, yaml.Node] = {}
    for key_node, _value_node in node.value:
        key = _direct_mapping_key(loader, key_node)
        try:
            duplicate = key in direct_keys
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            rendered = "<<" if key is _MERGE_KEY else repr(key)
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {rendered}",
                key_node.start_mark,
            )
        direct_keys[key] = key_node


def _check_merge_sources(
    loader: UniqueKeySafeLoader,
    node: yaml.MappingNode,
    seen: set[int],
) -> None:
    """Preflight mapping nodes that PyYAML splices without constructing."""
    for key_node, value_node in node.value:
        if key_node.tag != "tag:yaml.org,2002:merge":
            continue
        sources: tuple[yaml.MappingNode, ...]
        if isinstance(value_node, yaml.MappingNode):
            sources = (value_node,)
        elif isinstance(value_node, yaml.SequenceNode):
            sources = tuple(
                item for item in value_node.value if isinstance(item, yaml.MappingNode)
            )
        else:
            # ``flatten_mapping`` owns the standard invalid-merge diagnostic.
            continue
        for source in sources:
            identity = id(source)
            if identity in seen:
                continue
            seen.add(identity)
            _check_literal_keys(loader, source)
            _check_merge_sources(loader, source, seen)


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    # Check the keys as written before ``flatten_mapping`` expands ``<<``.
    # PyYAML deliberately prepends merged keys and then lets explicit keys
    # override them. Treating that flattened sequence as literal duplicates
    # would reject standard, previously supported merge precedence. Merge
    # source nodes need their own recursive preflight because flattening can
    # splice an inline source without invoking this constructor for that node.
    _check_literal_keys(loader, node)
    _check_merge_sources(loader, node, {id(node)})
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        result[key] = loader.construct_object(  # type: ignore[no-untyped-call]
            value_node, deep=deep
        )
    return result


UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_unique_yaml(text: str) -> object:
    """Safely load one YAML document while preserving key uniqueness."""
    return yaml.load(text, Loader=UniqueKeySafeLoader)
