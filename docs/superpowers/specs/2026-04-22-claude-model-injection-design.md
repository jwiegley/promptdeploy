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
- `api_key` / `base_url` are **omitted** (Claude Code authenticates through its own `claude login` flow; `promptdeploy` never reads Anthropic credentials from `models.yaml`).
- `except: [droid, opencode, opencode-vulcan]` prevents the Droid and OpenCode targets from trying to consume a provider that has no `droid:` or `opencode:` subsection. This mirrors the existing `except:` pattern used by `litellm:` for `opencode-vulcan`.
- `models:` subsection is informational — used only for soft validation (see §5). Claude target continues to `should_skip` item_type `models` and no provider data is written to any Claude target file.
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
- Backward-compatible default: existing callers that pass only `(content, target_id)` continue to work unchanged.

**`src/promptdeploy/targets/claude.py`:**
- `ClaudeTarget.__init__` gains a `model: Optional[str] = None` parameter.
- Compute once: `self._injected = {"model": self._model} if self._model else None`.
- `deploy_agent` and `deploy_skill` pass `inject=self._injected` to `transform_for_target`.
- `deploy_command` continues to pass no `inject` argument (commands are excluded per scope).
- Skill handling: the existing post-copytree `SKILL.md` rewrite (line 74–76) gets the same `inject` argument.

**`src/promptdeploy/targets/__init__.py` (factory `create_target`):**
- Propagate the resolved effective model into `ClaudeTarget(...)`. The factory looks up `target.model` (per-target override) and the global default from wherever the caller provides it, and passes the resolved value into the constructor.

**No changes to:**
- `source.py` — existing `item_type` distinction between `"agent"`, `"skill"`, `"command"` is enough; gating happens in `claude.py`.
- `targets/droid.py`, `targets/opencode.py` — they skip the new `anthropic:` provider via the `except:` list in `models.yaml`.
- `targets/remote.py` — remote wrapper delegates to the inner `ClaudeTarget`, which already has the injection; no special handling needed.
- `filters.py` — orthogonal to this feature.

### 5. Validation

Added to `promptdeploy validate` (and reused as a load-time check in `load_config`):

- **Hard error**: per-target `model:` specified on a non-`claude` target.
- **Soft warning** (stderr, not exit-code failure): `effective_model` resolves to a string that does not appear as a key in `providers.anthropic.models:`. Soft because Claude Code also accepts aliases (`opus`, `sonnet`, `haiku`), date-stamped IDs (`claude-opus-4-7-20260416`), and the `[1m]` suffix; maintaining an exhaustive list of valid strings isn't worth the churn. The warning tells users the value isn't in their own documented list and may be a typo.
- **No warning** when `providers.anthropic` is absent entirely — the feature is opt-in.

### 6. Behavior for existing source-file `model:` fields

Current repo state: a `rg "^model:" agents/ skills/` run at plan time will tell us how many source files already carry a `model:` frontmatter field. **All of them get overwritten** at deploy time (force semantics per Q1). This is by design — the user explicitly chose force over respect-existing. The source files themselves are not modified; only the deployed copies.

If a future need for per-agent pinning emerges, the escape hatch is: do not configure the feature (leave `providers.anthropic.claude.default_model` unset and no per-target `model:`), in which case the source file's `model:` field is preserved unchanged.

## File impact summary

Modified:
- `src/promptdeploy/config.py` — `TargetConfig.model` field; models.yaml global default lookup
- `src/promptdeploy/frontmatter.py` — `transform_for_target(..., inject=)` parameter
- `src/promptdeploy/targets/claude.py` — injection wiring in agent/skill deploy paths
- `src/promptdeploy/targets/__init__.py` — factory passes effective model to `ClaudeTarget`
- `models.yaml` — new `anthropic:` provider with `claude.default_model: claude-opus-4-7`
- `tests/test_frontmatter.py` — injection, key-order preservation, override behavior
- `tests/test_claude_target.py` — agent/skill receive `model:`, command does not
- `tests/test_config.py` (or `tests/test_config_loading.py` if different) — per-target override parsed, models.yaml global default parsed, non-claude-target warning
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
