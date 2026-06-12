# src/promptdeploy/settings_sync.py
"""I/O orchestration for `settings init` and `settings reconcile`.

Uses ruamel.yaml round-trip so comments and key order survive write-back.
Pure rendering/merge logic lives in ``settings.py``.
"""

from __future__ import annotations

import copy
import io
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import ScalarString, SingleQuotedScalarString

from .config import Config
from .settings import (
    MANAGED_ELSEWHERE,
    generate_merge_patch,
    render_pre_exact,
    render_settings,
    strip_keys,
    strip_nulls,
)
from .targets import create_target

_MANAGED_ELSEWHERE = MANAGED_ELSEWHERE

# Plain scalars that YAML 1.1 resolves to booleans but YAML 1.2 leaves as
# strings. The deploy pipeline reads settings.yaml with PyYAML (1.1) while
# this module writes it with ruamel (1.2), so such strings must be quoted on
# write or the two sides disagree on the value. y/n are included for the
# YAML 1.1 spec even though PyYAML's resolver ignores the single-letter forms.
_YAML11_BOOLEAN_STRINGS = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off"}
)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_settings_doc(path: Path):
    """Load settings.yaml as a round-trip mapping ({} if absent/empty)."""
    if not path.exists():
        return _yaml().load("{}\n")
    data = _yaml().load(path.read_text("utf-8"))
    return data if data is not None else _yaml().load("{}\n")


def _quote_yaml11_booleans(node: Any) -> Any:
    """Force-quote YAML-1.1 boolean-like strings (in place for containers).

    Already-styled scalars (loaded with ``preserve_quotes``) keep their
    original quoting; only plain strings are wrapped.
    """
    if isinstance(node, str):
        if (
            not isinstance(node, ScalarString)
            and node.lower() in _YAML11_BOOLEAN_STRINGS
        ):
            return SingleQuotedScalarString(node)
        return node
    if isinstance(node, dict):
        for key in node:
            node[key] = _quote_yaml11_booleans(node[key])
        return node
    if isinstance(node, list):
        for i, value in enumerate(node):
            node[i] = _quote_yaml11_booleans(value)
        return node
    return node


def dump_settings_doc(doc, path: Path) -> None:
    """Atomically write a round-trip doc back to settings.yaml.

    Boolean-like plain strings are quoted in place (see
    :func:`_quote_yaml11_booleans`) and the destination's existing file mode
    is preserved (mkstemp would otherwise reset it to 0600); a new file gets
    the umask-derived default.
    """
    _quote_yaml11_booleans(doc)
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    text = buf.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = stat.S_IMODE(os.stat(path).st_mode)
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_live_settings(target_config) -> Dict[str, Any]:
    """Return a target's live settings.json minus the MANAGED_ELSEWHERE keys
    (``hooks``/``mcpServers``/``extraKnownMarketplaces``/``enabledPlugins``)
    and any explicit ``null`` values.

    Nulls are stripped to mirror ``render_settings``: RFC 7396 merge patches
    cannot express an explicit ``null`` value, so leaving them in would make
    host state both undiffable and unreachable. Pulls remote state via the
    target's prepare()/cleanup() lifecycle (rsync for remote targets, no-op
    locally) and reads through the public accessor.
    """
    target = create_target(target_config)
    try:
        target.prepare()
        raw = target.read_settings_json()
    finally:
        target.cleanup()
    return strip_nulls(strip_keys(raw, _MANAGED_ELSEWHERE))


def _claude_target_ids(config: Config, target_ids: List[str]) -> List[str]:
    return [tid for tid in target_ids if config.targets[tid].type == "claude"]


