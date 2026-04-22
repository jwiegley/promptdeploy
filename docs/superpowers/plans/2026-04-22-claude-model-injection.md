# Claude Model Injection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject `model:` frontmatter into every agent and skill deployed to Claude Code targets, resolving an "effective model" from per-target override → global default (`providers.anthropic.claude.default_model`) → no injection.

**Architecture:** Extend `transform_for_target()` with an optional `inject` parameter. Add a `model` field to `TargetConfig` and a loader for the `models.yaml` global default. Wire the effective-model resolution through `targets.__init__.create_target()` and into `ClaudeTarget` which injects only on agents and skills (never commands, MCP, hooks, or models). Relax `validate.py` so claude-only providers don't require `base_url`/`api_key`; add an error for per-target `model:` on non-claude targets and a warning for unknown model strings.

**Tech Stack:** Python 3.12, PyYAML, pytest, existing `promptdeploy` package (src layout, 100% coverage gate).

**Reference spec:** `docs/superpowers/specs/2026-04-22-claude-model-injection-design.md`

---

## File Structure

### Modified files

| File | Responsibility | Approximate change |
|------|----------------|--------------------|
| `src/promptdeploy/frontmatter.py` | YAML frontmatter parsing and serialization | Add `inject=` kwarg to `transform_for_target`, applied after `strip_deployment_fields`. `None`-valued keys are skipped. |
| `src/promptdeploy/config.py` | deploy.yaml loading, `TargetConfig`, `remap_targets_to_root` | Add `model: Optional[str] = None` to `TargetConfig`; parse field in `load_config`; preserve in `remap_targets_to_root`. Add `load_anthropic_default_model(models_yaml_path)` helper. |
| `src/promptdeploy/targets/claude.py` | Claude Code target | Constructor gains `model: Optional[str] = None`. `deploy_agent` and `deploy_skill` (SKILL.md rewrite) pass `inject=self._injected`. Commands unchanged. |
| `src/promptdeploy/targets/__init__.py` | Target factory | `create_target` accepts an optional `global_model` string; resolves effective model per target and threads into `ClaudeTarget`. |
| `src/promptdeploy/deploy.py` | Deploy orchestration | `deploy()` loads the Anthropic default once and passes it into `create_target(..., global_model=...)`. |
| `src/promptdeploy/validate.py` | Source item validation | Relax required-provider-fields rule (`base_url`/`api_key` only required when `droid:` or `opencode:` subsection present). Add error for per-target `model:` on non-claude target. Add warning for unknown-model string. |
| `models.yaml` | Model/provider source | Add new `anthropic:` provider with `claude.default_model: claude-opus-4-7`, `except: [droid, opencode, opencode-vulcan]`, and informational `models:` dict. |
| `tests/test_frontmatter.py` | Unit tests | Add `TestTransformForTargetInjection` class (no-op, overwrite, skip-None, key-order). |
| `tests/test_config.py` | Unit tests | Parse per-target `model:`; `remap_targets_to_root` preserves it; `TargetConfig.model` default. Add parsing helper test. |
| `tests/test_claude_target.py` | Unit tests | Agent and skill get injected `model:`; command does not; backward-compatible 2-arg constructor still works. |
| `tests/test_validate.py` | Unit tests | Claude-only provider accepted without credentials; per-target `model:` on non-claude target errors; unknown model string warns. |
| `PROMPTDEPLOY.md` | User-facing docs | Document per-target `model:` and `providers.anthropic.claude.default_model`. |
| `CLAUDE.md` | Agent-facing docs | Update "models.yaml -- Droid and OpenCode only; Claude skipped" note. |

### Not touched

- `src/promptdeploy/source.py`, `targets/droid.py`, `targets/opencode.py`, `targets/remote.py`, `targets/base.py`
- `src/promptdeploy/filters.py`, `manifest.py`
- `src/promptdeploy/cli.py` (no CLI-surface changes)
- `deploy.yaml` (no per-target overrides at rollout)

---

## Chunk 1: Frontmatter injection

**Goal:** Extend `transform_for_target()` with an opt-in `inject=` kwarg that overwrites frontmatter keys after `strip_deployment_fields`. `None`-valued entries in `inject` are skipped so the Claude target can pass `{"model": effective_or_none}` without filtering upstream.

### Task 1.1: Add injection test scaffolding and failing test for no-op semantics

**Files:**
- Modify: `tests/test_frontmatter.py` (append new test class at end of file)

- [ ] **Step 1: Write the failing test class** covering the backward-compatible no-op paths (`inject=None`, `inject={}`) and the no-op on content without frontmatter.

Append after the existing `TestTransformForTarget` class:

```python
class TestTransformForTargetInjection:
    def test_inject_none_is_noop(self):
        content = b"---\nname: test\nonly:\n  - target-a\n---\nBody.\n"
        result = transform_for_target(content, "target-a", inject=None)
        meta, body = parse_frontmatter(result)
        assert meta == {"name": "test"}
        assert body == b"Body.\n"
        assert "model" not in meta

    def test_inject_empty_dict_is_noop(self):
        content = b"---\nname: test\n---\nBody.\n"
        result = transform_for_target(content, "target-a", inject={})
        meta, _ = parse_frontmatter(result)
        assert meta == {"name": "test"}

    def test_inject_no_frontmatter_returns_original(self):
        content = b"No frontmatter here.\n"
        result = transform_for_target(content, "target-a", inject={"model": "opus"})
        assert result == content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_frontmatter.py::TestTransformForTargetInjection -v`
Expected: FAIL with `TypeError: transform_for_target() got an unexpected keyword argument 'inject'`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_frontmatter.py
git commit -m "test(frontmatter): add failing tests for transform_for_target inject kwarg"
```

### Task 1.2: Implement the `inject` parameter (no-op cases)

**Files:**
- Modify: `src/promptdeploy/frontmatter.py:60-70`

- [ ] **Step 1: Replace the existing `transform_for_target` function**

Replace lines 60-70 of `src/promptdeploy/frontmatter.py` entirely with:

```python
def transform_for_target(
    content: bytes,
    target_id: str,
    inject: Optional[dict] = None,
) -> bytes:
    """Parse frontmatter, strip deployment fields, inject overrides, and re-serialize.

    When ``inject`` is provided and non-empty, each key is written into the
    metadata dict after deployment fields are stripped, overwriting any existing
    value. Keys whose value is ``None`` are skipped (not emitted as ``null``)
    and leave any existing value untouched.
    Returns original content unchanged if no frontmatter is present.
    """
    metadata, body = parse_frontmatter(content)
    if metadata is None:
        return content

    cleaned = strip_deployment_fields(metadata)
    if inject:
        for key, value in inject.items():
            if value is None:
                continue
            cleaned[key] = value
    return serialize_frontmatter(cleaned, body)
