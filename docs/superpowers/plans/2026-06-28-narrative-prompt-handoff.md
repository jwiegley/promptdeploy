# Narrative Prompt Handoff

Updated: 2026-06-28 PST8PDT

## Objective

Create a new `narrative` prompt/command for promptdeploy. The prompt should use
a journal file, the relevant git history, the current working tree, and any
planning/design/handoff documents from a feature's development to write a
human-oriented design narrative: the story of the work, the challenges
encountered, how they were overcome, principles discovered, and lessons learned.

The prompt must also incorporate an English prose standard based on
`~/work/positron/it-plan.pdf`, using that PDF for tone and language only, not
content.

The request came from:

`/Users/johnw/.codex/attachments/fa744798-b82f-4552-a770-002c913f7add/pasted-text-1.txt`

## Resume Instructions

After any context compaction or fresh resume:

1. Re-read `commands/wiggum.md`.
2. Re-read this handoff document in full.
3. Inspect the current worktree with `git status --short --branch`.
4. Continue from the task list below, using the current files as authoritative.

## Evidence Read

- Read `command-wiggum` skill instructions from
  `/Users/johnw/.agents/skills/command-wiggum/SKILL.md`.
- Read the pasted objective file in full.
- Inspected repository structure and confirmed commands are stored under
  `commands/*.md` and deployed to Codex as generated skills.
- Read `commands/journal.md`, `commands/fess.md`, `commands/wiggum.md`, and
  neighboring command prompts for style.
- Confirmed the working tree was clean before this work began.
- Confirmed `~/work/positron/it-plan.pdf` exists and `pdftotext` is available.
- Sampled the PDF's table of contents, opening, Chapter 1, and body sections to
  derive tone standards: calm institutional English, practical framing,
  principles before procedure, modest headings, clear transitions, and concrete
  consequences without over-technical detail.

## Design Decisions

- Implemented the request as a standard promptdeploy command:
  `commands/narrative.md`.
- This is the correct source location because promptdeploy deploys commands as
  slash commands for Claude/OpenCode and as generated `command-*` skills for
  Codex.
- The command instructs the future agent to read the style-reference PDF when
  accessible, but also embeds a style standard so the prompt remains useful if
  the PDF is absent in another environment.
- The command tells the future agent to stop and ask for the journal path if no
  clear journal can be inferred, since the journal is central evidence rather
  than optional context.

## Task List

- [x] Read wiggum instructions and pasted objective.
- [x] Inspect repository conventions for commands and generated Codex skills.
- [x] Inspect the style-reference PDF enough to derive tone guidance.
- [x] Add `commands/narrative.md`.
- [x] Create this tasks/handoff document.
- [x] Validate the new command with promptdeploy validation and targeted tests.
- [x] Review the final diff for scope and quality.
- [x] Preserve commit and post-commit audit requirements in the handoff for any
  later commit workflow.

## Files Added By This Work

- `commands/narrative.md`
- `docs/superpowers/plans/2026-06-28-narrative-prompt-handoff.md`

## Verification Run

- `PYTHONPATH=src python -m promptdeploy validate`
  - Result: failed under the system Python because `jinja2` was not installed.
- `PYTHONPATH=src python -m pytest tests/test_source.py::TestDiscoverCommands -v`
  - Result: failed under the system Python because `pytest` was not installed.
- `nix develop -c env PYTHONPATH=src python -m promptdeploy validate`
  - Result: passed with 0 errors and 2 pre-existing warnings:
    `skills/johnw/SKILL.md` length warning, and `forge` command/skill namespace
    warning.
- `nix develop -c env PYTHONPATH=src python -m pytest tests/test_source.py::TestDiscoverCommands -v`
  - Result: passed, 3 tests.
- `nix develop -c env PYTHONPATH=src python -m promptdeploy deploy --dry-run --only-type commands --target-root "$tmpdir"`
  - Result: passed; output included `command narrative` for Claude and Codex
    targets.
- `nix develop -c env PYTHONPATH=src python -m promptdeploy deploy --quiet --only-type commands --target codex-hera --target claude-personal --target-root "$tmpdir"`
  - Result: passed; wrote temporary artifacts at:
    - `$tmpdir/codex-hera/.agents/skills/command-narrative/SKILL.md`
    - `$tmpdir/claude-personal/commands/narrative.md`
  - Inspected the generated Codex `SKILL.md` and confirmed it contains the
    command wrapper and the narrative prompt body.

## Completion Notes

- The working tree changes are limited to the new command and this handoff
  document.
- When this work is committed, run the requested post-commit fess audit and
  check for partner observations as described by `command-wiggum`.

## Open Questions

- No user clarification is currently needed.
