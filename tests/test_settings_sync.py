# tests/test_settings_sync.py
"""Tests for settings init/reconcile I/O orchestration."""

from promptdeploy.settings_sync import dump_settings_doc, load_settings_doc


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


def test_dump_quotes_yaml11_boolean_like_strings(tmp_path):
    # The deploy pipeline reads settings.yaml with PyYAML (YAML 1.1, where
    # bare on/off/yes/no are booleans) while init/reconcile write it with
    # ruamel (YAML 1.2, where they are plain strings). dump must quote such
    # strings so both parsers agree on the value.
    import yaml

    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)
    doc["base"] = {
        "a": "on",
        "b": "Yes",
        "c": "n",
        "mixed": ["off", True, 3],
        "plain": "dark",
    }
    dump_settings_doc(doc, path)
    data = yaml.safe_load(path.read_text())
    assert data["base"]["a"] == "on"  # not True
    assert data["base"]["b"] == "Yes"  # not True
    assert data["base"]["c"] == "n"
    assert data["base"]["mixed"] == ["off", True, 3]  # real bools untouched
    assert data["base"]["plain"] == "dark"


def test_dump_preserves_existing_quote_style(tmp_path):
    # An already-quoted scalar keeps its original style instead of being
    # re-wrapped by the YAML-1.1 boolean quoting pass.
    import yaml

    path = tmp_path / "settings.yaml"
    path.write_text('base:\n  a: "on"\n')
    doc = load_settings_doc(path)
    doc["base"]["b"] = "off"
    dump_settings_doc(doc, path)
    text = path.read_text()
    assert 'a: "on"' in text  # double quotes preserved
    data = yaml.safe_load(text)
    assert data["base"] == {"a": "on", "b": "off"}


def test_repo_settings_yaml_parses_identically_in_both_dialects():
    # Guard the dual-parser seam on the real file: source.py reads
    # settings.yaml with yaml.safe_load (YAML 1.1) while settings_sync
    # round-trips it with ruamel (YAML 1.2). Both must see the same data.
    from pathlib import Path

    import pytest
    import yaml
    from ruamel.yaml import YAML

    path = Path(__file__).resolve().parent.parent / "settings.yaml"
    if not path.exists():
        pytest.skip("repo settings.yaml not present")
    text = path.read_text("utf-8")
    assert yaml.safe_load(text) == YAML(typ="safe").load(text)


def test_dump_preserves_existing_file_mode(tmp_path):
    # The mkstemp temp file is created 0600; dump must restore the
    # destination's prior mode instead of clobbering it.
    import os
    import stat

    path = tmp_path / "settings.yaml"
    path.write_text("base: {}\n")
    os.chmod(path, 0o640)
    doc = load_settings_doc(path)
    doc["base"] = {"effortLevel": "low"}
    dump_settings_doc(doc, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_dump_new_file_mode_honors_umask(tmp_path):
    import os
    import stat

    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)
    doc["base"] = {"effortLevel": "low"}
    old = os.umask(0o027)
    try:
        dump_settings_doc(doc, path)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o666 & ~0o027


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
                "extraKnownMarketplaces": {"acme": {}},
                "enabledPlugins": {"p@acme": True},
            }
        )
    )
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    # hooks/mcpServers AND the marketplace keys are managed elsewhere and must
    # be stripped from live settings before reconcile compares them.
    assert read_live_settings(tc) == {"effortLevel": "low"}


def test_read_live_settings_strips_nulls(tmp_path):
    # B10: explicit nulls in live settings.json are unrepresentable in a merge
    # patch (RFC 7396 uses null to mean delete), so they are stripped before
    # any diffing -- mirroring render_settings. Without this, reconcile
    # --apply could never converge on a host whose settings.json holds nulls.
    import json

    from promptdeploy.config import TargetConfig
    from promptdeploy.settings_sync import read_live_settings

    tgt = tmp_path / "claude-x"
    tgt.mkdir()
    (tgt / "settings.json").write_text(
        json.dumps({"effortLevel": None, "env": {"X": None, "Y": "2"}})
    )
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    assert read_live_settings(tc) == {"env": {"Y": "2"}}


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


