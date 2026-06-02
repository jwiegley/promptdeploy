# settings.yaml Single-Source Claude Settings — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repo-level `settings.yaml` that renders per-target Claude Code `settings.json` (via `base` + `overrides` JSON Merge Patch), deployed as a first-class promptdeploy item, plus `settings init`/`settings reconcile` CLI subcommands.

**Architecture:** A pure rendering core (`settings.py`) computes a target's settings dict from `base` + matching `overrides` (RFC 7386 merge patch, `null` deletes). The deploy loop gets a dedicated `settings` branch that gently merges those keys into `settings.json` — touching only top-level keys it renders, tracked per-target in the manifest's new `managed_keys`, leaving `hooks`/`mcpServers`/external/unknown keys intact. An I/O module (`settings_sync.py`, using ruamel.yaml for comment-preserving write-back) powers `init` (bootstrap from live hosts) and `reconcile` (pull host drift into overrides).

**Tech Stack:** Python 3.12, PyYAML (deploy-time reads), ruamel.yaml (write-back), pytest with a 100% coverage gate, Nix (`nix flake check`).

**Spec:** `docs/superpowers/specs/2026-06-01-settings-yaml-design.md` — read it first.

**Conventions (match these):**
- Tests use `tmp_path`, plain functions/classes, and build `Config`/`TargetConfig`/`ClaudeTarget` inline (see `tests/test_deploy.py`, `tests/test_claude_target.py`). `conftest.py` holds no shared fixtures.
- All JSON/file writes are atomic (`tempfile.mkstemp` + `os.replace`) — reuse `ClaudeTarget._save_json` / mirror it.
- 100% coverage is enforced; every new branch needs a test.
- Run the full suite with: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
- Lint/type/format gate: `nix flake check` (or `ruff format --check . && ruff check . && PYTHONPATH=src mypy src/ tests/`).
- Commit per task. Branch is `settings-yaml` (already created; spec already committed).

---

## File Structure

**New source modules:**
- `src/promptdeploy/settings.py` — pure functions only (no I/O): `apply_merge_patch`, `generate_merge_patch`, `strip_keys`, `strip_nulls`, `render_settings`.
- `src/promptdeploy/settings_sync.py` — I/O orchestration for `init`/`reconcile`: ruamel load/dump, live-settings read via a target, factoring, diffing, write-back.

**Modified source:**
- `src/promptdeploy/manifest.py` — `ManifestItem.managed_keys`; serialize it.
- `src/promptdeploy/source.py` — `discover_settings()`; add to `discover_all()`.
- `src/promptdeploy/targets/base.py` — default no-op `deploy_settings`/`remove_settings`, default `read_settings_json` → `{}`.
- `src/promptdeploy/targets/claude.py` — implement the three methods.
- `src/promptdeploy/targets/droid.py`, `targets/opencode.py` — `should_skip` skips `settings`.
- `src/promptdeploy/targets/remote.py` — delegate `read_settings_json`.
- `src/promptdeploy/deploy.py` — type maps; dedicated `settings` deploy branch; `_remove_item` routing.
- `src/promptdeploy/status.py` — add `settings` (and missing `prompt`) to its `_TYPE_TO_CATEGORY`.
- `src/promptdeploy/cli.py` — `--only-type settings`; `list` labels/iteration; `settings` subcommand group.
- `src/promptdeploy/validate.py` — `validate_settings`.
- `pyproject.toml`, `flake.nix` — ruamel.yaml dependency.
- `CLAUDE.md`, `README.md`, `PROMPTDEPLOY.md` — docs.

**New tests:** `tests/test_settings.py`, `tests/test_settings_sync.py`. **Extended tests:** `test_manifest.py`, `test_claude_target.py`, `test_droid_target.py`, `test_opencode_target.py`, `test_gptel_target.py`, `test_remote_target.py`, `test_source.py`, `test_deploy.py`, `test_status.py`, `test_cli.py`, `test_validate.py`.

---

## Chunk 1: Pure rendering core (`settings.py`)

Self-contained, no integration. Establishes the merge-patch invariant the whole feature rests on.

### Task 1.1: `apply_merge_patch`

**Files:**
- Create: `src/promptdeploy/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
"""Tests for the pure settings rendering core."""

from promptdeploy.settings import (
    apply_merge_patch,
    generate_merge_patch,
    render_settings,
    strip_keys,
    strip_nulls,
)


class TestApplyMergePatch:
    def test_adds_and_changes_scalars(self):
        base = {"a": 1, "b": 2}
        assert apply_merge_patch(base, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}

    def test_null_deletes_key(self):
        assert apply_merge_patch({"a": 1, "b": 2}, {"b": None}) == {"a": 1}

    def test_null_on_absent_key_is_noop(self):
        assert apply_merge_patch({"a": 1}, {"z": None}) == {"a": 1}

    def test_nested_dicts_merge_deeply(self):
        base = {"env": {"X": "1", "Y": "2"}}
        patch = {"env": {"Y": "3", "Z": "4"}}
        assert apply_merge_patch(base, patch) == {"env": {"X": "1", "Y": "3", "Z": "4"}}

    def test_nested_null_deletes_one_subkey(self):
        base = {"env": {"X": "1", "Y": "2"}}
        assert apply_merge_patch(base, {"env": {"X": None}}) == {"env": {"Y": "2"}}

    def test_dict_patch_over_nondict_target_replaces(self):
        # RFC 7386: a dict patch over a non-dict target treats target as {}.
        assert apply_merge_patch({"a": 5}, {"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_inputs_not_mutated(self):
        base = {"env": {"X": "1"}}
        apply_merge_patch(base, {"env": {"X": "2"}})
        assert base == {"env": {"X": "1"}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestApplyMergePatch -v`
Expected: FAIL — `ModuleNotFoundError: promptdeploy.settings`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/promptdeploy/settings.py
"""Pure rendering core for settings.yaml -> per-target settings.json.

No I/O lives here. ``apply_merge_patch``/``generate_merge_patch`` implement
RFC 7386 (JSON Merge Patch); ``render_settings`` composes ``base`` with the
``overrides`` that match a target.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable

from .config import Config


def apply_merge_patch(base: Any, patch: Any) -> Any:
    """Apply an RFC 7386 JSON Merge Patch. Pure; inputs are never mutated."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result: Dict[str, Any] = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = apply_merge_patch(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestApplyMergePatch -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings.py tests/test_settings.py
