"""Tests for the pure settings rendering core."""

from promptdeploy.settings import (
    apply_merge_patch,
    generate_merge_patch,
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
        # RFC 7386: a dict patch over a non-dict target treats target as {}.
        assert apply_merge_patch({"a": 5}, {"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_scalar_patch_replaces_whole_value(self):
        # RFC 7386: when the patch itself is not an object, it replaces base.
        assert apply_merge_patch({"a": 1}, "replaced") == "replaced"

    def test_inputs_not_mutated(self):
        base = {"env": {"X": "1"}}
        apply_merge_patch(base, {"env": {"X": "2"}})
        assert base == {"env": {"X": "1"}}


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
