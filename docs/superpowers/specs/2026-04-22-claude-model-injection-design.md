# Inject `model:` Frontmatter into Claude-Deployed Agents and Skills

**Date:** 2026-04-22
**Status:** Approved (pending implementation)

## Background

Claude Code agents (`agents/*.md`) and skills (`skills/*/SKILL.md`) both support an optional `model:` YAML frontmatter field that forces a specific model for every invocation of that agent/skill. When absent, Claude Code falls back to the parent session's model (`inherit` semantics).

Today, `promptdeploy` writes Claude content with `transform_for_target()` which only *strips* deployment fields (`only`/`except`). It never injects anything. This means we rely on individual source files to set `model:` themselves — and most don't, so subagents and skills silently run on whatever model the user happens to have active.

The user wants to **force** a specific Claude model (default `claude-opus-4-7`) on every agent and skill deployed to a Claude target, configurable through `models.yaml` with per-target override in `deploy.yaml`.

## Goal

Add an injection pass to the Claude target that writes a `model:` field into the frontmatter of every deployed agent and skill, using an "effective model" resolved from (per-target override) → (global default) → (none, no injection).

## Non-goals

- No injection for commands, MCP servers, hooks, or models output. Only agents (`item_type == "agent"`) and skills (`item_type == "skill"`).
- No injection for Droid or OpenCode targets. The Claude `model:` frontmatter field has no equivalent in those tools; existing Droid/OpenCode model handling (via `models.yaml` → `settings.json`/`opencode.json`) is untouched.
- No new injection for other frontmatter fields (description, name, etc.). This is a `model:`-only feature.
- No changes to the filter system (`only`/`except`, filetags). Existing filtering semantics are orthogonal.
- No opt-out at the per-source-file level. Per Q1 decision, the injection is **force** — if a source file declares `model: sonnet`, it gets overwritten to match the configured model. If users need a per-target variation, they use the per-target deploy.yaml override.

## Design

### 1. Effective-model resolution

For each Claude target at deploy time:

```
effective_model(target) =
    target.model                                       if set in deploy.yaml
    else models_yaml.providers.anthropic.claude.default_model   if set
    else None   → no injection (legacy behavior preserved)
```

If `effective_model` is `None`, the Claude target writes agents and skills exactly as it does today. This preserves backward compatibility for any future consumers that do not configure the feature.

### 2. Schema — `models.yaml`

Add a new `anthropic:` provider entry alongside the existing providers:

```yaml
providers:
  anthropic:
    display_name: "Anthropic"
    claude:
      default_model: claude-opus-4-7
    except: [droid, opencode, opencode-vulcan]
    models:
      claude-opus-4-7:
        display_name: "Claude Opus 4.7"
      claude-sonnet-4-6:
        display_name: "Claude Sonnet 4.6"
      claude-haiku-4-5:
        display_name: "Claude Haiku 4.5"

  positron-anthropic:   # unchanged — Droid routes through Positron's gateway
    ...
```

Notes:
- `api_key` / `base_url` are **omitted** (Claude Code authenticates through its own `claude login` flow; `promptdeploy` never reads Anthropic credentials from `models.yaml`). This requires a corresponding change in `validate.py` (see §4 — the current validator at `validate.py:251-259` requires those fields on *every* provider).
- `except: [droid, opencode, opencode-vulcan]` is primarily needed for the **Droid** target — `droid.py::deploy_models` does not check for a `droid:` subsection and would fall back to `provider_type: "generic-chat-completion-api"` on a claude-only provider. The OpenCode target already guards with `if not oc_cfg: continue` (`opencode.py:197`) and would skip the provider silently; the opencode entries in `except:` are belt-and-suspenders. This mirrors the existing `except:` pattern used by `litellm:` for `opencode-vulcan`.
- The list contains **target IDs**, not target types. If a second Droid or OpenCode target is added to `deploy.yaml` later, it must be added here too. An alternative future-proof approach would be to support a group-based `except: [droid-group, opencode-group]` or invert the filter to `only: [claude]` — deferred as out-of-scope to keep this change minimal.
- `models:` subsection is informational — used only for soft validation (see §5). Claude target continues to `should_skip` item_type `models` and no provider data is written to any Claude target file. The current `validate.py:261-268` requires `models:` to be non-empty; the three models listed above satisfy that without further changes.
- The subsection is named `claude:` (mirroring `droid:` / `opencode:`) to reserve namespace for future Claude-specific options.

### 3. Schema — `deploy.yaml`

Add an optional flat `model:` field on any target:

```yaml
claude-vulcan:
  type: claude
  path: ~/.claude
  host: vulcan
  model: claude-sonnet-4-6     # overrides anthropic.claude.default_model for this target
  labels: [claude, personal, remote]
```

- Silently ignored for non-`claude` targets (consistent with optional-field semantics used elsewhere).
- Not required. Targets without `model:` fall back to the global default.

### 4. Code changes

