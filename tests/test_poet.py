"""Tests for the poet module: parsing and rendering of .poet files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptdeploy.poet import (
    PLAIN_EXTENSIONS,
    POET_EXTENSIONS,
    PoetDocument,
    PoetError,
    PoetTurn,
    extract_comment_frontmatter,
    parse_plain,
    parse_poet,
    render_for_command,
    render_for_gptel,
)


class TestExtractCommentFrontmatter:
    def test_empty_content(self):
        assert extract_comment_frontmatter(b"") == {}

    def test_no_comments(self):
        assert extract_comment_frontmatter(b"- role: system\n") == {}

    def test_single_key(self):
        result = extract_comment_frontmatter(b"# description: hello\n- role: system\n")
        assert result == {"description": "hello"}

    def test_multiple_keys(self):
        result = extract_comment_frontmatter(
            b"# name: demo\n# description: a thing\n- role: system\n"
        )
        assert result == {"name": "demo", "description": "a thing"}

    def test_list_value(self):
        result = extract_comment_frontmatter(b"# only: [claude, opencode]\n")
        assert result == {"only": ["claude", "opencode"]}

    def test_blank_lines_between_comments(self):
        result = extract_comment_frontmatter(b"# a: 1\n\n# b: 2\n- role: system\n")
        assert result == {"a": 1, "b": 2}

    def test_stops_at_first_non_comment(self):
        result = extract_comment_frontmatter(b"# a: 1\n- role: system\n# b: ignored\n")
        assert result == {"a": 1}

    def test_ignores_comment_without_colon(self):
        result = extract_comment_frontmatter(b"# just a comment\n# real: value\n")
        assert result == {"real": "value"}

    def test_ignores_empty_key(self):
        result = extract_comment_frontmatter(b"#  : value\n# real: ok\n")
        assert result == {"real": "ok"}

    def test_key_with_no_value(self):
        result = extract_comment_frontmatter(b"# bare:\n")
        assert result == {"bare": None}

    def test_invalid_yaml_value_kept_as_string(self):
        # ``[broken`` is not valid YAML; the parser falls back to the
        # unparsed string instead of raising.
        result = extract_comment_frontmatter(b"# crash: [broken\n")
        assert result == {"crash": "[broken"}


class TestParsePoet:
    def test_well_formed(self):
        content = (
            b"- role: system\n  content: 'Hello'\n"
            b"- role: user\n  content: 'How are you?'\n"
        )
        doc = parse_poet(content)
        assert len(doc.turns) == 2
        assert doc.turns[0] == PoetTurn(role="system", content="Hello")
        assert doc.turns[1].role == "user"
        assert doc.warnings == []

    def test_empty_body_yields_no_turns(self):
        doc = parse_poet(b"")
        assert doc.turns == []

    def test_with_frontmatter(self):
        content = (
            b"# description: Translator\n# only: [claude]\n"
            b"- role: system\n  content: x\n"
        )
        doc = parse_poet(content)
        assert doc.frontmatter == {"description": "Translator", "only": ["claude"]}
        assert len(doc.turns) == 1

    def test_malformed_yaml_raises(self):
        with pytest.raises(PoetError, match="Invalid YAML"):
            parse_poet(b"- role: system\n  content: [unclosed\n")

    def test_non_list_yaml_raises(self):
        with pytest.raises(PoetError, match="must be a YAML list"):
            parse_poet(b"role: system\ncontent: x\n")

    def test_non_mapping_turn_raises(self):
        with pytest.raises(PoetError, match="must be a mapping"):
            parse_poet(b"- just a string\n")

    def test_unknown_role_raises(self):
        with pytest.raises(PoetError, match="Unknown role"):
            parse_poet(b"- role: bogus\n  content: x\n")

    def test_missing_content_raises(self):
        with pytest.raises(PoetError, match="string 'content'"):
            parse_poet(b"- role: system\n")

    def test_invalid_name_raises(self):
        with pytest.raises(PoetError, match="'name' field"):
            parse_poet(b"- role: system\n  content: x\n  name: 42\n")

    def test_jinja_current_time_substituted(self):
        content = b"- role: system\n  content: 'time {{ current_time }}'\n"
        doc = parse_poet(content)
        # Should contain a 4-digit year
        assert "time " in doc.turns[0].content
        assert any(c.isdigit() for c in doc.turns[0].content)
        assert "{{" not in doc.turns[0].content

    def test_jinja_undefined_var_records_warning(self):
        content = b"- role: system\n  content: 'hi {{ missing }}'\n"
        doc = parse_poet(content)
        assert doc.warnings == ["Undefined Jinja variable: missing"]
        # Placeholder is preserved literally so user can see what's missing.
        assert "{{ missing }}" in doc.turns[0].content

    def test_jinja_duplicate_undefined_var_recorded_once(self):
        # Referencing the same missing variable twice keeps both literal
        # placeholders but reports the name only once.
        content = b"- role: system\n  content: 'a {{ missing }} b {{ missing }}'\n"
        doc = parse_poet(content)
        assert doc.warnings == ["Undefined Jinja variable: missing"]
        assert doc.turns[0].content == "a {{ missing }} b {{ missing }}"

    def test_jinja_user_supplied_vars(self):
        content = b"- role: system\n  content: 'hi {{ who }}'\n"
        doc = parse_poet(content, vars={"who": "world"})
        assert doc.turns[0].content == "hi world"

    def test_jinja_syntax_error_raises(self):
        with pytest.raises(PoetError, match="Jinja syntax error"):
            parse_poet(b"- role: system\n  content: '{% bad %}'\n")

    def test_leading_blank_lines_before_comments(self):
        # Blank lines preceding comment block are skipped, then comments
        # are read as frontmatter.
        content = b"\n\n# description: leading blanks\n- role: system\n  content: x\n"
        doc = parse_poet(content)
        assert doc.frontmatter == {"description": "leading blanks"}

    def test_jinja_extends_via_filesystem(self, tmp_path: Path):
        base = tmp_path / "base.j2"
        base.write_bytes(b"- role: system\n  content: 'BASE'\n")
        child = tmp_path / "child.poet"
        child.write_bytes(b"{% include 'base.j2' %}\n- role: user\n  content: 'X'\n")
        doc = parse_poet(child.read_bytes(), source_path=child)
        assert len(doc.turns) == 2
        assert doc.turns[0].content == "BASE"
        assert doc.turns[1].role == "user"

    def test_name_field_preserved(self):
        content = b"- role: system\n  content: x\n  name: greeting\n"
        doc = parse_poet(content)
        assert doc.turns[0].name == "greeting"

    def test_undefined_iter_and_bool(self):
        # A bare reference where the value is iterated over still records
        # the missing name and doesn't raise.
        content = (
            b"- role: system\n"
            b'  content: "{% for x in maybe %}{{ x }}{% endfor %}'
            b'|{% if maybe %}y{% endif %}"\n'
        )
        doc = parse_poet(content)
        assert any("maybe" in w for w in doc.warnings)
        # Iterating an undefined value should annotate the warning.
        assert any("iterated" in w for w in doc.warnings)
        # Using an undefined value in a conditional should annotate too.
        assert any("conditional" in w for w in doc.warnings)

    def test_undefined_attribute_chain(self):
        # ``{{ foo.bar }}`` should render as ``{{ foo.bar }}`` (preserved
        # placeholder reflecting the dotted path) and record ``foo.bar`` as
        # missing.
        content = b"- role: system\n  content: 'a {{ foo.bar }} b'\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "a {{ foo.bar }} b"
        assert "Undefined Jinja variable: foo.bar" in doc.warnings

    def test_undefined_attribute_chain_deep(self):
        # ``{{ foo.bar.baz }}`` should chain attribute accesses.
        content = b"- role: system\n  content: 'x {{ foo.bar.baz }} y'\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "x {{ foo.bar.baz }} y"
        assert "Undefined Jinja variable: foo.bar.baz" in doc.warnings

    def test_undefined_dunder_attr_raises(self):
        # Internal/dunder attribute access on a CapturingUndefined must
        # raise AttributeError (not be absorbed) so jinja's own machinery
        # can probe for hooks like ``__html__`` without getting a chained
        # placeholder back.
        from promptdeploy.poet import _make_undefined_class

        cls = _make_undefined_class([])
        u = cls(name="foo")
        with pytest.raises(AttributeError):
            _ = u._private


class TestParsePlain:
    def test_simple(self):
        doc = parse_plain(b"This is a system prompt.\n")
        assert len(doc.turns) == 1
        assert doc.turns[0].role == "system"
        assert doc.turns[0].content == "This is a system prompt."

    def test_empty_yields_no_turns(self):
        doc = parse_plain(b"")
        assert doc.turns == []

    def test_whitespace_only_yields_no_turns(self):
        doc = parse_plain(b"   \n\n")
        assert doc.turns == []


class TestRenderForCommand:
    def test_system_only(self):
        doc = PoetDocument(turns=[PoetTurn(role="system", content="Be terse.")])
        out = render_for_command(doc).decode("utf-8")
        assert "<instructions>\nBe terse.\n</instructions>" in out
        assert "<examples>" not in out
        assert "<task>\n$ARGUMENTS\n</task>" in out

    def test_user_assistant_only(self):
        doc = PoetDocument(
            turns=[
                PoetTurn(role="user", content="Hi"),
                PoetTurn(role="assistant", content="Hello"),
            ]
        )
        out = render_for_command(doc).decode("utf-8")
        assert "<instructions>" not in out
        assert "<examples>" in out
        assert "<user>\nHi\n</user>" in out
        assert "<assistant>\nHello\n</assistant>" in out

    def test_full_combined(self):
        doc = PoetDocument(
            turns=[
                PoetTurn(role="system", content="A"),
                PoetTurn(role="system", content="B"),
                PoetTurn(role="user", content="Q"),
                PoetTurn(role="assistant", content="R"),
            ]
        )
        out = render_for_command(doc).decode("utf-8")
        assert "<instructions>\nA\n\nB\n</instructions>" in out
        assert "<user>\nQ\n</user>" in out
        assert "<assistant>\nR\n</assistant>" in out

    def test_description_from_frontmatter(self):
        doc = PoetDocument(
            frontmatter={"description": "Demo"},
            turns=[PoetTurn(role="system", content="x")],
        )
        out = render_for_command(doc).decode("utf-8")
        assert out.startswith("---\ndescription: Demo\n---\n")

    def test_description_explicit_overrides_frontmatter(self):
        doc = PoetDocument(
            frontmatter={"description": "From FM"},
            turns=[PoetTurn(role="system", content="x")],
        )
        out = render_for_command(doc, description="Override").decode("utf-8")
        assert "description: Override" in out
        assert "From FM" not in out

    def test_no_description_no_frontmatter_block(self):
        doc = PoetDocument(turns=[PoetTurn(role="system", content="x")])
        out = render_for_command(doc).decode("utf-8")
        assert not out.startswith("---")

    def test_non_string_frontmatter_description_skipped(self):
        doc = PoetDocument(
            frontmatter={"description": ["not", "a", "string"]},
            turns=[PoetTurn(role="system", content="x")],
        )
        out = render_for_command(doc).decode("utf-8")
        assert not out.startswith("---")

    def test_closing_tag_escaping(self):
        doc = PoetDocument(
            turns=[
                PoetTurn(role="user", content="break: </user> here"),
                PoetTurn(role="assistant", content="and </assistant>"),
            ]
        )
        out = render_for_command(doc).decode("utf-8")
        assert "&lt;/user&gt;" in out
        assert "&lt;/assistant&gt;" in out
        # The wrapping tags must remain intact.
        assert out.count("<user>") == 1
        assert out.count("</user>") == 1

    def test_trailing_whitespace_trimmed(self):
        doc = PoetDocument(
            turns=[PoetTurn(role="system", content="line1   \nline2\t  \n")]
        )
        out = render_for_command(doc).decode("utf-8")
        assert "line1   " not in out
        assert "line2\t  " not in out
        assert "line1\nline2" in out

    def test_no_turns_still_emits_task(self):
        doc = PoetDocument(turns=[])
        out = render_for_command(doc).decode("utf-8")
        assert "<task>\n$ARGUMENTS\n</task>" in out

    def test_trailing_user_alone_pulled_into_task(self):
        # A single trailing user turn with no preceding pair becomes the
        # task prefix; ``<examples>`` is omitted entirely.
        doc = PoetDocument(
            turns=[PoetTurn(role="user", content="Translate this:")],
        )
        out = render_for_command(doc).decode("utf-8")
        assert "<examples>" not in out
        assert "<task>\nTranslate this:\n$ARGUMENTS\n</task>" in out

    def test_trailing_user_after_full_pair(self):
        # A full user/assistant pair stays in <examples>; the unpaired
        # trailing user joins <task> as a prefix.
        doc = PoetDocument(
            turns=[
                PoetTurn(role="user", content="Q1"),
                PoetTurn(role="assistant", content="A1"),
                PoetTurn(role="user", content="Now do this:"),
            ],
        )
        out = render_for_command(doc).decode("utf-8")
        assert "<examples>" in out
        assert "<user>\nQ1\n</user>" in out
        assert "<assistant>\nA1\n</assistant>" in out
        assert "<task>\nNow do this:\n$ARGUMENTS\n</task>" in out
        # The trailing user must NOT also appear as an example.
        assert out.count("<user>") == 1

    def test_dialog_ending_with_assistant_unchanged(self):
        # When the dialog ends with an assistant turn, all dialog goes
        # into examples and <task> contains only $ARGUMENTS.
        doc = PoetDocument(
            turns=[
                PoetTurn(role="user", content="Q"),
                PoetTurn(role="assistant", content="A"),
            ],
        )
        out = render_for_command(doc).decode("utf-8")
        assert "<task>\n$ARGUMENTS\n</task>" in out
        assert "<user>\nQ\n</user>" in out
        assert "<assistant>\nA\n</assistant>" in out

    def test_spanish_poet_example(self):
        # Smoke test against the canonical spanish.poet shape.
        doc = PoetDocument(
            turns=[
                PoetTurn(
                    role="system",
                    content="You are a translator.",
                ),
                PoetTurn(
                    role="user",
                    content="Please translate the following into Spanish:",
                ),
            ],
        )
        out = render_for_command(doc).decode("utf-8")
        assert (
            "<task>\nPlease translate the following into Spanish:\n$ARGUMENTS\n</task>"
        ) in out

    def test_uppercase_closing_tag_escaped(self):
        doc = PoetDocument(
            turns=[
                PoetTurn(role="user", content="break: </USER> here"),
                PoetTurn(role="assistant", content="and </Assistant>"),
            ],
        )
        out = render_for_command(doc).decode("utf-8")
        assert "&lt;/USER&gt;" in out
        assert "&lt;/Assistant&gt;" in out

    def test_description_with_colon_quoted_safely(self):
        doc = PoetDocument(
            frontmatter={"description": "warn: be careful"},
            turns=[PoetTurn(role="system", content="x")],
        )
        out = render_for_command(doc).decode("utf-8")
        # The description is YAML-quoted so the colon doesn't break the
        # frontmatter mapping when the file is re-read.
        import yaml

        # The first --- block should round-trip through yaml.safe_load.
        parts = out.split("---\n", 2)
        assert len(parts) >= 3
        loaded = yaml.safe_load(parts[1])
        assert loaded == {"description": "warn: be careful"}

    def test_description_with_newline_quoted_safely(self):
        doc = PoetDocument(
            frontmatter={"description": "line1\nline2"},
            turns=[PoetTurn(role="system", content="x")],
        )
        out = render_for_command(doc).decode("utf-8")
        import yaml

        parts = out.split("---\n", 2)
        loaded = yaml.safe_load(parts[1])
        assert loaded == {"description": "line1\nline2"}


class TestRenderForGptel:
    def test_system_only(self):
        doc = PoetDocument(turns=[PoetTurn(role="system", content="Hi")])
        data = json.loads(render_for_gptel(doc))
        assert data == [{"role": "system", "content": "Hi"}]

    def test_multi_turn(self):
        doc = PoetDocument(
            turns=[
                PoetTurn(role="system", content="S"),
                PoetTurn(role="user", content="Q"),
                PoetTurn(role="assistant", content="R"),
            ]
        )
        data = json.loads(render_for_gptel(doc))
        assert data == [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "R"},
        ]

    def test_name_preserved(self):
        doc = PoetDocument(
            turns=[PoetTurn(role="system", content="x", name="greeting")]
        )
        data = json.loads(render_for_gptel(doc))
        assert data == [{"role": "system", "content": "x", "name": "greeting"}]

    def test_trailing_newline_present(self):
        doc = PoetDocument(turns=[PoetTurn(role="system", content="x")])
        out = render_for_gptel(doc)
        assert out.endswith(b"\n")


class TestModuleConstants:
    def test_extension_sets_disjoint(self):
        assert POET_EXTENSIONS.isdisjoint(PLAIN_EXTENSIONS)


class TestUndefinedDegradation:
    """Undefined Jinja variables degrade (recording a warning) for the
    operations the docstring promises, instead of raising UndefinedError."""

    def test_equality_comparison_degrades(self):
        content = b"- role: system\n  content: \"{% if foo == 'x' %}eq{% endif %}ok\"\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "ok"
        assert any("foo" in w for w in doc.warnings)

    def test_inequality_comparison_degrades(self):
        content = b"- role: system\n  content: \"{% if foo != 'x' %}ne{% endif %}ok\"\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "neok"
        assert any("foo" in w for w in doc.warnings)

    def test_ordering_comparison_degrades(self):
        content = b'- role: system\n  content: "{% if foo > 1 %}gt{% endif %}ok"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "ok"
        assert any("foo" in w for w in doc.warnings)

    def test_length_filter_degrades(self):
        content = b'- role: system\n  content: "len {{ foo | length }}"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "len 0"
        assert any("foo" in w for w in doc.warnings)

    def test_call_degrades_to_placeholder(self):
        content = b'- role: system\n  content: "x {{ foo() }} y"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "x {{ foo }} y"
        assert any("foo" in w for w in doc.warnings)

    def test_subscript_degrades_to_chained_placeholder(self):
        content = b"- role: system\n  content: \"a {{ foo['bar'] }} b\"\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "a {{ foo['bar'] }} b"
        assert any("foo['bar']" in w for w in doc.warnings)

    def test_arithmetic_degrades_to_placeholder(self):
        content = b'- role: system\n  content: "n {{ foo + 1 }} m"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "n {{ foo }} m"
        assert any("foo" in w for w in doc.warnings)

    def test_containment_degrades(self):
        content = b"- role: system\n  content: \"{% if 'x' in foo %}in{% endif %}ok\"\n"
        doc = parse_poet(content)
        assert doc.turns[0].content == "ok"
        assert any("foo" in w for w in doc.warnings)

    def test_int_coercion_degrades_to_zero(self):
        content = b'- role: system\n  content: "i {{ foo | int }}"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "i 0"
        assert any("foo" in w for w in doc.warnings)

    def test_float_coercion_degrades_to_zero(self):
        content = b'- role: system\n  content: "f {{ foo | float }}"\n'
        doc = parse_poet(content)
        assert doc.turns[0].content == "f 0.0"
        assert any("foo" in w for w in doc.warnings)

    def test_undefined_is_hashable(self):
        from promptdeploy.poet import _make_undefined_class

        cls = _make_undefined_class([])
        assert isinstance(hash(cls(name="foo")), int)


class TestRenderTimeErrors:
    def test_include_missing_template_raises_poet_error(self, tmp_path: Path):
        child = tmp_path / "child.poet"
        child.write_bytes(b"{% include 'missing.j2' %}\n")
        with pytest.raises(PoetError, match="Jinja render error"):
            parse_poet(child.read_bytes(), source_path=child)

    def test_syntax_error_in_included_template_raises_poet_error(self, tmp_path: Path):
        (tmp_path / "broken.j2").write_bytes(b"{% bad %}\n")
        child = tmp_path / "child.poet"
        child.write_bytes(b"{% include 'broken.j2' %}\n")
        with pytest.raises(PoetError, match="Jinja render error"):
            parse_poet(child.read_bytes(), source_path=child)


class TestColonInCommentFrontmatterValue:
    def test_inline_value_with_colon_kept_as_string(self):
        # "Review code: thoroughly" YAML-parses to a mapping; the raw string
        # must be preserved instead of silently dropping the description.
        result = extract_comment_frontmatter(
            b"# description: Review code: thoroughly\n"
        )
        assert result == {"description": "Review code: thoroughly"}

    def test_colon_description_survives_render_for_command(self):
        content = (
            b"# description: Review code: thoroughly\n- role: system\n  content: x\n"
        )
        doc = parse_poet(content)
        out = render_for_command(doc).decode("utf-8")
        assert "Review code: thoroughly" in out


class TestNonUtf8Prompts:
    def test_parse_poet_non_utf8_raises_poet_error(self):
        with pytest.raises(PoetError, match="not valid UTF-8"):
            parse_poet(b"- role: system\n  content: \xff\xfe\n")

    def test_parse_poet_non_utf8_names_source_path(self, tmp_path: Path):
        path = tmp_path / "bad.poet"
        with pytest.raises(PoetError, match=r"bad\.poet"):
            parse_poet(b"\xff\xfe", source_path=path)

    def test_parse_plain_non_utf8_raises_poet_error(self):
        with pytest.raises(PoetError, match="not valid UTF-8"):
            parse_plain(b"plain \xff\xfe text\n")


class TestYamlErrorUndefinedHint:
    def test_yaml_error_names_undefined_variables(self):
        # `content: {{ foo }}` with foo undefined renders to a literal
        # placeholder that YAML reads as a flow mapping with a dict key;
        # the error must name the undefined variable as the likely cause.
        content = b"- role: system\n  content: {{ foo }}\n"
        with pytest.raises(PoetError, match="foo"):
            parse_poet(content)

    def test_yaml_error_without_undefined_vars_has_no_hint(self):
        with pytest.raises(PoetError) as exc_info:
            parse_poet(b"- role: system\n  content: [broken\n")
        assert "undefined Jinja variable" not in str(exc_info.value)