git commit -m "feat(settings): add apply_merge_patch (RFC 7386)"
```

### Task 1.2: `generate_merge_patch` (the inverse)

**Files:**
- Modify: `src/promptdeploy/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
class TestGenerateMergePatch:
    def test_added_key(self):
        assert generate_merge_patch({"a": 1}, {"a": 1, "b": 2}) == {"b": 2}

    def test_removed_key_becomes_null(self):
        assert generate_merge_patch({"a": 1, "b": 2}, {"a": 1}) == {"b": None}

    def test_changed_scalar(self):
        assert generate_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}

    def test_identical_yields_empty_patch(self):
        assert generate_merge_patch({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 2}}) == {}

    def test_nested_diff_is_minimal(self):
        base = {"env": {"X": "1", "Y": "2"}}
        target = {"env": {"X": "1", "Y": "9", "Z": "3"}}
        assert generate_merge_patch(base, target) == {"env": {"Y": "9", "Z": "3"}}

    def test_roundtrip_reproduces_target(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}, "e": 5}
        target = {"a": 1, "b": {"c": 9}, "f": 7}  # d removed within b, e removed, f added
        patch = generate_merge_patch(base, target)
        assert apply_merge_patch(base, patch) == target
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestGenerateMergePatch -v`
Expected: FAIL — `ImportError: cannot import name 'generate_merge_patch'`.

- [ ] **Step 3: Implement**

```python
def generate_merge_patch(base: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    """Return the minimal patch ``P`` with ``apply_merge_patch(base, P) == target``.

    ``base`` and ``target`` are both dicts (the settings domain). Keys dropped
    in ``target`` become ``None``; nested dicts recurse; everything else is
    replaced by the ``target`` value.
    """
    patch: Dict[str, Any] = {}
    for key in base:
        if key not in target:
            patch[key] = None
    for key, tval in target.items():
        if key not in base:
            patch[key] = copy.deepcopy(tval)
            continue
        bval = base[key]
        if bval == tval:
            continue
        if isinstance(bval, dict) and isinstance(tval, dict):
            patch[key] = generate_merge_patch(bval, tval)
        else:
            patch[key] = copy.deepcopy(tval)
    return patch
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestGenerateMergePatch -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings.py tests/test_settings.py
git commit -m "feat(settings): add generate_merge_patch (RFC 7386 diff)"
```

### Task 1.3: `strip_keys` + `strip_nulls`

**Files:**
- Modify: `src/promptdeploy/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
class TestStripHelpers:
    def test_strip_keys_removes_named_top_level_keys(self):
        d = {"env": {}, "hooks": {"x": 1}, "mcpServers": {"y": 2}, "model": "a"}
        assert strip_keys(d, {"hooks", "mcpServers"}) == {"env": {}, "model": "a"}

    def test_strip_nulls_removes_none_values_recursively(self):
        d = {"a": 1, "b": None, "env": {"X": "1", "Y": None}}
        assert strip_nulls(d) == {"a": 1, "env": {"X": "1"}}

    def test_strip_nulls_preserves_empty_dicts(self):
        # extraKnownMarketplaces: {} is a legitimate setting and must survive.
        d = {"extraKnownMarketplaces": {}, "env": {"X": None}}
        assert strip_nulls(d) == {"extraKnownMarketplaces": {}, "env": {}}

    def test_strip_nulls_leaves_lists_atomic(self):
        d = {"allowWrite": ["/tmp", None]}
        assert strip_nulls(d) == {"allowWrite": ["/tmp", None]}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestStripHelpers -v`
Expected: FAIL — import error for `strip_keys`/`strip_nulls`.

- [ ] **Step 3: Implement**

```python
def strip_keys(d: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Return a shallow copy of ``d`` without the named top-level keys."""
    drop = set(keys)
    return {k: v for k, v in d.items() if k not in drop}


def strip_nulls(value: Any) -> Any:
    """Recursively drop ``None`` values from dicts.

    Empty dicts are preserved (e.g. ``extraKnownMarketplaces: {}`` is a valid
    setting). Lists are atomic per RFC 7386 — their elements are not inspected.
    """
    if not isinstance(value, dict):
        return value
    return {k: strip_nulls(v) for k, v in value.items() if v is not None}
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestStripHelpers -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings.py tests/test_settings.py
git commit -m "feat(settings): add strip_keys and strip_nulls helpers"
```

### Task 1.4: `render_settings`

**Files:**
- Modify: `src/promptdeploy/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
from promptdeploy.config import Config, TargetConfig


def _cfg(*target_ids: str, groups=None) -> Config:
    targets = {
        tid: TargetConfig(id=tid, type="claude", path=__import__("pathlib").Path("/x") / tid)
        for tid in target_ids
    }
    return Config(source_root=__import__("pathlib").Path("/x"),
                  targets=targets, groups=groups or {})


class TestRenderSettings:
    def test_base_only_when_no_override_matches(self):
        doc = {"base": {"effortLevel": "low", "env": {"A": "1"}}}
        cfg = _cfg("claude-personal")
        assert render_settings(doc, "claude-personal", cfg) == {
            "effortLevel": "low", "env": {"A": "1"}}

    def test_exact_target_override_add_change_delete(self):
        doc = {
            "base": {"effortLevel": "low", "env": {"A": "1", "B": "2"}},
            "overrides": {"claude-positron": {
                "effortLevel": None, "model": "sonnet",
                "env": {"B": "9", "A": None}}},
        }
        cfg = _cfg("claude-positron")
        assert render_settings(doc, "claude-positron", cfg) == {
            "model": "sonnet", "env": {"B": "9"}}

    def test_group_override_applies_via_config_groups(self):
        doc = {"base": {"effortLevel": "low"},
               "overrides": {"positron": {"effortLevel": "med"}}}
        cfg = _cfg("claude-positron", groups={"positron": ["claude-positron"]})
        assert render_settings(doc, "claude-positron", cfg) == {"effortLevel": "med"}

    def test_exact_target_wins_over_group(self):
        doc = {
            "base": {"x": "base"},
            "overrides": {
                "positron": {"x": "group"},          # group, applied first
                "claude-positron": {"x": "exact"},   # exact id, applied last
            },
        }
        cfg = _cfg("claude-positron", groups={"positron": ["claude-positron"]})
        assert render_settings(doc, "claude-positron", cfg)["x"] == "exact"

    def test_overlapping_groups_apply_in_file_order(self):
        doc = {
            "base": {"x": "base"},
            "overrides": {"g1": {"x": "first"}, "g2": {"x": "second"}},
        }
        cfg = _cfg("t", groups={"g1": ["t"], "g2": ["t"]})
        # g2 appears later in file order -> wins
        assert render_settings(doc, "t", cfg)["x"] == "second"

    def test_strips_hooks_and_mcp_servers(self):
        doc = {"base": {"hooks": {"X": 1}, "mcpServers": {"Y": 2}, "model": "a"}}
        assert render_settings(doc, "t", _cfg("t")) == {"model": "a"}

    def test_strips_literal_null_in_base(self):
        doc = {"base": {"a": 1, "b": None}}
        assert render_settings(doc, "t", _cfg("t")) == {"a": 1}

    def test_missing_base_and_overrides_yield_empty(self):
        assert render_settings({}, "t", _cfg("t")) == {}

    def test_none_override_value_is_ignored(self):
        doc = {"base": {"a": 1}, "overrides": {"t": None}}
        assert render_settings(doc, "t", _cfg("t")) == {"a": 1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py::TestRenderSettings -v`
Expected: FAIL — `render_settings` not yet defined.

- [ ] **Step 3: Implement**

```python
def render_settings(doc: Dict[str, Any], target_id: str, config: Config) -> Dict[str, Any]:
    """Render the concrete managed settings for one target.

    Starts from ``doc['base']`` and applies every matching ``overrides`` entry as
    a merge patch: group/label overrides first (in file order), then the exact
    ``target_id`` override last (most specific wins). Finally strips
    ``hooks``/``mcpServers`` and any remaining ``null`` values. Returns plain
    dicts only — no ``null`` reaches the caller.
    """
    base = doc.get("base") or {}
    result: Dict[str, Any] = copy.deepcopy(dict(base))

    overrides = doc.get("overrides") or {}
    exact = None
    for key, patch in overrides.items():
        if patch is None:
            continue
        if key == target_id:
            exact = patch
            continue
        if target_id in config.groups.get(key, []):
            result = apply_merge_patch(result, patch)
    if exact is not None:
        result = apply_merge_patch(result, exact)

    result = strip_keys(result, {"hooks", "mcpServers"})
    return strip_nulls(result)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py -v`
Expected: PASS (all classes).

- [ ] **Step 5: Coverage + lint check, then commit**

Run: `PYTHONPATH=src python -m pytest tests/test_settings.py --cov=promptdeploy.settings --cov-report=term-missing`
Expected: `settings.py` 100%.
Run: `ruff format --check src/promptdeploy/settings.py tests/test_settings.py && ruff check src/promptdeploy/settings.py tests/test_settings.py && PYTHONPATH=src mypy src/promptdeploy/settings.py`
Expected: clean.

```bash
git add src/promptdeploy/settings.py tests/test_settings.py
git commit -m "feat(settings): add render_settings (base + overrides precedence)"
```

---

## Chunk 2: Manifest field + target layer

Makes the target/manifest layer ready to deploy settings, fully unit-tested, before wiring the loop.

### Task 2.1: `ManifestItem.managed_keys`

**Files:**
- Modify: `src/promptdeploy/manifest.py:20-25` (dataclass), `:87-96` (serialize)
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_manifest.py`)

```python
def test_managed_keys_roundtrips(tmp_path):
    from promptdeploy.manifest import (
        Manifest, ManifestItem, load_manifest, save_manifest,
    )
    m = Manifest()
    m.items.setdefault("settings", {})["settings"] = ManifestItem(
        source_hash="sha256:abc", managed_keys=["env", "model"])
    path = tmp_path / ".prompt-deploy-manifest.json"
    save_manifest(m, path)
    loaded = load_manifest(path)
    item = loaded.items["settings"]["settings"]
    assert item.managed_keys == ["env", "model"]


def test_managed_keys_absent_serializes_without_field(tmp_path):
    import json
    from promptdeploy.manifest import Manifest, ManifestItem, save_manifest
    m = Manifest()
    m.items.setdefault("agents", {})["a"] = ManifestItem(source_hash="sha256:x")
    path = tmp_path / ".prompt-deploy-manifest.json"
    save_manifest(m, path)
    data = json.loads(path.read_text())
    assert "managed_keys" not in data["items"]["agents"]["a"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_manifest.py::test_managed_keys_roundtrips -v`
Expected: FAIL — `TypeError: ManifestItem.__init__() got an unexpected keyword argument 'managed_keys'`.

- [ ] **Step 3: Implement** — edit the dataclass:

```python
@dataclass
class ManifestItem:
    """Tracks a single deployed item."""

    source_hash: str
    target_path: Optional[str] = None
    config_key: Optional[str] = None
    managed_keys: Optional[list[str]] = None
```

In `save_manifest`, inside the per-item loop, after the `config_key` block:

```python
            if item.config_key is not None:
                entry["config_key"] = item.config_key
            if item.managed_keys is not None:
                entry["managed_keys"] = item.managed_keys
```

(`load_manifest` already uses `ManifestItem(**vals)`, so deserialization needs no change.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/manifest.py tests/test_manifest.py
git commit -m "feat(manifest): track managed_keys on ManifestItem"
```

### Task 2.2: Base `Target` default hooks

**Files:**
- Modify: `src/promptdeploy/targets/base.py` (add three methods near `prepare`/`finalize`)
- Test: covered indirectly; add a focused test in `tests/test_gptel_target.py` (gptel inherits the no-ops).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gptel_target.py`)

```python
def test_base_settings_methods_are_noops(tmp_path):
    from promptdeploy.targets.gptel import GptelTarget
    d = tmp_path / "g"
    d.mkdir()
    t = GptelTarget("g", d)
    # Inherited no-ops must not raise and read returns {}.
    t.deploy_settings({"a": 1}, [])
    t.remove_settings(["a"])
    assert t.read_settings_json() == {}
```

(If `GptelTarget`'s constructor signature differs, mirror the existing gptel tests in the file.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_gptel_target.py::test_base_settings_methods_are_noops -v`
Expected: FAIL — `AttributeError: 'GptelTarget' object has no attribute 'deploy_settings'`.

- [ ] **Step 3: Implement** — add to `base.py` `Target` (after `cleanup`):

```python
    def deploy_settings(self, rendered: dict, previous_keys: list[str]) -> None:
        """Merge rendered Claude settings into the target's settings.json.

        Default no-op so non-Claude targets need no changes.
        """

    def remove_settings(self, previous_keys: list[str]) -> None:
        """Remove previously-managed settings keys. No-op by default."""

    def read_settings_json(self) -> dict:
        """Return the target's current settings.json as a dict.

        Returns ``{}`` when the target has no Claude settings file (the default
        for non-Claude targets).
        """
        return {}
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_gptel_target.py::test_base_settings_methods_are_noops -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/targets/base.py tests/test_gptel_target.py
git commit -m "feat(targets): add default settings hooks to Target ABC"
```

### Task 2.3: `ClaudeTarget.deploy_settings` (gentle merge)

**Files:**
- Modify: `src/promptdeploy/targets/claude.py` (add near the other deploy methods; uses existing `_settings_path`, `_load_json`, `_save_json`)
- Test: `tests/test_claude_target.py`

- [ ] **Step 1: Write the failing test** (append a class to `tests/test_claude_target.py`)

```python
class TestDeploySettings:
    def _seed(self, tmp_path: Path, data: dict) -> ClaudeTarget:
        target = _make_target(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(data))
        return target

    def test_creates_file_when_absent(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_settings({"effortLevel": "low"}, [])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data == {"effortLevel": "low"}

    def test_merges_without_touching_hooks_or_mcp(self, tmp_path: Path):
        target = self._seed(tmp_path, {
            "hooks": {"Stop": [{"_source": "claude-vault"}]},
            "mcpServers": {"pal": {"command": "x"}},
            "model": "opus",
        })
        target.deploy_settings({"effortLevel": "high", "model": "sonnet"}, ["model"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["hooks"] == {"Stop": [{"_source": "claude-vault"}]}
        assert data["mcpServers"] == {"pal": {"command": "x"}}
        assert data["effortLevel"] == "high"
        assert data["model"] == "sonnet"

    def test_removes_previously_managed_key_dropped_from_render(self, tmp_path: Path):
        target = self._seed(tmp_path, {"model": "sonnet", "env": {"A": "1"}})
        # Previously managed {model, env}; now render only {env}. model must go.
        target.deploy_settings({"env": {"A": "1"}}, ["model", "env"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "model" not in data
        assert data["env"] == {"A": "1"}

    def test_preserves_unmanaged_keys(self, tmp_path: Path):
        target = self._seed(tmp_path, {"feedbackSurveyState": {"x": 1}})
        target.deploy_settings({"effortLevel": "low"}, [])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["feedbackSurveyState"] == {"x": 1}
        assert data["effortLevel"] == "low"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestDeploySettings -v`
Expected: FAIL — `deploy_settings` is the base no-op, so the file is never written / assertions fail.

- [ ] **Step 3: Implement** — add to `ClaudeTarget`:

```python
    def deploy_settings(self, rendered: dict, previous_keys: list[str]) -> None:
        path = self._settings_path()
        settings = self._load_json(path)
        for key in previous_keys:
            if key not in rendered:
                settings.pop(key, None)
        for key, value in rendered.items():
            settings[key] = value
        self._save_json(path, settings)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestDeploySettings -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/targets/claude.py tests/test_claude_target.py
git commit -m "feat(claude): gentle settings.json merge via deploy_settings"
```

### Task 2.4: `ClaudeTarget.remove_settings` + `read_settings_json`

**Files:**
- Modify: `src/promptdeploy/targets/claude.py`
- Test: `tests/test_claude_target.py`

- [ ] **Step 1: Write the failing test** (append to `TestDeploySettings` or a new class)

```python
class TestRemoveAndReadSettings:
    def test_remove_settings_pops_keys_preserving_rest(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"model": "x", "env": {"A": "1"}, "hooks": {"Y": 1}}))
        target.remove_settings(["model", "env"])
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data == {"hooks": {"Y": 1}}

    def test_remove_settings_no_file_is_noop(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_settings(["model"])  # must not raise
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_read_settings_json_returns_dict_or_empty(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.read_settings_json() == {}
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "x"}))
        assert target.read_settings_json() == {"model": "x"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestRemoveAndReadSettings -v`
Expected: FAIL (base no-op `remove_settings` leaves the file unchanged; base `read_settings_json` returns `{}` even when a file exists).

- [ ] **Step 3: Implement** — add to `ClaudeTarget`:

```python
    def remove_settings(self, previous_keys: list[str]) -> None:
        path = self._settings_path()
        if not path.exists():
            return
        settings = self._load_json(path)
        for key in previous_keys:
            settings.pop(key, None)
        self._save_json(path, settings)

    def read_settings_json(self) -> dict:
        return self._load_json(self._settings_path())
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestRemoveAndReadSettings -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/targets/claude.py tests/test_claude_target.py
git commit -m "feat(claude): add remove_settings and read_settings_json"
```

### Task 2.5: Droid + OpenCode skip `settings`

**Files:**
- Modify: `src/promptdeploy/targets/droid.py:53-` (`should_skip`), `src/promptdeploy/targets/opencode.py:157-164` (`should_skip`)
- Test: `tests/test_droid_target.py`, `tests/test_opencode_target.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_droid_target.py
def test_should_skip_settings(tmp_path):
    from promptdeploy.targets.droid import DroidTarget
    d = tmp_path / "f"; d.mkdir()
    assert DroidTarget("droid", d).should_skip("settings", "settings") is True
```

```python
# tests/test_opencode_target.py
def test_should_skip_settings(tmp_path):
    from promptdeploy.targets.opencode import OpenCodeTarget
    d = tmp_path / "oc"; d.mkdir()
    assert OpenCodeTarget("opencode", d).should_skip("settings", "settings") is True
```

(Match each file's existing target-construction helper if one exists.)

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_droid_target.py::test_should_skip_settings tests/test_opencode_target.py::test_should_skip_settings -v`
Expected: FAIL (returns `False`).

- [ ] **Step 3: Implement**

In `droid.py` `should_skip`, add as the first check:

```python
        if item_type == "settings":
            return True
```

In `opencode.py` `should_skip`, change the final return:

```python
        return item_type in ("hook", "settings")
```

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_droid_target.py::test_should_skip_settings tests/test_opencode_target.py::test_should_skip_settings -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/targets/droid.py src/promptdeploy/targets/opencode.py tests/test_droid_target.py tests/test_opencode_target.py
git commit -m "feat(targets): droid and opencode skip the settings item"
```

### Task 2.6: `RemoteTarget` delegates `read_settings_json`

**Files:**
- Modify: `src/promptdeploy/targets/remote.py` (add a delegating method alongside the others)
- Test: `tests/test_remote_target.py`

- [ ] **Step 1: Write the failing test** — use the file's existing `MagicMock` inner convention (the real ctor is `RemoteTarget(inner, host, remote_path, staging_path)`; `tests/test_remote_target.py` already builds one with a `MagicMock` inner via keyword args).

```python
def test_read_settings_json_delegates_to_inner():
    from pathlib import Path
    from unittest.mock import MagicMock
    from promptdeploy.targets.remote import RemoteTarget

    inner = MagicMock()
    inner.read_settings_json.return_value = {"model": "x"}
    remote = RemoteTarget(
        inner=inner,
        host="user@host",
        remote_path=Path("/remote/target"),
        staging_path=Path("/staging"),
    )
    assert remote.read_settings_json() == {"model": "x"}
    inner.read_settings_json.assert_called_once_with()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_remote_target.py::test_read_settings_json_delegates_to_inner -v`
Expected: FAIL — without the delegating override, `RemoteTarget` inherits the base `read_settings_json` (returns `{}`), so the assertion `{} == {"model": "x"}` fails (and `assert_called_once_with` fails, since the inner is never consulted).

- [ ] **Step 3: Implement** — in `RemoteTarget`, next to the other delegations (e.g. `rsync_includes`):

```python
    def read_settings_json(self) -> dict:
        return self._inner.read_settings_json()
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_remote_target.py::test_read_settings_json_delegates_to_inner -v`
Expected: PASS.

- [ ] **Step 5: Full chunk check + commit**

Run: `PYTHONPATH=src python -m pytest tests/ -q`
Expected: all pass.

```bash
git add src/promptdeploy/targets/remote.py tests/test_remote_target.py
git commit -m "feat(remote): delegate read_settings_json to inner target"
```

---

## Chunk 3: Discovery + deploy loop + status + CLI list/only-type

End-to-end: after this chunk `promptdeploy deploy`, `status`, and `list` fully handle a `settings.yaml`.

### Task 3.1: `discover_settings`

**Files:**
- Modify: `src/promptdeploy/source.py:40-48` (`discover_all`), add `discover_settings`
- Test: `tests/test_source.py`

- [ ] **Step 1: Write the failing test**

```python
def test_discover_settings_yields_singleton(tmp_path):
    from promptdeploy.source import SourceDiscovery
    (tmp_path / "settings.yaml").write_text(
        "base:\n  effortLevel: low\noverrides:\n  claude-positron:\n    model: sonnet\n")
    items = list(SourceDiscovery(tmp_path).discover_settings())
    assert len(items) == 1
    it = items[0]
    assert it.item_type == "settings"
    assert it.name == "settings"
    assert it.metadata["base"]["effortLevel"] == "low"


def test_discover_settings_absent_yields_nothing(tmp_path):
    from promptdeploy.source import SourceDiscovery
    assert list(SourceDiscovery(tmp_path).discover_settings()) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_source.py::test_discover_settings_yields_singleton -v`
Expected: FAIL — no `discover_settings`.

- [ ] **Step 3: Implement** — add the method and register it in `discover_all`:

```python
    def discover_settings(self) -> Iterator[SourceItem]:
        """Discover the singleton settings master from settings.yaml."""
        settings_path = self.source_root / "settings.yaml"
        if not settings_path.exists():
            return
        content = settings_path.read_bytes()
        try:
            metadata = yaml.safe_load(content)
        except yaml.YAMLError:
            metadata = None
        if not isinstance(metadata, dict):
            metadata = None
        yield SourceItem(
            item_type="settings",
            name="settings",
            path=settings_path,
            metadata=metadata,
            content=content,
        )
```

In `discover_all`, add after `discover_models`:

```python
        yield from self.discover_settings()
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_source.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/source.py tests/test_source.py
git commit -m "feat(source): discover settings.yaml as a singleton item"
```

### Task 3.2: Deploy loop `settings` branch

**Files:**
- Modify: `src/promptdeploy/deploy.py` — `_TYPE_TO_CATEGORY` (~:27), `_CLI_TYPE_TO_ITEM_TYPE` (~:38), add `from .settings import render_settings`, insert the settings branch after `changed` is computed (~:295), extend `_remove_item` (~:189) for `settings`.
- Test: `tests/test_deploy.py`

- [ ] **Step 1: Write the failing tests** (append a class)

```python
class TestDeploySettingsItem:
    def _src_with_settings(self, tmp_path: Path, yaml_text: str) -> Path:
        src = tmp_path / "source"; src.mkdir()
        (src / "settings.yaml").write_text(yaml_text)
        return src

    def test_create_then_skip_then_update(self, tmp_path: Path):
        src = self._src_with_settings(
            tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        a1 = deploy(config)
        assert [a for a in a1 if a.item_type == "settings"][0].action == "create"
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["effortLevel"] == "low"

        a2 = deploy(config)
        assert [a for a in a2 if a.item_type == "settings"][0].action == "skip"

        (src / "settings.yaml").write_text("base:\n  effortLevel: high\n")
        a3 = deploy(config)
        s3 = [a for a in a3 if a.item_type == "settings"][0]
        assert s3.action == "update"
        assert json.loads((tc.path / "settings.json").read_text())["effortLevel"] == "high"

    def test_manifest_records_managed_keys(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n  model: opus\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        manifest = load_manifest(tc.path / MANIFEST_FILENAME)
        assert set(manifest.items["settings"]["settings"].managed_keys) == {"effortLevel", "model"}

    def test_override_applies_per_target(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, (
            "base:\n  effortLevel: low\n"
            "overrides:\n  claude-positron:\n    effortLevel: high\n"))
        personal = _make_claude_target(tmp_path, "claude-personal")
        positron = _make_claude_target(tmp_path, "claude-positron")
        config = _make_config(src, {personal.id: personal, positron.id: positron})
        deploy(config)
        assert json.loads((personal.path / "settings.json").read_text())["effortLevel"] == "low"
        assert json.loads((positron.path / "settings.json").read_text())["effortLevel"] == "high"

    def test_removing_settings_yaml_removes_managed_keys(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        (src / "settings.yaml").unlink()
        actions = deploy(config)
        removed = [a for a in actions if a.item_type == "settings" and a.action == "remove"]
        assert len(removed) == 1
        assert "effortLevel" not in json.loads((tc.path / "settings.json").read_text())

    def test_settings_preserves_hooks_and_mcp(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        (tc.path / "settings.json").write_text(json.dumps(
            {"hooks": {"Stop": [1]}, "mcpServers": {"pal": {}}}))
        config = _make_config(src, {tc.id: tc})
        deploy(config)
        data = json.loads((tc.path / "settings.json").read_text())
        assert data["hooks"] == {"Stop": [1]}
        assert data["mcpServers"] == {"pal": {}}
        assert data["effortLevel"] == "low"

    def test_only_type_settings_filters(self, tmp_path: Path):
        src = _make_source(tmp_path)  # has agent/command/skill
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config, item_types=["settings"])
        assert {a.item_type for a in actions if a.action == "create"} == {"settings"}

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        src = self._src_with_settings(tmp_path, "base:\n  effortLevel: low\n")
        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        deploy(config, dry_run=True)
        assert not (tc.path / "settings.json").exists()
        assert not (tc.path / MANIFEST_FILENAME).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_deploy.py::TestDeploySettingsItem -v`
Expected: FAIL — `KeyError: 'settings'` in `_TYPE_TO_CATEGORY`, then assorted failures.

- [ ] **Step 3: Implement**

(a) Add to `_TYPE_TO_CATEGORY`: `"settings": "settings",` and to `_CLI_TYPE_TO_ITEM_TYPE`: `"settings": "settings",`.

(b) Add the import at the top of `deploy.py`:

```python
from .settings import render_settings
```

(c) Insert the branch in the `for item in all_items:` loop, immediately **after** the line `changed = has_changed(manifest, category, item.name, current_hash)` and **before** `exists_on_target = target.item_exists(...)`:

```python
                if item.item_type == "settings":
                    rendered = render_settings(item.metadata or {}, target_id, config)
                    prev = manifest.items.get("settings", {}).get(item.name)
                    previous_keys = (
                        list(prev.managed_keys) if prev and prev.managed_keys else []
                    )
                    is_update = prev is not None
                    if force or changed:
                        if not dry_run:
                            target.deploy_settings(rendered, previous_keys)
                        actions.append(
                            DeployAction(
                                action="update" if is_update else "create",
                                item_type="settings",
                                name=item.name,
                                target_id=target_id,
                                source_path=str(item.path),
                            )
                        )
                    else:
                        actions.append(
                            DeployAction(
                                action="skip",
                                item_type="settings",
                                name=item.name,
                                target_id=target_id,
                                source_path=str(item.path),
                            )
                        )
                    new_manifest.items.setdefault("settings", {})[item.name] = (
                        ManifestItem(
                            source_hash=current_hash,
                            managed_keys=list(rendered.keys()),
                        )
                    )
                    continue
```

(d) Extend `_remove_item` to route settings. Change its signature to accept `managed_keys` and add a branch:

```python
def _remove_item(
    target: Target,
    category: str,
    name: str,
    target_path: Optional[Path] = None,
    managed_keys: Optional[list[str]] = None,
) -> None:
    ...
    elif category == "settings":
        target.remove_settings(managed_keys or [])
```

(e) In the stale-removal loop, pass `managed_keys` when calling `_remove_item`:

```python
                    if not dry_run:
                        _remove_item(
                            target, category, name,
                            target_path=target_path,
                            managed_keys=(prev_item.managed_keys if prev_item else None),
                        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_deploy.py::TestDeploySettingsItem -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Full deploy-test check + commit**

Run: `PYTHONPATH=src python -m pytest tests/test_deploy.py -q`
Expected: all pass.

```bash
git add src/promptdeploy/deploy.py tests/test_deploy.py
git commit -m "feat(deploy): dedicated settings branch with managed_keys tracking"
```

### Task 3.3: `status.py` type map (settings + prompt)

**Files:**
- Modify: `src/promptdeploy/status.py:30-37`
- Test: `tests/test_status.py`

- [ ] **Step 1: Write the failing test**

```python
def test_status_handles_settings_and_prompts(tmp_path):
    from promptdeploy.config import Config, TargetConfig
    from promptdeploy.status import get_status

    src = tmp_path / "source"; src.mkdir()
    (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
    (src / "prompts").mkdir()
    (src / "prompts" / "p.txt").write_text("hello")
    tgt = tmp_path / "claude"; tgt.mkdir()
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    config = Config(source_root=src, targets={tc.id: tc}, groups={})

    entries = get_status(config, ["claude-x"])  # must not KeyError
    kinds = {e.item_type for e in entries}
    assert "settings" in kinds
    assert "prompt" in kinds
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_status.py::test_status_handles_settings_and_prompts -v`
Expected: FAIL — `KeyError: 'prompt'` (or `'settings'`).

- [ ] **Step 3: Implement** — extend `status.py`'s `_TYPE_TO_CATEGORY`:

```python
_TYPE_TO_CATEGORY = {
    "agent": "agents",
    "command": "commands",
    "skill": "skills",
    "mcp": "mcp_servers",
    "models": "models",
    "hook": "hooks",
    "prompt": "prompts",
    "settings": "settings",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/status.py tests/test_status.py
git commit -m "fix(status): handle settings and prompt item types"
```

### Task 3.4: CLI `--only-type settings` + `list`

**Files:**
- Modify: `src/promptdeploy/cli.py:28` (choices), `:234` (`category_labels`), `:243-251` (iteration tuple)
- Test: `tests/test_cli.py` and/or `tests/test_list.py`

- [ ] **Step 1: Write the failing test** (use the file's existing CLI-invocation pattern; example using argv + capsys)

```python
def test_list_includes_settings(tmp_path, monkeypatch, capsys):
    # Mirror the existing list-command test setup in tests/test_list.py:
    # build a source tree with settings.yaml, deploy, then run `list`.
    ...
    # After running `promptdeploy list --target claude-x`:
    out = capsys.readouterr().out
    assert "Settings" in out
```

> Follow `tests/test_list.py`'s established harness rather than inventing one. The assertion that matters: a deployed settings item appears under a `Settings:` label. Also add (or extend) a test asserting `--only-type` accepts `settings` without an argparse error.

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_list.py -v` (the new assertion)
Expected: FAIL — `Settings` label absent / `--only-type settings` rejected.

- [ ] **Step 3: Implement**

In the `deploy` subparser `--only-type` `choices`, add `"settings"`:

```python
        choices=["agents", "commands", "skills", "mcp", "models", "hooks", "prompts", "settings"],
```

In `_run_list`, add to `category_labels`:

```python
                "settings": "Settings",
```

and add `"settings"` to the iteration tuple (after `"prompts"`):

```python
            for category in (
                "agents", "commands", "skills", "mcp_servers",
                "models", "hooks", "prompts", "settings",
            ):
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_list.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Full-suite + lint, then commit**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all pass; coverage 100% (the new lines are covered).

```bash
git add src/promptdeploy/cli.py tests/test_list.py tests/test_cli.py
git commit -m "feat(cli): list and --only-type support the settings item"
```

---

## Chunk 4: Validation

### Task 4.1: `validate_settings`

**Files:**
- Modify: `src/promptdeploy/validate.py` — add `validate_settings(config)`, call it from `validate_all` (append its issues before `return issues`)
- Test: `tests/test_validate.py`

- [ ] **Step 1: Write the failing tests**

```python
def _cfg_with(tmp_path, settings_yaml: str):
    from promptdeploy.config import Config, TargetConfig
    (tmp_path / "settings.yaml").write_text(settings_yaml)
    tc = TargetConfig(id="claude-positron", type="claude", path=tmp_path / "p",
                      labels=["claude", "positron"])
    return Config(source_root=tmp_path,
                  targets={tc.id: tc},
                  groups={"positron": ["claude-positron"], "claude": ["claude-positron"]})


def test_validate_settings_ok(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path,
        "base:\n  effortLevel: low\noverrides:\n  claude-positron:\n    model: sonnet\n")
    issues = [i for i in validate_all(cfg) if "settings.yaml" in str(i.file_path)]
    assert issues == []


def test_validate_settings_unknown_override_key_errors(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path, "base: {}\noverrides:\n  nope-target:\n    model: x\n")
    msgs = [i.message for i in validate_all(cfg) if i.level == "error"]
    assert any("nope-target" in m for m in msgs)


def test_validate_settings_hooks_in_base_warns(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path, "base:\n  hooks:\n    Stop: []\n")
    warns = [i for i in validate_all(cfg) if i.level == "warning" and "hooks" in i.message]
    assert warns


def test_validate_settings_null_in_base_warns(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path, "base:\n  effortLevel: null\n")
    assert any(i.level == "warning" and "null" in i.message.lower()
               for i in validate_all(cfg))


def test_validate_settings_non_dict_base_errors(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path, "base:\n  - 1\n  - 2\n")
    assert any(i.level == "error" for i in validate_all(cfg))


def test_validate_settings_group_key_accepted(tmp_path):
    from promptdeploy.validate import validate_all
    cfg = _cfg_with(tmp_path, "base: {}\noverrides:\n  positron:\n    model: x\n")
    assert [i for i in validate_all(cfg) if i.level == "error"
            and "positron" in i.message] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py -k settings -v`
Expected: FAIL — `validate_settings` not wired in.

- [ ] **Step 3: Implement** — add to `validate.py`:

```python
def validate_settings(config: Config) -> List[ValidationIssue]:
    """Validate settings.yaml structure and override targeting."""
    path = config.source_root / "settings.yaml"
    if not path.exists():
        return []
    issues: List[ValidationIssue] = []
    try:
        doc = yaml.safe_load(path.read_text("utf-8"))
    except yaml.YAMLError as exc:
        return [ValidationIssue("error", f"settings.yaml: {exc}", path)]
    if doc is None:
        return []
    if not isinstance(doc, dict):
        return [ValidationIssue("error", "settings.yaml: top level must be a mapping", path)]

    known = set(config.targets) | set(config.groups)

    base = doc.get("base")
    if base is not None and not isinstance(base, dict):
        issues.append(ValidationIssue("error", "settings.yaml: 'base' must be a mapping", path))
        base = None

    def _check_section(section: dict, where: str) -> None:
        for key in ("hooks", "mcpServers"):
            if key in section:
                issues.append(ValidationIssue(
                    "warning",
                    f"settings.yaml: '{key}' in {where} is ignored "
                    f"(managed by {'hooks/' if key == 'hooks' else 'mcp/'})",
                    path))

    if isinstance(base, dict):
        _check_section(base, "base")
        for k, v in base.items():
            if v is None:
                issues.append(ValidationIssue(
                    "warning",
                    f"settings.yaml: 'base.{k}' is null and will be stripped "
                    f"(null deletes only inside overrides)",
                    path))

    overrides = doc.get("overrides")
    if overrides is not None:
        if not isinstance(overrides, dict):
            issues.append(ValidationIssue("error", "settings.yaml: 'overrides' must be a mapping", path))
        else:
            for ov_key, ov_val in overrides.items():
                if ov_key not in known:
                    issues.append(ValidationIssue(
                        "error",
                        f"settings.yaml: override key '{ov_key}' is not a known "
                        f"target id or group",
                        path))
                if ov_val is not None and not isinstance(ov_val, dict):
                    issues.append(ValidationIssue(
                        "error",
                        f"settings.yaml: override '{ov_key}' must be a mapping",
                        path))
                elif isinstance(ov_val, dict):
                    _check_section(ov_val, f"overrides.{ov_key}")
    return issues
```

Then, in `validate_all`, before `return issues`:

```python
    issues.extend(validate_settings(config))
```

> Note: JSON-serializability (rejecting dates/sets) is implicitly enforced because `yaml.safe_load` only produces JSON-compatible scalars/dicts/lists for the inputs here; an explicit type sweep is YAGNI for this file. If a future need arises, add it then.

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py -k settings -v`
Expected: PASS.

- [ ] **Step 5: Full validate-test + commit**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py -q`
Expected: all pass.

```bash
git add src/promptdeploy/validate.py tests/test_validate.py
git commit -m "feat(validate): validate settings.yaml base/overrides"
```

---

## Chunk 5: Bootstrap & reconcile (`settings_sync.py` + CLI subcommands)

The heaviest chunk. Adds the ruamel dependency, the I/O module, and the `settings` subcommand group.

### Task 5.1: Add ruamel.yaml dependency

**Files:**
- Modify: `pyproject.toml:10`, `flake.nix:16-23` and `:39-42`
- Verify: import works in the dev shell.

- [ ] **Step 1: Edit `pyproject.toml`**

```toml
dependencies = ["PyYAML>=6.0", "Jinja2>=3.1", "ruamel.yaml>=0.18"]
```

- [ ] **Step 2: Edit `flake.nix`** — add `ruamel-yaml` to the dev-shell `pythonWithDeps` list:

```nix
        pythonWithDeps = python.withPackages (ps:
          with ps; [
            pyyaml
            jinja2
            ruamel-yaml
            pytest
            pytest-cov
            mypy
          ]);
```

and to the build `dependencies` list:

```nix
          dependencies = with python.pkgs; [
            pyyaml
            jinja2
            ruamel-yaml
          ];
```

- [ ] **Step 3: Verify the import resolves**

Run: `PYTHONPATH=src python -c "import ruamel.yaml; print(ruamel.yaml.__version__)"`
Expected: prints a version (≥ 0.18). If it errors, the dev shell needs reloading (`direnv reload` / re-enter `nix develop`).

> **mypy note:** `ruamel.yaml` ships a PEP 561 `py.typed` marker, so `mypy src/ tests/` should not need an `ignore_missing_imports` override for it (the existing `pyproject.toml` override is scoped to `module = "yaml"` only). If, after Task 5.2 introduces `from ruamel.yaml import YAML`, `nix flake check`'s mypy step complains about ruamel's partial stubs, add a second override block to `pyproject.toml`:
> ```toml
> [[tool.mypy.overrides]]
> module = "ruamel.*"
> ignore_missing_imports = true
> ```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml flake.nix
git commit -m "build: add ruamel.yaml dependency for settings write-back"
```

### Task 5.2: ruamel load/dump helpers in `settings_sync.py`

**Files:**
- Create: `src/promptdeploy/settings_sync.py`
- Test: `tests/test_settings_sync.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings_sync.py
"""Tests for settings init/reconcile I/O orchestration."""

from promptdeploy.settings_sync import load_settings_doc, dump_settings_doc


def test_dump_then_load_roundtrips(tmp_path):
    path = tmp_path / "settings.yaml"
    doc = load_settings_doc(path)            # absent -> empty mapping
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement** — create `settings_sync.py` with the helpers:

> **Import discipline (lefthook runs `ruff` per commit):** `settings_sync.py` is
> built across Tasks 5.2–5.5. Each task adds **only** the imports its code uses, so
> every intermediate commit is free of unused imports. The complete final import
> block (after Task 5.5) is:
> ```python
> from __future__ import annotations
> import io
> import os
> import tempfile
> from dataclasses import dataclass
> from pathlib import Path
> from typing import Any, Dict, List, Optional
> from ruamel.yaml import YAML
> from ruamel.yaml.comments import CommentedMap
> from .config import Config
> from .settings import generate_merge_patch, render_settings, strip_keys
> from .targets import create_target
> ```
> This task (5.2) adds only the subset below.

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings_sync.py tests/test_settings_sync.py
git commit -m "feat(settings-sync): ruamel load/dump with atomic write"
```

### Task 5.3: `read_live_settings` (pull + strip via a target)

**Files:**
- Modify: `src/promptdeploy/settings_sync.py`
- Test: `tests/test_settings_sync.py`

- [ ] **Step 1: Write the failing test**

```python
def test_read_live_settings_strips_hooks_and_mcp(tmp_path):
    import json
    from promptdeploy.config import TargetConfig
    from promptdeploy.settings_sync import read_live_settings

    tgt = tmp_path / "claude-x"; tgt.mkdir()
    (tgt / "settings.json").write_text(json.dumps({
        "effortLevel": "low",
        "hooks": {"Stop": [1]},
        "mcpServers": {"pal": {}},
    }))
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    assert read_live_settings(tc) == {"effortLevel": "low"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py::test_read_live_settings_strips_hooks_and_mcp -v`
Expected: FAIL — `read_live_settings` undefined.

- [ ] **Step 3: Implement** — extend the imports, add the module constant, and the function:

```python
# add to the imports
from typing import Any, Dict

from .settings import strip_keys
from .targets import create_target

# module-level constant (place after imports)
_MANAGED_ELSEWHERE = {"hooks", "mcpServers"}


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py::test_read_live_settings_strips_hooks_and_mcp -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings_sync.py tests/test_settings_sync.py
git commit -m "feat(settings-sync): read_live_settings via target lifecycle"
```

### Task 5.4: `init_settings` (bootstrap from live hosts)

**Files:**
- Modify: `src/promptdeploy/settings_sync.py`
- Test: `tests/test_settings_sync.py`

- [ ] **Step 1: Write the failing test**

```python
def _claude_tc(tmp_path, tid, settings: dict):
    import json
    from promptdeploy.config import TargetConfig
    d = tmp_path / tid; d.mkdir()
    (d / "settings.json").write_text(json.dumps(settings))
    return TargetConfig(id=tid, type="claude", path=d)


def test_init_settings_factors_base_and_overrides(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import init_settings, load_settings_doc

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low", "env": {"A": "1"}})
    q = _claude_tc(tmp_path, "claude-positron", {"effortLevel": "high", "env": {"A": "1"}})
    config = Config(source_root=tmp_path, targets={p.id: p, q.id: q}, groups={})
    out = tmp_path / "settings.yaml"

    init_settings(config, ["claude-personal", "claude-positron"],
                  from_ref="claude-personal", out_path=out, force=False)

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
    out = tmp_path / "settings.yaml"; out.write_text("base: {}\n")
    with pytest.raises(FileExistsError):
        init_settings(config, ["claude-personal"], from_ref=None, out_path=out, force=False)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -k init -v`
Expected: FAIL — `init_settings` undefined.

- [ ] **Step 3: Implement** — extend the imports, then add the helpers:

```python
# extend the imports
from typing import List, Optional  # add alongside Any, Dict

from .config import Config
from .settings import generate_merge_patch  # add alongside strip_keys
from ruamel.yaml.comments import CommentedMap


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
            f"{out_path} exists; pass --force to overwrite or use reconcile")

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
```

Add the `CommentedMap` import to the top of `settings_sync.py` (with the other ruamel import):

```python
from ruamel.yaml.comments import CommentedMap
```

> Note: this is the single, canonical `init_settings` body — there is no `os.devnull`/`doc` local. The earlier `dump_settings_doc` already handles a non-existent `out_path` (it writes fresh), and the `out_path.exists() and not force` guard at the top covers the overwrite case.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -k init -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings_sync.py tests/test_settings_sync.py
git commit -m "feat(settings-sync): init_settings factors base + overrides"
```

### Task 5.5: `reconcile_settings` (diff + write-back)

**Files:**
- Modify: `src/promptdeploy/settings_sync.py`
- Test: `tests/test_settings_sync.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_reports_diff_without_apply(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings

    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low", "autoUpdates": False})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"; out.write_text("base:\n  effortLevel: low\n")

    diffs = reconcile_settings(config, ["claude-personal"], settings_path=out, apply=False)
    # autoUpdates is on the host but not rendered -> reported as host-only ("+").
    keys = {(d.target_id, d.kind, d.key) for d in diffs}
    assert ("claude-personal", "+", "autoUpdates") in keys
    # No write happened.
    assert "autoUpdates" not in out.read_text()


def test_reconcile_apply_writes_override(tmp_path):
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings, load_settings_doc

    p = _claude_tc(tmp_path, "claude-positron", {"effortLevel": "high"})
    config = Config(source_root=tmp_path,
                    targets={p.id: p},
                    groups={})
    out = tmp_path / "settings.yaml"; out.write_text("base:\n  effortLevel: low\n")

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
        reconcile_settings(config, ["claude-personal"],
                           settings_path=tmp_path / "nope.yaml", apply=False)


def test_reconcile_reports_rendered_only_key(tmp_path):
    # Covers the '-' diff kind: settings.yaml renders a key the host lacks.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings
    p = _claude_tc(tmp_path, "claude-personal", {})  # empty host settings.json
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"; out.write_text("base:\n  model: opus\n")
    diffs = reconcile_settings(config, ["claude-personal"], settings_path=out, apply=False)
    assert ("claude-personal", "-", "model") in {(d.target_id, d.kind, d.key) for d in diffs}


def test_reconcile_apply_no_drift_writes_nothing(tmp_path):
    # Covers `if not drifted: continue` and the `apply and not changed` no-dump path.
    from promptdeploy.config import Config
    from promptdeploy.settings_sync import reconcile_settings
    p = _claude_tc(tmp_path, "claude-personal", {"effortLevel": "low"})
    config = Config(source_root=tmp_path, targets={p.id: p}, groups={})
    out = tmp_path / "settings.yaml"; out.write_text("base:\n  effortLevel: low\n")
    before = out.read_text()
    diffs = reconcile_settings(config, ["claude-personal"], settings_path=out, apply=True)
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
        "base:\n  effortLevel: low\n"
        "overrides:\n  claude-x:\n    effortLevel: high\n")
    reconcile_settings(config, ["claude-x"], settings_path=out, apply=True)
    doc = load_settings_doc(out)
    assert "effortLevel" not in doc["overrides"]["claude-x"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -k reconcile -v`
Expected: FAIL — `reconcile_settings`/`SettingsDiff` undefined.

- [ ] **Step 3: Implement** — extend the imports (`from dataclasses import dataclass` at the top of the stdlib group; add `render_settings` to the `.settings` import), then add the diff/reconcile code:

```python
# extend the imports
from dataclasses import dataclass
from .settings import render_settings  # add alongside generate_merge_patch, strip_keys


@dataclass
class SettingsDiff:
    target_id: str
    kind: str   # '+' host-only, '~' differs, '-' rendered-only
    key: str
    host_value: Any = None
    rendered_value: Any = None


def _diff_target(target_id: str, host: Dict[str, Any], rendered: Dict[str, Any]) -> List[SettingsDiff]:
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

    With ``apply``, write each host's drifted top-level keys into that target's
    overrides block (a ``null`` when the host lacks a key that ``base`` has),
    preserving comments on untouched override keys.
    """
    if not settings_path.exists():
        raise FileNotFoundError(
            f"{settings_path} not found; run `promptdeploy settings init` first")

    doc = load_settings_doc(settings_path)
    base = dict(doc.get("base") or {})
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
        drifted = [d for d in diffs if d.kind in ("+", "~")]
        if not drifted:
            continue
        patch = generate_merge_patch(base, host)   # base -> host, per key
        overrides = doc.setdefault("overrides", {})
        ov = overrides.setdefault(tid, {})
        for d in drifted:
            if d.key in patch:
                ov[d.key] = patch[d.key]
            else:
                ov.pop(d.key, None)
        changed = True
    if apply and changed:
        dump_settings_doc(doc, settings_path)
    return all_diffs
```

> `ov[d.key] = patch[d.key]` assigns into the ruamel `CommentedMap` (when `overrides[tid]` already existed) or a plain dict (newly created) — both round-trip. Comments on untouched override keys survive; a regenerated key's inner-nested comments are best-effort (documented in the spec, §6.10).

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_settings_sync.py -k reconcile -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/settings_sync.py tests/test_settings_sync.py
git commit -m "feat(settings-sync): reconcile diff and override write-back"
```

### Task 5.6: `settings` CLI subcommand group

**Files:**
- Modify: `src/promptdeploy/cli.py` — add the `settings` subparser with `init`/`reconcile`, dispatch in `main`, add `_run_settings_init`/`_run_settings_reconcile`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_settings_init_and_reconcile_cli(tmp_path, monkeypatch, capsys):
    import json
    from promptdeploy import cli

    # Source tree with deploy.yaml pointing at two local claude targets.
    src = tmp_path / "src"; src.mkdir()
    p = src.parent / "claude-personal"; p.mkdir()
    q = src.parent / "claude-positron"; q.mkdir()
    (p / "settings.json").write_text(json.dumps({"effortLevel": "low"}))
    (q / "settings.json").write_text(json.dumps({"effortLevel": "high"}))
    (src / "deploy.yaml").write_text(
        "source_root: .\n"
        "targets:\n"
        f"  claude-personal:\n    type: claude\n    path: {p}\n    labels: [claude]\n"
        f"  claude-positron:\n    type: claude\n    path: {q}\n    labels: [claude]\n")
    monkeypatch.chdir(src)

    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "init",
                                     "--from", "claude-personal"])
    cli.main()
    doc_text = (src / "settings.yaml").read_text()
    assert "effortLevel: low" in doc_text
    assert "claude-positron" in doc_text  # override captured

    # Reconcile (report-only) must not raise and should print a diff or "clean".
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "reconcile"])
    cli.main()
    capsys.readouterr()  # drained; no exception is the contract
