"""Digest-guarded Ponytail projections for instruction-only GPTel."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from .bundles import BundleSchemaError
from .frontmatter import FrontmatterError, parse_frontmatter
from .ponytail import (
    GPTEL_PRESET_TRANSFORM,
    ONE_SHOT_REVIEW_TRANSFORM,
    PONYTAIL_REVISION,
    PONYTAIL_VERSION,
    STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM,
)


class PonytailTransformError(BundleSchemaError):
    """A pinned Ponytail input cannot be projected without semantic drift."""


@dataclass(frozen=True)
class _TransformGuard:
    byte_count: int
    sha256: str
    metadata_keys: frozenset[str]
    source_h1: str | None
    headings: tuple[str, ...]


@dataclass(frozen=True)
class _RuntimeTransformGuard:
    logical_path: str
    byte_count: int
    sha256: str


_TRANSFORM_GUARDS: dict[str, _TransformGuard] = {
    "ponytail": _TransformGuard(
        6637,
        "sha256:1316a2f3f95741d2300b116fe0c2d81ce4a9568656ed0a62643f54aaf09957f2",
        frozenset({"name", "description", "argument-hint", "license"}),
        "# Ponytail",
        (
            "Persistence",
            "The ladder",
            "Rules",
            "Output",
            "Intensity",
            "When NOT to be lazy",
            "Boundaries",
        ),
    ),
    "ponytail-review": _TransformGuard(
        2383,
        "sha256:40df33b58fc6ef889b93585733feb9566b76e9586efa7f376785c1e995197ac0",
        frozenset({"name", "description"}),
        None,
        ("Format", "Examples", "Scoring", "Boundaries"),
    ),
    "ponytail-audit": _TransformGuard(
        1652,
        "sha256:5560b8e383dbe2ddfddc873a1e2bf2e586e23e0cd7d995537482b2315331f6d1",
        frozenset({"name", "description"}),
        None,
        ("Tags", "Hunt", "Output", "Boundaries"),
    ),
    "ponytail-debt": _TransformGuard(
        1703,
        "sha256:c84fba75f0ca12bfe83f9a78ea02fd125c5dd3f1fbb18124105a489937f284e6",
        frozenset({"name", "description"}),
        None,
        ("Scan", "Output", "Boundaries"),
    ),
    "ponytail-gain": _TransformGuard(
        1973,
        "sha256:24e01d1c9715cb136ba1c4f1e52a95940c0193558b876828e537736480d6408b",
        frozenset({"name", "description"}),
        "# Ponytail Gain",
        ("Scoreboard", "Honesty boundary", "Boundaries"),
    ),
    "ponytail-help": _TransformGuard(
        2796,
        "sha256:2264d1615117b02b0fd5a69ec84cd2757006471a78e4d6c22eed6d581c1d37a4",
        frozenset({"name", "description"}),
        "# Ponytail Help",
        ("Levels", "Skills", "Deactivate", "Configure Default Mode", "Update", "More"),
    ),
}

_RUNTIME_TRANSFORM_GUARDS = {
    STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM: _RuntimeTransformGuard(
        "hooks/ponytail-instructions.js",
        5487,
        "sha256:23c050103f28dbe6bad953ae21d98cd06d720a20f33d4716e9de419f947d495e",
    ),
    ONE_SHOT_REVIEW_TRANSFORM: _RuntimeTransformGuard(
        "hooks/ponytail-mode-tracker.js",
        5318,
        "sha256:5d1a960ff01b73f651ec0242052a8cf1e064cb88147806bf6e13f92798aca251",
    ),
}

_COMMON_SCOPE = (
    "> **GPTel preset scope:** Apply this prompt only to the current invocation.\n"
    "> This is a prompt preset, not a native skill: it provides no lifecycle\n"
    "> activation, persistent mode or mode switching, slash command, subagent\n"
    "> propagation, plugin configuration, or plugin update mechanism."
)

_FULL_INTENSITY = """## Intensity

Apply **full** intensity for this invocation: the ladder is enforced.
Standard-library and native platform features come first. Choose the
shortest correct diff and shortest explanation, but only after understanding
the real flow.

"""

_MAIN_BOUNDARIES = """## Boundaries

Ponytail governs what you build, not how you talk.

The shortest path to done is the right path.
"""

_DEBT_BOUNDARIES = """## Boundaries

