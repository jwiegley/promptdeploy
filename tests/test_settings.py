"""Tests for the pure settings rendering core."""

from promptdeploy.settings import apply_merge_patch


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