```

> Follow the harness style already used in `tests/test_cli.py` (it may invoke `cli.main()` under `monkeypatch`/`argv`, or call `_run_*` directly). Match it; the assertions above are the contract.

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_cli.py::test_settings_init_and_reconcile_cli -v`
Expected: FAIL — `settings` is not a valid subcommand (argparse error / SystemExit).

- [ ] **Step 3: Implement**

In `main()`, after the `list` subparser block, add:

```python
    # settings subcommand group
    settings_parser = subparsers.add_parser("settings", help="Manage settings.yaml")
    settings_sub = settings_parser.add_subparsers(dest="settings_command", required=True)

    init_parser = settings_sub.add_parser("init", help="Bootstrap settings.yaml from live hosts")
    init_parser.add_argument("--from", dest="from_ref", help="Reference target for base")
    init_parser.add_argument("--target", action="append", help="Targets to pull from")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing settings.yaml")

    rec_parser = settings_sub.add_parser("reconcile", help="Pull host settings drift into overrides")
    rec_parser.add_argument("--target", action="append", help="Targets to reconcile")
    rec_parser.add_argument("--apply", action="store_true", help="Write drift into overrides")
```

In the dispatch block at the end of `main()`:

```python
    elif args.command == "settings":
        if args.settings_command == "init":
            _run_settings_init(args)
        elif args.settings_command == "reconcile":
            _run_settings_reconcile(args)
```

