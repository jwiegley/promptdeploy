# Split `claude-git-ai` into Local and Remote Targets

**Date:** 2026-04-22
**Status:** Approved (pending implementation)

## Background

`promptdeploy`'s `deploy.yaml` defines the set of AI-coding-tool environments that source content is deployed to. Until now there has been a single Claude target named `claude-git-ai`, configured as a local path (`~/.config/claude/git-ai`). A new remote Claude environment on the SSH host `git-ai` now needs to coexist alongside the original local one.

## Goal

Replace the single `claude-git-ai` target with two explicitly-named targets so both environments can receive deployments independently:

- `claude-git-ai-local` — the original local target (unchanged config, renamed key only).
- `claude-git-ai-remote` — a new remote target on SSH host `git-ai`.

Reflect the split in tests (`tests/test_filters.py`) and docs (`PROMPTDEPLOY.md`). `README.md` needs no changes because it does not reference `claude-git-ai` by name.

## Non-goals

- No changes to deploy pipeline code (`src/promptdeploy/`). The existing `host:`-based remote-target machinery already supports this configuration.
- No changes to hook tests that reference the unrelated `git-ai` checkpoint hook (`tests/test_hooks_deploy.py`, `tests/test_droid_target.py`, `tests/test_opencode_target.py`). The hook named `git-ai` is a distinct concept from the target formerly named `claude-git-ai`.
- No changes to content filtering semantics. Existing `only`/`except` frontmatter in agents/commands/skills/MCP/hooks is unaffected — no source item currently uses `claude-git-ai` as a filter value (verified by grep; all matches in source content are for the hook name, not the target).

## Design

### 1. `deploy.yaml`

Replace the existing `claude-git-ai` entry with two entries:

```yaml
claude-git-ai-local:
  type: claude
  path: ~/.config/claude/git-ai
  labels: [claude, personal, local]

claude-git-ai-remote:
  type: claude
  path: ~/.claude
  host: git-ai
  labels: [claude, git-ai, remote]
```

- The local entry replicates HEAD's `claude-git-ai` configuration byte-for-byte. Only the YAML key changes.
- The remote entry is the user's working-tree change, with the key renamed from `claude-git-ai` to `claude-git-ai-remote`.

The `labels` field auto-generates groups in `config.py`, so:
- The `claude` group continues to include all Claude targets (now with both `-local` and `-remote` members).
- The `personal` group grows to include `claude-git-ai-local` (as before).
- A new `git-ai` group gets created (containing only `claude-git-ai-remote`).
- The `remote` group gains `claude-git-ai-remote`; the `local` group retains `claude-git-ai-local`.

### 2. Tests — `tests/test_filters.py`

Three edits, all in the top-level fixtures:

```python
ALL_TARGETS = [
    "claude-personal",
    "claude-positron",
    "claude-git-ai-local",      # was "claude-git-ai"
    "claude-git-ai-remote",     # new
    "droid",
    "opencode",
]
CLAUDE_TARGETS = [
    "claude-personal",
    "claude-positron",
    "claude-git-ai-local",      # was "claude-git-ai"
    "claude-git-ai-remote",     # new
]
```

In the `config` fixture, replace the `("claude-git-ai", "claude")` tuple with `("claude-git-ai-local", "claude")` and `("claude-git-ai-remote", "claude")`.

No other changes. Tests iterate over these lists and do not depend on exact cardinality.

### 3. Docs — `PROMPTDEPLOY.md`

Target table: replace the `claude-git-ai` row with two rows:

| Target | Type | Path |
|--------|------|------|
| claude-git-ai-local | Claude Code | ~/.config/claude/git-ai |
| claude-git-ai-remote | Claude Code | git-ai:~/.claude |

Update the parenthetical on line 51 from `(personal, positron, git-ai)` to `(personal, positron, git-ai-local, git-ai-remote)`.

### 4. `README.md`

No changes. Grepping `README.md` for `claude-git-ai` returns no matches; the README's example YAML uses `claude-personal` only.

## Verification

- `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing` — must pass with the 100% coverage gate enforced in `pyproject.toml`.
- `nix flake check` — must pass (runs `ruff format --check`, `ruff check`, `mypy`, `pytest` with coverage gate, and `nix build`).
- Sanity check: `PYTHONPATH=src python -m promptdeploy validate` and `PYTHONPATH=src python -m promptdeploy list --target claude-git-ai-local --target claude-git-ai-remote` behave correctly.

## Risk

Very low. The change is purely configuration and documentation:
- No code paths change. Existing `host:`-keyed remote dispatch handles the new remote target.
- The only source-level name collision was in `tests/test_filters.py`, which uses a synthetic in-memory `Config`, not the real `deploy.yaml`.
- A grep of the full repo for `claude-git-ai` confirms no content item (agent/command/skill/MCP/hook) currently filters on `claude-git-ai` as an `only`/`except` value.

## Rollback

Revert the three affected files (`deploy.yaml`, `tests/test_filters.py`, `PROMPTDEPLOY.md`) to their prior state.
