---
name: anvil
description: Drive the user's live Emacs session through the anvil MCP servers — `anvil` (Elisp eval suite) and `anvil-tools` (typed file/org/git/data tools). This skill should be used when the user says "use the anvil skill", and on any host where these MCP tools are available whenever a task touches org files, files open in Emacs, the running Emacs configuration, bulk file edits, or needs structured git/agenda/project queries. Covers tool selection, progressive-disclosure reads, batched token-efficient edits, async eval for heavy work, and live-session safety.
---

# Anvil — the live-Emacs workbench

Anvil (anvil.el) bridges MCP to the user's *running* Emacs session over
`emacsclient`. Two servers expose it:

- **`anvil`** — the eval suite: `emacs-eval`, `emacs-eval-async`,
  `emacs-eval-result`, `emacs-eval-jobs`, `nelisp-eval`, `nelisp-eval-reset`.
  The universal escape hatch: anything Emacs can do, this can do.
- **`anvil-tools`** — 43 typed tools for files, org-mode, git, JSON/state,
  workers, and telemetry. Prefer these over raw eval: they are
  schema-checked, cheaper in tokens, and safe on large files.

See `references/tools.md` for the full catalog with parameters. Client
prefixes vary (Claude Code: `mcp__anvil__emacs-eval`,
`mcp__anvil-tools__file-batch`); this document uses bare names.

## Availability gate

Apply this skill only where the tools actually exist. Check the available
tool list for `emacs-eval` and `file-batch` (one from each server). If they
are absent, or a probe call errors with a connection failure, state that
anvil is unavailable on this host and fall back to standard tools — never
treat anvil as a hard dependency. A cheap liveness probe:
`emacs-eval` with `(emacs-version)`.

If `emacs-eval` works but typed tools error "No active MCP server", the
Emacs session needs `(anvil-enable)` + `(anvil-server-start)` run — report
this to the user rather than working around it.

## Core rules

1. **Typed tool first, eval second.** Reach for `emacs-eval` only when no
   typed tool covers the operation (introspecting variables, driving modes,
   invoking user functions, buffer manipulation).
2. **Never read a whole file to answer a structural question.** Use the
   layered read surface (below).
3. **Batch edits.** N separate edit calls waste N-1 round trips; use
   `file-batch` / `file-batch-across`.
4. **Respect the live session.** The user is working in this Emacs. Follow
   the safety rules at the end — they override everything else here.

## Reading efficiently (progressive disclosure)

Work down the layers; stop as soon as the question is answered:

1. `file-outline` — structural outline without the body (headings, defuns,
   sections; format inferred). Answers "what is in this file / where is X".
2. `file-read` with `start-line`/`end-line` pagination — just the region
   that matters. For org files prefer `org-read-headline` / `org-read-by-id`
   (subtree only) over reading the file.
3. `file-read-delta` for files read earlier in the session — a byte-identical
   re-read returns just the unchanged-hash marker instead of full content.
   Use it when re-checking a file after edits elsewhere.

For git state, use the structured queries (`git-status`, `git-log`,
`git-diff-names`, `git-diff-stats`, `git-repo-root`, `git-worktree-list`)
instead of shelling out and parsing porcelain output.

## Editing efficiently

- Single literal change: `file-replace-string`. Regexp change:
  `file-replace-regexp` (Emacs regexp syntax — `\\(...\\)` groups, `\\1`
  in replacements — not PCRE).
- New file: `file-create` (one call, errors if the file exists unless
  overwrite is set). Append: `file-append`. Positional: `file-insert-at-line`,
  `file-delete-lines` (1-indexed).
- **Multiple edits to one file: `file-batch`** — the whole edit plan in one
  call. **Multiple files: `file-batch-across`.** These are the
  token-efficient workhorses; default to them for any multi-step edit.
- Imports/headers: `file-ensure-import` (idempotent, no-op when present).
- JSON: `json-object-add` for bulk key additions preserving formatting;
  `data-get-path` / `data-set-path` / `data-delete-path` / `data-list-keys`
  for dotted-path access. The mutating data tools have a preview/apply
  contract — preview first, then apply, for anything destructive.
- All file tools operate on disk via temp buffers: no live-buffer side
  effects, no auto-revert disruption, safe on files over 1.2 MB. The
  flip side: they do NOT see unsaved buffer edits (see safety rules).

Verify edits from the return plist (e.g. `(:replaced 3 ...)`) — a count of
0 means the pattern missed; re-read the region rather than re-firing blind.

## Org-mode work

Org files may be gated by an allowlist — check `org-get-allowed-files`
when a call errors on file access.

- Discover: `org-read-outline` (hierarchy as JSON), then `org-read-headline`
  (subtree by path) or `org-read-by-id` (stable across refiles; prefer IDs
  once known).
- Mutate: `org-update-todo-state`, `org-add-todo`, `org-rename-headline`,
  `org-edit-body` (partial string replacement within a headline's body).
  These preserve structure, properties, and tags, and mint org IDs —
  always prefer them over textual edits to org files.
- Capture: `org-capture-string` drives the user's own capture templates.
- Query: `org-agenda-view` renders a real agenda buffer (same engine the
  user sees); `org-habit-summary` for habit state;
  `org-get-todo-config` / `org-get-tag-config` before constructing TODO
  states or tags by hand.

## Eval — the escape hatch

- `emacs-eval` for anything under ~30 s: query variables, call functions,
  inspect buffers, drive packages. Return values print as Elisp data —
  shape results with `format`/`prin1-to-string` or return plists for easy
  parsing.
- Anything potentially slow (byte-compile, package ops, network, large
  searches): `emacs-eval-async` → poll `emacs-eval-result` with the job ID;
  `emacs-eval-jobs` to list/debug. Do not run slow forms through the
  synchronous tool — it blocks the user's editor.
- `nelisp-eval` is a stateful pure-Elisp scratch REPL isolated from the
  session's globals (reset with `nelisp-eval-reset`) — use it for Elisp
  experiments that should not touch the user's state.
- Worker pool: `anvil-worker-probe` shows per-lane worker health;
  `anvil-worker-reset-pool` recovers a stuck pool. Probe before assuming
  async infrastructure is broken.
- `metrics-token-report` reports per-tool payload telemetry — use it when
  asked to audit or tune MCP token usage.

## Live-session safety (overrides all of the above)

- The session belongs to the user. Never kill buffers you did not create,
  never `save-buffers-kill-emacs`, never toggle global modes or mutate
  user configuration unless that is the task.
- Before disk-editing a file the user may have open: check
  `(let ((b (find-buffer-visiting FILE))) (and b (buffer-modified-p b)))`.
  If it is modified, do not edit the file on disk — the user has unsaved
  work; either operate on the buffer via eval (edit, then leave saving to
  the user) or ask.
- Keep synchronous eval short; route heavy work through async or workers.
- Prefer read-only forms when only reading: don't "query" with mutating
  functions.
- Preview before apply on the `data-*` mutating tools; state what changed
  after applying.
- Results containing user data (buffers, agendas, journals) may be
  personal — quote only what the task needs.

## When NOT to use anvil

- The host has no anvil tools (gate above) — standard tools, no commentary.
- Plain project file edits when nothing is open in Emacs, no org
  structure is involved, and one small edit suffices — native Edit/Write
  tools are fine; anvil adds value with scale, structure, or live state.
- Long-lived shell processes (servers, watchers) — that is not what the
  eval bridge is for.
