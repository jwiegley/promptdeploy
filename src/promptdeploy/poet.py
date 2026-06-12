"""Parsing and rendering for Prompt Poet (.poet) and related prompt files.

Ports the relevant logic of ``gptel-prompts.el`` to pure Python:

* ``.poet`` / ``.j2`` / ``.jinja`` files combine a YAML body (a list of
  role/content turns) with Jinja2 templating.
* ``.txt`` / ``.md`` / ``.org`` files are plain prompts treated as a single
  system message.
* ``.json`` files are already in the role/content format and not parsed here.

The module exposes two render targets:

* ``render_for_command`` -- structured Markdown suitable for slash-command
  surfaces (Claude Code, OpenCode commands, Droid skills).
* ``render_for_gptel`` -- a JSON array of ``{role, content}`` objects
  consumable by ``gptel-prompts.el``'s built-in JSON handler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import yaml
from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    TemplateSyntaxError,
    Undefined,
)

_VALID_ROLES = frozenset({"system", "user", "assistant"})


@dataclass
class PoetTurn:
    """A single role/content turn in a Poet conversation."""

    role: str
    content: str
    name: Optional[str] = None


@dataclass
class PoetDocument:
    """Parsed Poet document with optional comment-frontmatter metadata."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    turns: List[PoetTurn] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class PoetError(ValueError):
    """Raised when a .poet file cannot be parsed."""


def extract_comment_frontmatter(content: bytes) -> dict[str, Any]:
    """Extract leading ``# key: value`` comment frontmatter from content.

    Reads contiguous comment/blank lines from the very top of the file.
    Lines starting with ``#`` are treated as ``key: value`` pairs (the value
    is YAML-decoded so list and scalar forms both work; an inline value that
    YAML-parses to a mapping — e.g. ``description: Review code: thoroughly``
    — is kept as the raw string instead). The first line that is neither a
    comment nor blank stops the scan.

    Returns an empty dict when no comment frontmatter is present.
    """
    text = content.decode("utf-8", errors="replace")
    metadata: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        # Strip leading '#' plus surrounding whitespace, leaving "key: value".
        body = stripped[1:].strip()
        if not body or ":" not in body:
            continue
        key, _, raw_value = body.partition(":")
        key = key.strip()
        if not key:
            continue
        try:
            value = yaml.safe_load(raw_value.strip()) if raw_value.strip() else None
        except yaml.YAMLError:
            value = raw_value.strip()
        else:
            if isinstance(value, dict):
                # A second ``: `` in an inline scalar makes YAML read it as a
                # nested mapping; comment-frontmatter values are scalars or
                # lists, so keep the raw string rather than silently dropping
                # the value downstream (render_for_command requires a str).
                value = raw_value.strip()
        metadata[key] = value
    return metadata