Add the handlers:

```python
def _run_settings_init(args):
    from .settings_sync import init_settings

    config = load_config()
    out_path = config.source_root / "settings.yaml"
    try:
        target_ids = expand_target_arg(args.target, config)
        init_settings(config, target_ids, from_ref=args.from_ref,
                      out_path=out_path, force=args.force)
    except (FileExistsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {out_path}")


def _run_settings_reconcile(args):
    from .settings_sync import reconcile_settings

    config = load_config()
    settings_path = config.source_root / "settings.yaml"
    try:
        target_ids = expand_target_arg(args.target, config)
        diffs = reconcile_settings(config, target_ids,
                                   settings_path=settings_path, apply=args.apply)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if not diffs:
        print("settings.yaml is in sync with all selected targets.")
        return
    for d in diffs:
        detail = {
            "+": f"{d.key} = {d.host_value!r} (host only)",
            "~": f"{d.key}: {d.rendered_value!r} -> {d.host_value!r}",
            "-": f"{d.key} (settings.yaml only; deploy would add)",
        }[d.kind]
        print(f"  {d.kind}  {d.target_id}: {detail}")
    if args.apply:
        print("Applied host drift into overrides.")
    else:
        print("Re-run with --apply to write these into overrides.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_cli.py::test_settings_init_and_reconcile_cli -v`
