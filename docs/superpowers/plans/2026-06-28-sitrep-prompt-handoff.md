# Sitrep Prompt Handoff

Updated: 2026-06-28 PST8PDT

## Objective

Create `commands/sitrep.md`: a promptdeploy command that asks the currently
working AI agent to produce a situational report, or sitrep, describing the
current status of the project.

The sitrep prompt must request:

- A full accounting of accomplishments, next steps, blockers, and stumbling
  blocks.
- A restatement of the full aim the agent is presently trying to accomplish.
- Recent measurements that show progress toward the objective, such as
  performance, memory, quality, tests, throughput, counts, or other relevant
  metrics.
- The agent's estimate of how far away the goal is in time and effort.
- Upcoming work that can be done in parallel without disrupting the current
  work, so a reviewer can decide how to allocate available compute resources.

The user also asked to use the `skill-creator` skill and invoked
`$command-wiggum`.

## Resume Instructions

After any context compaction or fresh resume:

1. Re-read `commands/wiggum.md` or the `command-wiggum` skill instructions.
2. Re-read this handoff document in full.
3. Inspect `git status --short --branch`.
4. Continue from the task list below, treating the current worktree as
   authoritative.

## Evidence Read

- Read `/Users/johnw/.agents/skills/command-wiggum/SKILL.md`.
- Read `/Users/johnw/.config/codex/skills/.system/skill-creator/SKILL.md`.
- Confirmed `commands/sitrep.md` did not already exist.
- Inspected neighboring command prompts, especially `commands/report.md`,
  `commands/journal.md`, and `commands/wiggum.md`.
- Observed pre-existing uncommitted work from the prior narrative task:
  `commands/narrative.md` and
  `docs/superpowers/plans/2026-06-28-narrative-prompt-handoff.md`.

## Skill-Creator Application

- The requested artifact is a promptdeploy command, not a new filesystem-backed
  Codex skill folder, so `init_skill.py` is not applicable.
- The resulting command will still deploy to Codex as a generated
  `command-sitrep` skill through promptdeploy's existing command pipeline.
- No scripts, references, or assets are needed. The prompt is a high-freedom
  reporting workflow whose value is in clear evidence-gathering instructions and
  output requirements.

## Task List

- [x] Read wiggum and skill-creator instructions.
- [x] Inspect current worktree and confirm no existing sitrep command.
- [x] Create this handoff document.
- [x] Add `commands/sitrep.md`.
- [x] Validate the new command with promptdeploy validation and command
  discovery tests.
- [x] Preview deployment into a temporary target root and inspect the generated
  Codex skill.
- [x] Review final diff for scope and requirement coverage.
- [x] Mark goal complete only after the current evidence proves all requested
  requirements are met.

## Verification Run

- `nix develop -c env PYTHONPATH=src python -m promptdeploy validate`
  - Result: passed with 0 errors and 2 pre-existing warnings:
    `skills/johnw/SKILL.md` length warning, and `forge` command/skill namespace
    warning.
- `nix develop -c env PYTHONPATH=src python -m pytest tests/test_source.py::TestDiscoverCommands -v`
  - Result: passed, 3 tests.
- `nix develop -c env PYTHONPATH=src python -m promptdeploy deploy --quiet --only-type commands --target codex-hera --target claude-personal --target-root "$tmpdir"`
  - Result: passed; wrote temporary artifacts at:
    - `$tmpdir/codex-hera/.agents/skills/command-sitrep/SKILL.md`
    - `$tmpdir/claude-personal/commands/sitrep.md`
  - Inspected the generated Codex `SKILL.md` and confirmed it contains the
    `command-sitrep` wrapper plus the sitrep prompt body.

## Completion Notes

- `commands/sitrep.md` explicitly includes sections for aim, accomplishments,
  next steps, blockers and stumbling blocks, measurements, distance to
  completion, parallel work, and a final recommendation.
- The prompt requires evidence gathering from the current project state and
  instructs the agent not to invent missing measurements.
- When this work is committed, run the requested post-commit fess audit and
  check for partner observations as described by `command-wiggum`.

## Open Questions

- No user clarification is currently needed.
