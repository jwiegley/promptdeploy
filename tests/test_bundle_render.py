"""Exhaustive pure tests for host paths and Ponytail hook registration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from promptdeploy import bundle_render as render_module
from promptdeploy.bundle_catalog import discover_bundle_items
from promptdeploy.bundle_render import (
    BundleRenderContext,
    BundleRenderError,
    EmittedHostPath,
    HookCommand,
    HookEventTemplate,
    HookMatcher,
    HookRegistration,
    ParsedHookMap,
    RenderedBundlePlan,
    parse_claude_codex_hook_map,
    registration_semantic_sha256,
    render_bundle,
    render_claude_codex_registration,
    revalidate_rendered_bundle,
)
from promptdeploy.bundles import BundleConfig, BundleSourceBinding
from promptdeploy.imported_tree import (
    ImportedTreeEntry,
    ImportedTreeSnapshot,
    framed_tree_sha256,
)
from promptdeploy.ponytail import (
    CLAUDE_CODEX_RUNTIME_PAYLOAD,
    OPENCODE_PLUGIN_PAYLOAD,
    PONYTAIL_NAMES,
)
from promptdeploy.source import SourceItem

TREE_DIGEST = "sha256:" + "01" * 32
OTHER_TREE_DIGEST = "sha256:" + "02" * 32
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bundles" / "ponytail.yaml"


def _pinned_ponytail_root() -> Path:
    configured = os.environ.get("PONYTAIL_TEST_SOURCE")
    root = Path(configured) if configured else Path("/Users/johnw/Desktop/ponytail")
    if not root.is_dir():
        pytest.fail(f"pinned Ponytail source is unavailable: {root}")
    return root.resolve()


@pytest.fixture(scope="module")
def ponytail_bundle() -> SourceItem:
    resolved = _pinned_ponytail_root()
    bundle = BundleConfig(
        "ponytail",
        MANIFEST,
        BundleSourceBinding(
            "ponytail",
            resolved,
            True,
            None,
            None,
            None,
            "cli",
        ),
    )
    return discover_bundle_items(bundle)[0]


def _selected_source_copy(source: Path, destination: Path) -> Path:
    destination.mkdir()
    shutil.copy2(source / "package.json", destination / "package.json")
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    (destination / "skills").mkdir()
    (destination / "skills").chmod((source / "skills").stat().st_mode | 0o700)
    for name in PONYTAIL_NAMES:
        shutil.copytree(source / "skills" / name, destination / "skills" / name)
    shutil.copytree(source / "hooks", destination / "hooks")
    shutil.copytree(source / ".opencode", destination / ".opencode")
    for path in (destination, *destination.rglob("*")):
        mode = path.stat().st_mode
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))
    return destination


def _reviewed_source_command(script: str, status: str) -> dict[str, object]:
    return {
        "type": "command",
        "command": f'node "${{CLAUDE_PLUGIN_ROOT}}/hooks/{script}"',
        "commandWindows": (
            "if (Get-Command node -ErrorAction SilentlyContinue) { "
            f'node "$env:CLAUDE_PLUGIN_ROOT\\hooks\\{script}" }}'
        ),
        "timeout": 5,
        "statusMessage": status,
    }


HOOK_MAP = json.dumps(
    {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume|clear|compact",
                    "hooks": [
                        _reviewed_source_command(
                            "ponytail-activate.js", "Loading ponytail mode..."
                        )
                    ],
                }
            ],
            "SubagentStart": [
                {
                    "hooks": [
                        _reviewed_source_command(
                            "ponytail-subagent.js", "Loading ponytail mode..."
                        )
                    ]
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        _reviewed_source_command(
                            "ponytail-mode-tracker.js", "Tracking ponytail mode..."
                        )
                    ]
                }
            ],
        }
    },
    indent=2,
).encode()


def _document() -> dict[str, Any]:
    value = json.loads(HOOK_MAP)
    assert isinstance(value, dict)
    return value


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _command(value: dict[str, Any], event: str) -> dict[str, Any]:
    result = value["hooks"][event][0]["hooks"][0]
    assert isinstance(result, dict)
    return result


def _claude_context(root: EmittedHostPath | None = None) -> BundleRenderContext:
    selected = root or EmittedHostPath(
        "local-target",
        "posix",
        "absolute",
        ("srv", "claude profile"),
    )
    return BundleRenderContext("claude", selected, selected, None)


def _codex_context(root: EmittedHostPath | None = None) -> BundleRenderContext:
    selected = root or EmittedHostPath("local-target", "posix", "home", ())
    return BundleRenderContext(
        "codex",
        selected,
        None,
        selected.child(".promptdeploy", "plugin-data", "codex", "ponytail"),
    )


def test_host_path_renders_exact_absolute_home_and_windows_forms() -> None:
    absolute = EmittedHostPath(
        "local-target",
        "posix",
        "absolute",
        ("srv", "AI Tools", "o'Neil", "$cash", "`tick`"),
    )
    assert absolute.posix_shell_word() == ("'/srv/AI Tools/o'\"'\"'Neil/$cash/`tick`'")
    assert absolute.powershell_expression() == ("'/srv/AI Tools/o''Neil/$cash/`tick`'")

    home = EmittedHostPath(
        "local-target",
        "posix",
        "home",
        (".config", "AI Tools", "o'Neil", "$cash", "`tick`"),
    )
    assert home.posix_shell_word() == (
        "\"${HOME:?HOME is required}\"/'.config/AI Tools/o'\"'\"'Neil/$cash/`tick`'"
    )
    assert home.powershell_expression() == (
        "(Join-Path $(if ([string]::IsNullOrEmpty($HOME)) { "
        "throw 'HOME is required' } else { $HOME }) "
        "'.config/AI Tools/o''Neil/$cash/`tick`')"
    )

    windows = EmittedHostPath(
        "local-target",
        "windows",
        "absolute",
        ("C:", "Program Files", "O'Neil", "$cash", "`tick`"),
    )
    assert windows.posix_shell_word() == (
        "'C:\\Program Files\\O'\"'\"'Neil\\$cash\\`tick`'"
    )
    assert windows.powershell_expression() == (
        "'C:\\Program Files\\O''Neil\\$cash\\`tick`'"
    )


def test_host_path_home_root_and_child_are_explicit() -> None:
    home = EmittedHostPath("local-target", "posix", "home", ())
    assert home.posix_shell_word() == '"${HOME:?HOME is required}"'
    assert home.powershell_expression() == (
        "$(if ([string]::IsNullOrEmpty($HOME)) { "
        "throw 'HOME is required' } else { $HOME })"
    )
    with pytest.raises(BundleRenderError, match="no literal absolute"):
        home._absolute_text()
    child = home.child(".promptdeploy", "bundle")
    assert child.components == (".promptdeploy", "bundle")
    assert child.posix_shell_word() == (
        '"${HOME:?HOME is required}"/.promptdeploy/bundle'
    )
    with pytest.raises(BundleRenderError, match="requires a component"):
        home.child()


@pytest.mark.parametrize(
    "path",
    [
        lambda: EmittedHostPath(cast(Any, "staging"), "posix", "absolute", ("path",)),
        lambda: EmittedHostPath(
            "local-target", cast(Any, "other"), "absolute", ("path",)
        ),
        lambda: EmittedHostPath("local-target", "posix", cast(Any, "other"), ("path",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ()),
        lambda: EmittedHostPath("local-target", "windows", "home", ()),
        lambda: EmittedHostPath("local-target", "windows", "absolute", ("c:", "path")),
        lambda: EmittedHostPath("local-target", "windows", "absolute", ("C:",)),
        lambda: EmittedHostPath("local-target", "windows", "absolute", ("path",)),
        lambda: EmittedHostPath(
            "local-target", "posix", "absolute", cast(Any, ["path"])
        ),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", (".",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("..",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("~user",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad/name",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad\\name",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad\x00name",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad\rname",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad\nname",)),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("bad\tname",)),
        lambda: EmittedHostPath(
            "local-target", "posix", "absolute", ("bad\u200ename",)
        ),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("e\u0301",)),
        lambda: EmittedHostPath(
            "local-target", "posix", "absolute", ("bad\ud800name",)
        ),
        lambda: EmittedHostPath("local-target", "posix", "absolute", ("x" * 256,)),
        lambda: EmittedHostPath(
            "local-target", "posix", "absolute", tuple("x" for _ in range(65))
        ),
        lambda: EmittedHostPath("local-target", "windows", "absolute", ("C:", "CON")),
        lambda: EmittedHostPath(
            "local-target", "windows", "absolute", ("C:", "nul.txt")
        ),
        lambda: EmittedHostPath(
            "local-target", "windows", "absolute", ("C:", "bad:name")
        ),
        lambda: EmittedHostPath(
            "local-target", "windows", "absolute", ("C:", "trailing.")
        ),
        lambda: EmittedHostPath(
            "local-target", "windows", "absolute", ("C:", "trailing ")
        ),
    ],
)
def test_host_path_rejects_noncanonical_or_ambiguous_inputs(path: Any) -> None:
    with pytest.raises(BundleRenderError):
        path()


def test_emitted_host_path_enforces_the_whole_path_budget() -> None:
    with pytest.raises(BundleRenderError, match="path exceeds"):
        EmittedHostPath(
            "remote-target",
            "posix",
            "absolute",
            tuple("x" * 250 for _ in range(17)),
        )


@pytest.mark.parametrize("home", [None, ""])
def test_posix_home_anchor_fails_closed_when_home_is_unavailable(
    home: str | None,
) -> None:
    path = EmittedHostPath(
        "local-target",
        "posix",
        "home",
        (".promptdeploy", "runtime"),
    )
    environment = os.environ.copy()
    if home is None:
        environment.pop("HOME", None)
    else:
        environment["HOME"] = home
    result = subprocess.run(
        ["sh", "-c", f"printf '%s' {path.posix_shell_word()}"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert result.returncode != 0
    assert "HOME is required" in result.stderr


def test_context_rejects_unknown_target_and_mixed_host_styles() -> None:
    posix = EmittedHostPath("local-target", "posix", "absolute", ("srv", "target"))
    windows = EmittedHostPath("local-target", "windows", "absolute", ("C:", "target"))
    remote = EmittedHostPath("remote-target", "posix", "absolute", ("srv", "target"))
    with pytest.raises(BundleRenderError, match="target type"):
        BundleRenderContext(cast(Any, "unknown"), posix, None, None)
    with pytest.raises(BundleRenderError, match="emitted host path"):
        BundleRenderContext("droid", cast(Any, "staging/path"), None, None)
    with pytest.raises(BundleRenderError, match="emitted host paths"):
        BundleRenderContext("droid", posix, cast(Any, "staging/path"), None)
    with pytest.raises(BundleRenderError, match="one host style"):
        BundleRenderContext("claude", posix, windows, None)
    with pytest.raises(BundleRenderError, match="one target origin"):
        BundleRenderContext("claude", posix, remote, None)


def test_context_enforces_the_complete_target_path_matrix() -> None:
    managed = EmittedHostPath("local-target", "posix", "absolute", ("srv", "managed"))
    other = EmittedHostPath("local-target", "posix", "absolute", ("srv", "other"))
    plugin_data = managed.child(".promptdeploy", "plugin-data", "codex", "ponytail")
    with pytest.raises(BundleRenderError, match="profile as managed root"):
        BundleRenderContext("claude", managed, other, None)
    with pytest.raises(BundleRenderError, match="profile as managed root"):
        BundleRenderContext("claude", managed, managed, plugin_data)
    with pytest.raises(BundleRenderError, match="stable emitted plugin-data"):
        BundleRenderContext("codex", managed, None, other)
    with pytest.raises(BundleRenderError, match="stable emitted plugin-data"):
        BundleRenderContext("codex", managed, managed, plugin_data)
    with pytest.raises(BundleRenderError, match="only an emitted managed root"):
        BundleRenderContext("opencode", managed, other, None)


def test_public_renderers_reject_noncontext_values(
    ponytail_bundle: SourceItem,
) -> None:
    with pytest.raises(BundleRenderError, match="closed render context"):
        render_bundle(ponytail_bundle, cast(Any, "staging context"))
    with pytest.raises(BundleRenderError, match="closed render context"):
        render_claude_codex_registration(
            HOOK_MAP,
            cast(Any, "staging context"),
            TREE_DIGEST,
        )

    class BypassContext(BundleRenderContext):
        def __post_init__(self) -> None:
            pass

    bypass = BypassContext(
        "droid",
        cast(Any, "/var/tmp/promptdeploy-hostile-stage"),
        None,
        None,
    )
    with pytest.raises(BundleRenderError, match="closed render context"):
        render_bundle(ponytail_bundle, bypass)
    with pytest.raises(BundleRenderError, match="closed render context"):
        render_claude_codex_registration(HOOK_MAP, bypass, TREE_DIGEST)


def test_remote_emitted_plan_renders_the_configured_live_path(
    ponytail_bundle: SourceItem,
) -> None:
    live = EmittedHostPath(
        "remote-target",
        "posix",
        "absolute",
        ("home", "agent", ".claude"),
    )
    rendered = render_bundle(
        ponytail_bundle,
        BundleRenderContext("claude", live, live, None),
    )
    assert rendered.hook_registration is not None
    serialized = json.dumps(rendered.hook_registration.value.to_json_value())
    assert "/home/agent/.claude/.promptdeploy/" in serialized


def test_parser_accepts_only_the_exact_reviewed_semantics() -> None:
    parsed = parse_claude_codex_hook_map(HOOK_MAP)
    assert parsed == ParsedHookMap(
        (
            HookEventTemplate(
                "SessionStart",
                "startup|resume|clear|compact",
                "ponytail-activate.js",
                "Loading ponytail mode...",
            ),
            HookEventTemplate(
                "SubagentStart",
                None,
                "ponytail-subagent.js",
                "Loading ponytail mode...",
            ),
            HookEventTemplate(
                "UserPromptSubmit",
                None,
                "ponytail-mode-tracker.js",
                "Tracking ponytail mode...",
            ),
        )
    )

    # Whitespace and irrelevant object-key presentation are not semantic.
    reordered = _document()
    command = _command(reordered, "SessionStart")
    reordered["hooks"]["SessionStart"][0]["hooks"][0] = {
        key: command[key] for key in reversed(tuple(command))
    }
    assert parse_claude_codex_hook_map(_encoded(reordered)) == parsed


@pytest.mark.parametrize(
    "source",
    [
        bytearray(HOOK_MAP),
        b"\xff",
        b"{",
        b"\xef\xbb\xbf" + HOOK_MAP,
        b'{"hooks": NaN}',
        b"[]",
    ],
)
def test_parser_rejects_nonbyte_or_invalid_json_inputs(source: Any) -> None:
    with pytest.raises(BundleRenderError):
        parse_claude_codex_hook_map(source)


@pytest.mark.parametrize(
    "source",
    [
        b'{"hooks":{},"hooks":{}}',
        b'{"hooks":{"SessionStart":[],"SessionStart":[]}}',
        (
            b'{"hooks":{"SessionStart":[{"matcher":"x","hooks":'
            b'[{"type":"command","type":"command"}]}]}}'
        ),
    ],
)
def test_parser_rejects_duplicate_keys_at_every_depth(source: bytes) -> None:
    with pytest.raises(BundleRenderError, match="duplicate key"):
        parse_claude_codex_hook_map(source)


def test_parser_rejects_root_and_event_set_or_order_drift() -> None:
    missing_root = _document()
    missing_root.pop("hooks")
    with pytest.raises(BundleRenderError, match="root must contain exactly"):
        parse_claude_codex_hook_map(_encoded(missing_root))

    extra_root = _document()
    extra_root["extra"] = True
    with pytest.raises(BundleRenderError, match="root must contain exactly"):
        parse_claude_codex_hook_map(_encoded(extra_root))

    wrong_hooks = _document()
    wrong_hooks["hooks"] = []
    with pytest.raises(BundleRenderError, match="hooks must be an object"):
        parse_claude_codex_hook_map(_encoded(wrong_hooks))

    for mutation in ("missing", "extra", "order"):
        changed = _document()
        hooks = changed["hooks"]
        if mutation == "missing":
            hooks.pop("SubagentStart")
        elif mutation == "extra":
            hooks["Extra"] = []
        else:
            changed["hooks"] = {key: hooks[key] for key in reversed(tuple(hooks))}
        with pytest.raises(BundleRenderError, match="missing, extra, or out of order"):
            parse_claude_codex_hook_map(_encoded(changed))


@pytest.mark.parametrize("replacement", [None, {}, [], [None], [None, None]])
def test_parser_rejects_event_cardinality_and_shape(replacement: object) -> None:
    changed = _document()
    changed["hooks"]["SessionStart"] = replacement
    with pytest.raises(BundleRenderError):
        parse_claude_codex_hook_map(_encoded(changed))


def test_parser_rejects_matcher_and_outer_key_drift() -> None:
    changed = _document()
    changed["hooks"]["SessionStart"][0].pop("matcher")
    with pytest.raises(BundleRenderError, match="must contain exactly"):
        parse_claude_codex_hook_map(_encoded(changed))

    changed = _document()
    changed["hooks"]["SessionStart"][0]["matcher"] = "startup"
    with pytest.raises(BundleRenderError, match="matcher is not reviewed"):
        parse_claude_codex_hook_map(_encoded(changed))

    changed = _document()
    changed["hooks"]["SubagentStart"][0]["matcher"] = ""
    with pytest.raises(BundleRenderError, match="must contain exactly"):
        parse_claude_codex_hook_map(_encoded(changed))

    changed = _document()
    changed["hooks"]["SubagentStart"][0]["extra"] = True
    with pytest.raises(BundleRenderError, match="must contain exactly"):
        parse_claude_codex_hook_map(_encoded(changed))


@pytest.mark.parametrize("replacement", [None, {}, [], [None], [None, None]])
def test_parser_rejects_command_cardinality_and_shape(replacement: object) -> None:
    changed = _document()
    changed["hooks"]["SubagentStart"][0]["hooks"] = replacement
    with pytest.raises(BundleRenderError):
        parse_claude_codex_hook_map(_encoded(changed))


def test_parser_rejects_missing_and_unknown_command_keys() -> None:
    changed = _document()
    _command(changed, "SessionStart").pop("command")
    with pytest.raises(BundleRenderError, match="must contain exactly"):
        parse_claude_codex_hook_map(_encoded(changed))

    changed = _document()
    _command(changed, "SessionStart")["extra"] = True
    with pytest.raises(BundleRenderError, match="must contain exactly"):
        parse_claude_codex_hook_map(_encoded(changed))


@pytest.mark.parametrize(
    ("event", "field", "replacement", "message"),
    [
        ("SessionStart", "type", "shell", "type is not command"),
        ("SessionStart", "command", "node other.js", "POSIX template"),
        (
            "SessionStart",
            "commandWindows",
            "node other.js",
            "PowerShell template",
        ),
        ("SessionStart", "statusMessage", "Other", "status message"),
        ("SubagentStart", "command", "node other.js", "POSIX template"),
        ("SubagentStart", "commandWindows", "node other.js", "PowerShell template"),
        ("SubagentStart", "statusMessage", "Other", "status message"),
        ("UserPromptSubmit", "command", "node other.js", "POSIX template"),
        (
            "UserPromptSubmit",
            "commandWindows",
            "node other.js",
            "PowerShell template",
        ),
        ("UserPromptSubmit", "statusMessage", "Other", "status message"),
    ],
)
def test_parser_rejects_each_unreviewed_command_semantic(
    event: str,
    field: str,
    replacement: object,
    message: str,
) -> None:
    changed = _document()
    _command(changed, event)[field] = replacement
    with pytest.raises(BundleRenderError, match=message):
        parse_claude_codex_hook_map(_encoded(changed))


@pytest.mark.parametrize("timeout", [True, False, 4, 5.0, "5", None])
def test_parser_requires_timeout_integer_five(timeout: object) -> None:
    changed = _document()
    _command(changed, "SessionStart")["timeout"] = timeout
    with pytest.raises(BundleRenderError, match="integer 5"):
        parse_claude_codex_hook_map(_encoded(changed))


def test_claude_registration_has_exact_commands_owner_and_digest() -> None:
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(),
        TREE_DIGEST,
    )
    assert rendered.abi == "claude-settings-hooks-v1"
    assert rendered.owner == "bundle:ponytail"
    assert rendered.sha256 == (
        "sha256:46fddfe9d5c1ea7c7127a41915ff395d947f70c87bae32a34de6df6fc19d67b7"
    )
    first = rendered.value.events[0][1]
    runtime = "/srv/claude profile/.promptdeploy/bundles/ponytail/runtimes/" + "01" * 32
    assert first.hook.command == (
        "env -u COPILOT_PLUGIN_DATA -u PLUGIN_DATA -u QODER_SESSION_ID "
        f"CLAUDE_PLUGIN_ROOT='{runtime}' PLUGIN_ROOT='{runtime}' "
        "CLAUDE_CONFIG_DIR='/srv/claude profile' "
        f"node '{runtime}/hooks/ponytail-activate.js'"
    )
    assert first.hook.command_windows == (
        "if (Get-Command node -ErrorAction SilentlyContinue) { "
        "Remove-Item Env:COPILOT_PLUGIN_DATA,Env:PLUGIN_DATA,Env:QODER_SESSION_ID "
        "-ErrorAction SilentlyContinue; "
        f"$env:CLAUDE_PLUGIN_ROOT='{runtime}'; $env:PLUGIN_ROOT='{runtime}'; "
        "$env:CLAUDE_CONFIG_DIR='/srv/claude profile'; "
        f"& node '{runtime}/hooks/ponytail-activate.js' }}"
    )

    document = rendered.value.to_json_value()
    hooks = cast(dict[str, list[dict[str, Any]]], document["hooks"])
    outer_entries = [entry for entries in hooks.values() for entry in entries]
    assert all(entry["_source"] == "bundle:ponytail" for entry in outer_entries)
    assert "matcher" in outer_entries[0]
    assert all("matcher" not in entry for entry in outer_entries[1:])
    assert json.dumps(document).count('"_source"') == 3
    assert "${CLAUDE_PLUGIN_ROOT}" not in json.dumps(document)
    assert "/Users/johnw/Desktop/ponytail" not in json.dumps(document)
    assert "/var/folders/" not in json.dumps(document)


def test_codex_home_registration_has_exact_commands_and_digest() -> None:
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _codex_context(),
        TREE_DIGEST,
    )
    assert rendered.abi == "codex-hooks-json-v1"
    assert rendered.sha256 == (
        "sha256:787bda798c1a30e572f723c671296288e041a70efd1a549d2684862bc4d338c4"
    )
    first = rendered.value.events[0][1].hook
    runtime = ".promptdeploy/bundles/ponytail/runtimes/" + "01" * 32
    assert first.command == (
        "env -u COPILOT_PLUGIN_DATA -u QODER_SESSION_ID "
        f'CLAUDE_PLUGIN_ROOT="${{HOME:?HOME is required}}"/{runtime} '
        f'PLUGIN_ROOT="${{HOME:?HOME is required}}"/{runtime} '
        'PLUGIN_DATA="${HOME:?HOME is required}"/'
        ".promptdeploy/plugin-data/codex/ponytail "
        f'node "${{HOME:?HOME is required}}"/{runtime}/hooks/ponytail-activate.js'
    )
    home = (
        "$(if ([string]::IsNullOrEmpty($HOME)) { "
        "throw 'HOME is required' } else { $HOME })"
    )
    assert first.command_windows == (
        "if (Get-Command node -ErrorAction SilentlyContinue) { "
        "Remove-Item Env:COPILOT_PLUGIN_DATA,Env:QODER_SESSION_ID "
        "-ErrorAction SilentlyContinue; "
        f"$env:CLAUDE_PLUGIN_ROOT=(Join-Path {home} '{runtime}'); "
        f"$env:PLUGIN_ROOT=(Join-Path {home} '{runtime}'); "
        f"$env:PLUGIN_DATA=(Join-Path {home} "
        "'.promptdeploy/plugin-data/codex/ponytail'); "
        f"& node (Join-Path {home} '{runtime}/hooks/ponytail-activate.js') }}"
    )


def test_windows_registration_quotes_apostrophe_dollar_and_backtick() -> None:
    root = EmittedHostPath(
        "local-target",
        "windows",
        "absolute",
        ("C:", "Users", "O'Neil $cash `tick`", "Claude"),
    )
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(root),
        TREE_DIGEST,
    )
    command = rendered.value.events[0][1].hook.command_windows
    assert "$env:CLAUDE_PLUGIN_ROOT='C:\\Users\\O''Neil $cash `tick`\\Claude" in command
    assert "$env:CLAUDE_CONFIG_DIR='C:\\Users\\O''Neil $cash `tick`\\Claude'" in command
    assert "${CLAUDE_PLUGIN_ROOT}" not in command


@pytest.mark.parametrize("quote", ["\u2018", "\u2019"])
def test_windows_registration_doubles_powershell_smart_quotes(quote: str) -> None:
    component = f"safe{quote}; Write-Output PWNED; {quote}rest"
    root = EmittedHostPath(
        "local-target",
        "windows",
        "absolute",
        ("C:", "Users", component, "Claude"),
    )
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(root),
        TREE_DIGEST,
    )
    command = rendered.value.events[0][1].hook.command_windows
    escaped = component.replace(quote, quote * 2)
    assert escaped in command
    assert component not in command


def test_registration_digest_is_semantic_deterministic_and_path_sensitive() -> None:
    first = render_claude_codex_registration(HOOK_MAP, _claude_context(), TREE_DIGEST)
    pretty = json.dumps(_document(), indent=7).encode()
    second = render_claude_codex_registration(pretty, _claude_context(), TREE_DIGEST)
    assert first == second
    assert registration_semantic_sha256(first.value) == first.sha256

    changed_tree = render_claude_codex_registration(
        HOOK_MAP, _claude_context(), OTHER_TREE_DIGEST
    )
    changed_profile = EmittedHostPath(
        "local-target", "posix", "absolute", ("srv", "other profile")
    )
    changed_path = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(changed_profile),
        TREE_DIGEST,
    )
    codex = render_claude_codex_registration(HOOK_MAP, _codex_context(), TREE_DIGEST)
    assert (
        len({first.sha256, changed_tree.sha256, changed_path.sha256, codex.sha256}) == 4
    )

    event_name, matcher = first.value.events[0]
    changed_command = replace(matcher.hook, command=matcher.hook.command + " changed")
    tampered = HookRegistration(
        ((event_name, replace(matcher, hook=changed_command)), *first.value.events[1:])
    )
    assert registration_semantic_sha256(tampered) != first.sha256


def test_rendered_registration_rejects_invalid_authority_and_shape() -> None:
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(),
        TREE_DIGEST,
    )
    with pytest.raises(BundleRenderError, match="ABI"):
        replace(rendered, abi=cast(Any, "other-v1"))
    with pytest.raises(BundleRenderError, match="owner"):
        replace(rendered, owner=cast(Any, "other"))
    with pytest.raises(BundleRenderError, match="lowercase SHA-256"):
        replace(rendered, sha256="bad")

    valid_events = rendered.value.events
    invalid_values = (
        cast(Any, "hooks"),
        HookRegistration(cast(Any, list(valid_events))),
        HookRegistration(()),
        replace(
            rendered.value,
            events=(cast(Any, ("SessionStart",)), *valid_events[1:]),
        ),
        replace(
            rendered.value,
            events=((cast(Any, "Other"), valid_events[0][1]), *valid_events[1:]),
        ),
        replace(
            rendered.value,
            events=(("SessionStart", cast(Any, "matcher")), *valid_events[1:]),
        ),
        replace(
            rendered.value,
            events=(
                (
                    "SessionStart",
                    replace(valid_events[0][1], matcher="other"),
                ),
                *valid_events[1:],
            ),
        ),
        replace(
            rendered.value,
            events=(
                (
                    "SessionStart",
                    HookMatcher(
                        valid_events[0][1].matcher,
                        cast(Any, "command"),
                    ),
                ),
                *valid_events[1:],
            ),
        ),
    )
    for value in invalid_values:
        with pytest.raises(BundleRenderError):
            render_module._validate_rendered_hook_value(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", cast(Any, 1)),
        ("command", ""),
        ("command_windows", cast(Any, 1)),
        ("command_windows", ""),
        ("timeout", cast(Any, True)),
        ("timeout", cast(Any, 4)),
        ("status_message", "other"),
    ],
)
def test_rendered_registration_rejects_each_invalid_command_field(
    field: str,
    value: object,
) -> None:
    rendered = render_claude_codex_registration(
        HOOK_MAP,
        _claude_context(),
        TREE_DIGEST,
    )
    events = rendered.value.events
    changed_hook = cast(
        HookCommand,
        replace(cast(Any, events[0][1].hook), **{field: value}),
    )
    changed = replace(
        rendered.value,
        events=(
            (events[0][0], replace(events[0][1], hook=changed_hook)),
            *events[1:],
        ),
    )
    with pytest.raises(BundleRenderError, match="command shape"):
        render_module._validate_rendered_hook_value(changed)


@pytest.mark.parametrize("digest", ["", "sha256:ABC", "sha256:" + "0" * 63])
def test_registration_rejects_invalid_runtime_digest(digest: str) -> None:
    with pytest.raises(BundleRenderError, match="lowercase SHA-256"):
        render_claude_codex_registration(HOOK_MAP, _claude_context(), digest)


def test_registration_rejects_inapplicable_or_incomplete_contexts() -> None:
    root = EmittedHostPath("local-target", "posix", "absolute", ("srv", "target"))
    plugin_data = root.child(".promptdeploy", "plugin-data", "codex", "ponytail")

    with pytest.raises(BundleRenderError, match="profile as managed root"):
        render_claude_codex_registration(
            HOOK_MAP,
            BundleRenderContext("claude", root, None, None),
            TREE_DIGEST,
        )
    with pytest.raises(BundleRenderError, match="profile as managed root"):
        render_claude_codex_registration(
            HOOK_MAP,
            BundleRenderContext("claude", root, root, plugin_data),
            TREE_DIGEST,
        )
    with pytest.raises(BundleRenderError, match="stable emitted plugin-data"):
        render_claude_codex_registration(
            HOOK_MAP,
            BundleRenderContext("codex", root, None, None),
            TREE_DIGEST,
        )
    with pytest.raises(BundleRenderError, match="stable emitted plugin-data"):
        render_claude_codex_registration(
            HOOK_MAP,
            BundleRenderContext("codex", root, root, plugin_data),
            TREE_DIGEST,
        )
    with pytest.raises(BundleRenderError, match="stable emitted plugin-data"):
        render_claude_codex_registration(
            HOOK_MAP,
            BundleRenderContext("codex", root, None, root.child("other")),
            TREE_DIGEST,
        )
    for target_type in ("droid", "opencode", "gptel"):
        with pytest.raises(BundleRenderError, match="only valid for Claude or Codex"):
            render_claude_codex_registration(
                HOOK_MAP,
                BundleRenderContext(target_type, root, None, None),
                TREE_DIGEST,
            )


def test_registration_value_contains_all_exact_events_and_statuses() -> None:
    rendered = render_claude_codex_registration(
        HOOK_MAP, _claude_context(), TREE_DIGEST
    )
    assert [name for name, _matcher in rendered.value.events] == [
        "SessionStart",
        "SubagentStart",
        "UserPromptSubmit",
    ]
    assert [
        matcher.hook.status_message for _name, matcher in rendered.value.events
    ] == [
        "Loading ponytail mode...",
        "Loading ponytail mode...",
        "Tracking ponytail mode...",
    ]
    assert [matcher.hook.timeout for _name, matcher in rendered.value.events] == [
        5,
        5,
        5,
    ]
    assert [
        matcher.hook.command.rsplit("/", 1)[-1].rstrip("'")
        for _name, matcher in rendered.value.events
    ] == [
        "ponytail-activate.js",
        "ponytail-subagent.js",
        "ponytail-mode-tracker.js",
    ]


def test_registration_semantic_digest_canonicalizes_key_order() -> None:
    command = HookCommand("command", "windows", status_message="status")
    registration = HookRegistration(
        (("SessionStart", HookMatcher("matcher", command)),)
    )
    canonical = json.dumps(
        registration.to_json_value(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert registration_semantic_sha256(registration) == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )


def _target_context(target_type: str) -> BundleRenderContext:
    if target_type == "codex":
        home = EmittedHostPath("local-target", "posix", "home", ())
        return BundleRenderContext(
            "codex",
            home,
            None,
            home.child(".promptdeploy", "plugin-data", "codex", "ponytail"),
        )
    root = EmittedHostPath("local-target", "posix", "absolute", ("srv", "target"))
    if target_type == "claude":
        return BundleRenderContext("claude", root, root, None)
    return BundleRenderContext(cast(Any, target_type), root, None, None)


@pytest.mark.parametrize(
    (
        "target_type",
        "payload_name",
        "runtime_digest",
        "source_hash",
        "registration_digest",
    ),
    [
        (
            "claude",
            CLAUDE_CODEX_RUNTIME_PAYLOAD,
            "sha256:46bd65bad6023d631340e3262418866206e95ea5afb38d9bab8dbd567fc32d24",
            "sha256:6fd3d8442d78ae37c845d0cebd24ef74c2560139763dfd581aa87d501b555443",
            "sha256:02501dd96b7dd68dcba2e0d70a4ee7383643ca7ffbd96ff14edaa86456bc48ed",
        ),
        (
            "codex",
            CLAUDE_CODEX_RUNTIME_PAYLOAD,
            "sha256:46bd65bad6023d631340e3262418866206e95ea5afb38d9bab8dbd567fc32d24",
            "sha256:7e50bbdf2e911bc74cf9cea218c93c742d70a5ae5b47034b67673d33693979df",
            "sha256:defcffb1173e6356381e537064ad98cd89c68c61ac5a0adb1a3fd0c3b5463b16",
        ),
        (
            "opencode",
            OPENCODE_PLUGIN_PAYLOAD,
            "sha256:897de1f6cdc260d6243a6920c20773407e3b654cd4e0d47681fb5d90472adfc0",
            "sha256:0585006b2505279809208e963befb411e2e23a8ba39dd987db0dccb71b824770",
            "sha256:cb27a5e594de8397e400741357857779cecbd2fcc9c74fcd49daeefb6e85483a",
        ),
        (
            "droid",
            "support-v1",
            None,
            "sha256:eda5a621500f96f8a1fab94b5e75e77524a541a21414c27a9ef5980cd3f76e05",
            None,
        ),
        (
            "gptel",
            "support-v1",
            None,
            "sha256:1fc66caeb9b201dd9be78c67f11ce15cd78ec95e5cda79262094085a9c879e39",
            None,
        ),
    ],
)
def test_complete_pinned_bundle_plan_is_exact_for_all_targets(
    ponytail_bundle: SourceItem,
    target_type: str,
    payload_name: str,
    runtime_digest: str | None,
    source_hash: str,
    registration_digest: str | None,
) -> None:
    rendered = render_bundle(ponytail_bundle, _target_context(target_type))
    desired = rendered.desired
    assert desired.selected.name == payload_name
    assert desired.runtime_tree_sha256 == runtime_digest
    if runtime_digest is None:
        assert desired.runtime_path is None
    else:
        leaf = runtime_digest.removeprefix("sha256:")
        expected_prefix = (
            ".promptdeploy/bundles/ponytail"
            if target_type == "opencode"
            else ".promptdeploy/bundles/ponytail/runtimes"
        )
        assert desired.runtime_path == f"{expected_prefix}/{leaf}"
    assert desired.source_hash == source_hash
    assert desired.support_tree_sha256 == (
        "sha256:5dd1e01459a1ae1f5b5fa5bdf181905ba8dbecfb4585d400a4622f5b4842ec83"
    )
    assert (
        desired.registration.sha256 if desired.registration is not None else None
    ) == registration_digest
    assert (
        rendered.hook_registration.sha256
        if rendered.hook_registration is not None
        else None
    ) == (registration_digest if target_type in {"claude", "codex"} else None)
    revalidate_rendered_bundle(
        ponytail_bundle,
        _target_context(target_type),
        rendered,
    )


def test_complete_plans_retain_no_source_authority_after_capture(
    tmp_path: Path,
) -> None:
    source = _selected_source_copy(
        _pinned_ponytail_root(),
        tmp_path / "mutable-ponytail",
    )
    bundle = BundleConfig(
        "ponytail",
        MANIFEST,
        BundleSourceBinding(
            "ponytail",
            source.resolve(),
            True,
            None,
            None,
            None,
            "cli",
        ),
    )
    item = discover_bundle_items(bundle)[0]
    shutil.rmtree(source)
    for target_type in ("claude", "codex", "droid", "opencode", "gptel"):
        context = _target_context(target_type)
        rendered = render_bundle(item, context)
        revalidate_rendered_bundle(item, context, rendered)


def test_complete_plan_rejects_payload_and_context_tampering(
    ponytail_bundle: SourceItem,
) -> None:
    original = ponytail_bundle.bundle_payloads[1]
    entries = list(original.imported_tree.entries)
    selected = next(
        index for index, entry in enumerate(entries) if entry.kind == "file"
    )
    entry = entries[selected]
    assert entry.content is not None
    entries[selected] = replace(entry, content=entry.content + b"drift")
    changed_entries = tuple(entries)
    changed_tree = ImportedTreeSnapshot(
        original.imported_tree.logical_root,
        changed_entries,
        framed_tree_sha256(changed_entries),
    )
    changed_payload = replace(original, imported_tree=changed_tree)
    changed_item = replace(
        ponytail_bundle,
        bundle_payloads=(ponytail_bundle.bundle_payloads[0], changed_payload),
    )
    with pytest.raises(BundleRenderError, match="payload digests are not exact"):
        render_bundle(changed_item, _target_context("claude"))

    rendered = render_bundle(ponytail_bundle, _target_context("claude"))
    changed_root = EmittedHostPath(
        "local-target", "posix", "absolute", ("srv", "other")
    )
    with pytest.raises(BundleRenderError, match="changed before target mutation"):
        revalidate_rendered_bundle(
            ponytail_bundle,
            BundleRenderContext("claude", changed_root, changed_root, None),
            rendered,
        )


def test_plan_cross_checks_hook_registration_and_context_shape(
    ponytail_bundle: SourceItem,
) -> None:
    rendered = render_bundle(ponytail_bundle, _target_context("claude"))
    with pytest.raises(BundleRenderError, match="does not match its target"):
        RenderedBundlePlan(rendered.desired, None)
    assert rendered.hook_registration is not None
    with pytest.raises(BundleRenderError, match="does not match its value"):
        replace(
            rendered.hook_registration,
            sha256="sha256:" + "0" * 64,
        )

    event_name, matcher = rendered.hook_registration.value.events[0]
    changed_value = replace(
        rendered.hook_registration.value,
        events=(
            (
                event_name,
                replace(
                    matcher,
                    hook=replace(matcher.hook, command="malicious command"),
                ),
            ),
            *rendered.hook_registration.value.events[1:],
        ),
    )
    with pytest.raises(BundleRenderError, match="does not match its value"):
        replace(rendered.hook_registration, value=changed_value)

    forged = object.__new__(type(rendered.hook_registration))
    object.__setattr__(forged, "abi", rendered.hook_registration.abi)
    object.__setattr__(forged, "owner", rendered.hook_registration.owner)
    object.__setattr__(forged, "value", changed_value)
    object.__setattr__(forged, "sha256", rendered.hook_registration.sha256)
    with pytest.raises(BundleRenderError, match="does not match its value"):
        RenderedBundlePlan(rendered.desired, forged)

    codex_registration = render_claude_codex_registration(
        HOOK_MAP,
        _codex_context(),
        rendered.desired.runtime_tree_sha256 or TREE_DIGEST,
    )
    with pytest.raises(BundleRenderError, match="bundle provenance"):
        RenderedBundlePlan(rendered.desired, codex_registration)

    root = EmittedHostPath("local-target", "posix", "absolute", ("srv", "target"))
    with pytest.raises(BundleRenderError, match="only an emitted managed root"):
        render_bundle(
            ponytail_bundle,
            BundleRenderContext("droid", root, root, None),
        )


@pytest.mark.parametrize("replacement_kind", [None, "directory"])
def test_runtime_hook_map_must_be_one_regular_file(
    ponytail_bundle: SourceItem,
    replacement_kind: str | None,
) -> None:
    payload = ponytail_bundle.bundle_payloads[0]
    entries = [
        entry
        for entry in payload.imported_tree.entries
        if entry.relative_path != "hooks/claude-codex-hooks.json"
    ]
    if replacement_kind == "directory":
        entries.append(
            ImportedTreeEntry(
                "directory",
                "hooks/claude-codex-hooks.json",
                0o755,
            )
        )
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    changed_tree = ImportedTreeSnapshot(
        payload.imported_tree.logical_root,
        ordered,
        framed_tree_sha256(ordered),
    )
    changed_item = replace(
        ponytail_bundle,
        bundle_payloads=(
            replace(payload, imported_tree=changed_tree),
            ponytail_bundle.bundle_payloads[1],
        ),
    )
    with pytest.raises(BundleRenderError, match="regular hook map"):
        render_module._runtime_hook_map(changed_item)