def _make_undefined_class(missing: List[str]) -> type[Undefined]:
    """Build a Jinja Undefined subclass that records missing names per-render.

    Each render gets its own subclass so the captured ``missing`` list is
    isolated between calls.

    The class supports:

    * ``{{ foo }}`` -- renders as the literal placeholder ``{{ foo }}`` and
      records ``foo`` as missing.
    * ``{{ foo.bar }}`` / ``{{ foo.bar.baz }}`` -- attribute access returns
      a chained instance whose placeholder reflects the full dotted path.
    * ``{% if foo %}...{% endif %}`` -- evaluates falsy AND records a warning
      so the user sees that the conditional block silently disappeared.
    * ``{% for x in foo %}...{% endfor %}`` -- iterates as empty AND records
      a warning so the user sees the loop body was skipped.
    * comparisons, ``in``, ``| length``, ``| int``/``| float`` -- evaluate
      neutrally (False / 0) AND record a warning.
    * calls, subscripts, and arithmetic -- degrade to an undefined value so
      the eventual use (printing, boolean test) records the placeholder.

    Anything not covered here still raises UndefinedError, which
    :func:`_render_template` wraps as :class:`PoetError`.
    """

    class CapturingUndefined(StrictUndefined):
        """Renders missing variables as ``{{ name }}`` and records the name."""

        def _record(self, *, suffix: str = "") -> str:
            name = self._undefined_name or "<unknown>"
            full_name = f"{name}{suffix}"
            if full_name not in missing:
                missing.append(full_name)
            return "{{ " + full_name + " }}"

        def __str__(self) -> str:  # type: ignore[override]
            return self._record()

        def __getattr__(self, attr: str) -> "CapturingUndefined":
            # Skip dunders so internal attribute lookups don't get hijacked.
            if attr.startswith("_"):
                raise AttributeError(attr)
            base_name = self._undefined_name or "<unknown>"
            chained_name = f"{base_name}.{attr}"
            return type(self)(name=chained_name)

        def __iter__(self):  # type: ignore[no-untyped-def]
            # Record with a [] suffix so the user can tell that the missing
            # name was iterated (and the loop body was therefore skipped).
            self._record(suffix=" (iterated, no items)")
            return iter([])

        def __bool__(self) -> bool:  # type: ignore[override]
            # Record with a (?) suffix so the user can tell that the missing
            # name was used in a conditional and the block was skipped.
            self._record(suffix=" (used in conditional, treated as false)")
            return False

        def __eq__(self, other: object) -> bool:  # type: ignore[override]
            self._record(suffix=" (compared, treated as not equal)")
            return False

        def __ne__(self, other: object) -> bool:  # type: ignore[override]
            self._record(suffix=" (compared, treated as not equal)")
            return True

        # Defining __eq__ would otherwise set __hash__ to None; keep the
        # base Undefined hash so the value stays hashable.
        __hash__ = Undefined.__hash__  # type: ignore[assignment]

        def _degrade_comparison(self, other: object) -> bool:
            self._record(suffix=" (compared, treated as false)")
            return False

        __lt__ = __le__ = __gt__ = __ge__ = _degrade_comparison  # type: ignore[assignment]

        def __len__(self) -> int:  # type: ignore[override]
            self._record(suffix=" (length taken, treated as 0)")
            return 0

        def __contains__(self, item: object) -> bool:  # type: ignore[override]
            self._record(suffix=" (membership test, treated as empty)")
            return False

        def __call__(  # type: ignore[override]
            self, *args: object, **kwargs: object
        ) -> "CapturingUndefined":
            # Calling an undefined degrades to the undefined itself so the
            # eventual use of the result records the placeholder.
            return self

        def __getitem__(self, key: object) -> "CapturingUndefined":  # type: ignore[override]
            # Subscripts chain like attribute access, so ``{{ foo['bar'] }}``
            # degrades to the placeholder ``{{ foo['bar'] }}``.
            base_name = self._undefined_name or "<unknown>"
            return type(self)(name=f"{base_name}[{key!r}]")

        def __int__(self) -> int:  # type: ignore[override]
            self._record(suffix=" (coerced to int, treated as 0)")
            return 0

        def __float__(self) -> float:  # type: ignore[override]
            self._record(suffix=" (coerced to float, treated as 0.0)")
            return 0.0

        def _degrade_arithmetic(
            self, *args: object, **kwargs: object
        ) -> "CapturingUndefined":
            # Arithmetic degrades to the undefined itself; the eventual use
            # of the result records the placeholder.
            return self

        __add__ = __radd__ = __sub__ = __rsub__ = _degrade_arithmetic  # type: ignore[assignment]
        __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _degrade_arithmetic  # type: ignore[assignment]
        __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = _degrade_arithmetic  # type: ignore[assignment]
        __pos__ = __neg__ = __pow__ = __rpow__ = _degrade_arithmetic  # type: ignore[assignment]

    return CapturingUndefined


