"""Tests for the pure settings rendering core."""

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.settings import (
    apply_merge_patch,
    generate_merge_patch,
    render_pre_exact,
    render_settings,
    strip_keys,
    strip_nulls,
)


class TestApplyMergePatch:
    def test_adds_and_changes_scalars(self):
        base = {"a": 1, "b": 2}
        assert apply_merge_patch(base, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}

    def test_null_deletes_key(self):
        assert apply_merge_patch({"a": 1, "b": 2}, {"b": None}) == {"a": 1}

    def test_null_on_absent_key_is_noop(self):
        assert apply_merge_patch({"a": 1}, {"z": None}) == {"a": 1}

    def test_nested_dicts_merge_deeply(self):
        base = {"env": {"X": "1", "Y": "2"}}
        patch = {"env": {"Y": "3", "Z": "4"}}
        assert apply_merge_patch(base, patch) == {"env": {"X": "1", "Y": "3", "Z": "4"}}

    def test_nested_null_deletes_one_subkey(self):
        base = {"env": {"X": "1", "Y": "2"}}
        assert apply_merge_patch(base, {"env": {"X": None}}) == {"env": {"Y": "2"}}

    def test_dict_patch_over_nondict_target_replaces(self):
        # RFC 7396: a dict patch over a non-dict target treats target as {}.
        assert apply_merge_patch({"a": 5}, {"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_scalar_patch_replaces_whole_value(self):
        # RFC 7396: when the patch itself is not an object, it replaces base.
        assert apply_merge_patch({"a": 1}, "replaced") == "replaced"

    def test_inputs_not_mutated(self):
        base = {"env": {"X": "1"}}
        apply_merge_patch(base, {"env": {"X": "2"}})
        assert base == {"env": {"X": "1"}}


# The complete example test-case table from RFC 7396, Appendix A.
RFC7396_APPENDIX_A = [
    ({"a": "b"}, {"a": "c"}, {"a": "c"}),
    ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
    ({"a": "b"}, {"a": None}, {}),
    ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
    ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
    ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
    ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
    ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
    (["a", "b"], ["c", "d"], ["c", "d"]),
    ({"a": "b"}, ["c"], ["c"]),
    ({"a": "foo"}, None, None),
    ({"a": "foo"}, "bar", "bar"),
    ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
    ([1, 2], {"a": "b", "c": None}, {"a": "b"}),
    ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
]


@pytest.mark.parametrize("original,patch,expected", RFC7396_APPENDIX_A)
def test_rfc7396_appendix_a_vectors(original, patch, expected):
    assert apply_merge_patch(original, patch) == expected


class TestGenerateMergePatch:
    def test_added_key(self):
        assert generate_merge_patch({"a": 1}, {"a": 1, "b": 2}) == {"b": 2}

    def test_removed_key_becomes_null(self):
        assert generate_merge_patch({"a": 1, "b": 2}, {"a": 1}) == {"b": None}

    def test_changed_scalar(self):
        assert generate_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}

    def test_identical_yields_empty_patch(self):
        assert (
            generate_merge_patch({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 2}}) == {}
        )

    def test_nested_diff_is_minimal(self):
        base = {"env": {"X": "1", "Y": "2"}}
        target = {"env": {"X": "1", "Y": "9", "Z": "3"}}
        assert generate_merge_patch(base, target) == {"env": {"Y": "9", "Z": "3"}}

    def test_roundtrip_reproduces_target(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}, "e": 5}
        target = {
            "a": 1,
            "b": {"c": 9},
            "f": 7,
        }  # d removed within b, e removed, f added
        patch = generate_merge_patch(base, target)
        assert apply_merge_patch(base, patch) == target

    def test_explicit_null_in_target_is_unexpressible(self):
        # RFC 7396 cannot distinguish "set to null" from "delete": a None in
        # `target` becomes a deletion in the patch, so the round-trip yields
        # the null-stripped target rather than the target itself. Callers
        # must strip nulls first (render_settings and read_live_settings do).
        base = {"a": 1}
        target = {"a": None, "b": 2}
        patch = generate_merge_patch(base, target)
        assert patch == {"a": None, "b": 2}
        assert apply_merge_patch(base, patch) == {"b": 2}  # not == target


class TestStripHelpers:
    def test_strip_keys_removes_named_top_level_keys(self):
        d = {"env": {}, "hooks": {"x": 1}, "mcpServers": {"y": 2}, "model": "a"}
        assert strip_keys(d, {"hooks", "mcpServers"}) == {"env": {}, "model": "a"}

    def test_strip_nulls_removes_none_values_recursively(self):
        d = {"a": 1, "b": None, "env": {"X": "1", "Y": None}}
        assert strip_nulls(d) == {"a": 1, "env": {"X": "1"}}

    def test_strip_nulls_preserves_empty_dicts(self):
        # extraKnownMarketplaces: {} is a legitimate setting and must survive.
        d = {"extraKnownMarketplaces": {}, "env": {"X": None}}
        assert strip_nulls(d) == {"extraKnownMarketplaces": {}, "env": {}}

    def test_strip_nulls_leaves_lists_atomic(self):
        d = {"allowWrite": ["/tmp", None]}
        assert strip_nulls(d) == {"allowWrite": ["/tmp", None]}


