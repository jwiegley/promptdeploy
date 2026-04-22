# Split `claude-git-ai` into Local and Remote Targets — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `claude-git-ai` target in `deploy.yaml` with two explicit targets (`claude-git-ai-local`, `claude-git-ai-remote`) so both the original local and the new remote `git-ai` Claude environments coexist. Update tests and docs to match.

**Architecture:** Pure configuration and documentation change. The `host:`-based remote-target machinery in `src/promptdeploy/` already supports this split (see `src/promptdeploy/targets/__init__.py` `create_target()` and `src/promptdeploy/targets/remote.py`). No source code changes.

**Tech Stack:** Python 3.12, PyYAML, pytest, ruff, mypy, Nix flake checks.

**Spec:** `docs/superpowers/specs/2026-04-22-git-ai-split-design.md`

---

## Repo Preconditions

- Working tree has uncommitted change to `deploy.yaml` (the remote-target rename).
- HEAD commit is `43063f9` (spec refinement) on branch `main`.
- The following three files will be touched by this plan. No other files should change:
  - `deploy.yaml`
  - `tests/test_filters.py`
  - `PROMPTDEPLOY.md`

---

## File Structure

No files are created or deleted. Three existing files are modified:

- **`deploy.yaml`** (responsibility: target registry) — the `claude-git-ai` block becomes two blocks.
- **`tests/test_filters.py`** (responsibility: unit tests for `promptdeploy.filters`) — the top-of-file constants and the `config` fixture are updated so the synthetic test config reflects the new target-naming convention. The tests exercise filter logic against a synthetic `Config`, not the real `deploy.yaml`; adding two targets where one existed exercises the same code paths.
- **`PROMPTDEPLOY.md`** (responsibility: user-facing docs) — the target table gains a row; the stale parenthetical is dropped.

`README.md` is **not** modified (verified: no `claude-git-ai` references).

---

## Chunk 1: Split target, update tests, update docs, verify

### Task 1: Update `tests/test_filters.py` fixtures

**Files:**
- Modify: `tests/test_filters.py:16-23` (module constants)
- Modify: `tests/test_filters.py:26-39` (`config` fixture)

**Why first:** The fixture update encodes what we expect `deploy.yaml` to look like. Completing it first means the test suite still passes on the new fixture shape while `deploy.yaml` still has the old name — proving the tests use synthetic config and not the real file. We catch any hidden coupling immediately.

- [ ] **Step 1: Read current state**

Run: use the Read tool on `tests/test_filters.py` lines 1-40 to confirm the exact text of the module constants and fixture before editing.

- [ ] **Step 2: Update `ALL_TARGETS` and `CLAUDE_TARGETS`**

Replace lines 16-23:

```python
ALL_TARGETS = [
    "claude-personal",
    "claude-positron",
    "claude-git-ai-local",
    "claude-git-ai-remote",
    "droid",
    "opencode",
]
CLAUDE_TARGETS = [
    "claude-personal",
    "claude-positron",
    "claude-git-ai-local",
    "claude-git-ai-remote",
]
```

- [ ] **Step 3: Update the `config` fixture target tuples**

Replace the `("claude-git-ai", "claude"),` tuple on line 33 with two tuples:

```python
            ("claude-git-ai-local", "claude"),
            ("claude-git-ai-remote", "claude"),
```

The surrounding context (lines 28-37) must end up looking like:

```python
    targets = {
        tid: TargetConfig(id=tid, type=t, path=Path(f"/tmp/{tid}"))
        for tid, t in [
            ("claude-personal", "claude"),
            ("claude-positron", "claude"),
            ("claude-git-ai-local", "claude"),
            ("claude-git-ai-remote", "claude"),
            ("droid", "droid"),
            ("opencode", "opencode"),
        ]
    }
```

- [ ] **Step 4: Run `test_filters.py` in isolation**

Run: `PYTHONPATH=src python -m pytest tests/test_filters.py -v`
Expected: all tests pass. The tests iterate `ALL_TARGETS` and `CLAUDE_TARGETS` in for-loops and use `expand_group("claude", config) == CLAUDE_TARGETS` for ordered comparison; none of them hardcode cardinality or the string `claude-git-ai`.