Read and report only; change nothing. Return the ledger in the response.
This is a one-invocation preset.
"""

_HELP_OUTPUT = (
    "# Ponytail Help\n\n"
    f"{_COMMON_SCOPE}\n\n"
    "## Available presets\n\n"
    "| GPTel prompt name | Current-invocation behavior |\n"
    "|---|---|\n"
    "| `ponytail` | Full-intensity lazy-senior coding: understand the real flow, "
    "then choose the smallest correct solution. |\n"
    "| `ponytail-review` | Read-only diff review for removable "
    "over-engineering. |\n"
    "| `ponytail-audit` | Read-only whole-repository audit, ranked by the "
    "largest removable complexity. |\n"
    "| `ponytail-debt` | Harvest `ponytail:` markers into a read-only ledger "
    "report. |\n"
    "| `ponytail-gain` | Show the pinned upstream benchmark card without "
    "inventing per-repository savings. |\n"
    "| `ponytail-help` | Show this GPTel-specific catalog and capability "
    "boundary. |\n\n"
    "## Availability\n\n"
    "Select a preset by its GPTel prompt name through the configured\n"
    "`gptel-prompts` interface. Each selection applies only to that invocation;\n"
    "select it again when wanted.\n\n"
    "GPTel receives no Ponytail lifecycle activation, persistent mode or mode\n"
    "switching, slash commands, subagent propagation, plugin configuration, or\n"
    "plugin updates from promptdeploy. Preset content changes only when a later\n"
    "promptdeploy deployment updates the pinned Ponytail bundle; this help preset\n"
    "cannot update it.\n"
)


def _content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _guard_runtime_input(
    source: bytes,
    logical_path: str,
    transform: str,
    *,
    bundle_name: str,
    version: str,
    revision: str,
) -> str:
    if bundle_name != "ponytail":
        raise PonytailTransformError(
            f"{logical_path}: {transform} requires bundle ponytail"
        )
    if version != PONYTAIL_VERSION:
        raise PonytailTransformError(
            f"{logical_path}: {transform} requires ponytail@{PONYTAIL_VERSION}"
        )
    if revision != PONYTAIL_REVISION:
        raise PonytailTransformError(
            f"{logical_path}: {transform} requires revision {PONYTAIL_REVISION}"
        )
    guard = _RUNTIME_TRANSFORM_GUARDS[transform]
    if logical_path != guard.logical_path:
        raise PonytailTransformError(
            f"{logical_path}: {transform} requires input {guard.logical_path}"
        )
    if len(source) != guard.byte_count:
        raise PonytailTransformError(
            f"{logical_path}: guarded input length mismatch; expected "
            f"{guard.byte_count} bytes, got {len(source)}"
        )
    actual = _content_sha256(source)
    if actual != guard.sha256:
        raise PonytailTransformError(
            f"{logical_path}: guarded input digest mismatch; expected "
            f"{guard.sha256}, got {actual}"
        )
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PonytailTransformError(
            f"{logical_path}: {transform} input must be valid UTF-8"
        ) from exc
    if text.startswith("\ufeff"):
        raise PonytailTransformError(
            f"{logical_path}: {transform} input must not start with a UTF-8 BOM"
        )
    if "\r" in text:
        raise PonytailTransformError(
            f"{logical_path}: {transform} input must use LF line endings"
        )
    if not text.endswith("\n"):
        raise PonytailTransformError(
            f"{logical_path}: {transform} input must end with an LF"
        )
    return text


def _replace_once(
    text: str,
    old: str,
    new: str,
    *,
    logical_path: str,
    description: str,
) -> str:
    if text.count(old) != 1:
        raise PonytailTransformError(
            f"{logical_path}: expected exactly one {description}"
        )
    return text.replace(old, new)


def _remove_between_once(
    text: str,
    start: str,
    end: str,
    *,
    logical_path: str,
    description: str,
) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise PonytailTransformError(
            f"{logical_path}: expected exactly one {description}"
        )
    prefix, remainder = text.split(start, 1)
    _removed, suffix = remainder.split(end, 1)
    return prefix + end + suffix


def render_strict_canonical_instructions_v1(
    source: bytes,
    *,
    bundle_name: str,
    version: str,
    revision: str,
    logical_path: str,
) -> bytes:
    """Remove Ponytail's embedded rules fallback from the reviewed runtime."""
    text = _guard_runtime_input(
        source,
        logical_path,
        STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM,
        bundle_name=bundle_name,
        version=version,
        revision=revision,
    )
    text = _remove_between_once(
        text,
        "\nfunction getFallbackInstructions(mode) {",
        "\nfunction getPonytailInstructions(mode) {",
        logical_path=logical_path,
        description="embedded fallback function",
    )
    text = _replace_once(
        text,
        """  try {
    return 'PONYTAIL MODE ACTIVE — level: ' + effectiveMode + '\\n\\n' +
      filterSkillBodyForMode(fs.readFileSync(SKILL_PATH, 'utf8'), effectiveMode);
  } catch (e) {
    return getFallbackInstructions(effectiveMode);
  }
""",
        """  return 'PONYTAIL MODE ACTIVE — level: ' + effectiveMode + '\\n\\n' +
    filterSkillBodyForMode(fs.readFileSync(SKILL_PATH, 'utf8'), effectiveMode);
""",
        logical_path=logical_path,
        description="fallback try/catch",
    )
    text = _replace_once(
        text,
        "  getFallbackInstructions,\n",
        "",
        logical_path=logical_path,
        description="fallback export",
    )
    if "getFallbackInstructions" in text:
        raise PonytailTransformError(
            f"{logical_path}: embedded fallback reference survived transform"
        )
    return text.encode("utf-8")


