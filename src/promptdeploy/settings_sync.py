# src/promptdeploy/settings_sync.py
"""I/O orchestration for `settings init` and `settings reconcile`.

Uses ruamel.yaml round-trip so comments and key order survive write-back.
Pure rendering/merge logic lives in ``settings.py``.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .config import Config
from .settings import generate_merge_patch, strip_keys
from .targets import create_target

_MANAGED_ELSEWHERE = {"hooks", "mcpServers"}


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


def dump_settings_doc(doc, path: Path) -> None:
    """Atomically write a round-trip doc back to settings.yaml."""
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    text = buf.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_live_settings(target_config) -> Dict[str, Any]:
    """Return a target's live settings.json minus hooks/mcpServers.

    Pulls remote state via the target's prepare()/cleanup() lifecycle (rsync for
    remote targets, no-op locally) and reads through the public accessor.
    """
    target = create_target(target_config)
    try:
        target.prepare()
        raw = target.read_settings_json()
    finally:
        target.cleanup()
    return strip_keys(raw, _MANAGED_ELSEWHERE)


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