def init_settings(
    config: Config,
    target_ids: List[str],
    *,
    from_ref: Optional[str],
    out_path: Path,
    force: bool,
) -> None:
    """Bootstrap settings.yaml from live host settings.json files."""
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} exists; pass --force to overwrite or use reconcile"
        )

    claude_ids = _claude_target_ids(config, target_ids)
    if not claude_ids:
        raise ValueError("no claude targets selected")

    ref = from_ref or claude_ids[0]
    if ref not in claude_ids:
        raise ValueError(f"--from {ref} is not among the selected claude targets")

    live = {tid: read_live_settings(config.targets[tid]) for tid in claude_ids}
    base = live[ref]
    overrides: Dict[str, Any] = {}
    for tid in claude_ids:
        if tid == ref:
            continue
        patch = generate_merge_patch(base, live[tid])
        if patch:
            overrides[tid] = patch

    # init always produces a clean document — build a fresh CommentedMap rather
    # than round-tripping any pre-existing file.
    fresh = CommentedMap()
    fresh["base"] = base
    if overrides:
        fresh["overrides"] = overrides
    dump_settings_doc(fresh, out_path)


@dataclass
class SettingsDiff:
    target_id: str
    kind: str  # '+' host-only, '~' differs, '-' rendered-only
    key: str
    host_value: Any = None
    rendered_value: Any = None


def _diff_target(
    target_id: str, host: Dict[str, Any], rendered: Dict[str, Any]
) -> List[SettingsDiff]:
    diffs: List[SettingsDiff] = []
    for k in sorted(set(host) | set(rendered)):
        in_host, in_rend = k in host, k in rendered
        if in_host and not in_rend:
            diffs.append(SettingsDiff(target_id, "+", k, host_value=host[k]))
        elif in_rend and not in_host:
            diffs.append(SettingsDiff(target_id, "-", k, rendered_value=rendered[k]))
        elif host[k] != rendered[k]:
            diffs.append(SettingsDiff(target_id, "~", k, host[k], rendered[k]))
    return diffs


def reconcile_settings(
    config: Config,
    target_ids: List[str],
    *,
    settings_path: Path,
    apply: bool,
) -> List[SettingsDiff]:
    """Diff each claude target's live settings against settings.yaml.

    With ``apply``, fold every reported diff into that target's exact
    override entry. The drift patch is generated against the
    pre-exact-override intermediate (``base`` + matching group/label
    overrides, via ``render_pre_exact``) so the written override composes
    with group overrides instead of fighting them: a key the host deleted
    folds back as an explicit ``null`` override (stripped at render time),
    and a key where the host already matches the intermediate is pinned
    verbatim so a stale exact override stops re-introducing drift. Comments
    on untouched override keys are preserved.
    """
    if not settings_path.exists():
        raise FileNotFoundError(
            f"{settings_path} not found; run `promptdeploy settings init` first"
        )

    doc = load_settings_doc(settings_path)
    claude_ids = _claude_target_ids(config, target_ids)

    all_diffs: List[SettingsDiff] = []
    changed = False
    for tid in claude_ids:
        host = read_live_settings(config.targets[tid])
        rendered = render_settings(doc, tid, config)
        diffs = _diff_target(tid, host, rendered)
        all_diffs.extend(diffs)
        if not apply:
            continue
        if not diffs:
            continue
        intermediate = render_pre_exact(doc, tid, config)
        patch = generate_merge_patch(intermediate, host)  # intermediate -> host
        overrides = doc.get("overrides")
        if overrides is None:  # absent key or a bare `overrides:` (null)
            overrides = CommentedMap()
            doc["overrides"] = overrides
        ov = overrides.get(tid)
        if ov is None:  # absent entry or a bare `<target>:` (null)
            ov = CommentedMap()
            overrides[tid] = ov
        for d in diffs:
            if d.key in patch:
                ov[d.key] = copy.deepcopy(patch[d.key])
            else:
                # Host matches the intermediate on this key, so only a stale
                # exact override can explain the drift: pin the host value
                # (None for '-' diffs) over it.
                ov[d.key] = copy.deepcopy(d.host_value)
        changed = True
    if apply and changed:
        dump_settings_doc(doc, settings_path)
    return all_diffs