**`src/promptdeploy/config.py`:**
- Add `model: Optional[str] = None` to `TargetConfig`.
- Parse the field from each target entry in `load_config()`.
- Add a top-level helper `load_models_config(path: Path) -> dict` (or extend existing models loading) that reads `models.yaml` and returns the parsed dict, so the Claude target can look up `providers.anthropic.claude.default_model`. Alternatively, pass the global default through as a separate field on `Config` to keep the Claude target code decoupled from `models.yaml`'s full schema. The plan will pick one; design allows either.
- `remap_targets_to_root()` must preserve `model` on the remapped `TargetConfig` (easy to miss — added to the construction call).

**`src/promptdeploy/frontmatter.py`:**
- Extend `transform_for_target()` signature:
  ```python
  def transform_for_target(
      content: bytes,
      target_id: str,
      inject: Optional[dict] = None,
  ) -> bytes:
  ```
  When `inject` is provided and non-empty, after `strip_deployment_fields`, each key in `inject` is set (overwriting any existing value) in the metadata dict. Key insertion order: existing keys retain their position; new keys are appended (Python dict insertion order; `yaml.dump(sort_keys=False)` preserves this).
- Semantics:
  - `inject=None` (default) → no-op, existing behavior preserved.
  - `inject={}` → no-op (treated identically to `None`).
  - `inject={"model": None}` → key is **skipped** (not written as `model: null`). This symmetry lets the effective-model resolver pass a raw `{"model": effective_model}` without needing to filter `None` upstream.
- Backward-compatible default: existing callers that pass only `(content, target_id)` continue to work unchanged.

**`src/promptdeploy/targets/claude.py`:**
- `ClaudeTarget.__init__` gains a `model: Optional[str] = None` keyword parameter (defaulted so existing test instantiations like `ClaudeTarget(id, path)` continue to work unchanged).
- Compute once: `self._injected = {"model": self._model} if self._model else None`.
- `deploy_agent` and `deploy_skill` pass `inject=self._injected` to `transform_for_target`.
- `deploy_command` continues to pass no `inject` argument — relying on the `inject=None` default — so commands are untouched per scope.
- Skill handling: the existing post-copytree `SKILL.md` rewrite (line 74–76) gets the same `inject` argument.

**`src/promptdeploy/targets/__init__.py` (factory `create_target`):**
- Propagate the resolved effective model into `ClaudeTarget(...)`. The factory looks up `target.model` (per-target override) and the global default from wherever the caller provides it, and passes the resolved value into the constructor.