def _cfg(*target_ids: str, groups=None) -> Config:
    targets = {
        tid: TargetConfig(
            id=tid, type="claude", path=__import__("pathlib").Path("/x") / tid
        )
        for tid in target_ids
    }
    return Config(
        source_root=__import__("pathlib").Path("/x"),
        targets=targets,
        groups=groups or {},
    )


class TestRenderSettings:
    def test_base_only_when_no_override_matches(self):
        doc = {"base": {"effortLevel": "low", "env": {"A": "1"}}}
        cfg = _cfg("claude-personal")
        assert render_settings(doc, "claude-personal", cfg) == {
            "effortLevel": "low",
            "env": {"A": "1"},
        }

    def test_exact_target_override_add_change_delete(self):
        doc = {
            "base": {"effortLevel": "low", "env": {"A": "1", "B": "2"}},
            "overrides": {
                "claude-positron": {
                    "effortLevel": None,
                    "model": "sonnet",
                    "env": {"B": "9", "A": None},
                }
            },
        }
        cfg = _cfg("claude-positron")
        assert render_settings(doc, "claude-positron", cfg) == {
            "model": "sonnet",
            "env": {"B": "9"},
        }

    def test_group_override_applies_via_config_groups(self):
        doc = {
            "base": {"effortLevel": "low"},
            "overrides": {"positron": {"effortLevel": "med"}},
        }
        cfg = _cfg("claude-positron", groups={"positron": ["claude-positron"]})
        assert render_settings(doc, "claude-positron", cfg) == {"effortLevel": "med"}

    def test_exact_target_wins_over_group(self):
        doc = {
            "base": {"x": "base"},
            "overrides": {
                "positron": {"x": "group"},  # group, applied first
                "claude-positron": {"x": "exact"},  # exact id, applied last
            },
        }
        cfg = _cfg("claude-positron", groups={"positron": ["claude-positron"]})
        assert render_settings(doc, "claude-positron", cfg)["x"] == "exact"

    def test_overlapping_groups_apply_in_file_order(self):
        doc = {
            "base": {"x": "base"},
            "overrides": {"g1": {"x": "first"}, "g2": {"x": "second"}},
        }
        cfg = _cfg("t", groups={"g1": ["t"], "g2": ["t"]})
        # g2 appears later in file order -> wins
        assert render_settings(doc, "t", cfg)["x"] == "second"

    def test_strips_hooks_and_mcp_servers(self):
        doc = {"base": {"hooks": {"X": 1}, "mcpServers": {"Y": 2}, "model": "a"}}
        assert render_settings(doc, "t", _cfg("t")) == {"model": "a"}

    def test_strips_marketplace_keys(self):
        # extraKnownMarketplaces/enabledPlugins are managed by marketplaces/*.yaml
        # and must never reach the deploy layer through settings.yaml.
        doc = {
            "base": {
                "extraKnownMarketplaces": {"acme": {}},
                "enabledPlugins": {"p@acme": True},
                "model": "a",
            }
        }
        assert render_settings(doc, "t", _cfg("t")) == {"model": "a"}

    def test_strips_literal_null_in_base(self):
        doc = {"base": {"a": 1, "b": None}}
        assert render_settings(doc, "t", _cfg("t")) == {"a": 1}

    def test_missing_base_and_overrides_yield_empty(self):
        assert render_settings({}, "t", _cfg("t")) == {}

    def test_none_override_value_is_ignored(self):
        doc = {"base": {"a": 1}, "overrides": {"t": None}}
        assert render_settings(doc, "t", _cfg("t")) == {"a": 1}

    def test_none_group_override_value_is_ignored(self):
        doc = {"base": {"a": 1}, "overrides": {"g": None}}
        cfg = _cfg("t", groups={"g": ["t"]})
        assert render_settings(doc, "t", cfg) == {"a": 1}


class TestRenderPreExact:
    def test_applies_groups_but_not_exact_override(self):
        doc = {
            "base": {"x": "base"},
            "overrides": {
                "g": {"x": "group"},
                "t": {"x": "exact"},
            },
        }
        cfg = _cfg("t", groups={"g": ["t"]})
        # The exact `t` override is reconcile's output, not its input.
        assert render_pre_exact(doc, "t", cfg) == {"x": "group"}
        assert render_settings(doc, "t", cfg) == {"x": "exact"}

    def test_strips_managed_keys_and_nulls(self):
        doc = {"base": {"a": None, "mcpServers": {"m": 1}, "b": 2}}
        assert render_pre_exact(doc, "t", _cfg("t")) == {"b": 2}