```

- [ ] **Step 2: Run the no-op tests**

Run: `PYTHONPATH=src python -m pytest tests/test_frontmatter.py::TestTransformForTargetInjection -v`
Expected: the three no-op tests PASS.

- [ ] **Step 3: Run the full frontmatter suite to confirm no regression**

Run: `PYTHONPATH=src python -m pytest tests/test_frontmatter.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/promptdeploy/frontmatter.py
git commit -m "feat(frontmatter): add inject kwarg to transform_for_target"
```

### Task 1.3: Add overwrite, skip-None, key-order, and new-key tests

**Files:**
- Modify: `tests/test_frontmatter.py` (append to `TestTransformForTargetInjection`)

- [ ] **Step 1: Write the failing tests for active-injection semantics**

Append to `TestTransformForTargetInjection`:

```python
    def test_inject_overwrites_existing_key(self):
        content = b"---\nname: test\nmodel: sonnet\n---\nBody.\n"
        result = transform_for_target(
            content, "target-a", inject={"model": "claude-opus-4-7"}
        )
        meta, _ = parse_frontmatter(result)
        assert meta["model"] == "claude-opus-4-7"

    def test_inject_adds_new_key_when_absent(self):
        content = b"---\nname: test\n---\nBody.\n"
        result = transform_for_target(
            content, "target-a", inject={"model": "claude-opus-4-7"}
        )
        meta, _ = parse_frontmatter(result)
        assert meta["model"] == "claude-opus-4-7"
        assert meta["name"] == "test"

    def test_inject_none_value_is_skipped(self):
        content = b"---\nname: test\nmodel: sonnet\n---\nBody.\n"
        result = transform_for_target(content, "target-a", inject={"model": None})
        meta, _ = parse_frontmatter(result)
        # None-valued inject key is a no-op for that key: existing value preserved.
        assert meta["model"] == "sonnet"

    def test_inject_preserves_existing_key_order(self):
        # When inject overwrites an existing key, its position is preserved.
        content = b"---\nname: a\nmodel: old\ndescription: d\n---\nBody.\n"
        result = transform_for_target(
            content, "target-a", inject={"model": "new"}
        )
        text = result.decode("utf-8")
        # Confirm order: name, then model, then description.
        assert text.index("name:") < text.index("model:") < text.index("description:")
        meta, _ = parse_frontmatter(result)
        assert meta["model"] == "new"

    def test_inject_new_key_appended_last(self):
        # A new key is appended after existing ones.
        content = b"---\nname: a\ndescription: d\n---\nBody.\n"
        result = transform_for_target(
            content, "target-a", inject={"model": "claude-opus-4-7"}
        )
        text = result.decode("utf-8")
        assert text.index("description:") < text.index("model:")

    def test_inject_multiple_keys(self):
        # Multi-key inject: each non-None key is written, each None is skipped.
        content = b"---\nname: a\n---\nBody.\n"
        result = transform_for_target(
            content,
            "target-a",
            inject={"model": "claude-opus-4-7", "tools": None, "priority": 1},
        )
        meta, _ = parse_frontmatter(result)
        assert meta["model"] == "claude-opus-4-7"
        assert meta["priority"] == 1
        assert "tools" not in meta