def _render_template(
    body: str,
    *,
    source_path: Optional[Path],
    vars: dict[str, Any],
    missing: List[str],
) -> str:
    loader: Optional[FileSystemLoader] = None
    if source_path is not None:
        loader = FileSystemLoader(str(source_path.parent))
    env = Environment(
        loader=loader,
        undefined=_make_undefined_class(missing),
        autoescape=False,
        keep_trailing_newline=True,
    )
    try:
        template = env.from_string(body)
    except TemplateSyntaxError as exc:
        raise PoetError(f"Jinja syntax error: {exc}") from exc
    try:
        return template.render(**vars)
    except TemplateError as exc:
        # Render-time failures: TemplateNotFound from {% include %}, syntax
        # errors inside included templates, and any UndefinedError from an
        # operation CapturingUndefined does not degrade.
        raise PoetError(f"Jinja render error: {exc}") from exc


def _split_comment_frontmatter(text: str) -> tuple[str, str]:
    """Split text into (comment-block, body) at the first non-comment line."""
    lines = text.splitlines(keepends=True)
    cut = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            cut = i + 1
            continue
        break
    return "".join(lines[:cut]), "".join(lines[cut:])


def parse_poet(
    content: bytes,
    *,
    source_path: Optional[Path] = None,
    vars: Optional[dict[str, Any]] = None,
) -> PoetDocument:
    """Parse a YAML+Jinja Poet file into a :class:`PoetDocument`.

    The leading comment-frontmatter block (if any) is preserved on the
    document but stripped from the Jinja-rendered body. Undefined Jinja
    variables degrade to a literal ``{{ name }}`` placeholder and are
    recorded as warnings rather than raising.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        where = f" in {source_path}" if source_path is not None else ""
        raise PoetError(f"File is not valid UTF-8{where}: {exc}") from exc
    comment_block, body = _split_comment_frontmatter(text)
    frontmatter = extract_comment_frontmatter(comment_block.encode("utf-8"))

    template_vars: dict[str, Any] = {
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if vars:
        template_vars.update(vars)

    missing: List[str] = []
    rendered = _render_template(
        body,
        source_path=source_path,
        vars=template_vars,
        missing=missing,
    )

    try:
        data = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        hint = ""
        if missing:
            # An undefined variable rendered as a literal "{{ name }}"
            # placeholder is frequently what broke the YAML (in unquoted
            # scalar position it parses as a flow mapping); name the
            # variables so the error is actionable.
            hint = (
                "; hint: undefined Jinja variables were rendered as literal"
                f" placeholders and may have produced invalid YAML:"
                f" {', '.join(missing)}"
            )
        raise PoetError(f"Invalid YAML in poet body: {exc}{hint}") from exc

    if data is None:
        turns: List[PoetTurn] = []
    elif not isinstance(data, list):
        raise PoetError("Poet body must be a YAML list of role/content turns")
    else:
        turns = [_convert_turn(entry) for entry in data]

    warnings = [f"Undefined Jinja variable: {name}" for name in missing]
    return PoetDocument(frontmatter=frontmatter, turns=turns, warnings=warnings)


def parse_plain(content: bytes) -> PoetDocument:
    """Wrap a plain text/markdown/org prompt as a single system turn.

    No Jinja rendering is applied. The whole file becomes one
    ``role: system`` turn.
    """
    try:
        text = content.decode("utf-8").rstrip()
    except UnicodeDecodeError as exc:
        raise PoetError(f"File is not valid UTF-8: {exc}") from exc
    turns = [PoetTurn(role="system", content=text)] if text else []
    return PoetDocument(turns=turns)


def _convert_turn(entry: object) -> PoetTurn:
    if not isinstance(entry, dict):
        raise PoetError(
            f"Each poet turn must be a mapping, got: {type(entry).__name__}"
        )
    role = entry.get("role")
    content = entry.get("content")
    if role not in _VALID_ROLES:
        raise PoetError(f"Unknown role {role!r}; expected one of system/user/assistant")
    if not isinstance(content, str):
        raise PoetError("Each poet turn must have a string 'content'")
    name = entry.get("name")
    if name is not None and not isinstance(name, str):
        raise PoetError("'name' field on a poet turn must be a string when present")
    return PoetTurn(role=role, content=content, name=name)


# ----------------------------------------------------------------------
# Slash-command rendering
# ----------------------------------------------------------------------

# Closing tags that must be neutralised in user-supplied content so they
# cannot escape the surrounding XML-ish structure produced by
# render_for_command.  Matched case-insensitively so mixed-case variants
# like ``</User>`` or ``</TASK>`` are also escaped.
_CLOSING_TAG_RE = re.compile(
    r"</(user|assistant|example|examples|instructions|task)>",
    re.IGNORECASE,
)


def _escape_closing_tags(text: str) -> str:
    return _CLOSING_TAG_RE.sub(lambda m: f"&lt;/{m.group(1)}&gt;", text)


def _clean(text: str) -> str:
    """Trim trailing whitespace per line; preserve internal whitespace."""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def render_for_command(
    doc: PoetDocument, *, description: Optional[str] = None
) -> bytes:
    """Render a poet document as a slash-command Markdown file.

    The output structure is::

        ---
        description: ...
        ---
        <instructions>
        ...
        </instructions>

        <examples>
        <example>
        <user>...</user>
        <assistant>...</assistant>
        </example>
        </examples>

        <task>
        $ARGUMENTS
        </task>

    Sections without content are omitted, except ``<task>`` which is always
    emitted so users always have somewhere for input to land. Closing tags
    in user content are HTML-escaped so they cannot break the surrounding
    structure.
    """
    parts: list[str] = []

    effective_description = description
    if effective_description is None:
        fm_desc = doc.frontmatter.get("description")
        if isinstance(fm_desc, str):
            effective_description = fm_desc

    if effective_description:
        # Quote the description as a YAML-safe scalar so values containing
        # ``:``/newlines/quotes don't break the frontmatter.
        # ``yaml.safe_dump`` always emits a valid YAML representation.
        quoted = yaml.safe_dump(
            {"description": effective_description},
            default_flow_style=False,
            allow_unicode=True,
            width=10**9,
        ).rstrip("\n")
        parts.append(f"---\n{quoted}\n---")

    system_turns = [t for t in doc.turns if t.role == "system"]
    dialog_turns = [t for t in doc.turns if t.role in ("user", "assistant")]

    # If the dialog ends with an unpaired user turn (no following assistant),
    # treat that user content as a *task prefix* rather than an example.
    trailing_user: Optional[PoetTurn] = None
    if dialog_turns and dialog_turns[-1].role == "user":
        trailing_user = dialog_turns[-1]
        dialog_turns = dialog_turns[:-1]

    if system_turns:
        joined = "\n\n".join(
            _clean(_escape_closing_tags(t.content)) for t in system_turns
        )
        parts.append(f"<instructions>\n{joined}\n</instructions>")

    if dialog_turns:
        ex_lines: list[str] = ["<examples>", "<example>"]
        for t in dialog_turns:
            tag = "user" if t.role == "user" else "assistant"
            cleaned = _clean(_escape_closing_tags(t.content))
            ex_lines.append(f"<{tag}>\n{cleaned}\n</{tag}>")
        ex_lines.append("</example>")
        ex_lines.append("</examples>")
        parts.append("\n".join(ex_lines))

    if trailing_user is not None:
        prefix = _clean(_escape_closing_tags(trailing_user.content))
        parts.append(f"<task>\n{prefix}\n$ARGUMENTS\n</task>")
    else:
        parts.append("<task>\n$ARGUMENTS\n</task>")

    rendered = "\n\n".join(parts) + "\n"
    return rendered.encode("utf-8")


# ----------------------------------------------------------------------
# gptel JSON rendering
# ----------------------------------------------------------------------


def render_for_gptel(doc: PoetDocument) -> bytes:
    """Render a poet document as the JSON array gptel-prompts.el consumes."""
    payload = []
    for t in doc.turns:
        entry: dict[str, Any] = {"role": t.role, "content": t.content}
        if t.name is not None:
            entry["name"] = t.name
        payload.append(entry)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


# ----------------------------------------------------------------------
# Misc helpers exposed for callers
# ----------------------------------------------------------------------

POET_EXTENSIONS = frozenset({".poet", ".j2", ".jinja", ".jinja2"})
PLAIN_EXTENSIONS = frozenset({".txt", ".md", ".org"})
