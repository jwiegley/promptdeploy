# tests/test_settings_sync.py
"""Tests for settings init/reconcile I/O orchestration."""

from promptdeploy.settings_sync import load_settings_doc, dump_settings_doc


def test_dump_then_load_roundtrips(tmp_path):
    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)  # absent -> empty mapping
    doc["base"] = {"effortLevel": "low"}
    dump_settings_doc(doc, path)
    again = load_settings_doc(path)
    assert again["base"]["effortLevel"] == "low"


def test_dump_preserves_comments(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("base:\n  # keep me\n  effortLevel: low\n")
    doc = load_settings_doc(path)
    doc["base"]["model"] = "sonnet"
    dump_settings_doc(doc, path)
    text = path.read_text()
    assert "# keep me" in text
    assert "model: sonnet" in text


def test_dump_is_atomic_no_tmp_left(tmp_path):
    path = tmp_path / "settings.yaml"
    dump_settings_doc(load_settings_doc(path), path)
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_dump_cleans_up_tmp_on_replace_failure(tmp_path, monkeypatch):
    # Drives the `except BaseException -> os.unlink(tmp)` cleanup branch.
    # Mirrors tests/test_manifest.py::test_cleanup_on_replace_failure_unlink_fails.
    import os
    import pytest

    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)
    doc["base"] = {"effortLevel": "low"}

    def boom(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        dump_settings_doc(doc, path)
    # tmp file was unlinked; nothing left behind.
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_dump_swallows_unlink_failure_and_reraises(tmp_path, monkeypatch):
    # Drives the inner `except OSError: pass` while the original error propagates:
    # os.replace raises, then os.unlink ALSO raises -> the unlink OSError is
    # swallowed and the ORIGINAL replace error is re-raised.
    import os
    import pytest

    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)
    doc["base"] = {"effortLevel": "low"}

    def boom_replace(_src, _dst):
        raise RuntimeError("replace failed")

    def boom_unlink(_p):
        raise OSError("unlink failed too")

    monkeypatch.setattr(os, "replace", boom_replace)
    monkeypatch.setattr(os, "unlink", boom_unlink)
    with pytest.raises(RuntimeError, match="replace failed"):
        dump_settings_doc(doc, path)


def test_read_live_settings_strips_hooks_and_mcp(tmp_path):
    import json
    from promptdeploy.config import TargetConfig
    from promptdeploy.settings_sync import read_live_settings

    tgt = tmp_path / "claude-x"
    tgt.mkdir()
    (tgt / "settings.json").write_text(
        json.dumps(
            {
                "effortLevel": "low",
                "hooks": {"Stop": [1]},
                "mcpServers": {"pal": {}},
            }
        )
    )
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    assert read_live_settings(tc) == {"effortLevel": "low"}
