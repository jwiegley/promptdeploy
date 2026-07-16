"""Pure operation-catalog construction and target-selection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from promptdeploy import catalog
from promptdeploy.bundle_catalog import BundleCatalogError
from promptdeploy.config import Config, TargetConfig
from promptdeploy.manifest import ManifestSource
from promptdeploy.source import SourceItem, SourceProvenance
from promptdeploy.targets.base import Target


def _item(
    item_type: str,
    name: str,
    *,
    target_types: frozenset[str] | None = None,
    metadata: dict[str, Any] | None = None,
    requires: tuple[tuple[str, str], ...] = (),
) -> SourceItem:
    relative = f"{item_type}s/{name}"
    return SourceItem(
        item_type=item_type,
        name=name,
        path=Path("/source") / relative,
        metadata=metadata,
        content=b"content",
        provenance=SourceProvenance.primary(relative),
        target_types=target_types,
        requires=requires,
    )


def _config(tmp_path: Path, **target_types: str) -> Config:
    targets = {
        target_id: TargetConfig(
            id=target_id,
            type=target_type,
            path=tmp_path / target_id,
        )
        for target_id, target_type in target_types.items()
    }
    return Config(source_root=tmp_path / "source", targets=targets, groups={})


class _TargetSpy:
    def __init__(self, *, skip: bool = False, fail: bool = False) -> None:
        self.skip = skip
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def should_skip(
        self,
        item_type: str,
        name: str,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        self.calls.append((item_type, name))
        if self.fail:
            raise AssertionError("should_skip must not be called")
        return self.skip


def _target(spy: _TargetSpy | None = None) -> tuple[Target, _TargetSpy]:
    target_spy = spy or _TargetSpy()
    return cast(Target, target_spy), target_spy


def test_source_label_never_exposes_an_imported_binding_root() -> None:
    primary = _item("skill", "local")
    assert catalog.source_label(primary) == "primary:skills/local"

    fallback = SourceItem("prompt", "legacy", Path("prompts/legacy.md"), None, b"")
    assert catalog.source_label(fallback) == "primary:prompts/legacy.md"

    source = ManifestSource(
        bundle="ponytail",
        path="skills/ponytail",
        version="4.8.4",
        revision=None,
        nar_hash=None,
        mutable=True,
        transform=None,
        license="MIT",
    )
    imported = SourceItem(
        "skill",
        "ponytail",
        Path("/private/authorization-root/skills/ponytail/SKILL.md"),
        None,
        b"content",
        provenance=SourceProvenance.imported(source),
    )
    assert catalog.source_label(imported) == "ponytail:skills/ponytail"


def test_static_collision_applicability_matches_namespace_matrix(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        claude="claude",
        codex="codex",
        droid="droid",
        opencode="opencode",
        gptel="gptel",
        future="future",
    )
    command = _item("command", "same")
    droid_command = _item("command", "same", metadata={"droid_deploy": "skill"})
    skill = _item("skill", "same")
    prompt = _item("prompt", "same")

    for target_id in ("claude", "codex", "opencode"):
        for item in (command, skill, prompt):
            assert catalog.catalog_item_applies(
                item, target_id, config.targets[target_id].type, config
            )

    assert not catalog.catalog_item_applies(command, "droid", "droid", config)
    assert catalog.catalog_item_applies(droid_command, "droid", "droid", config)
    assert catalog.catalog_item_applies(skill, "droid", "droid", config)
    assert catalog.catalog_item_applies(prompt, "droid", "droid", config)
    assert not catalog.catalog_item_applies(command, "gptel", "gptel", config)
    assert not catalog.catalog_item_applies(skill, "gptel", "gptel", config)
    assert catalog.catalog_item_applies(prompt, "gptel", "gptel", config)
    assert not catalog.catalog_item_applies(command, "future", "future", config)

    support = _item("bundle", "support")
    assert catalog.catalog_item_applies(support, "gptel", "gptel", config)
    codex_only = _item("skill", "codex", target_types=frozenset({"codex"}))
    assert not catalog.catalog_item_applies(codex_only, "gptel", "gptel", config)
    filtered = _item("prompt", "filtered", metadata={"only": ["claude"]})
    assert not catalog.catalog_item_applies(filtered, "codex", "codex", config)


def test_discover_operation_catalog_preflights_all_configured_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, local="codex", otherwise="gptel")
    primary = _item("skill", "local")
    calls: dict[str, Any] = {}

    class Discovery:
        def __init__(self, root: Path) -> None:
            calls["root"] = root

        def discover_all(self) -> tuple[SourceItem, ...]:
            calls["discoveries"] = int(calls.get("discoveries", 0)) + 1
            return (primary,)

    def compose(
        primary_items: tuple[SourceItem, ...],
        bundles: tuple[object, ...],
        *,
        configured_target_types: dict[str, str],
        applies: Any,
    ) -> tuple[SourceItem, ...]:
        calls["primary"] = primary_items
        calls["bundles"] = bundles
        calls["targets"] = configured_target_types
        calls["applies"] = applies(primary, "local", "codex")
        return primary_items

    monkeypatch.setattr(catalog, "SourceDiscovery", Discovery)
    monkeypatch.setattr(catalog, "compose_catalog", compose)

    assert catalog.discover_operation_catalog(config) == (primary,)
    assert calls == {
        "root": config.source_root,
        "discoveries": 1,
        "primary": (primary,),
        "bundles": (),
        "targets": {"local": "codex", "otherwise": "gptel"},
        "applies": True,
    }


def test_item_selected_guards_target_type_before_target_behavior(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, target="gptel")
    wrong_tier = _item("skill", "local", target_types=frozenset({"codex"}))
    target, spy = _target(_TargetSpy(fail=True))
    assert not catalog.item_selected(wrong_tier, target, "target", config)
    assert spy.calls == []

    filtered = _item("prompt", "filtered", metadata={"only": []})
    assert not catalog.item_selected(filtered, target, "target", config)
    assert spy.calls == []

    prompt = _item("prompt", "selected")
    accepting, accepting_spy = _target()
    assert catalog.item_selected(prompt, accepting, "target", config)
    assert accepting_spy.calls == [("prompt", "selected")]

    skipping, skipping_spy = _target(_TargetSpy(skip=True))
    assert not catalog.item_selected(prompt, skipping, "target", config)
    assert skipping_spy.calls == [("prompt", "selected")]

    from promptdeploy.deploy import item_selected as compatibility_export

    assert compatibility_export is catalog.item_selected


@pytest.mark.parametrize(
    ("target_id", "requested", "expected_applicable", "expected_closed"),
    [
        (
            "claude",
            frozenset({("skill", "ponytail")}),
            frozenset({("skill", "ponytail")}),
            frozenset({("bundle", "ponytail"), ("skill", "ponytail")}),
        ),
        (
            "codex",
            frozenset({("skill", "ponytail")}),
            frozenset({("skill", "ponytail")}),
            frozenset({("bundle", "ponytail"), ("skill", "ponytail")}),
        ),
        (
            "droid",
            frozenset({("skill", "ponytail")}),
            frozenset({("skill", "ponytail")}),
            frozenset({("bundle", "ponytail"), ("skill", "ponytail")}),
        ),
        (
            "gptel",
            frozenset({("skill", "ponytail")}),
            frozenset(),
            frozenset(),
        ),
        (
            "opencode",
            frozenset({("skill", "ponytail")}),
            frozenset(),
            frozenset(),
        ),
        (
            "opencode",
            frozenset({("bundle", "ponytail"), ("skill", "ponytail")}),
            frozenset({("bundle", "ponytail")}),
            frozenset({("bundle", "ponytail")}),
        ),
        (
            "gptel",
            frozenset({("prompt", "ponytail")}),
            frozenset({("prompt", "ponytail")}),
            frozenset({("bundle", "ponytail"), ("prompt", "ponytail")}),
        ),
        (
            "codex",
            frozenset({("prompt", "ponytail")}),
            frozenset(),
            frozenset(),
        ),
    ],
)
def test_target_catalog_selection_is_target_specific_and_dependency_closed(
    tmp_path: Path,
    target_id: str,
    requested: frozenset[tuple[str, str]],
    expected_applicable: frozenset[tuple[str, str]],
    expected_closed: frozenset[tuple[str, str]],
) -> None:
    all_targets = frozenset({"claude", "codex", "droid", "opencode", "gptel"})
    support = _item("bundle", "ponytail", target_types=all_targets)
    skill = _item(
        "skill",
        "ponytail",
        target_types=frozenset({"claude", "codex", "droid"}),
        requires=(("bundle", "ponytail"),),
    )
    prompt = _item(
        "prompt",
        "ponytail",
        target_types=frozenset({"gptel"}),
        requires=(("bundle", "ponytail"),),
    )
    items = (support, skill, prompt)
    config = _config(
        tmp_path,
        claude="claude",
        codex="codex",
        droid="droid",
        opencode="opencode",
        gptel="gptel",
    )
    target, _spy = _target()

    selection = catalog.select_catalog_for_target(
        items,
        requested,
        target=target,
        target_id=target_id,
        config=config,
    )

    assert selection.requested == requested
    assert selection.applicable_requested == expected_applicable
    assert selection.closed == expected_closed
    assert tuple((item.item_type, item.name) for item in selection.items) == tuple(
        identity
        for identity in (
            ("bundle", "ponytail"),
            ("skill", "ponytail"),
            ("prompt", "ponytail"),
        )
        if identity in expected_closed
    )
    assert len(_spy.calls) == len(expected_applicable)


def test_selection_rejects_unknown_request_and_does_not_leak_dependency(
    tmp_path: Path,
) -> None:
    support = _item("bundle", "ponytail", target_types=frozenset({"codex", "gptel"}))
    skill = _item(
        "skill",
        "ponytail",
        target_types=frozenset({"codex"}),
        requires=(("bundle", "ponytail"),),
    )
    config = _config(tmp_path, gptel="gptel")
    target, _spy = _target()

    selection = catalog.select_catalog_for_target(
        (support, skill),
        {("skill", "ponytail")},
        target=target,
        target_id="gptel",
        config=config,
    )
    assert selection.closed == frozenset()
    assert selection.items == ()

    with pytest.raises(BundleCatalogError, match="unknown selected item"):
        catalog.select_catalog_for_target(
            (support, skill),
            {("skill", "missing")},
            target=target,
            target_id="gptel",
            config=config,
        )