Expected: PASS.

- [ ] **Step 5: Add error-path tests for the handlers, then full-suite + coverage + lint, then commit**

The happy-path CLI test above does not exercise the handlers' `except` (`sys.exit(1)`) branches; the 100% gate needs them. Add two small tests (mirroring the harness of `test_settings_init_and_reconcile_cli`):

```python
def test_settings_init_bad_target_exits(tmp_path, monkeypatch, capsys):
    # expand_target_arg raises ValueError on an unknown --target -> ERROR + exit 1
    import pytest
    from promptdeploy import cli
    # ... build the same deploy.yaml + chdir as the happy-path test ...
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "init", "--target", "nope"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "ERROR" in capsys.readouterr().err


def test_settings_reconcile_missing_yaml_exits(tmp_path, monkeypatch, capsys):
    import pytest
    from promptdeploy import cli
    # ... build deploy.yaml with one claude target, NO settings.yaml, chdir ...
    monkeypatch.setattr("sys.argv", ["promptdeploy", "settings", "reconcile"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "init" in capsys.readouterr().err  # FileNotFoundError message mentions init
```

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all pass; **100%** coverage. If the report still flags a line, add a focused test (likely candidates already covered by Task 5.5's tests: the `-` diff kind, the `apply`-no-drift no-dump path, and the override-pop branch; and Task 5.4's `init` no-claude-targets `ValueError` and `--from` not-among-targets `ValueError`).