def render_one_shot_review_v1(
    source: bytes,
    *,
    bundle_name: str,
    version: str,
    revision: str,
    logical_path: str,
) -> bytes:
    """Handle Ponytail review as one invocation without persisting its mode."""
    text = _guard_runtime_input(
        source,
        logical_path,
        ONE_SHOT_REVIEW_TRANSFORM,
        bundle_name=bundle_name,
        version=version,
        revision=revision,
    )
    old_branch = (
        "      if (cmd === '/ponytail-review' || "
        "cmd === '/ponytail:ponytail-review') {\n"
        "        mode = 'review';\n"
        "      } else if (cmd === '/ponytail' || "
        "cmd === '/ponytail:ponytail') {\n"
    )
    new_branch = (
        "      if (cmd === '/ponytail-review' || "
        "cmd === '/ponytail:ponytail-review') {\n"
        "        writeHookOutput(\n"
        "          'UserPromptSubmit',\n"
        "          'review',\n"
        "          getPonytailInstructions('review'),\n"
        "        );\n"
        "        return;\n"
        "      } else if (cmd === '/ponytail' || "
        "cmd === '/ponytail:ponytail') {\n"
    )
    return _replace_once(
        text,
        old_branch,
        new_branch,
        logical_path=logical_path,
        description="persistent review branch",
    ).encode("utf-8")