- [ ] **Step 5: Run the full test suite with coverage**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing`
Expected: all tests pass with coverage at 100% (the `[tool.coverage.report]` `fail_under = 100` gate in `pyproject.toml` will fail the run otherwise). Stop and investigate if either assertion fails.

- [ ] **Step 6: Do NOT commit yet**

Keep the test change staged (or unstaged) — it needs to land in the same commit as the `deploy.yaml` change so the repo is never in a state where they are out of sync.

---

### Task 2: Update `deploy.yaml`

**Files:**
- Modify: `deploy.yaml:14-18` (the `claude-git-ai:` block)

- [ ] **Step 1: Read current state**

Run: use the Read tool on `deploy.yaml` lines 1-20 to confirm exact spacing and that the working tree still has the remote-flavored `claude-git-ai` the user edited in.

- [ ] **Step 2: Replace the single block with two blocks**

Replace lines 14-18 (the entire `claude-git-ai:` block including its labels line) with:

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

Preserve the two-space indent that the rest of `deploy.yaml` uses, and keep one blank line separating the two blocks (matching the surrounding style).

- [ ] **Step 3: Validate the YAML parses and the config is accepted**

Run: `PYTHONPATH=src python -m promptdeploy validate`
Expected: exit 0, no errors. This loads `deploy.yaml` via `src/promptdeploy/config.py` and walks all source items to check `only`/`except` frontmatter against the known target set. Any source item still filtering on the old bare `claude-git-ai` would fail here — the spec verified none do, but `validate` is the authoritative check.

- [ ] **Step 4: Smoke-test target listing**

Run: `PYTHONPATH=src python -m promptdeploy list --target claude-git-ai-local`
Expected: exit 0, shows items managed under the local git-ai target (or an empty list if never deployed). No traceback.

Run: `PYTHONPATH=src python -m promptdeploy list --target claude-git-ai-remote`
Expected: exit 0 as well. Note: `list` reads the local manifest via the `RemoteTarget.prepare()` rsync pull, so this will attempt an SSH connection to host `git-ai`. If the host is unreachable in the current environment, skip this step and note it; the `validate` check in Step 3 already exercises the config parsing.

- [ ] **Step 5: Do NOT commit yet**

The docs update is part of the same logical change. Commit all three files together at the end.

---

### Task 3: Update `PROMPTDEPLOY.md`

**Files:**
- Modify: `PROMPTDEPLOY.md:11` (target table row)
- Modify: `PROMPTDEPLOY.md:51` (stale parenthetical)

- [ ] **Step 1: Replace the `claude-git-ai` table row**

Replace line 11:

```
| claude-git-ai | Claude Code | ~/.config/claude/git-ai |
```

with two rows:

```
| claude-git-ai-local | Claude Code | ~/.config/claude/git-ai |
| claude-git-ai-remote | Claude Code | git-ai:~/.claude |
```

The `git-ai:~/.claude` notation in the Path column signals an SSH target (host:path). This matches the informal convention used in READMEs for rsync-style remote paths.

- [ ] **Step 2: Drop the stale parenthetical**

Replace line 51:

```
- `only: [claude]` -- Deploy only to the `claude` group (personal, positron, git-ai).
```

with:

```
- `only: [claude]` -- Deploy only to the `claude` group.
```

Rationale: the `(personal, positron, git-ai)` enumeration was already inaccurate before this change — the `claude` group also includes `claude-vulcan`, `claude-vps`, and `claude-andoria` via labels. Dropping the enumeration avoids having to chase it when new Claude targets appear.

- [ ] **Step 3: Visually verify the table renders**

Run: use the Read tool on `PROMPTDEPLOY.md` lines 5-15 and confirm the table shape is intact (column alignment does not need to be perfect, but the pipe-separator count per row must be 4).

---

### Task 4: Full verification

**Files:**
- None (running checks only)

- [ ] **Step 1: Run the full `nix flake check`**

Run: `nix flake check`
Expected: all five checks pass (`ruff format --check`, `ruff check`, `mypy`, `pytest` with coverage gate, `nix build`). This is the authoritative CI equivalent.

If `nix flake check` is unavailable in the execution environment, fall back to the individual commands:

```bash
ruff format --check src/ tests/
ruff check src/ tests/
PYTHONPATH=src mypy src/ tests/
PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing
```

All four must pass.

- [ ] **Step 2: Re-run `promptdeploy validate`**

Run: `PYTHONPATH=src python -m promptdeploy validate`
Expected: exit 0, no errors. Re-run after all edits to confirm nothing regressed between tasks.

---

### Task 5: Commit

**Files:**
- The three modified files + the implementation plan doc itself.

- [ ] **Step 1: Review the working tree**

Run: `git status` and `git diff`
Expected: three modified files (`deploy.yaml`, `tests/test_filters.py`, `PROMPTDEPLOY.md`) and one new plan file under `docs/superpowers/plans/`. Pre-existing untracked entries such as `.claude/` may also appear in `git status`; those are unrelated and should be left alone.

- [ ] **Step 2: Stage exactly the intended files**

Run:

```bash
git add deploy.yaml tests/test_filters.py PROMPTDEPLOY.md docs/superpowers/plans/2026-04-22-git-ai-split.md
```

Do not `git add -A` — the working tree may contain untracked `.claude/` or similar dirs that are not part of this change.

- [ ] **Step 3: Create the commit**

Run:

```bash
git commit -m "$(cat <<'EOF'
Split claude-git-ai into -local and -remote targets

Replace the single claude-git-ai target with two explicit targets so
the existing local Claude environment (~/.config/claude/git-ai) and a
new remote Claude environment on SSH host git-ai can receive deploys
independently. Update the test_filters fixture and PROMPTDEPLOY.md
target table to match; drop the already-stale parenthetical
enumeration of claude group members.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds. Lefthook pre-commit hooks will run `ruff-format`, `ruff-lint`, `mypy`, `pytest`, and `nix-check` in parallel on the staged files. All must pass. If any fails, fix the underlying issue and retry with a NEW commit (do not `--amend`).

- [ ] **Step 4: Confirm clean state**

Run: `git status`
Expected: working tree clean (modulo any pre-existing untracked dirs like `.claude/`).

Run: `git log --oneline -3`
Expected: the new commit sits at the top of `main`, above `43063f9 Refine git-ai split spec after review`.

---

## Rollback

If any step fails and cannot be recovered, run `git checkout -- deploy.yaml tests/test_filters.py PROMPTDEPLOY.md` to restore the pre-plan state. The plan doc under `docs/superpowers/plans/` and the spec docs under `docs/superpowers/specs/` are already committed and do not need rollback.

---

## Out-of-Scope

- Actually deploying to the new remote target. The plan only wires it into the config; whether and when to run `promptdeploy deploy --target claude-git-ai-remote` is a follow-up decision for the user.
- Migrating existing source items to filter on `git-ai-local` or `git-ai-remote` via `only`/`except`. No items currently filter on the old `claude-git-ai` name; if the user later wants to restrict content to one variant, that is a separate change.
- Adding new groups or labels beyond the ones already implied by the two `labels:` lists.
