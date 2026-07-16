"""Pinned byte and semantic goldens for Ponytail GPTel projections."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from promptdeploy import ponytail_transforms as transforms
from promptdeploy.bundles import BundleSchemaError
from promptdeploy.ponytail import (
    GPTEL_PRESET_TRANSFORM,
    PONYTAIL_NAMES,
    PONYTAIL_REVISION,
    PONYTAIL_VERSION,
)

INPUTS = {
    "ponytail": (
        6637,
        "1316a2f3f95741d2300b116fe0c2d81ce4a9568656ed0a62643f54aaf09957f2",
    ),
    "ponytail-review": (
        2383,
        "40df33b58fc6ef889b93585733feb9566b76e9586efa7f376785c1e995197ac0",
    ),
    "ponytail-audit": (
        1652,
        "5560b8e383dbe2ddfddc873a1e2bf2e586e23e0cd7d995537482b2315331f6d1",
    ),
    "ponytail-debt": (
        1703,
        "c84fba75f0ca12bfe83f9a78ea02fd125c5dd3f1fbb18124105a489937f284e6",
    ),
    "ponytail-gain": (
        1973,
        "24e01d1c9715cb136ba1c4f1e52a95940c0193558b876828e537736480d6408b",
    ),
    "ponytail-help": (
        2796,
        "2264d1615117b02b0fd5a69ec84cd2757006471a78e4d6c22eed6d581c1d37a4",
    ),
}
OUTPUTS = {
    "ponytail": (
        5081,
        "a0a605617dbc7e70db2a99096464cb308f4a76109e86912d53db38e4ebc9bf97",
    ),
    "ponytail-review": (
        2099,
        "f65e49b61f085f3226228f10c1a41c9e6274dc097e149c2e5880eca4e76c71a4",
    ),
    "ponytail-audit": (
        1449,
        "3b0359c99b0d8b25ae903d2e546c29e86391f4722115177817984f7164c54d47",
    ),
    "ponytail-debt": (
        1491,
        "0852cf46301fbb2793483683136b585e344bc3b484f613c374749834a96b5cd5",
    ),
    "ponytail-gain": (
        1879,
        "e9746f24ed29ba85ae7d595ce454abaa728b7fe16a38fd070b55e67ccf137545",
    ),
    "ponytail-help": (
        1458,
        "25660c52476af639f2c62f42dc95967b6ceb62e1fed38f563b0bc113ca66f16c",
    ),
}
SCOPE = (
    "> **GPTel preset scope:** Apply this prompt only to the current invocation.\n"
    "> This is a prompt preset, not a native skill: it provides no lifecycle\n"
    "> activation, persistent mode or mode switching, slash command, subagent\n"
    "> propagation, plugin configuration, or plugin update mechanism."
)


@pytest.fixture(scope="module")
def ponytail_root() -> Path:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    return root


def _source(root: Path, name: str) -> bytes:
    return (root / "skills" / name / "SKILL.md").read_bytes()


def _arguments(name: str) -> dict[str, str]:
    return {
        "bundle_name": "ponytail",
        "version": PONYTAIL_VERSION,
        "revision": PONYTAIL_REVISION,
        "logical_path": f"skills/{name}/SKILL.md",
    }


def _render(root: Path, name: str) -> bytes:
    return transforms.render_gptel_preset_v1(
        name,
        _source(root, name),
        **_arguments(name),
    )


def _patch_guard(monkeypatch: pytest.MonkeyPatch, name: str, source: bytes) -> None:
    guard = transforms._TRANSFORM_GUARDS[name]
    monkeypatch.setitem(
        transforms._TRANSFORM_GUARDS,
        name,
        replace(
            guard,
            byte_count=len(source),
            sha256=f"sha256:{hashlib.sha256(source).hexdigest()}",
        ),
    )


def test_public_registry_and_error_contract() -> None:
    assert {
        GPTEL_PRESET_TRANSFORM: transforms.render_gptel_preset_v1
    } == transforms.PONYTAIL_TRANSFORMS
    assert issubclass(transforms.PonytailTransformError, BundleSchemaError)


def test_all_pinned_inputs_and_output_byte_goldens(ponytail_root: Path) -> None:
    assert tuple(INPUTS) == PONYTAIL_NAMES
    for name in PONYTAIL_NAMES:
        source = _source(ponytail_root, name)
        expected_size, expected_hash = INPUTS[name]
        assert len(source) == expected_size
        assert hashlib.sha256(source).hexdigest() == expected_hash
        assert source.decode("utf-8").encode("utf-8") == source
        assert not source.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in source
        assert source.endswith(b"\n") and not source.endswith(b"\n\n")

        first = _render(ponytail_root, name)
        second = _render(ponytail_root, name)
        expected_output_size, expected_output_hash = OUTPUTS[name]
        assert first == second
        assert len(first) == expected_output_size
        assert hashlib.sha256(first).hexdigest() == expected_output_hash


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("unknown", {}, "unsupported preset"),
        ("ponytail", {"bundle_name": "other"}, "requires bundle"),
        ("ponytail", {"version": "4.8.5"}, "requires ponytail@"),
        ("ponytail", {"revision": "0" * 40}, "requires revision"),
        ("ponytail", {"logical_path": "skills/other/SKILL.md"}, "unexpected"),
    ],
)
def test_identity_guards(
    ponytail_root: Path,
    name: str,
    arguments: dict[str, str],
    message: str,
) -> None:
    source_name = "ponytail" if name == "unknown" else name
    values = _arguments(source_name) | arguments
    with pytest.raises(transforms.PonytailTransformError, match=message) as error:
        transforms.render_gptel_preset_v1(
            name,
            _source(ponytail_root, source_name),
            **values,
        )
    assert str(error.value).startswith(values["logical_path"] + ":")


def test_byte_guards_precede_structural_parsing(ponytail_root: Path) -> None:
    source = _source(ponytail_root, "ponytail")
    changed = bytearray(source)
    changed[-1] ^= 1
    with pytest.raises(transforms.PonytailTransformError, match="digest mismatch"):
        transforms.render_gptel_preset_v1(
            "ponytail", bytes(changed), **_arguments("ponytail")
        )
    for altered in (source[:-1], source + b"x"):
        with pytest.raises(transforms.PonytailTransformError, match="length mismatch"):
            transforms.render_gptel_preset_v1(
                "ponytail", altered, **_arguments("ponytail")
            )
    with pytest.raises(transforms.PonytailTransformError, match="digest mismatch"):
        transforms.render_gptel_preset_v1(
            "ponytail", b"\xff" + source[1:], **_arguments("ponytail")
        )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"\xff", "invalid frontmatter"),
        (b"plain body\n", "mapping is required"),
        (b"---\n: bad: yaml\n---\nbody\n", "invalid frontmatter"),
        (b"---\n- one\n---\nbody\n", "contain a mapping"),
        (b"---\nname: ponytail\n---\nbody\n", "frontmatter keys"),
        (
            b"---\nname: wrong\ndescription: x\nargument-hint: x\n"
            b"license: MIT\n---\nbody\n",
            "frontmatter name",
        ),
        (
            b"---\nname: ponytail\nname: ponytail\ndescription: x\n"
            b"argument-hint: x\nlicense: MIT\n---\nbody\n",
            "invalid frontmatter",
        ),
    ],
)
def test_post_digest_frontmatter_guards(
    monkeypatch: pytest.MonkeyPatch, source: bytes, message: str
) -> None:
    _patch_guard(monkeypatch, "ponytail", source)
    with pytest.raises(transforms.PonytailTransformError, match=message):
        transforms.render_gptel_preset_v1("ponytail", source, **_arguments("ponytail"))


def test_post_digest_rejects_cr_anywhere(
    monkeypatch: pytest.MonkeyPatch, ponytail_root: Path
) -> None:
    source = _source(ponytail_root, "ponytail").replace(b"\n", b"\r\n", 1)
    _patch_guard(monkeypatch, "ponytail", source)
    with pytest.raises(transforms.PonytailTransformError, match="LF line endings"):
        transforms.render_gptel_preset_v1("ponytail", source, **_arguments("ponytail"))


@pytest.mark.parametrize(
    ("text", "source_h1", "message"),
    [
        ("plain\n", None, None),
        ("# Expected\nintro\n", "# Expected", None),
        ("# Unexpected\nintro\n", None, "must not contain"),
        ("intro\n", "# Expected", "must begin"),
        ("# Expected\n# Second\n", "# Expected", "must begin"),
        ("intro\n## A\nbody\n## A\nbody\n", None, "duplicate H2"),
    ],
)
def test_section_preamble_and_duplicate_h1_h2_guards(
    text: str, source_h1: str | None, message: str | None
) -> None:
    if message is None:
        preamble, _order, _sections = transforms._split_sections(
            text,
            logical_path="skills/x/SKILL.md",
            source_h1=source_h1,
        )
        assert preamble
    else:
        with pytest.raises(transforms.PonytailTransformError, match=message):
            transforms._split_sections(
                text,
                logical_path="skills/x/SKILL.md",
                source_h1=source_h1,
            )


def test_structural_helper_failures_are_path_prefixed() -> None:
    path = "skills/x/SKILL.md"
    with pytest.raises(transforms.PonytailTransformError, match=f"^{path}:"):
        transforms._scope_after_h1(
            "wrong\n", expected_h1="# Expected", logical_path=path
        )
    with pytest.raises(transforms.PonytailTransformError, match=f"^{path}:"):
        transforms._drop_exact_final_line(
            "## Boundary\nwrong\n", "expected", logical_path=path
        )
    with pytest.raises(transforms.PonytailTransformError, match=f"^{path}:"):
        transforms._replace_exactly_once(
            "none", "old", "new", logical_path=path, section="Section"
        )


@pytest.mark.parametrize(
    ("name", "mutation", "message"),
    [
        ("ponytail", (b"## The ladder", b"## Renamed"), "H2 sequence"),
        ("ponytail-review", (b"## Format", b"# Injected\n## Format"), "H1"),
        ("ponytail-audit", (b"## Tags", b"## Extra\n## Tags"), "H2 sequence"),
        ("ponytail-debt", (b"## Scan", b""), "H2 sequence"),
        ("ponytail-gain", (b"# Ponytail Gain", b"# Wrong"), "must begin"),
        ("ponytail-help", (b"# Ponytail Help", b"# Wrong"), "must begin"),
    ],
)
def test_each_source_shape_is_guarded_after_pin_review(
    monkeypatch: pytest.MonkeyPatch,
    ponytail_root: Path,
    name: str,
    mutation: tuple[bytes, bytes],
    message: str,
) -> None:
    source = _source(ponytail_root, name).replace(*mutation, 1)
    _patch_guard(monkeypatch, name, source)
    with pytest.raises(transforms.PonytailTransformError, match=message):
        transforms.render_gptel_preset_v1(name, source, **_arguments(name))


@pytest.mark.parametrize(
    ("name", "old"),
    [
        (
            "ponytail-review",
            b'"stop ponytail-review" or "normal mode": revert to verbose review style.',
        ),
        (
            "ponytail-audit",
            b'"stop ponytail-audit" or "normal mode" to revert.',
        ),
        ("ponytail-gain", b'"stop ponytail" or "normal mode": revert.'),
    ],
)
def test_removable_lines_are_exact_and_final(
    monkeypatch: pytest.MonkeyPatch,
    ponytail_root: Path,
    name: str,
    old: bytes,
) -> None:
    source = _source(ponytail_root, name).replace(old, old + b" changed", 1)
    _patch_guard(monkeypatch, name, source)
    with pytest.raises(transforms.PonytailTransformError, match="removable boundary"):
        transforms.render_gptel_preset_v1(name, source, **_arguments(name))


@pytest.mark.parametrize(
    ("old", "replacement"),
    [
        (b"/ponytail-audit", b"audit preset"),
        (b"/ponytail-debt", b"debt preset"),
        (b"/ponytail-audit", b"/ponytail-audit /ponytail-audit"),
    ],
)
def test_gain_substitution_counts_are_guarded(
    monkeypatch: pytest.MonkeyPatch,
    ponytail_root: Path,
    old: bytes,
    replacement: bytes,
) -> None:
    source = _source(ponytail_root, "ponytail-gain").replace(old, replacement, 1)
    _patch_guard(monkeypatch, "ponytail-gain", source)
    with pytest.raises(transforms.PonytailTransformError, match="exactly one"):
        transforms.render_gptel_preset_v1(
            "ponytail-gain", source, **_arguments("ponytail-gain")
        )


def test_output_envelope_and_shared_forbidden_claims(ponytail_root: Path) -> None:
    forbidden = (
        "active every response",
        "level persists until changed",
        "level sticks until changed",
        "auto-active every session",
        "auto-activation on session start",
        "default mode =",
        "ponytail_default_mode",
        "plugin_data",
        "/plugin",
        "/reload-plugins",
        "marketplace update",
        "@ponytail",
        "claude code",
        "opencode",
        "stop ponytail",
        "normal mode",
    )
    slash = re.compile(r"(?<!https:)/ponytail(?:-(?:review|audit|debt|gain|help))?\b")
    headings = {
        "ponytail": "# Ponytail",
        "ponytail-review": "# Ponytail Review",
        "ponytail-audit": "# Ponytail Audit",
        "ponytail-debt": "# Ponytail Debt",
        "ponytail-gain": "# Ponytail Gain",
        "ponytail-help": "# Ponytail Help",
    }
    for name in PONYTAIL_NAMES:
        output = _render(ponytail_root, name)
        text = output.decode("utf-8")
        assert text.startswith(f"{headings[name]}\n\n{SCOPE}\n\n")
        assert text.count("\n# ") == 0
        assert text.count(SCOPE) == 1
        assert not output.startswith(b"\xef\xbb\xbf---")
        assert b"\r" not in output
        assert output.endswith(b"\n") and not output.endswith(b"\n\n")
        assert slash.search(text) is None
        lowered = text.lower()
        for claim in forbidden:
            assert claim not in lowered


def test_per_preset_semantic_goldens(ponytail_root: Path) -> None:
    rendered = {
        name: _render(ponytail_root, name).decode("utf-8") for name in PONYTAIL_NAMES
    }
    main = rendered["ponytail"]
    for anchor in (
        "## The ladder",
        "trace the real flow end to end",
        "Bug fix = root cause, not symptom",
        "## Rules",
        "## Output",
        "Apply **full** intensity for this invocation",
        "input validation at trust boundaries",
        "PCA9685",
        "ONE runnable check",
    ):
        assert anchor in main
    for absent in ("## Persistence", "**lite**", "**ultra**", "Caveman"):
        assert absent not in main

    review = rendered["ponytail-review"]
    assert "net: -<N> lines possible." in review
    assert "Lean already. Ship." in review
    assert "Does not apply the fixes, only lists them." in review

    audit = rendered["ponytail-audit"]
    assert "repo-wide" in audit and "## Hunt" in audit
    assert "net: -<N> lines, -<M> deps possible." in audit
    assert "applies nothing" in audit

    debt = rendered["ponytail-debt"]
    for anchor in ("grep -rnE", "git blame", "no-trigger", "change nothing"):
        assert anchor in debt
    assert "writes the\nledger to a file" not in debt

    gain = rendered["ponytail-gain"]
    # These stale figures are deliberately retained from the pinned source;
    # corrected figures require an upstream pin and adapter review.
    for anchor in (
        "5 everyday tasks",
        "80\u201394%",
        "47\u201377%",
        "3\u20136\u00d7",
        "Ponytail Debt preset",
        "Ponytail Audit preset",
    ):
        assert anchor in gain
    for synthesized in ("~54%", "~20%", "~27%", "12 feature tasks"):
        assert synthesized not in gain

    help_text = rendered["ponytail-help"]
    assert help_text.count("| `ponytail") == 6
    assert "`gptel-prompts` interface" in help_text
    assert "promptdeploy deployment updates the pinned Ponytail bundle" in help_text
    for heading in (
        "## Levels",
        "## Skills",
        "## Deactivate",
        "## Configure Default Mode",
        "## Update",
        "## More",
    ):
        assert heading not in help_text