def _split_sections(
    text: str,
    *,
    logical_path: str,
    source_h1: str | None,
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    first_start = starts[0] if starts else len(lines)
    preamble = "".join(lines[:first_start])
    h1_lines = [
        line.rstrip("\n") for line in lines[:first_start] if line.startswith("# ")
    ]
    if source_h1 is None:
        if h1_lines:
            raise PonytailTransformError(
                f"{logical_path}: source preamble must not contain an H1"
            )
    elif not lines or lines[0].rstrip("\n") != source_h1 or h1_lines != [source_h1]:
        raise PonytailTransformError(
            f"{logical_path}: source must begin with exactly {source_h1!r}"
        )
    sections: dict[str, str] = {}
    order: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        heading = lines[start][3:].rstrip("\n")
        if heading in sections:
            raise PonytailTransformError(
                f"{logical_path}: duplicate H2 heading {heading!r}"
            )
        order.append(heading)
        sections[heading] = "".join(lines[start:end])
    return preamble, tuple(order), sections


def _scope_after_h1(preamble: str, *, expected_h1: str, logical_path: str) -> str:
    first, separator, remainder = preamble.partition("\n")
    if first != expected_h1 or not separator:
        raise PonytailTransformError(
            f"{logical_path}: expected exact H1 {expected_h1!r}"
        )
    return f"{first}\n\n{_COMMON_SCOPE}\n\n{remainder.lstrip(chr(10))}"


def _drop_exact_final_line(section: str, line: str, *, logical_path: str) -> str:
    suffix = f"{line}\n"
    if not section.endswith(suffix):
        raise PonytailTransformError(
            f"{logical_path}: expected exact removable boundary line {line!r}"
        )
    return section[: -len(suffix)].rstrip("\n") + "\n"


def _one_final_newline(text: str) -> bytes:
    return (text.rstrip("\n") + "\n").encode("utf-8")


def _replace_exactly_once(
    text: str,
    old: str,
    new: str,
    *,
    logical_path: str,
    section: str,
) -> str:
    if text.count(old) != 1:
        raise PonytailTransformError(
            f"{logical_path}: {section} must contain exactly one {old!r}"
        )
    return text.replace(old, new)


def render_gptel_preset_v1(
    name: str,
    source: bytes,
    *,
    bundle_name: str,
    version: str,
    revision: str,
    logical_path: str,
) -> bytes:
    """Render one reviewed semantic projection, with no unguarded fallback."""
    guard = _TRANSFORM_GUARDS.get(name)
    if guard is None:
        raise PonytailTransformError(
            f"{logical_path}: unsupported preset name {name!r}"
        )
    if bundle_name != "ponytail":
        raise PonytailTransformError(
            f"{logical_path}: {GPTEL_PRESET_TRANSFORM} requires bundle ponytail"
        )
    if version != PONYTAIL_VERSION:
        raise PonytailTransformError(
            f"{logical_path}: {GPTEL_PRESET_TRANSFORM} requires "
            f"ponytail@{PONYTAIL_VERSION}"
        )
    if revision != PONYTAIL_REVISION:
        raise PonytailTransformError(
            f"{logical_path}: {GPTEL_PRESET_TRANSFORM} requires revision "
            f"{PONYTAIL_REVISION}"
        )
    if logical_path != f"skills/{name}/SKILL.md":
        raise PonytailTransformError(f"{logical_path}: unexpected guarded input path")
    actual = _content_sha256(source)
    if len(source) != guard.byte_count:
        raise PonytailTransformError(
            f"{logical_path}: guarded input length mismatch; expected "
            f"{guard.byte_count} bytes, got {len(source)}"
        )
    if actual != guard.sha256:
        raise PonytailTransformError(
            f"{logical_path}: guarded input digest mismatch; expected "
            f"{guard.sha256}, got {actual}"
        )
    if b"\r" in source:
        raise PonytailTransformError(
            f"{logical_path}: guarded input must use LF line endings"
        )
    try:
        metadata, body = parse_frontmatter(source)
    except FrontmatterError as exc:
        raise PonytailTransformError(
            f"{logical_path}: invalid frontmatter: {exc}"
        ) from exc
    if not isinstance(metadata, dict):
        raise PonytailTransformError(f"{logical_path}: frontmatter mapping is required")
    if set(metadata) != guard.metadata_keys:
        raise PonytailTransformError(
            f"{logical_path}: frontmatter keys must be {sorted(guard.metadata_keys)!r}"
        )
    if metadata.get("name") != name:
        raise PonytailTransformError(
            f"{logical_path}: frontmatter name must be {name!r}"
        )
    body_text = body.decode("utf-8")
    preamble, order, sections = _split_sections(
        body_text,
        logical_path=logical_path,
        source_h1=guard.source_h1,
    )
    if order != guard.headings:
        raise PonytailTransformError(
            f"{logical_path}: H2 sequence must be {guard.headings!r}, got {order!r}"
        )

    if name == "ponytail":
        rendered = (
            _scope_after_h1(
                preamble,
                expected_h1="# Ponytail",
                logical_path=logical_path,
            )
            + sections["The ladder"]
            + sections["Rules"]
            + sections["Output"]
            + _FULL_INTENSITY
            + sections["When NOT to be lazy"]
            + _MAIN_BOUNDARIES
        )
    elif name == "ponytail-review":
        rendered = (
            f"# Ponytail Review\n\n{_COMMON_SCOPE}\n\n"
            + preamble
            + sections["Format"]
            + sections["Examples"]
            + sections["Scoring"]
            + _drop_exact_final_line(
                sections["Boundaries"],
                (
                    '"stop ponytail-review" or "normal mode": revert to '
                    "verbose review style."
                ),
                logical_path=logical_path,
            )
        )
    elif name == "ponytail-audit":
        rendered = (
            f"# Ponytail Audit\n\n{_COMMON_SCOPE}\n\n"
            + preamble
            + sections["Tags"]
            + sections["Hunt"]
            + sections["Output"]
            + _drop_exact_final_line(
                sections["Boundaries"],
                '"stop ponytail-audit" or "normal mode" to revert.',
                logical_path=logical_path,
            )
        )
    elif name == "ponytail-debt":
        rendered = (
            f"# Ponytail Debt\n\n{_COMMON_SCOPE}\n\n"
            + preamble
            + sections["Scan"]
            + sections["Output"]
            + _DEBT_BOUNDARIES
        )
    elif name == "ponytail-gain":
        scoreboard = _replace_exactly_once(
            _replace_exactly_once(
                sections["Scoreboard"],
                "/ponytail-debt",
                "Ponytail Debt preset",
                logical_path=logical_path,
                section="Scoreboard",
            ),
            "/ponytail-audit",
            "Ponytail Audit preset",
            logical_path=logical_path,
            section="Scoreboard",
        )
        honesty = _replace_exactly_once(
            sections["Honesty boundary"],
            "/ponytail-debt",
            "Ponytail Debt preset",
            logical_path=logical_path,
            section="Honesty boundary",
        )
        rendered = (
            _scope_after_h1(
                preamble,
                expected_h1="# Ponytail Gain",
                logical_path=logical_path,
            )
            + scoreboard
            + honesty
            + _drop_exact_final_line(
                sections["Boundaries"],
                '"stop ponytail" or "normal mode": revert.',
                logical_path=logical_path,
            )
        )
    else:
        rendered = _HELP_OUTPUT
    return _one_final_newline(rendered)


PONYTAIL_TRANSFORMS = {GPTEL_PRESET_TRANSFORM: render_gptel_preset_v1}
PONYTAIL_RUNTIME_TRANSFORMS: dict[str, Callable[..., bytes]] = {
    STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM: render_strict_canonical_instructions_v1,
    ONE_SHOT_REVIEW_TRANSFORM: render_one_shot_review_v1,
}