def test_init_settings_identical_hosts_omit_overrides(tmp_path):
    # When every selected host matches the reference exactly, the generated
    # settings.yaml carries only `base` -- no empty `overrides` block and no
    # empty per-target entries.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import init_settings, load_settings_doc

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    q = _claude_tc(tmp_path, "claude-positron", {"effortLevel": "low"})
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
    assert doc["base"] == {"effortLevel": "low"}
    assert "overrides" not in doc


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


# --- guard-branch tests (each hits one `raise ValueError` statement, keeping
# --- the 100% line-coverage gate satisfied) ---


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
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

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
    out.write_text("base:\n  model: fable\n")
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


def test_reconcile_apply_pins_override_when_host_matches_base(tmp_path):
    # Host equals base (the pre-exact intermediate), but a stale exact
    # override makes rendered differ -> the override key is pinned to the
    # host value rather than popped, and a second reconcile is clean.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "low"})  # == base
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text(
        "base:\n  effortLevel: low\noverrides:\n  claude-x:\n    effortLevel: high\n"
    )
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert doc["overrides"]["claude-x"]["effortLevel"] == "low"
    assert (
        reconcile_settings(config, ["claude-x"], settings_path=out, apply=False) == []
    )


def test_reconcile_apply_converges_under_group_override(tmp_path):
    # B7: the drift patch must be generated against the pre-exact-override
    # intermediate (base + group patches), not raw base. Here the host sits
    # at the base value while a group override moves rendered away from it;
    # apply must pin the host value in the exact override. (The old code
    # diffed against raw base, found no patch entry, popped a nonexistent
    # exact key, and never converged.)
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "low"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={"g": ["claude-x"]})
    out = tmp_path / "settings.yaml"
    out.write_text(
        "base:\n  effortLevel: low\noverrides:\n  g:\n    effortLevel: high\n"
    )

    diffs = reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    assert [(d.kind, d.key) for d in diffs] == [("~", "effortLevel")]

    doc = load_settings_doc(out)
    # Exact override pins the host value over the group override...
    assert doc["overrides"]["claude-x"]["effortLevel"] == "low"
    # ...without touching the group override itself.
    assert doc["overrides"]["g"]["effortLevel"] == "high"
    # Converged: a second reconcile reports no drift.
    assert (
        reconcile_settings(config, ["claude-x"], settings_path=out, apply=False) == []
    )


def test_reconcile_apply_folds_host_deletion_as_null_override(tmp_path):
    # B8: a key the host deleted ('-' diff) folds back as an explicit null
    # override, which render_settings strips -> the next reconcile is clean.
    # (The old code skipped '-' diffs entirely and wrote nothing.)
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "low"})  # no 'theme'
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\n  theme: dark\n")

    diffs = reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    assert [(d.kind, d.key) for d in diffs] == [("-", "theme")]

    doc = load_settings_doc(out)
    ov = doc["overrides"]["claude-x"]
    assert "theme" in ov and ov["theme"] is None
    assert (
        reconcile_settings(config, ["claude-x"], settings_path=out, apply=False) == []
    )


def test_reconcile_apply_nulls_key_added_only_by_stale_exact_override(tmp_path):
    # '-' diff where the pre-exact intermediate also lacks the key (it came
    # only from the stale exact override): the key is not in the drift patch,
    # so the pin path writes the host's "value" -- an explicit null.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "low"})  # no 'theme'
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text(
        "base:\n  effortLevel: low\noverrides:\n  claude-x:\n    theme: dark\n"
    )
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert doc["overrides"]["claude-x"]["theme"] is None
    assert (
        reconcile_settings(config, ["claude-x"], settings_path=out, apply=False) == []
    )


def test_reconcile_apply_normalizes_null_override_entry(tmp_path):
    # B9: an empty `claude-x:` override entry loads as None; apply must
    # replace it with a fresh mapping instead of raising TypeError on
    # item assignment.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "high"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\noverrides:\n  claude-x:\n")
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert doc["overrides"]["claude-x"]["effortLevel"] == "high"


def test_reconcile_apply_normalizes_null_overrides_block(tmp_path):
    # B9 (sibling case): a bare `overrides:` block loads as None; apply must
    # replace it with a fresh mapping instead of raising AttributeError.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import load_settings_doc, reconcile_settings

    p = _claude_tc(tmp_path, "claude-x", {"effortLevel": "high"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"
    out.write_text("base:\n  effortLevel: low\noverrides:\n")
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert doc["overrides"]["claude-x"]["effortLevel"] == "high"