```bash
git add src/promptdeploy/cli.py tests/test_cli.py
git commit -m "feat(cli): add settings init and reconcile subcommands"
```

---

## Chunk 6: Documentation

### Task 6.1: `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a `Settings` row** to the Content Types table:

```markdown
| Settings | `settings.yaml` | Single YAML, `base:` + `overrides:` (per target/group) | Claude-only; rendered per target and gently merged into `settings.json` (managed top-level keys only; hooks/mcpServers untouched) |
```

- [ ] **Step 2: Add a subsection** under the architecture/patterns describing: render = base + matching overrides (RFC 7386, `null` deletes, exact-target-id wins over groups, file order among groups); deploy merges only rendered top-level keys and removes only previously-managed ones (manifest `managed_keys`); `settings init`/`settings reconcile`. Correct the stale **"single dependency (PyYAML)"** phrasing at `CLAUDE.md:24` to "PyYAML + Jinja2 + ruamel.yaml". (Verified: this exact phrasing is at `CLAUDE.md:24`; `PROMPTDEPLOY.md` does **not** contain it.)

- [ ] **Step 3: Verify** the file still parses as Markdown and the dependency note is accurate. No automated test; read it back.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document settings.yaml and correct deps note"
```

### Task 6.2: `README.md` + `PROMPTDEPLOY.md`

**Files:**
- Modify: `README.md`, `PROMPTDEPLOY.md`

- [ ] **Step 1: Document** `settings.yaml` (schema + example), `promptdeploy settings init`, and `promptdeploy settings reconcile [--apply]` in both files, matching their existing structure. Fix the dependency note at `README.md:5` — change "one dependency (PyYAML)" to reflect PyYAML + Jinja2 + ruamel.yaml (e.g. "a few small dependencies (PyYAML, Jinja2, ruamel.yaml)"). `PROMPTDEPLOY.md` has no dependency-count claim to fix, so just add the settings documentation there.

- [ ] **Step 2: Verify** by reading; ensure examples match the implemented CLI flags.

- [ ] **Step 3: Commit**

```bash
git add README.md PROMPTDEPLOY.md
git commit -m "docs: document settings.yaml, init, and reconcile"
```

---

## Final verification

- [ ] **Full test suite with coverage gate**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all pass, **100%** coverage.

- [ ] **Nix flake check (authoritative gate)**

Run: `nix flake check`
Expected: `ruff format --check`, `ruff check`, `mypy`, `pytest` (100%), and `nix build` all pass with `ruamel-yaml` resolved.

- [ ] **Smoke test against a scratch tree**

Run:
```bash
PYTHONPATH=src python -m promptdeploy validate
PYTHONPATH=src python -m promptdeploy deploy --only-type settings --dry-run --target-root /tmp/pd-preview
```
Expected: `validate` clean (or only expected warnings); dry-run reports a `settings` action per claude target and writes nothing real.

- [ ] **Stop here.** Do **not** run a real `settings init` against live hosts or a real deploy as part of implementation — that is the user's call (it pulls/pushes remote machines). Surface readiness and let the user drive the bootstrap.
