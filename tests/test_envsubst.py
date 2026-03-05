"""Tests for environment variable expansion."""

from promptdeploy.envsubst import expand_env_in_dict, expand_env_vars


class TestExpandEnvVars:
    def test_basic_expansion(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert expand_env_vars("${MY_VAR}") == "hello"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "alpha")
        monkeypatch.setenv("B", "beta")
        assert expand_env_vars("${A}-${B}") == "alpha-beta"

    def test_unset_var_preserved(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert expand_env_vars("${UNSET_VAR_XYZ}") == "${UNSET_VAR_XYZ}"

    def test_no_vars_returns_unchanged(self):
        assert expand_env_vars("plain string") == "plain string"

    def test_empty_string(self):
        assert expand_env_vars("") == ""

    def test_mixed_set_and_unset(self, monkeypatch):
        monkeypatch.setenv("SET_ONE", "yes")
        monkeypatch.delenv("NOT_SET_ONE", raising=False)
        result = expand_env_vars("${SET_ONE}:${NOT_SET_ONE}")
        assert result == "yes:${NOT_SET_ONE}"

    def test_var_with_surrounding_text(self, monkeypatch):
        monkeypatch.setenv("KEY", "secret")
        assert expand_env_vars("prefix-${KEY}-suffix") == "prefix-secret-suffix"


class TestExpandEnvInDict:
    def test_string_values_expanded(self, monkeypatch):
        monkeypatch.setenv("VAL", "expanded")
        result = expand_env_in_dict({"key": "${VAL}"})
        assert result == {"key": "expanded"}

    def test_nested_dicts_expanded(self, monkeypatch):
        monkeypatch.setenv("INNER", "deep")
        result = expand_env_in_dict({"outer": {"inner": "${INNER}"}})
        assert result == {"outer": {"inner": "deep"}}

    def test_lists_with_strings_expanded(self, monkeypatch):
        monkeypatch.setenv("ITEM", "value")
        result = expand_env_in_dict({"items": ["${ITEM}", "literal"]})
        assert result == {"items": ["value", "literal"]}

    def test_non_string_values_passed_through(self):
        data = {"count": 42, "flag": True, "empty": None}
        result = expand_env_in_dict(data)
        assert result == {"count": 42, "flag": True, "empty": None}

    def test_mixed_dict_with_all_types(self, monkeypatch):
        monkeypatch.setenv("NAME", "test")
        data = {
            "name": "${NAME}",
            "count": 5,
            "enabled": False,
            "tags": ["${NAME}", "fixed"],
            "nested": {"ref": "${NAME}"},
            "nothing": None,
        }
        result = expand_env_in_dict(data)
        assert result == {
            "name": "test",
            "count": 5,
            "enabled": False,
            "tags": ["test", "fixed"],
            "nested": {"ref": "test"},
            "nothing": None,
        }

    def test_list_with_non_string_elements(self, monkeypatch):
        monkeypatch.setenv("X", "val")
        data = {"items": ["${X}", 123, True, None]}
        result = expand_env_in_dict(data)
        assert result == {"items": ["val", 123, True, None]}

    def test_empty_dict(self):
        assert expand_env_in_dict({}) == {}
