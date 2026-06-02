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

from ruamel.yaml import YAML


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
