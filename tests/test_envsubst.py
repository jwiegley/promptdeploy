"""Tests for environment variable expansion."""

from pathlib import Path

import pytest

from promptdeploy.envsubst import (
    EnvVarError,
    expand_env_vars,
    expand_env_vars_strict,
    find_env_refs,
    load_dotenv,
    read_env_example_keys,
)


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

    def test_unset_var_warns_on_stderr(self, monkeypatch, capsys):
        """Lenient expansion warns about unresolved ${VAR} references."""
        monkeypatch.delenv("UNSET_WARN_XYZ", raising=False)
        assert expand_env_vars("${UNSET_WARN_XYZ}") == "${UNSET_WARN_XYZ}"
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "UNSET_WARN_XYZ" in err

    def test_set_var_emits_no_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("SET_WARN_VAR", "v")
        expand_env_vars("${SET_WARN_VAR}")
        assert capsys.readouterr().err == ""


class TestLoadDotenv:
    def test_basic(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DOTENV_TEST_A", raising=False)
        (tmp_path / ".env").write_text("DOTENV_TEST_A=hello\n")
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ.get("DOTENV_TEST_A") == "hello"
        monkeypatch.delenv("DOTENV_TEST_A")

    def test_skips_comments_and_blanks(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DOTENV_REAL", raising=False)
        (tmp_path / ".env").write_text("# comment\n\n  \nDOTENV_REAL=yes\n")
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ.get("DOTENV_REAL") == "yes"
        monkeypatch.delenv("DOTENV_REAL")

    def test_no_overwrite(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DOTENV_EXISTING", "original")
        (tmp_path / ".env").write_text("DOTENV_EXISTING=replaced\n")
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ["DOTENV_EXISTING"] == "original"

    def test_missing_file(self, tmp_path: Path):
        load_dotenv(tmp_path / "nonexistent")  # should not raise

    def test_quoted_values(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DOTENV_DQ", raising=False)
        monkeypatch.delenv("DOTENV_SQ", raising=False)
        (tmp_path / ".env").write_text(
            "DOTENV_DQ=\"double quoted\"\nDOTENV_SQ='single quoted'\n"
        )
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ.get("DOTENV_DQ") == "double quoted"
        assert os.environ.get("DOTENV_SQ") == "single quoted"
        monkeypatch.delenv("DOTENV_DQ")
        monkeypatch.delenv("DOTENV_SQ")

    def test_skips_lines_without_equals(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DOTENV_GOOD", raising=False)
        (tmp_path / ".env").write_text("no-equals-here\nDOTENV_GOOD=ok\n")
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ.get("DOTENV_GOOD") == "ok"
        monkeypatch.delenv("DOTENV_GOOD")

    def test_empty_key_skipped(self, tmp_path: Path):
        (tmp_path / ".env").write_text("=value\n")
        load_dotenv(tmp_path / ".env")  # should not raise or set empty key

    def test_export_prefix_stripped(self, tmp_path: Path, monkeypatch):
        """Shell-style 'export KEY=value' lines set KEY, not 'export KEY'."""
        monkeypatch.delenv("DOTENV_EXPORTED", raising=False)
        (tmp_path / ".env").write_text("export DOTENV_EXPORTED=val\n")
        load_dotenv(tmp_path / ".env")
        import os

        assert os.environ.get("DOTENV_EXPORTED") == "val"
        assert "export DOTENV_EXPORTED" not in os.environ
        monkeypatch.delenv("DOTENV_EXPORTED")


class TestFindEnvRefs:
    def test_string_with_ref(self):
        assert find_env_refs("${FOO}") == {"FOO"}

    def test_string_without_ref(self):
        assert find_env_refs("plain text") == set()

    def test_multiple_refs_in_one_string(self):
        assert find_env_refs("${A}:${B}") == {"A", "B"}

    def test_nested_dict_and_list(self):
        data = {
            "env": {"KEY": "${ONE}"},
            "args": ["${TWO}", {"deep": "x ${THREE} y"}],
        }
        assert find_env_refs(data) == {"ONE", "TWO", "THREE"}

    def test_non_string_scalars_ignored(self):
        assert find_env_refs({"a": 1, "b": None, "c": True, "d": 2.5}) == set()

    def test_duplicate_refs_deduplicated(self):
        assert find_env_refs(["${X}", {"k": "${X}"}]) == {"X"}

    def test_none_input(self):
        assert find_env_refs(None) == set()


class TestReadEnvExampleKeys:
    def test_missing_file_returns_none(self, tmp_path: Path):
        assert read_env_example_keys(tmp_path / ".env.example") is None

    def test_basic_keys(self, tmp_path: Path):
        path = tmp_path / ".env.example"
        path.write_text("FOO=bar\nBAZ=qux\n")
        assert read_env_example_keys(path) == {"FOO", "BAZ"}

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        path = tmp_path / ".env.example"
        path.write_text("# comment\n\n  \nREAL=yes\n")
        assert read_env_example_keys(path) == {"REAL"}

    def test_export_prefix_stripped(self, tmp_path: Path):
        path = tmp_path / ".env.example"
        path.write_text("export EXPORTED=val\n")
        assert read_env_example_keys(path) == {"EXPORTED"}

    def test_skips_lines_without_equals(self, tmp_path: Path):
        path = tmp_path / ".env.example"
        path.write_text("no-equals-here\nGOOD=ok\n")
        assert read_env_example_keys(path) == {"GOOD"}

    def test_empty_key_skipped(self, tmp_path: Path):
        path = tmp_path / ".env.example"
        path.write_text("=value\n")
        assert read_env_example_keys(path) == set()

    def test_does_not_touch_environ(self, tmp_path: Path, monkeypatch):
        import os

        monkeypatch.delenv("ENV_EXAMPLE_PROBE", raising=False)
        path = tmp_path / ".env.example"
        path.write_text("ENV_EXAMPLE_PROBE=value\n")
        read_env_example_keys(path)
        assert "ENV_EXAMPLE_PROBE" not in os.environ


class TestExpandEnvVarsStrict:
    def test_basic_expansion(self, monkeypatch):
        monkeypatch.setenv("STRICT_VAR", "ok")
        assert expand_env_vars_strict("${STRICT_VAR}") == "ok"

    def test_no_vars_passthrough(self):
        assert expand_env_vars_strict("plain") == "plain"
        assert expand_env_vars_strict("") == ""

    def test_unset_var_raises(self, monkeypatch):
        monkeypatch.delenv("STRICT_MISSING", raising=False)
        with pytest.raises(EnvVarError, match="STRICT_MISSING"):
            expand_env_vars_strict("${STRICT_MISSING}")

    def test_error_includes_context(self, monkeypatch):
        monkeypatch.delenv("STRICT_MISSING", raising=False)
        with pytest.raises(EnvVarError, match="my.location") as info:
            expand_env_vars_strict("${STRICT_MISSING}", context="my.location")
        assert "STRICT_MISSING" in str(info.value)

    def test_error_lists_all_missing_vars(self, monkeypatch):
        monkeypatch.delenv("STRICT_MISS_A", raising=False)
        monkeypatch.delenv("STRICT_MISS_B", raising=False)
        with pytest.raises(EnvVarError) as info:
            expand_env_vars_strict("${STRICT_MISS_A}-${STRICT_MISS_B}")
        msg = str(info.value)
        assert "STRICT_MISS_A" in msg
        assert "STRICT_MISS_B" in msg

    def test_mixed_set_and_unset_raises(self, monkeypatch):
        monkeypatch.setenv("STRICT_HAS", "yes")
        monkeypatch.delenv("STRICT_LACKS", raising=False)
        with pytest.raises(EnvVarError, match="STRICT_LACKS"):
            expand_env_vars_strict("${STRICT_HAS}/${STRICT_LACKS}")
