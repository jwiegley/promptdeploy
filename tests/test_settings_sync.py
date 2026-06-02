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


def _claude_tc(tmp_path, tid, settings: dict):
    import json
    from promptdeploy.config import TargetConfig

    d = tmp_path / tid
    d.mkdir()
    (d / "settings.json").write_text(json.dumps(settings))
    return TargetConfig(id=tid, type="claude", path=d)


def test_init_settings_factors_base_and_overrides(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import init_settings, load_settings_doc

    p = _claude_tc(
        tmp_path, "claude-personal", {"effortLevel": "low", "env": {"A": "1"}}
    )
    q = _claude_tc(
        tmp_path, "claude-positron", {"effortLevel": "high", "env": {"A": "1"}}
    )
    config = Config(source_root=tmp_path, targets={p.id: p, q.id: q}, groups={})
    out = tmp_path / "settings.yaml"

    init_settings(
        config,
        ["claude-personal", "claude-positron"],
        from_ref="claude-personal",
        out_path=out,
        force=False,
    )

    doc = load_settings_doc(out)
    assert doc["base"]["effortLevel"] == "low"
    assert doc["base"]["env"] == {"A": "1"}
    assert doc["overrides"]["claude-positron"] == {"effortLevel": "high"}
    assert "claude-personal" not in doc.get("overrides", {})


def test_init_settings_refuses_existing_without_force(tmp_path):
    import pytest
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import init_settings

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base: {}\n")
    with pytest.raises(FileExistsError):
        init_settings(
            config, ["claude-personal"], from_ref=None, out_path=out, force=False
        )


# --- guard-branch tests (each hits one `raise ValueError` statement; required
# --- at THIS commit because the lefthook per-commit pytest hook enforces the
# --- 100% line gate — they cannot wait for Task 5.6) ---


def test_init_settings_no_claude_targets_raises(tmp_path):
    # A selection containing only a non-claude target -> `_claude_target_ids` is
    # empty -> `raise ValueError("no claude targets selected")`.
    import json
    import pytest
    from promptdeploy.config import Config, TargetConfig
    from promptdeploy.settings_sync import init_settings

    d = tmp_path / "droid-x"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({}))
    tc = TargetConfig(id="droid-x", type="droid", path=d)
    config = Config(source_root=tmp_path, targets={tc.id: tc}, groups={})
    out = tmp_path / "settings.yaml"
    with pytest.raises(ValueError, match="no claude targets"):
        init_settings(config, ["droid-x"], from_ref=None, out_path=out, force=False)


def test_init_settings_from_not_among_targets_raises(tmp_path):
    # `--from` names a claude target outside the selected set ->
    # `raise ValueError("--from ... is not among the selected claude targets")`.
    import pytest
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import init_settings

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    q = _claude_tc(tmp_path, "claude-positron", {"effortLevel": "high"})
    config = Config(source_root=tmp_path, targets={p.id: p, q.id: q}, groups={})
    out = tmp_path / "settings.yaml"
    with pytest.raises(ValueError, match="--from"):
        init_settings(
            config,
            ["claude-personal"],
            from_ref="claude-positron",
            out_path=out,
            force=False,
        )


def test_reconcile_reports_diff_without_apply(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings

    p = _claude_tc(
        tmp_path, "claude-personal", {"effortLevel": "low", "autoUpdates": False}
    )
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\n")

    diffs = reconcile_settings(
        config, ["claude-personal"], settings_path=out, apply=False
    )
    # autoUpdates is on the host but not rendered -> reported as host-only ("+").
    keys = {(d.target_id, d.kind, d.key) for d in diffs}
    assert ("claude-personal", "+", "autoUpdates") in keys
    # No write happened.
    assert "autoUpdates" not in out.read_text()


def test_reconcile_apply_writes_override(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings, load_settings_doc

    p = _claude_tc(tmp_path, "claude-positron", {"effortLevel": "high"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\n")

    reconcile_settings(config, ["claude-positron"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert doc["overrides"]["claude-positron"]["effortLevel"] == "high"


def test_reconcile_requires_existing_yaml(tmp_path):
    import pytest
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    with pytest.raises(FileNotFoundError):
        reconcile_settings(
            config,
            ["claude-personal"],
            settings_path=tmp_path / "nope.yaml",
            apply=False,
        )


def test_reconcile_reports_rendered_only_key(tmp_path):
    # Covers the '-' diff kind: settings.yaml renders a key the host lacks.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings

    p = _claude_tc(tmp_path, "claude-personal", {})  # empty host settings.json
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  model: opus\n")
    diffs = reconcile_settings(
        config, ["claude-personal"], settings_path=out, apply=False
    )
    assert ("claude-personal", "-", "model") in {
        (d.target_id, d.kind, d.key) for d in diffs
    }


def test_reconcile_apply_no_drift_writes_nothing(tmp_path):
    # Covers `if not drifted: continue` and the `apply and not changed` no-dump path.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\n")
    before = out.read_text()
    diffs = reconcile_settings(
        config, ["claude-personal"], settings_path=out, apply=True
    )
    assert diffs == []
    assert out.read_text() == before


def test_reconcile_apply_removes_override_when_host_matches_base(tmp_path):
    # Covers the `else: ov.pop(...)` branch: host equals base, but an existing
    # override makes rendered differ -> the override key is dropped.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings, load_settings_doc

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "low"})  # == base
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text(
        "base:\n  effortLevel: low\noverrides:\n  claude-x:\n    effortLevel: high\n"
    )
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert "effortLevel" not in doc["overrides"]["claude-x"]