```

- [ ] **Step 2: Run tests — expect all five to PASS with current implementation**

Run: `PYTHONPATH=src python -m pytest tests/test_frontmatter.py::TestTransformForTargetInjection -v`
Expected: all PASS (the implementation already handles these; we wrote tests last to verify behavior).

- [ ] **Step 3: Commit**

```bash
git add tests/test_frontmatter.py
git commit -m "test(frontmatter): cover overwrite, skip-None, and key order for inject"
```

### Task 1.4: Run full test suite with coverage

- [ ] **Step 1: Confirm 100% coverage still holds**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all tests PASS; no uncovered lines reported in `frontmatter.py`.

- [ ] **Step 2: If a line in `transform_for_target` is uncovered, add a targeted test**

Most likely-missed branches:
- `if metadata is None` (covered by `test_inject_no_frontmatter_returns_original`).
- `if inject:` false-branch (covered by `test_inject_none_is_noop` and `test_inject_empty_dict_is_noop`).
- `if value is None: continue` (covered by `test_inject_none_value_is_skipped`).
- non-`None` overwrite branch (covered by `test_inject_overwrites_existing_key`).

If the coverage report still flags a branch, write one additional test that exercises it and re-run.

- [ ] **Step 3: Commit any additional tests added in Step 2**

```bash
git add tests/test_frontmatter.py
git commit -m "test(frontmatter): cover remaining inject branches"
```

---

## Chunk 2: Config — per-target `model:` and global default loader

**Goal:** Add `TargetConfig.model`, parse it from `deploy.yaml`, preserve it in `remap_targets_to_root`, and add a top-level helper `load_anthropic_default_model()` that safely reads `providers.anthropic.claude.default_model` from a given `models.yaml` path. Defensive defaults: any missing step in the path resolves to `None`.

### Task 2.1: Add failing test for `TargetConfig.model` default

**Files:**
- Modify: `tests/test_config.py` (append to `TestLoadConfig` class)

- [ ] **Step 1: Write the failing test**

Append to `TestLoadConfig` (after the existing `test_group_definitions` method at line 84, just before the next class `TestFindConfigFile` at line 89):

```python
    def test_target_model_defaults_to_none(self, config: Config) -> None:
        # Without an explicit model field, TargetConfig.model is None.
        for tc in config.targets.values():
            assert tc.model is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadConfig::test_target_model_defaults_to_none -v`
Expected: FAIL with `AttributeError: 'TargetConfig' object has no attribute 'model'`.

- [ ] **Step 3: Add the field**

Modify `src/promptdeploy/config.py` lines 8-18. Replace the `TargetConfig` dataclass with:

```python
@dataclass
class TargetConfig:
    id: str
    type: str  # 'claude', 'droid', 'opencode'
    path: Path
    host: Optional[str] = None
    labels: List[str] = None  # type: ignore[assignment]
    model: Optional[str] = None

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadConfig::test_target_model_defaults_to_none -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/promptdeploy/config.py
git commit -m "feat(config): add optional model field to TargetConfig"
```

### Task 2.2: Parse per-target `model:` from `deploy.yaml`

**Files:**
- Modify: `tests/test_config.py` (append to `TestLoadConfig`)
- Modify: `src/promptdeploy/config.py:55-67`

- [ ] **Step 1: Write the failing test**

Append to `TestLoadConfig`:

```python
    def test_target_model_parsed_from_config(self, tmp_path: Path) -> None:
        data = {
            "source_root": ".",
            "targets": {
                "claude-vulcan": {
                    "type": "claude",
                    "path": str(tmp_path / "claude-vulcan"),
                    "model": "claude-sonnet-4-6",
                },
                "claude-personal": {
                    "type": "claude",
                    "path": str(tmp_path / "claude-personal"),
                },
            },
        }
        config_path = tmp_path / "deploy.yaml"
        with open(config_path, "w") as f:
            yaml.dump(data, f)
        config = load_config(config_path)
        assert config.targets["claude-vulcan"].model == "claude-sonnet-4-6"
        assert config.targets["claude-personal"].model is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadConfig::test_target_model_parsed_from_config -v`
Expected: FAIL — `config.targets["claude-vulcan"].model` is `None` because `load_config` never reads the field.

- [ ] **Step 3: Parse the field in `load_config`**

In `src/promptdeploy/config.py`, replace lines 55-67 (the `for target_id, target_data in ...` block) with:

```python
    targets = {}
    for target_id, target_data in data.get("targets", {}).items():
        host = target_data.get("host")
        path = Path(target_data["path"])
        if host is None:
            path = path.expanduser()
        labels = target_data.get("labels", [])
        model = target_data.get("model")
        targets[target_id] = TargetConfig(
            id=target_id,
            type=target_data["type"],
            path=path,
            host=host,
            labels=labels,
            model=model,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadConfig::test_target_model_parsed_from_config -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/promptdeploy/config.py
git commit -m "feat(config): parse per-target model field from deploy.yaml"
```

### Task 2.3: Propagate `model` through `remap_targets_to_root`

**Files:**
- Modify: `tests/test_config.py` (append to `TestRemapTargetsToRoot`)
- Modify: `src/promptdeploy/config.py:100-104`

- [ ] **Step 1: Write the failing test**

Append to `TestRemapTargetsToRoot`:

```python
    def test_remap_preserves_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="claude-remote",
            type="claude",
            path=Path("/remote/path"),
            model="claude-sonnet-4-6",
        )
        cfg = Config(source_root=tmp_path, targets={"claude-remote": tc}, groups={})
        remapped = remap_targets_to_root(cfg, tmp_path / "preview")
        assert remapped.targets["claude-remote"].model == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestRemapTargetsToRoot::test_remap_preserves_model -v`
Expected: FAIL — `remap_targets_to_root` does not pass `model=` through.

- [ ] **Step 3: Propagate `model` in `remap_targets_to_root`**

In `src/promptdeploy/config.py`, replace lines 100-104 (the `for tid, tc in config.targets.items()` block inside `remap_targets_to_root`) with:

```python
    new_targets = {}
    for tid, tc in config.targets.items():
        new_targets[tid] = TargetConfig(
            id=tc.id,
            type=tc.type,
            path=root / tid,
            host=None,
            labels=list(tc.labels),
            model=tc.model,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestRemapTargetsToRoot::test_remap_preserves_model -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/promptdeploy/config.py
git commit -m "feat(config): preserve model field through remap_targets_to_root"
```

### Task 2.4: Add `load_anthropic_default_model` helper

**Files:**
- Modify: `tests/test_config.py` (add new class `TestLoadAnthropicDefaultModel` at end of file)
- Modify: `src/promptdeploy/config.py` (add helper near bottom of file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
class TestLoadAnthropicDefaultModel:
    def test_returns_default_model(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        assert load_anthropic_default_model(models_path) == "claude-opus-4-7"

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        assert load_anthropic_default_model(tmp_path / "nope.yaml") is None

    def test_returns_none_when_providers_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text("something_else: 1\n")
        assert load_anthropic_default_model(models_path) is None

    def test_returns_none_when_anthropic_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  other:\n"
            "    display_name: Other\n"
        )
        assert load_anthropic_default_model(models_path) is None

    def test_returns_none_when_claude_subsection_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
        )
        assert load_anthropic_default_model(models_path) is None

    def test_returns_none_when_default_model_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude: {}\n"
        )
        assert load_anthropic_default_model(models_path) is None

    def test_returns_none_when_yaml_invalid(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text("providers: [unclosed\n")
        assert load_anthropic_default_model(models_path) is None

    def test_returns_none_when_default_model_not_string(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_default_model

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    claude:\n"
            "      default_model: 42\n"
        )
        assert load_anthropic_default_model(models_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadAnthropicDefaultModel -v`
Expected: FAIL — `ImportError: cannot import name 'load_anthropic_default_model' from 'promptdeploy.config'`.

- [ ] **Step 3: Add the helper to `src/promptdeploy/config.py`**

Append to the end of `src/promptdeploy/config.py`:

```python
def load_anthropic_default_model(models_yaml_path: Path) -> Optional[str]:
    """Read ``providers.anthropic.claude.default_model`` from a models.yaml.

    Returns ``None`` when the file is missing, YAML cannot be parsed, any
    intermediate key is absent, or the final value is not a string. Validation
    of the file's structure is the responsibility of :mod:`promptdeploy.validate`;
    this helper is deliberately permissive so deploy-time can short-circuit
    cleanly when the feature is not configured.
    """
    if not models_yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(models_yaml_path.read_text("utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    anthropic = providers.get("anthropic")
    if not isinstance(anthropic, dict):
        return None
    claude = anthropic.get("claude")
    if not isinstance(claude, dict):
        return None
    default_model = claude.get("default_model")
    if not isinstance(default_model, str):
        return None
    return default_model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadAnthropicDefaultModel -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/promptdeploy/config.py
git commit -m "feat(config): add load_anthropic_default_model helper"
```

### Task 2.5: Run full config suite with coverage

- [ ] **Step 1: Confirm 100% coverage and no regressions**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py --cov=promptdeploy.config --cov-report=term-missing -v`
Expected: all tests PASS; `config.py` shows 100% coverage.

- [ ] **Step 2: If any `load_anthropic_default_model` branch is uncovered, add a targeted test**

The eight tests above cover: happy path, missing file, missing providers, missing anthropic provider, missing claude subsection, missing default_model key, YAML syntax error, and non-string default_model. Together these exercise every `return None` branch. If coverage flags a line, add a test that hits it.

- [ ] **Step 3: Commit any additional tests**

```bash
git add tests/test_config.py
git commit -m "test(config): cover remaining load_anthropic_default_model branches"
```

---

## Chunk 3: Claude target injection + factory wiring + deploy plumbing

**Goal:** Accept an optional `model=` on `ClaudeTarget.__init__`. When set, inject `{"model": <value>}` into agent and skill frontmatter; never into commands. Extend `create_target()` to accept an optional `global_model` fallback and resolve the per-target effective model. Thread the resolved default from `deploy()` through by calling `load_anthropic_default_model()` once per run.

### Task 3.1: Add failing test — `ClaudeTarget(id, path)` still works

**Files:**
- Modify: `tests/test_claude_target.py` (append near `_make_target` helper)

- [ ] **Step 1: Write the test that documents backward compatibility**

Add a new test class after the existing `TestDeployAgent` class (after line 43):

```python
class TestClaudeTargetModelInjection:
    def test_constructor_without_model_is_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        # Two-argument form must continue to work — no model, no injection.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config)
        target.deploy_agent("a", b"---\nname: a\n---\nBody.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert "model" not in meta
```

- [ ] **Step 2: Run test to verify it PASSES with current code**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestClaudeTargetModelInjection::test_constructor_without_model_is_backward_compatible -v`
Expected: PASS. This is a regression-guard: we want to be sure nothing breaks when the new kwarg is added.

### Task 3.2: Add failing test — agent gets injected `model:`

**Files:**
- Modify: `tests/test_claude_target.py` (append to `TestClaudeTargetModelInjection`)

- [ ] **Step 1: Write the failing test**

Append to `TestClaudeTargetModelInjection`:

```python
    def test_agent_frontmatter_gets_injected_model(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent("a", b"---\nname: a\n---\nBody.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert meta["model"] == "claude-opus-4-7"

    def test_agent_existing_model_is_overwritten(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent(
            "a", b"---\nname: a\nmodel: sonnet\n---\nBody.\n"
        )
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "agents" / "a.md").read_bytes()
        )
        assert meta["model"] == "claude-opus-4-7"

    def test_agent_no_frontmatter_is_unchanged(self, tmp_path: Path) -> None:
        # Source files without frontmatter are written as-is.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_agent("plain", b"Plain body, no frontmatter.\n")
        assert (
            (tmp_path / ".claude" / "agents" / "plain.md").read_bytes()
            == b"Plain body, no frontmatter.\n"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestClaudeTargetModelInjection -v`
Expected: FAIL — `TypeError: ClaudeTarget.__init__() got an unexpected keyword argument 'model'`.

- [ ] **Step 3: Extend `ClaudeTarget.__init__` and the deploy_* methods**

In `src/promptdeploy/targets/claude.py`, replace lines 25-27 (the current `__init__`) with:

```python
    def __init__(
        self,
        target_id: str,
        config_path: Path,
        *,
        model: Optional[str] = None,
    ) -> None:
        self._id = target_id
        self._config_path = config_path.expanduser().resolve()
        self._model = model
        self._injected = {"model": model} if model else None
```

Then update `deploy_agent` (line 55-58):

```python
    def deploy_agent(self, name: str, content: bytes) -> None:
        dest = self._config_path / "agents" / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            transform_for_target(content, self._id, inject=self._injected)
        )
```

`deploy_command` at lines 60-63 is **unchanged** (commands never get the injection).

Update `deploy_skill` at line 76 (inside the `if skill_md.exists()` block):

```python
        if skill_md.exists():
            skill_md.write_bytes(
                transform_for_target(
                    skill_md.read_bytes(), self._id, inject=self._injected
                )
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestClaudeTargetModelInjection -v`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_claude_target.py src/promptdeploy/targets/claude.py
git commit -m "feat(claude-target): inject model frontmatter into agents"
```

### Task 3.3: Add failing test — skill's `SKILL.md` gets injected `model:`; command does NOT

**Files:**
- Modify: `tests/test_claude_target.py` (append to `TestClaudeTargetModelInjection`)

- [ ] **Step 1: Write the failing tests**

Append to `TestClaudeTargetModelInjection`:

```python
    def test_skill_md_gets_injected_model(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")

        src = tmp_path / "src-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"---\nname: s\n---\nSkill body.\n")

        target.deploy_skill("s", src)

        deployed_md = tmp_path / ".claude" / "skills" / "s" / "SKILL.md"
        meta, _ = parse_frontmatter(deployed_md.read_bytes())
        assert meta["model"] == "claude-opus-4-7"

    def test_command_is_not_injected(self, tmp_path: Path) -> None:
        # Commands must never receive the injected model field.
        config = tmp_path / ".claude"
        config.mkdir()
        target = ClaudeTarget("my-target", config, model="claude-opus-4-7")
        target.deploy_command("fix", b"---\nname: fix\n---\nFix things.\n")
        meta, _ = parse_frontmatter(
            (tmp_path / ".claude" / "commands" / "fix.md").read_bytes()
        )
        assert "model" not in meta
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py::TestClaudeTargetModelInjection -v`
Expected: all six tests PASS (implementation from Task 3.2 already handles skills; commands are already unchanged).

- [ ] **Step 3: Commit**

```bash
git add tests/test_claude_target.py
git commit -m "test(claude-target): cover skill injection and command non-injection"
```

### Task 3.4: Extend `create_target()` factory

**Files:**
- Modify: `src/promptdeploy/targets/__init__.py`
- Create: `tests/test_create_target.py` (new file — factory is currently tested only indirectly)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_create_target.py`:

```python
"""Tests for the target factory in promptdeploy.targets.create_target."""

from pathlib import Path

from promptdeploy.config import TargetConfig
from promptdeploy.targets import create_target
from promptdeploy.targets.claude import ClaudeTarget


class TestCreateTarget:
    def test_claude_target_without_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc)
        assert isinstance(target, ClaudeTarget)
        assert target._model is None  # noqa: SLF001
        assert target._injected is None  # noqa: SLF001

    def test_claude_target_with_per_target_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc)
        assert target._model == "claude-sonnet-4-6"  # noqa: SLF001

    def test_claude_target_with_global_model(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="c", type="claude", path=tmp_path / "c")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert target._model == "claude-opus-4-7"  # noqa: SLF001

    def test_per_target_overrides_global(self, tmp_path: Path) -> None:
        tc = TargetConfig(
            id="c",
            type="claude",
            path=tmp_path / "c",
            model="claude-sonnet-4-6",
        )
        target = create_target(tc, global_model="claude-opus-4-7")
        assert target._model == "claude-sonnet-4-6"  # noqa: SLF001

    def test_non_claude_target_does_not_get_model(self, tmp_path: Path) -> None:
        # Droid and OpenCode constructors don't accept `model`.
        from promptdeploy.targets.droid import DroidTarget

        tc = TargetConfig(id="d", type="droid", path=tmp_path / "d")
        target = create_target(tc, global_model="claude-opus-4-7")
        assert isinstance(target, DroidTarget)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_create_target.py -v`
Expected: FAIL — `create_target(tc, global_model=...)` raises `TypeError: create_target() got an unexpected keyword argument 'global_model'`.

- [ ] **Step 3: Extend the factory**

Replace the body of `src/promptdeploy/targets/__init__.py` (lines 11-44) with:

```python
def create_target(target_config, *, global_model=None):
    """Create a Target instance from a TargetConfig.

    When the config has a ``host`` field, the inner target operates on a
    local staging directory and is wrapped in :class:`RemoteTarget` which
    handles rsync-based sync to/from the remote host.

    For ``claude``-type targets, ``global_model`` supplies the default model
    to inject into deployed agents and skills when the target does not have
    its own ``model`` override. The per-target ``TargetConfig.model`` wins
    when both are set. ``None`` disables injection entirely.
    """
    from .claude import ClaudeTarget
    from .droid import DroidTarget
    from .opencode import OpenCodeTarget

    is_remote = target_config.host is not None
    if is_remote:
        staging_path = Path(
            tempfile.mkdtemp(prefix=f"promptdeploy-{target_config.id}-")
        )
    else:
        staging_path = target_config.path

    effective_model = target_config.model or global_model

    factories = {
        "claude": lambda tc, p: ClaudeTarget(tc.id, p, model=effective_model),
        "droid": lambda tc, p: DroidTarget(tc.id, p),
        "opencode": lambda tc, p: OpenCodeTarget(tc.id, p),
    }
    factory = factories.get(target_config.type)
    if factory is None:
        raise ValueError(f"Unknown target type: {target_config.type}")

    inner = factory(target_config, staging_path)

    if is_remote:
        return RemoteTarget(inner, target_config.host, target_config.path, staging_path)

    return inner
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_create_target.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_create_target.py src/promptdeploy/targets/__init__.py
git commit -m "feat(targets): resolve effective model in create_target factory"
```

### Task 3.5: Thread `global_model` through `deploy()`

**Files:**
- Modify: `src/promptdeploy/deploy.py:162-164`
- Modify: `tests/test_deploy.py` (add a new test case verifying the full plumbing)

- [ ] **Step 1: Write the failing integration test**

Find a suitable spot in `tests/test_deploy.py` (near any existing deploy-to-claude test) and add:

```python
class TestDeployModelInjection:
    def test_agent_deployed_with_injected_model_from_models_yaml(
        self, tmp_path: Path
    ) -> None:
        # Full integration: deploy() reads models.yaml, threads the default
        # through create_target, which threads it into ClaudeTarget.
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        (source_root / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(id="c", type="claude", path=target_dir),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert meta["model"] == "claude-opus-4-7"

    def test_per_target_model_overrides_global(self, tmp_path: Path) -> None:
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        (source_root / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=target_dir,
                    model="claude-sonnet-4-6",
                ),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert meta["model"] == "claude-sonnet-4-6"

    def test_no_models_yaml_means_no_injection(self, tmp_path: Path) -> None:
        from promptdeploy.config import Config, TargetConfig
        from promptdeploy.deploy import deploy

        source_root = tmp_path / "src"
        source_root.mkdir()
        (source_root / "agents").mkdir()
        (source_root / "agents" / "helper.md").write_bytes(
            b"---\nname: helper\n---\nAgent body.\n"
        )
        # No models.yaml at all.

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "c": TargetConfig(id="c", type="claude", path=target_dir),
            },
            groups={},
        )

        deploy(config)

        from promptdeploy.frontmatter import parse_frontmatter

        deployed = target_dir / "agents" / "helper.md"
        meta, _ = parse_frontmatter(deployed.read_bytes())
        assert "model" not in meta
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_deploy.py::TestDeployModelInjection -v`
Expected: FAIL on the first two tests — no `model` in the deployed file — because `deploy()` does not yet read `models.yaml` or pass a global default to `create_target()`. The third test should pass (no models.yaml → no injection is the current behavior).

- [ ] **Step 3: Wire `load_anthropic_default_model` into `deploy()`**

First, extend the existing module-top import block in `src/promptdeploy/deploy.py`. The current line imports `Config` from `.config`; add `load_anthropic_default_model` to the same import:

```python
from .config import Config, load_anthropic_default_model
```

Next, modify `deploy()` so the default is read once before the per-target loop. Locate the block that currently reads (lines 160-164):

```python
    actions: List[DeployAction] = []

    for target_id in target_ids:
        target_config = config.targets[target_id]
        target = create_target(target_config)
```

Replace it with:

```python
    actions: List[DeployAction] = []

    global_model = load_anthropic_default_model(config.source_root / "models.yaml")

    for target_id in target_ids:
        target_config = config.targets[target_id]
        target = create_target(target_config, global_model=global_model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_deploy.py::TestDeployModelInjection -v`
Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_deploy.py src/promptdeploy/deploy.py
git commit -m "feat(deploy): thread anthropic default model through create_target"
```

### Task 3.6: Run full suite with coverage

- [ ] **Step 1: Confirm 100% coverage and no regressions**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all tests PASS; `claude.py`, `targets/__init__.py`, and `deploy.py` show 100% coverage on new code.

- [ ] **Step 2: Likely-missed branches to verify**

- `ClaudeTarget.__init__` when `model=None` (covered by `test_constructor_without_model_is_backward_compatible`).
- `ClaudeTarget.__init__` when `model` is a truthy string (covered by `test_agent_frontmatter_gets_injected_model`).
- `create_target` with `global_model=None` (covered by `test_claude_target_without_model`).
- `create_target` with `global_model` set and per-target unset (covered by `test_claude_target_with_global_model`).
- `create_target` for non-claude targets receiving `global_model=` (covered by `test_non_claude_target_does_not_get_model` — the global_model is silently dropped because the non-claude factories don't take `model`).
- `deploy()` `load_anthropic_default_model` returning `None` vs. string (covered by `test_no_models_yaml_means_no_injection` and `test_agent_deployed_with_injected_model_from_models_yaml`).

If coverage flags a line, add one targeted test and re-run.

- [ ] **Step 3: Commit any additional tests**

```bash
git add tests/
git commit -m "test: cover remaining claude injection branches"
```

---

## Chunk 4: Validation changes

**Goal:** Make `validate.py` reflect the new schema. Three independent rules:

1. **Relax provider-credential requirement.** `display_name` is still required for every provider. `base_url` and `api_key` are required **only when** the provider has a `droid:` or `opencode:` subsection (because those targets actually dispatch HTTP requests). A claude-only provider (one that has a `claude:` subsection but no `droid:`/`opencode:` subsection) is valid without credentials.
2. **Error on per-target `model:` for non-claude targets.** A `TargetConfig.model` is only meaningful on `type: claude` — injecting a model into droid or opencode deploys has no effect. Flag this as a hard error.
3. **Warn on unknown effective-model strings.** For each claude target, compute the effective model (`target.model or providers.anthropic.claude.default_model`). If it resolves to a string not found in `providers.anthropic.models` **and** not in the always-accepted alias set `{"opus", "sonnet", "haiku", "inherit"}`, emit a `level="warning"` `ValidationIssue`. Warnings surface in `promptdeploy validate` output but do not fail the command.

To implement rule (3) cleanly, we also need a sibling helper to Chunk 2's `load_anthropic_default_model`: `load_anthropic_known_models(models_yaml_path)` returning `Optional[set[str]]`. Same permissive semantics — any missing key returns `None`.

### Task 4.1: Relax the required-provider-fields rule

**Files:**
- Modify: `tests/test_validate.py` (append to `TestValidateItemModels`; update existing `test_missing_required_fields`)
- Modify: `src/promptdeploy/validate.py:251-259`

- [ ] **Step 1: Write the failing test — claude-only provider does not require credentials**

Append to `TestValidateItemModels` (after the existing `test_missing_required_fields` at line 192):

```python
    def test_claude_only_provider_does_not_require_credentials(
        self, config: Config
    ) -> None:
        # A provider with only a claude: subsection (no droid:, no opencode:)
        # does not need base_url or api_key — Claude Code reads no credentials
        # from models.yaml.
        item = self._make_models_item(
            {
                "providers": {
                    "anthropic": {
                        "display_name": "Anthropic",
                        "claude": {"default_model": "claude-opus-4-7"},
                        "models": {
                            "claude-opus-4-7": {"display_name": "Claude Opus 4.7"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert not any("'base_url'" in m for m in messages)
        assert not any("'api_key'" in m for m in messages)

    def test_claude_only_provider_still_requires_display_name(
        self, config: Config
    ) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "anthropic": {
                        "claude": {"default_model": "claude-opus-4-7"},
                        "models": {
                            "claude-opus-4-7": {"display_name": "Claude Opus 4.7"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'display_name'" in m for m in messages)

    def test_provider_with_opencode_subsection_requires_credentials(
        self, config: Config
    ) -> None:
        # A provider with opencode: (or droid:) still requires base_url and api_key.
        item = self._make_models_item(
            {
                "providers": {
                    "vendor": {
                        "display_name": "Vendor",
                        "opencode": {"type": "openai"},
                        "models": {"m": {"display_name": "M"}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'base_url'" in m for m in messages)
        assert any("'api_key'" in m for m in messages)
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateItemModels -v`

Expected: `test_claude_only_provider_does_not_require_credentials` FAILS — the current rule emits errors for missing `base_url` and `api_key` regardless of subsections. `test_claude_only_provider_still_requires_display_name` and `test_provider_with_opencode_subsection_requires_credentials` PASS with the current code (because current code always reports all three).

- [ ] **Step 3: Implement the relaxed rule**

In `src/promptdeploy/validate.py`, replace lines 251-259 (the `for required in ("display_name", "base_url", "api_key"):` loop) with:

```python
                # display_name is always required.
                if "display_name" not in prov:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Provider '{prov_key}' missing required field 'display_name'",
                            file_path=item.path,
                        )
                    )
                # base_url and api_key are required only when the provider has
                # a droid: or opencode: subsection — those targets actually
                # dispatch HTTP requests. A claude-only provider carries no
                # credentials because Claude Code does not read them from
                # models.yaml.
                if "droid" in prov or "opencode" in prov:
                    for required in ("base_url", "api_key"):
                        if required not in prov:
                            issues.append(
                                ValidationIssue(
                                    level="error",
                                    message=f"Provider '{prov_key}' missing required field '{required}'",
                                    file_path=item.path,
                                )
                            )
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateItemModels::test_claude_only_provider_does_not_require_credentials tests/test_validate.py::TestValidateItemModels::test_claude_only_provider_still_requires_display_name tests/test_validate.py::TestValidateItemModels::test_provider_with_opencode_subsection_requires_credentials -v`

Expected: all three PASS.

- [ ] **Step 5: Update the existing `test_missing_required_fields` test**

The existing test at line 192 asserts that a bare provider (no subsections) reports all three of `display_name`, `base_url`, and `api_key` as missing. Under the new rule, a bare provider only reports `display_name` — so the old assertions are wrong.

Update the test to add a `droid:` subsection, preserving the original intent ("all three fields are required when the provider needs credentials"):

Replace `test_missing_required_fields` entirely with:

```python
    def test_missing_required_fields(self, config: Config) -> None:
        # Provider has a droid: subsection, so all three fields are required.
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "droid": {"type": "openai"},
                        "models": {"m": {}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'display_name'" in m for m in messages)
        assert any("'base_url'" in m for m in messages)
        assert any("'api_key'" in m for m in messages)
```

- [ ] **Step 6: Run all `TestValidateItemModels` tests**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateItemModels -v`

Expected: all tests PASS (including the updated `test_missing_required_fields`).

- [ ] **Step 7: Commit**

```bash
git add tests/test_validate.py src/promptdeploy/validate.py
git commit -m "feat(validate): require provider credentials only when droid/opencode present"
```

### Task 4.2: Error on per-target `model:` for non-claude targets

**Files:**
- Modify: `tests/test_validate.py` (append to `TestValidateAll`)
- Modify: `src/promptdeploy/validate.py` (inside `validate_all`, before `return issues`)

- [ ] **Step 1: Write the failing test**

Append to `TestValidateAll`:

```python
    def test_per_target_model_on_non_claude_target_is_error(
        self, tmp_path: Path
    ) -> None:
        # Setting model: on a droid or opencode target is a hard error —
        # model injection only applies to claude targets.
        config = Config(
            source_root=tmp_path,
            targets={
                "d": TargetConfig(
                    id="d",
                    type="droid",
                    path=tmp_path / "d",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        errors = [
            i for i in issues
            if i.level == "error" and "'model'" in i.message and "'d'" in i.message
        ]
        assert len(errors) == 1
        assert "only applies to claude targets" in errors[0].message

    def test_per_target_model_on_claude_target_is_ok(
        self, tmp_path: Path
    ) -> None:
        # Include a models.yaml so the model name is recognized and Task 4.4's
        # unknown-model warning does not fire for this valid configuration.
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        assert issues == []
```

- [ ] **Step 2: Run tests to verify first fails, second passes**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateAll::test_per_target_model_on_non_claude_target_is_error tests/test_validate.py::TestValidateAll::test_per_target_model_on_claude_target_is_ok -v`

Expected: `test_per_target_model_on_non_claude_target_is_error` FAILS (no validation catches the invalid combination). `test_per_target_model_on_claude_target_is_ok` PASSES (nothing wrong with it yet).

- [ ] **Step 3: Add the target-level check to `validate_all`**

In `src/promptdeploy/validate.py`, locate the `return issues` line at the bottom of `validate_all` (line 74). Insert the following block **before** `return issues`:

```python
    # Target-level rule: per-target model: only applies to claude targets.
    for target in config.targets.values():
        if target.model is not None and target.type != "claude":
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Target '{target.id}' has 'model' set but type is "
                        f"'{target.type}'; model injection only applies to "
                        f"claude targets"
                    ),
                    file_path=config.source_root / "deploy.yaml",
                )
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateAll::test_per_target_model_on_non_claude_target_is_error tests/test_validate.py::TestValidateAll::test_per_target_model_on_claude_target_is_ok -v`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_validate.py src/promptdeploy/validate.py
git commit -m "feat(validate): error when per-target model set on non-claude target"
```

### Task 4.3: Add `load_anthropic_known_models` helper

**Files:**
- Modify: `tests/test_config.py` (append new class `TestLoadAnthropicKnownModels` at end of file)
- Modify: `src/promptdeploy/config.py` (add helper after `load_anthropic_default_model`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
class TestLoadAnthropicKnownModels:
    def test_returns_model_keys(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
            "      claude-sonnet-4-6:\n"
            "        display_name: Claude Sonnet 4.6\n"
        )
        assert load_anthropic_known_models(models_path) == {
            "claude-opus-4-7",
            "claude-sonnet-4-6",
        }

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        assert load_anthropic_known_models(tmp_path / "nope.yaml") is None

    def test_returns_none_when_yaml_invalid(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text("providers: [unclosed\n")
        assert load_anthropic_known_models(models_path) is None

    def test_returns_none_when_data_not_dict(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text("- just\n- a\n- list\n")
        assert load_anthropic_known_models(models_path) is None

    def test_returns_none_when_providers_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text("something_else: 1\n")
        assert load_anthropic_known_models(models_path) is None

    def test_returns_none_when_anthropic_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  other:\n"
            "    display_name: Other\n"
        )
        assert load_anthropic_known_models(models_path) is None

    def test_returns_none_when_models_key_missing(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
        )
        assert load_anthropic_known_models(models_path) is None

    def test_returns_empty_set_when_models_empty(self, tmp_path: Path) -> None:
        from promptdeploy.config import load_anthropic_known_models

        models_path = tmp_path / "models.yaml"
        models_path.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    models: {}\n"
        )
        assert load_anthropic_known_models(models_path) == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadAnthropicKnownModels -v`

Expected: FAIL — `ImportError: cannot import name 'load_anthropic_known_models' from 'promptdeploy.config'`.

- [ ] **Step 3: Add the helper to `src/promptdeploy/config.py`**

Append to the end of `src/promptdeploy/config.py` (after `load_anthropic_default_model` added in Chunk 2):

```python
def load_anthropic_known_models(models_yaml_path: Path) -> Optional[set[str]]:
    """Return the set of keys under ``providers.anthropic.models`` in a models.yaml.

    Returns ``None`` when the file is missing, cannot be parsed, the top-level
    structure is wrong, or any intermediate key is absent. Returns an empty
    set when ``models:`` is present but empty. Used by
    :mod:`promptdeploy.validate` to surface warnings for unknown model strings;
    treated as the same permissive contract as
    :func:`load_anthropic_default_model`.
    """
    if not models_yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(models_yaml_path.read_text("utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    anthropic = providers.get("anthropic")
    if not isinstance(anthropic, dict):
        return None
    models = anthropic.get("models")
    if not isinstance(models, dict):
        return None
    return set(models.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py::TestLoadAnthropicKnownModels -v`

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/promptdeploy/config.py
git commit -m "feat(config): add load_anthropic_known_models helper"
```

### Task 4.4: Warn on unknown effective model strings

**Files:**
- Modify: `tests/test_validate.py` (append to `TestValidateAll`)
- Modify: `src/promptdeploy/validate.py` (extend imports; extend the target loop in `validate_all`)

- [ ] **Step 1: Write the failing tests**

Append to `TestValidateAll`:

```python
    def test_unknown_effective_model_produces_warning(
        self, tmp_path: Path
    ) -> None:
        # A claude target whose effective model is not in providers.anthropic.models
        # and not in the always-accepted alias set produces a warning.
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="made-up-model",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [
            i for i in issues
            if i.level == "warning" and "made-up-model" in i.message
        ]
        assert len(warnings) == 1

    def test_known_effective_model_no_warning(self, tmp_path: Path) -> None:
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(id="c", type="claude", path=tmp_path / "c"),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_alias_opus_is_always_accepted(self, tmp_path: Path) -> None:
        # The aliases opus/sonnet/haiku/inherit are always accepted, even when
        # models.yaml is missing or the anthropic provider has no models listed.
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="opus",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_no_effective_model_no_warning(self, tmp_path: Path) -> None:
        # No per-target model, no models.yaml => no effective model => no warning.
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(id="c", type="claude", path=tmp_path / "c"),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_unknown_model_without_models_yaml_warns(self, tmp_path: Path) -> None:
        # When models.yaml is absent, known_models is None — fall back to the
        # always-accepted alias set only. A non-alias model string warns.
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [
            i for i in issues
            if i.level == "warning" and "claude-opus-4-7" in i.message
        ]
        assert len(warnings) == 1

    def test_non_claude_target_without_model_is_silent(
        self, tmp_path: Path
    ) -> None:
        # Non-claude target with no model: set hits the second `continue` in
        # the target-level loop (not the first, which fires only when model is
        # set). This test exercises that branch for coverage.
        config = Config(
            source_root=tmp_path,
            targets={
                "d": TargetConfig(id="d", type="droid", path=tmp_path / "d"),
            },
            groups={},
        )
        issues = validate_all(config)
        assert [i for i in issues if i.level in ("error", "warning")] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateAll -v -k "model"`

Expected: the four "warning" tests fail because `validate_all` does not yet compute effective models. `test_known_effective_model_no_warning` and `test_no_effective_model_no_warning` may pass vacuously (no warnings emitted by current code).

- [ ] **Step 3: Extend imports in `validate.py`**

Replace the existing `from .config import Config` import at the top of `src/promptdeploy/validate.py` (around line 11) with:

```python
from .config import (
    Config,
    load_anthropic_default_model,
    load_anthropic_known_models,
)
```

- [ ] **Step 4: Extend the target-level loop in `validate_all`**

In the target-level loop added in Task 4.2 (inside `validate_all`, before `return issues`), extend it to also compute and check the effective model. Replace the block:

```python
    # Target-level rule: per-target model: only applies to claude targets.
    for target in config.targets.values():
        if target.model is not None and target.type != "claude":
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Target '{target.id}' has 'model' set but type is "
                        f"'{target.type}'; model injection only applies to "
                        f"claude targets"
                    ),
                    file_path=config.source_root / "deploy.yaml",
                )
            )
```

with:

```python
    # Target-level rules: (a) per-target model: only applies to claude targets;
    # (b) warn when the effective model on a claude target is neither in
    # providers.anthropic.models nor an always-accepted alias.
    models_yaml_path = config.source_root / "models.yaml"
    default_model = load_anthropic_default_model(models_yaml_path)
    known_models = load_anthropic_known_models(models_yaml_path)
    always_accepted_aliases = {"opus", "sonnet", "haiku", "inherit"}
    allowed_models = always_accepted_aliases | (known_models or set())
    deploy_yaml_path = config.source_root / "deploy.yaml"

    for target in config.targets.values():
        if target.model is not None and target.type != "claude":
            issues.append(
                ValidationIssue(
                    level="error",
                    message=(
                        f"Target '{target.id}' has 'model' set but type is "
                        f"'{target.type}'; model injection only applies to "
                        f"claude targets"
                    ),
                    file_path=deploy_yaml_path,
                )
            )
            continue
        if target.type != "claude":
            continue
        effective = target.model or default_model
        if effective is None:
            continue
        if effective not in allowed_models:
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=(
                        f"Target '{target.id}' effective model '{effective}' "
                        f"is not listed in providers.anthropic.models and is "
                        f"not a known alias"
                    ),
                    file_path=deploy_yaml_path,
                )
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py::TestValidateAll -v`

Expected: all `TestValidateAll` tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_validate.py src/promptdeploy/validate.py
git commit -m "feat(validate): warn when effective model is unknown"
```

### Task 4.5: Run full suite with coverage

- [ ] **Step 1: Confirm 100% coverage and no regressions**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`

Expected: all tests PASS; `validate.py` and `config.py` show 100% coverage on the new code paths.

- [ ] **Step 2: Likely-missed branches to verify**

- Relaxed rule, claude-only branch (covered by `test_claude_only_provider_does_not_require_credentials`).
- Relaxed rule, droid/opencode subsection branch (covered by `test_provider_with_opencode_subsection_requires_credentials` and updated `test_missing_required_fields`).
- Per-target `model:` on non-claude target error (covered by `test_per_target_model_on_non_claude_target_is_error`).
- Per-target `model:` on claude target, no error emitted (covered by `test_per_target_model_on_claude_target_is_ok`).
- Unknown-model warning emitted (covered by `test_unknown_effective_model_produces_warning` and `test_unknown_model_without_models_yaml_warns`).
- Known-model no warning (covered by `test_known_effective_model_no_warning`).
- Always-accepted alias no warning (covered by `test_alias_opus_is_always_accepted`).
- No effective model, no warning (covered by `test_no_effective_model_no_warning`).
- Non-claude target with `model=None`, hits the second `continue` in the target-level loop (covered by `test_non_claude_target_without_model_is_silent`).
- `load_anthropic_known_models` branches: happy path, missing file, invalid YAML, top-level not dict, missing providers, missing anthropic, missing models key, empty models dict (all 8 cases covered by `TestLoadAnthropicKnownModels`).

If coverage flags a line, write one targeted test and re-run.

- [ ] **Step 3: Commit any additional tests**

```bash
git add tests/
git commit -m "test(validate): cover remaining model-validation branches"
```

---

## Chunk 5: models.yaml — add `anthropic:` provider

**Goal:** Insert a new `anthropic:` provider at the top of `models.yaml`'s `providers:` map. This is the config surface the Claude-model-injection feature reads from:

- `display_name: "Anthropic"` — required by validation.
- `except: [droid, opencode, opencode-vulcan]` — scopes the provider to claude targets. Claude already skips models.yaml deployment entirely, so this filter is defensive against future droid/opencode deploys accidentally ingesting the provider.
- `claude:` subsection with `default_model: claude-opus-4-7` — the key that Chunk 2's `load_anthropic_default_model` and Chunk 4's `load_anthropic_known_models` read.
- `models:` dict listing the three current Claude model IDs (informational; prevents Chunk 4's unknown-model warning from firing on any of them).

This is a content-only chunk — no Python code changes. Validation and test suites (all established in prior chunks) should pass unchanged.

### Task 5.1: Insert the anthropic provider block

**Files:**
- Modify: `models.yaml` (insert new provider before `positron-anthropic:` at line 3)

- [ ] **Step 1: Edit `models.yaml`**

Use the Edit tool to replace the current opening of `models.yaml`:

Old string:
```yaml
providers:

  positron-anthropic:
    display_name: "Positron"
```

New string:
```yaml
providers:

  anthropic:
    display_name: "Anthropic"
    except: [droid, opencode, opencode-vulcan]
    claude:
      default_model: claude-opus-4-7
    models:
      claude-haiku-4-5-20251001:
        display_name: "Claude Haiku 4.5"
      claude-opus-4-7:
        display_name: "Claude Opus 4.7"
      claude-sonnet-4-6:
        display_name: "Claude Sonnet 4.6"

  positron-anthropic:
    display_name: "Positron"
```

- [ ] **Step 2: Run `promptdeploy validate`**

Run: `PYTHONPATH=src python -m promptdeploy validate`
Expected: exit code 0. No errors. The newly added `anthropic` provider:
- Passes the relaxed credentials rule from Task 4.1 (no `droid:` or `opencode:` subsection → `base_url` and `api_key` not required).
- Passes the provider-level `except:` validation (every ID in the list is a valid target defined in `deploy.yaml`).
- Has a non-empty `models:` dict (passes the existing "no models defined" check).

If validation reports errors, read them and fix the YAML block accordingly before continuing.

- [ ] **Step 3: Run full pytest suite**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all tests PASS; 100% coverage preserved.

This checks that the `load_anthropic_default_model` and `load_anthropic_known_models` helpers from Chunks 2 and 4 return the expected values when pointed at the real repo's `models.yaml`. No test modification needed — existing tests use synthetic fixtures, and the real-file behavior is implicitly exercised by the validate command above.

- [ ] **Step 4: Commit**

```bash
git add models.yaml
git commit -m "feat(models): add anthropic provider with claude default_model"
```

### Task 5.2: Dry-run spot check

**Files:**
- None (verification-only)

- [ ] **Step 1: Dry-run deploy to a claude target**

Run: `PYTHONPATH=src python -m promptdeploy deploy --dry-run --target claude-personal --verbose`
Expected: action output shows diffs for agent and skill deployments with `model: claude-opus-4-7` added to their frontmatter (new additions or overwrites of any prior `model:` value). The `models.yaml` file itself is not deployed to claude targets (claude skips models.yaml).

- [ ] **Step 2: Dry-run deploy to a non-claude target**

Run: `PYTHONPATH=src python -m promptdeploy deploy --dry-run --target droid --verbose`
Expected: the new `anthropic` provider does **not** appear in any droid-destined manifest (filtered out by `except: [droid, opencode, opencode-vulcan]`). The existing `positron-anthropic` and other providers still appear.

If either expectation fails, investigate — the filter or the injection may need revisiting.

- [ ] **Step 3: No commit**

This task is verification-only; nothing to commit.

## Chunk 6: Docs — PROMPTDEPLOY.md and CLAUDE.md

**Goal:** Document the two new configuration surfaces (per-target `model:` in `deploy.yaml` and `providers.anthropic.claude.default_model` in `models.yaml`) for users reading `PROMPTDEPLOY.md`, and update the architect-facing note in `CLAUDE.md` so future Claude sessions know the claude target now consults `models.yaml` for the default model.

This is a docs-only chunk — no Python code, no test changes, no new source items. It is the final chunk before commit and handoff.

### Task 6.1: Update `PROMPTDEPLOY.md` with per-target `model:` and global default

**Files:**
- Modify: `PROMPTDEPLOY.md` — insert a new subsection after the existing `## Environment Filtering` section (ends at line 55), before `## Development` (starts at line 57).

- [ ] **Step 1: Insert the "Model Injection" section**

Use the Edit tool to insert the new section between `Environment Filtering` and `Development`:

Old string:
```
- `only: [claude]` -- Deploy only to the `claude` group.
- `except: [droid]` -- Deploy everywhere except Factory Droid.
- Both cannot be used on the same item.
- Group names (defined in `deploy.yaml`) expand to their members.

## Development
```

New string:
```
- `only: [claude]` -- Deploy only to the `claude` group.
- `except: [droid]` -- Deploy everywhere except Factory Droid.
- Both cannot be used on the same item.
- Group names (defined in `deploy.yaml`) expand to their members.

## Model Injection (Claude targets)

For every agent and skill deployed to a Claude Code target, `promptdeploy` injects a `model:` field into the YAML frontmatter so the deployed copy explicitly pins the model. Injection is applied only to agents and skills -- commands, MCP servers, hooks, and models are not touched.

The effective model is resolved in this order:

1. **Per-target override** -- `model:` set on a specific target in `deploy.yaml` wins.
2. **Global default** -- `providers.anthropic.claude.default_model` in `models.yaml`.
3. **No injection** -- if neither is set, no `model:` field is written.

Injection overwrites any `model:` field authored in the source item. Remove the source `model:` if you want deployed behavior to match source behavior exactly, or set a per-target override when a specific target should use a different model.

### Per-target override

```yaml
# deploy.yaml
targets:
  claude-personal:
    type: claude
    path: ~/.config/claude/personal
    labels: [claude, personal, local]
    model: claude-sonnet-4-6
```

Accepted values: any model alias accepted by Claude Code's `model:` frontmatter field (e.g., `opus`, `sonnet`, `haiku`, `claude-opus-4-7`, `inherit`). The value is written verbatim. Setting `model:` on a non-claude target is a validation error.

### Global default

```yaml
# models.yaml
providers:
  anthropic:
    display_name: "Anthropic"
    except: [droid, opencode, opencode-vulcan]
    claude:
      default_model: claude-opus-4-7
    models:
      claude-haiku-4-5-20251001:
        display_name: "Claude Haiku 4.5"
      claude-opus-4-7:
        display_name: "Claude Opus 4.7"
      claude-sonnet-4-6:
        display_name: "Claude Sonnet 4.6"
```

The `anthropic` provider itself is scoped to claude targets via `except:` so it does not leak into Droid or OpenCode configuration. The `models:` dict is informational -- it lets `promptdeploy validate` warn when a per-target `model:` references a model not listed here (typo detection). `models:` entries require no credentials; `base_url` and `api_key` are only required when a provider deploys to Droid or OpenCode.

## Development
```

- [ ] **Step 2: Verify the insertion**

Run: use the Read tool on `PROMPTDEPLOY.md` lines 40-120 and confirm:
- The `## Environment Filtering` section is unchanged.
- The new `## Model Injection (Claude targets)` section follows it.
- The `## Development` section starts after the new subsection.
- Both code fences are balanced.

- [ ] **Step 3: Commit**

```bash
git add PROMPTDEPLOY.md
git commit -m "docs: document per-target model and anthropic default_model"
```

### Task 6.2: Update `CLAUDE.md` to reflect claude now reads `models.yaml`

**Files:**
- Modify: `CLAUDE.md:18` — the Models row of the content types table.
- Modify: `CLAUDE.md` — extend the `### Key Patterns` bullet list with a short model-injection note.

- [ ] **Step 1: Update the models row in the content types table**

Use the Edit tool to replace line 18's table row:

Old string:
```
| Models | `models.yaml` | Single YAML file, providers with nested models | Droid and OpenCode only; Claude skipped |
```

New string:
```
| Models | `models.yaml` | Single YAML file, providers with nested models | Droid and OpenCode consume the full config; claude targets only read `providers.anthropic.claude.default_model` to inject `model:` frontmatter into deployed agents and skills |
```

- [ ] **Step 2: Add a Key Patterns bullet for model injection**

Use the Edit tool to append a new bullet directly beneath the existing `**Remote deployment**` bullet (currently the last bullet in the `### Key Patterns` list at line 49).

Old string:
```
- **Remote deployment** -- Targets with `host:` in `deploy.yaml` are deployed via rsync over SSH. The `Target` ABC has `prepare()`/`finalize()`/`cleanup()` lifecycle hooks (no-ops for local targets). `RemoteTarget` wraps any inner target, using a local staging dir: `prepare()` pulls remote state, `finalize()` pushes back. Path `~` expansion is skipped for remote targets (rsync expands `~` on the remote). `--target-root` strips `host` to force local preview.
```

New string:
```
- **Remote deployment** -- Targets with `host:` in `deploy.yaml` are deployed via rsync over SSH. The `Target` ABC has `prepare()`/`finalize()`/`cleanup()` lifecycle hooks (no-ops for local targets). `RemoteTarget` wraps any inner target, using a local staging dir: `prepare()` pulls remote state, `finalize()` pushes back. Path `~` expansion is skipped for remote targets (rsync expands `~` on the remote). `--target-root` strips `host` to force local preview.
- **Claude model injection** -- For every agent and skill deployed to a claude target, `ClaudeTarget` injects a `model:` field into the YAML frontmatter via `frontmatter.transform_for_target(..., inject={"model": effective_model})`. The effective model is resolved by `targets.__init__.create_target` from `TargetConfig.model` (per-target override) with fallback to `load_anthropic_default_model` (global `providers.anthropic.claude.default_model` in `models.yaml`); `None` skips injection. Commands, MCP, hooks, and models are not touched.
```

- [ ] **Step 3: Verify CLAUDE.md renders correctly**

Run: use the Read tool to re-read `CLAUDE.md` lines 11-60 and confirm:
- The Models row now reads the updated text.
- The new Key Patterns bullet sits alongside the others in the same list.
- No stray blank lines or broken table rows.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): note model injection and models.yaml consumption"
```

### Task 6.3: Final full-suite check

**Files:**
- None (verification-only)

- [ ] **Step 1: Run the full test suite one last time**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all tests pass; 100% coverage.

- [ ] **Step 2: Run `nix flake check`**

Run: `nix flake check`
Expected: all five checks pass (ruff format, ruff check, mypy, pytest+coverage, nix build).

If any check fails, stop and fix before declaring the plan complete. Docs-only changes should not affect lint or types, but running the full gate confirms no regressions slipped in across the prior chunks.

- [ ] **Step 3: No commit**

Verification-only.

