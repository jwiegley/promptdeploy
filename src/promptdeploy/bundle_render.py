"""Pure host-path and Ponytail hook-registration rendering.

This module is deliberately effect-free: it accepts retained bytes and
validated emitted-host paths, and returns immutable desired registration
values.  It never accepts a staging ``Path`` or performs filesystem I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import unicodedata
from dataclasses import dataclass
from typing import Literal, cast

from .bundle_projection import (
    RegistrationProjection,
    installed_tree_sha256,
    project_installed_tree,
    select_bundle_payload,
)
from .bundle_projection import (
    RenderedBundle as ProjectedBundle,
)
from .bundle_projection import (
    render_bundle as project_bundle,
)
from .bundles import BundleSchemaError
from .imported_tree import MAX_PATH_BYTES, MAX_TREE_DEPTH
from .ponytail import (
    CLAUDE_CODEX_RUNTIME_PAYLOAD,
    CLAUDE_CODEX_RUNTIME_TREE_SHA256,
    OPENCODE_PLUGIN_PAYLOAD,
    OPENCODE_PLUGIN_TREE_SHA256,
)
from .source import SourceItem

TargetType = Literal["claude", "codex", "droid", "opencode", "gptel"]
PathStyle = Literal["posix", "windows"]
PathAnchor = Literal["absolute", "home"]
PathOrigin = Literal["local-target", "remote-target"]
HookTargetType = Literal["claude", "codex"]
HookEventName = Literal["SessionStart", "SubagentStart", "UserPromptSubmit"]
RegistrationAbi = Literal["claude-settings-hooks-v1", "codex-hooks-json-v1"]

_TARGET_TYPES = frozenset({"claude", "codex", "droid", "opencode", "gptel"})
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_WINDOWS_DRIVE = re.compile(r"[A-Z]:\Z")
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
_OWNER: Literal["bundle:ponytail"] = "bundle:ponytail"
_MAX_HOST_COMPONENT_BYTES = 255
_RUNTIME_COMPONENTS = (".promptdeploy", "bundles", "ponytail", "runtimes")
_CODEX_DATA_COMPONENTS = (".promptdeploy", "plugin-data", "codex", "ponytail")


class BundleRenderError(BundleSchemaError):
    """A pure bundle render input violates its closed contract."""


def _validate_component(
    component: object,
    *,
    style: PathStyle,
    drive: bool = False,
) -> str:
    if not isinstance(component, str) or not component:
        raise BundleRenderError("host path components must be non-empty strings")
    try:
        encoded = component.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleRenderError("host path components must be portable UTF-8") from exc
    if len(encoded) > _MAX_HOST_COMPONENT_BYTES:
        raise BundleRenderError("host path component exceeds the length limit")
    if component != unicodedata.normalize("NFC", component):
        raise BundleRenderError("host path components must use NFC")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in component):
        raise BundleRenderError("host path components may not contain control text")
    if component in {".", ".."} or component.startswith("~"):
        raise BundleRenderError("host path components may not use dot or tilde syntax")
    if "/" in component or "\\" in component:
        raise BundleRenderError("host path components may not contain separators")
    if drive:
        if _WINDOWS_DRIVE.fullmatch(component) is None:
            raise BundleRenderError("absolute Windows paths require an uppercase drive")
        return component
    if style == "windows" and (
        any(character in _WINDOWS_FORBIDDEN for character in component)
        or component.endswith((" ", "."))
        or component.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
    ):
        raise BundleRenderError("Windows host path component is not portable")
    return component


def _powershell_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    escaped = escaped.replace("\u2018", "\u2018\u2018")
    escaped = escaped.replace("\u2019", "\u2019\u2019")
    return "'" + escaped + "'"


@dataclass(frozen=True, slots=True)
class EmittedHostPath:
    """One explicitly live target path, represented without staging authority."""

    origin: PathOrigin
    style: PathStyle
    anchor: PathAnchor
    components: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.origin not in {"local-target", "remote-target"}:
            raise BundleRenderError(
                "emitted host path origin must be a local or remote target"
            )
        if self.style not in {"posix", "windows"}:
            raise BundleRenderError("host path style must be posix or windows")
        if self.anchor not in {"absolute", "home"}:
            raise BundleRenderError("host path anchor must be absolute or home")
        if not isinstance(self.components, tuple):
            raise BundleRenderError("host path components must be a tuple")
        if len(self.components) > MAX_TREE_DEPTH:
            raise BundleRenderError("emitted host path exceeds the depth limit")
        if self.style == "windows" and self.anchor != "absolute":
            raise BundleRenderError("Windows host paths must be drive-absolute")
        if self.anchor == "absolute" and not self.components:
            raise BundleRenderError(
                "absolute host paths may not name a filesystem root"
            )
        if (
            self.style == "windows"
            and self.anchor == "absolute"
            and len(self.components) == 1
        ):
            raise BundleRenderError("Windows host paths may not name a drive root")
        for index, component in enumerate(self.components):
            _validate_component(
                component,
                style=self.style,
                drive=self.style == "windows" and index == 0,
            )
        path_bytes = sum(
            len(component.encode("utf-8")) for component in self.components
        )
        path_bytes += max(len(self.components) - 1, 0)
        if self.anchor == "absolute":
            path_bytes += 1
        if path_bytes > MAX_PATH_BYTES:
            raise BundleRenderError("emitted host path exceeds the length limit")

    def child(self, *components: str) -> EmittedHostPath:
        """Append one or more already-logical components."""
        if not components:
            raise BundleRenderError("a host path child requires a component")
        return EmittedHostPath(
            self.origin,
            self.style,
            self.anchor,
            self.components + tuple(components),
        )

    def _absolute_text(self) -> str:
        if self.anchor != "absolute":
            raise BundleRenderError("home-anchored paths have no literal absolute text")
        if self.style == "posix":
            return "/" + "/".join(self.components)
        drive, *tail = self.components
        return drive + "\\" + "\\".join(tail) if tail else drive + "\\"

    def posix_shell_word(self) -> str:
        """Render one shell word without treating a home anchor as literal ``~``."""
        if self.anchor == "absolute":
            return shlex.quote(self._absolute_text())
        if not self.components:
            return '"${HOME:?HOME is required}"'
        relative = "/".join(self.components)
        return '"${HOME:?HOME is required}"/' + shlex.quote(relative)

    def powershell_expression(self) -> str:
        """Render a PowerShell literal or a ``Join-Path $HOME`` expression."""
        if self.anchor == "absolute":
            return _powershell_literal(self._absolute_text())
        home = (
            "$(if ([string]::IsNullOrEmpty($HOME)) { "
            "throw 'HOME is required' } else { $HOME })"
        )
        if not self.components:
            return home
        relative = "/".join(self.components)
        return f"(Join-Path {home} {_powershell_literal(relative)})"


@dataclass(frozen=True, slots=True)
class BundleRenderContext:
    """Emitted/live paths for one semantic target, never staging paths."""

    target_type: TargetType
    managed_root: EmittedHostPath
    profile_root: EmittedHostPath | None
    plugin_data_root: EmittedHostPath | None

    def __post_init__(self) -> None:
        if self.target_type not in _TARGET_TYPES:
            raise BundleRenderError("bundle render target type is unknown")
        if type(self.managed_root) is not EmittedHostPath:
            raise BundleRenderError("managed root must be an emitted host path")
        paths = tuple(
            path
            for path in (self.profile_root, self.plugin_data_root)
            if path is not None
        )
        if any(type(path) is not EmittedHostPath for path in paths):
            raise BundleRenderError("context roots must be emitted host paths")
        if any(path.style != self.managed_root.style for path in paths):
            raise BundleRenderError("all emitted paths must use one host style")
        if any(path.origin != self.managed_root.origin for path in paths):
            raise BundleRenderError("all emitted paths must use one target origin")
        if self.target_type == "claude":
            if (
                self.profile_root != self.managed_root
                or self.plugin_data_root is not None
            ):
                raise BundleRenderError(
                    "Claude rendering requires its emitted profile as managed root"
                )
        elif self.target_type == "codex":
            expected_data = self.managed_root.child(*_CODEX_DATA_COMPONENTS)
            if self.profile_root is not None or self.plugin_data_root != expected_data:
                raise BundleRenderError(
                    "Codex rendering requires its stable emitted plugin-data root"
                )
        elif self.profile_root is not None or self.plugin_data_root is not None:
            raise BundleRenderError(
                "support and OpenCode rendering accept only an emitted managed root"
            )


def _validate_render_context(context: BundleRenderContext) -> BundleRenderContext:
    if type(context) is not BundleRenderContext:
        raise BundleRenderError("bundle renderer requires a closed render context")
    BundleRenderContext.__post_init__(context)
    return context


@dataclass(frozen=True, slots=True)
class HookEventTemplate:
    """The strictly accepted semantic input for one upstream hook event."""

    name: HookEventName
    matcher: str | None
    script: str
    status_message: str


@dataclass(frozen=True, slots=True)
class ParsedHookMap:
    """The exact three-event Ponytail hook-map contract."""

    events: tuple[HookEventTemplate, ...]


@dataclass(frozen=True, slots=True)
class HookCommand:
    """One rendered target command pair."""

    command: str
    command_windows: str
    timeout: Literal[5] = 5
    status_message: str = ""

    def to_json_value(self) -> dict[str, object]:
        return {
            "type": "command",
            "command": self.command,
            "commandWindows": self.command_windows,
            "timeout": self.timeout,
            "statusMessage": self.status_message,
        }


@dataclass(frozen=True, slots=True)
class HookMatcher:
    """One owned outer matcher object."""

    matcher: str | None
    hook: HookCommand

    def to_json_value(self) -> dict[str, object]:
        value: dict[str, object] = {"_source": _OWNER}
        if self.matcher is not None:
            value["matcher"] = self.matcher
        value["hooks"] = [self.hook.to_json_value()]
        return value


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """Only the promptdeploy-owned event fragment, never a merged settings file."""

    events: tuple[tuple[HookEventName, HookMatcher], ...]

    def to_json_value(self) -> dict[str, object]:
        hooks: dict[str, object] = {}
        for name, matcher in self.events:
            hooks[name] = [matcher.to_json_value()]
        return {"hooks": hooks}


@dataclass(frozen=True, slots=True)
class RenderedHookRegistration:
    """One immutable owned registration and its canonical semantic digest."""

    abi: RegistrationAbi
    owner: Literal["bundle:ponytail"]
    value: HookRegistration
    sha256: str

    def __post_init__(self) -> None:
        if self.abi not in {"claude-settings-hooks-v1", "codex-hooks-json-v1"}:
            raise BundleRenderError("rendered hook registration ABI is unsupported")
        if self.owner != _OWNER:
            raise BundleRenderError("rendered hook registration owner is invalid")
        _validate_rendered_hook_value(self.value)
        if _SHA256.fullmatch(self.sha256) is None:
            raise BundleRenderError(
                "rendered hook registration digest must be lowercase SHA-256"
            )
        if registration_semantic_sha256(self.value) != self.sha256:
            raise BundleRenderError(
                "rendered hook registration digest does not match its value"
            )

    def to_projection(self) -> RegistrationProjection:
        """Return the exact behavior-bound projection used by bundle hashing."""
        return RegistrationProjection(
            self.abi,
            self.owner,
            self.sha256,
        )


_EVENT_SPECS: tuple[
    tuple[HookEventName, str | None, str, str],
    ...,
] = (
    (
        "SessionStart",
        "startup|resume|clear|compact",
        "ponytail-activate.js",
        "Loading ponytail mode...",
    ),
    (
        "SubagentStart",
        None,
        "ponytail-subagent.js",
        "Loading ponytail mode...",
    ),
    (
        "UserPromptSubmit",
        None,
        "ponytail-mode-tracker.js",
        "Tracking ponytail mode...",
    ),
)


def _validate_rendered_hook_value(value: HookRegistration) -> None:
    if not isinstance(value, HookRegistration) or type(value.events) is not tuple:
        raise BundleRenderError("rendered hook value must contain immutable events")
    if len(value.events) != len(_EVENT_SPECS):
        raise BundleRenderError("rendered hook value must contain exactly three events")
    for actual, expected in zip(value.events, _EVENT_SPECS, strict=True):
        if type(actual) is not tuple or len(actual) != 2:
            raise BundleRenderError("rendered hook event must be one immutable pair")
        name, matcher = actual
        expected_name, expected_matcher, _script, expected_status = expected
        if name != expected_name or not isinstance(matcher, HookMatcher):
            raise BundleRenderError("rendered hook event set or order is invalid")
        if matcher.matcher != expected_matcher or not isinstance(
            matcher.hook,
            HookCommand,
        ):
            raise BundleRenderError("rendered hook matcher is invalid")
        hook = matcher.hook
        if (
            not isinstance(hook.command, str)
            or not hook.command
            or not isinstance(hook.command_windows, str)
            or not hook.command_windows
            or type(hook.timeout) is not int
            or hook.timeout != 5
            or hook.status_message != expected_status
        ):
            raise BundleRenderError("rendered hook command shape is invalid")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise BundleRenderError(f"hook map has duplicate key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise BundleRenderError(f"hook map contains invalid constant {value}")


def _mapping(value: object, *, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BundleRenderError(f"{where} must be an object with string keys")
    return cast(dict[str, object], value)


def _exact_keys(
    value: dict[str, object],
    expected: frozenset[str],
    *,
    where: str,
) -> None:
    if value.keys() != expected:
        raise BundleRenderError(f"{where} must contain exactly {sorted(expected)!r}")


def _one_item_list(value: object, *, where: str) -> object:
    if not isinstance(value, list) or len(value) != 1:
        raise BundleRenderError(f"{where} must contain exactly one entry")
    return value[0]


def parse_claude_codex_hook_map(source: bytes) -> ParsedHookMap:
    """Parse and semantically pin the reviewed Ponytail hook declaration."""
    if not isinstance(source, bytes):
        raise BundleRenderError("hook map input must be immutable bytes")
    try:
        text = source.decode("utf-8")
        raw: object = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except BundleRenderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleRenderError("hook map must be valid UTF-8 JSON") from exc

    root = _mapping(raw, where="hook map root")
    _exact_keys(root, frozenset({"hooks"}), where="hook map root")
    hooks = _mapping(root["hooks"], where="hook map hooks")
    expected_order = tuple(spec[0] for spec in _EVENT_SPECS)
    if tuple(hooks) != expected_order:
        raise BundleRenderError("hook map events are missing, extra, or out of order")

    events: list[HookEventTemplate] = []
    for name, matcher, script, status_message in _EVENT_SPECS:
        outer = _mapping(
            _one_item_list(hooks[name], where=f"hook event {name}"),
            where=f"hook event {name} entry",
        )
        outer_keys = frozenset({"hooks", "matcher"} if matcher else {"hooks"})
        _exact_keys(outer, outer_keys, where=f"hook event {name} entry")
        if matcher is not None and outer["matcher"] != matcher:
            raise BundleRenderError(f"hook event {name} matcher is not reviewed")
        command = _mapping(
            _one_item_list(outer["hooks"], where=f"hook event {name} commands"),
            where=f"hook event {name} command",
        )
        _exact_keys(
            command,
            frozenset(
                {"type", "command", "commandWindows", "timeout", "statusMessage"}
            ),
            where=f"hook event {name} command",
        )
        expected_command = f'node "${{CLAUDE_PLUGIN_ROOT}}/hooks/{script}"'
        expected_windows = (
            "if (Get-Command node -ErrorAction SilentlyContinue) { "
            f'node "$env:CLAUDE_PLUGIN_ROOT\\hooks\\{script}" }}'
        )
        if command["type"] != "command":
            raise BundleRenderError(f"hook event {name} type is not command")
        if command["command"] != expected_command:
            raise BundleRenderError(f"hook event {name} POSIX template is not reviewed")
        if command["commandWindows"] != expected_windows:
            raise BundleRenderError(
                f"hook event {name} PowerShell template is not reviewed"
            )
        if type(command["timeout"]) is not int or command["timeout"] != 5:
            raise BundleRenderError(f"hook event {name} timeout must be integer 5")
        if command["statusMessage"] != status_message:
            raise BundleRenderError(f"hook event {name} status message is not reviewed")
        events.append(HookEventTemplate(name, matcher, script, status_message))
    return ParsedHookMap(tuple(events))


def _runtime_root(
    context: BundleRenderContext,
    rendered_tree_sha256: str,
) -> EmittedHostPath:
    if _SHA256.fullmatch(rendered_tree_sha256) is None:
        raise BundleRenderError("rendered runtime digest must be lowercase SHA-256")
    return context.managed_root.child(
        *_RUNTIME_COMPONENTS,
        rendered_tree_sha256.removeprefix("sha256:"),
    )


def _render_posix_command(
    target_type: HookTargetType,
    runtime_root: EmittedHostPath,
    state_root: EmittedHostPath,
    script: str,
) -> str:
    runtime = runtime_root.posix_shell_word()
    script_path = runtime_root.child("hooks", script).posix_shell_word()
    if target_type == "claude":
        prefix = "env -u COPILOT_PLUGIN_DATA -u PLUGIN_DATA -u QODER_SESSION_ID"
        state = f"CLAUDE_CONFIG_DIR={state_root.posix_shell_word()}"
    else:
        prefix = "env -u COPILOT_PLUGIN_DATA -u QODER_SESSION_ID"
        state = f"PLUGIN_DATA={state_root.posix_shell_word()}"
    return (
        f"{prefix} CLAUDE_PLUGIN_ROOT={runtime} PLUGIN_ROOT={runtime} "
        f"{state} node {script_path}"
    )


def _render_powershell_command(
    target_type: HookTargetType,
    runtime_root: EmittedHostPath,
    state_root: EmittedHostPath,
    script: str,
) -> str:
    runtime = runtime_root.powershell_expression()
    script_path = runtime_root.child("hooks", script).powershell_expression()
    if target_type == "claude":
        remove = (
            "Remove-Item "
            "Env:COPILOT_PLUGIN_DATA,Env:PLUGIN_DATA,Env:QODER_SESSION_ID "
            "-ErrorAction SilentlyContinue"
        )
        state = f"$env:CLAUDE_CONFIG_DIR={state_root.powershell_expression()}"
    else:
        remove = (
            "Remove-Item Env:COPILOT_PLUGIN_DATA,Env:QODER_SESSION_ID "
            "-ErrorAction SilentlyContinue"
        )
        state = f"$env:PLUGIN_DATA={state_root.powershell_expression()}"
    return (
        "if (Get-Command node -ErrorAction SilentlyContinue) { "
        f"{remove}; $env:CLAUDE_PLUGIN_ROOT={runtime}; $env:PLUGIN_ROOT={runtime}; "
        f"{state}; & node {script_path} }}"
    )


def registration_semantic_sha256(value: HookRegistration) -> str:
    """Hash canonical semantic JSON, independent of presentation whitespace."""
    document = json.dumps(
        value.to_json_value(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(document).hexdigest()}"


def render_claude_codex_registration(
    source: bytes,
    context: BundleRenderContext,
    rendered_tree_sha256: str,
) -> RenderedHookRegistration:
    """Return the complete owned hook fragment for Claude or Codex."""
    _validate_render_context(context)
    parsed = parse_claude_codex_hook_map(source)
    if context.target_type == "claude":
        assert context.profile_root is not None
        target_type: HookTargetType = "claude"
        state_root = context.profile_root
        abi: RegistrationAbi = "claude-settings-hooks-v1"
    elif context.target_type == "codex":
        assert context.plugin_data_root is not None
        target_type = "codex"
        state_root = context.plugin_data_root
        abi = "codex-hooks-json-v1"
    else:
        raise BundleRenderError("hook registration is only valid for Claude or Codex")

    runtime_root = _runtime_root(context, rendered_tree_sha256)
    events = tuple(
        (
            event.name,
            HookMatcher(
                event.matcher,
                HookCommand(
                    command=_render_posix_command(
                        target_type,
                        runtime_root,
                        state_root,
                        event.script,
                    ),
                    command_windows=_render_powershell_command(
                        target_type,
                        runtime_root,
                        state_root,
                        event.script,
                    ),
                    status_message=event.status_message,
                ),
            ),
        )
        for event in parsed.events
    )
    registration = HookRegistration(events)
    return RenderedHookRegistration(
        abi,
        _OWNER,
        registration,
        registration_semantic_sha256(registration),
    )


@dataclass(frozen=True, slots=True)
class RenderedBundlePlan:
    """One complete pure target plan, still uncommitted and effect-free."""

    desired: ProjectedBundle
    hook_registration: RenderedHookRegistration | None

    def __post_init__(self) -> None:
        expects_hooks = self.desired.target_type in {"claude", "codex"}
        if expects_hooks != (self.hook_registration is not None):
            raise BundleRenderError(
                "rendered bundle hook registration does not match its target"
            )
        if self.hook_registration is None:
            return
        _validate_rendered_hook_value(self.hook_registration.value)
        if (
            registration_semantic_sha256(self.hook_registration.value)
            != self.hook_registration.sha256
        ):
            raise BundleRenderError(
                "rendered hook registration digest does not match its value"
            )
        projected = self.desired.registration
        if projected != self.hook_registration.to_projection():
            raise BundleRenderError(
                "rendered hook registration does not match bundle provenance"
            )


def _runtime_hook_map(item: SourceItem) -> bytes:
    selected = select_bundle_payload(item, "claude")
    matches = [
        entry
        for entry in selected.snapshot.entries
        if entry.relative_path == "hooks/claude-codex-hooks.json"
    ]
    if len(matches) != 1 or matches[0].kind != "file":
        raise BundleRenderError("Ponytail runtime lacks its regular hook map")
    return cast(bytes, matches[0].content)


def _validate_pinned_payloads(item: SourceItem) -> None:
    expected = {
        CLAUDE_CODEX_RUNTIME_PAYLOAD: CLAUDE_CODEX_RUNTIME_TREE_SHA256,
        OPENCODE_PLUGIN_PAYLOAD: OPENCODE_PLUGIN_TREE_SHA256,
    }
    actual = {
        payload.name: payload.imported_tree.tree_sha256
        for payload in item.bundle_payloads
    }
    if actual != expected:
        raise BundleRenderError("Ponytail retained payload digests are not exact")


def render_bundle(item: SourceItem, context: BundleRenderContext) -> RenderedBundlePlan:
    """Render one exact target plan without reading or mutating a target."""
    _validate_render_context(context)
    _validate_pinned_payloads(item)
    selected = select_bundle_payload(item, context.target_type)
    registration: RenderedHookRegistration | None = None
    projected_registration: RegistrationProjection | None = None
    if context.target_type in {"claude", "codex"}:
        runtime_tree = project_installed_tree(
            selected.snapshot,
            exclude=frozenset({"hooks/claude-codex-hooks.json"}),
        )
        runtime_digest = installed_tree_sha256(runtime_tree)
        registration = render_claude_codex_registration(
            _runtime_hook_map(item),
            context,
            runtime_digest,
        )
        projected_registration = registration.to_projection()

    desired = project_bundle(
        item,
        context.target_type,
        registration=projected_registration,
    )
    return RenderedBundlePlan(desired, registration)


def revalidate_rendered_bundle(
    item: SourceItem,
    context: BundleRenderContext,
    expected: RenderedBundlePlan,
) -> None:
    """Recompute the whole pure plan immediately before a future write."""
    if render_bundle(item, context) != expected:
        raise BundleRenderError("rendered bundle changed before target mutation")