**`src/promptdeploy/validate.py`:**
- Relax the required-provider-fields check at lines 251-259. Currently every provider must have `display_name`, `base_url`, and `api_key`. After this change, `base_url` and `api_key` are required **only when the provider has a `droid:` or `opencode:` subsection** (i.e., when a deployment target will actually consume credentials). `display_name` remains required for all providers as an identity/readability concern. Claude-only providers (providers with a `claude:` subsection and no `droid:` / `opencode:`) pass validation without credentials.
- Add a new error for per-target `model:` set on a non-`claude` target (iterate `config.targets.values()` in `validate_all`).
- Add a new **warning** (level `"warning"`, reusing the existing `ValidationIssue` shape at lines 16-23) when `effective_model(target)` resolves to a string not found in `providers.anthropic.models:` **and** not in the always-accepted set `{"opus", "sonnet", "haiku", "inherit"}` (Claude Code's canonical aliases plus the default). The warning does not change the CLI exit code — the existing `_run_validate` distinction between errors and warnings is preserved.

**No changes to:**
- `source.py` — existing `item_type` distinction between `"agent"`, `"skill"`, `"command"` is enough; gating happens in `claude.py`.
- `targets/droid.py`, `targets/opencode.py` — they skip the new `anthropic:` provider via the `except:` list in `models.yaml`.
- `targets/remote.py` — remote wrapper delegates to the inner `ClaudeTarget`, which already has the injection; no special handling needed.
- `filters.py` — orthogonal to this feature.

### 5. Validation

Implemented in `validate.py` (see §4 for the exact code changes). Summary of resulting behavior:

- **Hard error**: per-target `model:` specified on a non-`claude` target (probably a user mistake).
- **Warning** (level `"warning"` on `ValidationIssue`): `effective_model` resolves to a string that is not in `providers.anthropic.models:` **and** not one of `{"opus", "sonnet", "haiku", "inherit"}`. Aliases are always accepted so users don't get nagged on the obvious shortcuts. Date-stamped IDs (`claude-opus-4-7-20260416`) and `[1m]` suffixes would still trigger the warning — that's acceptable, since those users can either add the value to `providers.anthropic.models:` (silencing it) or live with the one-line warning.
- **No warning** when `providers.anthropic` is absent entirely — the feature is opt-in.
- Relaxed provider-fields rule: `base_url` / `api_key` no longer required on providers that lack a `droid:` / `opencode:` subsection. `display_name` still required everywhere.

### 6. Behavior for existing source-file `model:` fields

Current repo state (grepped at spec time): **11 agents** already carry `model: sonnet` in their frontmatter (`agents/*-reviewer.md` — the 11 reviewer agents). **0 skills** currently set a frontmatter `model:` field (the match in `skills/forge/SKILL.md` is in the body, not the frontmatter).

**All 11 reviewer agents' `model:` fields get overwritten** at deploy time (force semantics per Q1). This is by design — the user explicitly chose force over respect-existing. The source files themselves are not modified; only the deployed copies. This gives implementers a concrete test surface: the Claude-target integration test can assert that a reviewer agent's deployed frontmatter has `model: claude-opus-4-7` (or whatever is configured), not the source's `model: sonnet`.

If a future need for per-agent pinning emerges, the escape hatch is: do not configure the feature (leave `providers.anthropic.claude.default_model` unset and no per-target `model:`), in which case the source file's `model:` field is preserved unchanged.

## File impact summary

Modified:
- `src/promptdeploy/config.py` — `TargetConfig.model` field; models.yaml global default lookup; `remap_targets_to_root()` must propagate `model=` through to the remapped `TargetConfig` construction
- `src/promptdeploy/frontmatter.py` — `transform_for_target(..., inject=)` parameter
- `src/promptdeploy/targets/claude.py` — injection wiring in agent/skill deploy paths
- `src/promptdeploy/targets/__init__.py` — factory passes effective model to `ClaudeTarget`
- `src/promptdeploy/validate.py` — relaxed required-fields rule for claude-only providers; new error on per-target `model:` on non-claude target; new warning on unknown model string
- `models.yaml` — new `anthropic:` provider with `claude.default_model: claude-opus-4-7`
- `tests/test_frontmatter.py` — injection no-op (None, {}), key-order preservation, overwrite-existing behavior, `None`-value skip semantics
- `tests/test_claude_target.py` — agent receives injected `model:`, skill's `SKILL.md` receives injected `model:`, command does NOT, `ClaudeTarget(id, path)` still works with no model arg
- `tests/test_config.py` (or the current config-loading test file) — per-target `model:` parsed, models.yaml global default parsed, `remap_targets_to_root()` round-trips `model=`, non-claude-target `model:` produces an error
- `tests/test_models_deploy.py` / `tests/test_validate.py` — relaxed required-fields rule accepts a claude-only provider; droid/opencode still skip an anthropic-only provider via `except:`
- `PROMPTDEPLOY.md` — document the two new config sites
- `CLAUDE.md` — update the "models.yaml -- Droid and OpenCode only; Claude skipped" note to reflect that Claude now reads a narrow slice (`providers.anthropic.claude.default_model`)

Not modified:
- `src/promptdeploy/source.py`
- `src/promptdeploy/targets/droid.py`, `opencode.py`, `remote.py`, `base.py`
- `src/promptdeploy/filters.py`, `manifest.py`, `deploy.py`
- `deploy.yaml` — no per-target overrides at rollout; users add them as needed

## Verification

- `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing` — 100% coverage gate must hold.
- `nix flake check` — full CI parity.
- Manual: `PYTHONPATH=src python -m promptdeploy deploy --target claude-personal --target-root /tmp/preview --dry-run` then inspect a deployed agent's frontmatter for the injected `model:` field.
- Manual: `PYTHONPATH=src python -m promptdeploy validate` — should exit 0 with the new `anthropic:` provider present.

## Risk

Low-to-medium:
- **Coverage gate** — any new code paths in `config.py` / `claude.py` / `frontmatter.py` need test coverage. This is the usual rigor, not a novel risk.
- **Frontmatter ordering** — YAML dict insertion order matters for readability. Tested explicitly.
- **`remap_targets_to_root()`** — easy to forget to propagate a new `TargetConfig` field. Mitigated by an explicit test that round-trips through `remap`.
- **`except:` list maintenance** — `anthropic: { except: [droid, opencode, opencode-vulcan] }` must be updated if new non-claude targets are added. Acceptable: the failure mode is Droid/OpenCode trying to serialize a provider with no applicable subsection, which will surface fast in tests or deploy.
- **Force-overwrite of authored `model:` fields** — deliberate per Q1, not a bug. Called out in docs.

## Rollback

Revert the modified files. The new `anthropic:` provider in `models.yaml` is purely additive; removing it silently reverts to "no injection." The only destructive consequence of reverting would be loss of the documented per-target overrides, if any were added — trivial to restore from git.

## Out-of-scope (follow-ups the user may want later)

- Adding per-agent / per-skill frontmatter overrides that beat the per-target override (e.g., "this one specialist agent always uses sonnet"). Today's force-overwrite is all-or-nothing per target.
- Other injected frontmatter fields (e.g., `tools:` gating, `disable-model-invocation:` for skills).
- Reading Claude model IDs from a live Anthropic API listing instead of hand-maintaining `providers.anthropic.models:`.
- Warning on `model: inherit` authored in a source file that will be overwritten — low value given the force semantics are documented.
