# MCP Launcher Bridge — Design

**Date:** 2026-06-12
**Status:** Approved (pending implementation plan)
**Owner:** John Wiegley
**Supersedes:** the June 2026 audit's "Do now" item 1 ("MCP deployment to Claude targets is ineffective")

## Problem

`ClaudeTarget.deploy_mcp_server` merges MCP server definitions into each claude
target's `settings.json` under a top-level `mcpServers` key. Claude Code has
never read that key: its MCP read surfaces are `.claude.json` (user and local
scopes), project `.mcp.json`, plugin-provided servers, claude.ai connectors,
enterprise `managed-mcp.json`, and the per-invocation `--mcp-config` flag.
(Confirmed against Claude Code 2.1.175 docs and changelog; see Evidence.)

The servers work on our hosts today only because duplicate entries were
hand-added to each profile's `.claude.json`, and those duplicates have drifted
from the repo source (live `context7` is HTTP with an API-key header; the
repo's `mcp/context7.yaml` is stdio `npx`). The single-source-of-truth premise
is silently defeated, and deploy-time-expanded plaintext API keys sit in
`settings.json` files that are rsynced to remote hosts.

## Decision

Keep `settings.json` as the canonical deployed store for MCP servers — same
file, same top-level `mcpServers` shape promptdeploy writes today (the only
content change is the secrets passthrough in Design 2) — and make it an
effective read surface by passing it to Claude Code at launch via
`--mcp-config`. Do not write to `.claude.json`, ever.

This was chosen over two alternatives:

- **Surgical `.claude.json` merge** — rejected: live sessions rewrite the whole
  file from in-memory state with no locking, so external merges can be silently
  reverted (lost-update race); the file is app-owned mixed state (OAuth session,
  caches, per-project state, 300 KB–1 MB here); remote targets would need to
  rsync that state file.
- **Driving `claude mcp add-json`** — rejected: same read-modify-write race on
  `.claude.json` underneath, requires the real binary path on every host (the
  `ai` wrapper unconditionally overrides `CLAUDE_CONFIG_DIR`), most moving
  parts.

Both remain documented fallbacks if a non-wrapper entry point ever appears.

## Evidence (all verified 2026-06-12 on Claude Code 2.1.175)

1. **settings.json `mcpServers` is not read.** The positron profile defines
   `context-hub` only in `settings.json`; it is absent from a live positron
   session. The docs' scope table maps MCP servers to `~/.claude.json` /
   `.mcp.json` only, and the changelog (0.2.21 → 2.1.175) never added or
   removed settings.json support. GitHub issues #24477 and #37245 confirm the
   key is silently ignored.
2. **`--mcp-config` accepts a `settings.json` file as-is.** A headless probe
   (`claude --strict-mcp-config --mcp-config=<positron settings.json> -p "Say ok"
   --output-format json --max-turns 1`) loaded exactly the five servers from
   `settings.json`, extra keys tolerated; `context-hub` connected with tools
   live within the startup window.
3. **`${VAR}` runtime expansion is active on the `--mcp-config` surface.**
   A probe server with `env: {PROBE: "${UNSET_VAR:-fallback-was-expanded}"}`
   received the literal string `fallback-was-expanded` in its environment.
   Two caveats: an unset variable without a default expands to empty rather
   than failing the parse (lenient on this surface), and the probe covered
   `env` values only — expansion inside HTTP `headers` is assumed from the
   `.mcp.json` documentation but must be probed before the context7 cutover
   (see Migration).
4. **`CLAUDE_CONFIG_DIR` is honored** for `.claude.json` location and all
   config; the `ai` wrapper (`~/src/scripts/ai`) unconditionally exports it per
   context, which is why every Claude launch on every host already flows
   through a single controllable point.

## Design

### 1. The bridge (in `~/src/scripts/ai`, not this repo)

The `ai` wrapper appends `--mcp-config="$CLAUDE_CONFIG_DIR/settings.json"` to
every `claude` invocation, guarded by file existence. No `--strict-mcp-config`,
so plugins, claude.ai connectors, and project `.mcp.json` continue to load.
The wrapper is in the synced scripts repo, so it propagates to all hosts
(vulcan, hera, clio, vps, andoria-08, and the git-ai host) the same way it
always has. All Claude launches that matter go through the wrapper (confirmed
by owner); the migration checklist verifies wrapper coverage on every host
named in a `deploy.yaml` `host:` field.

### 2. Secrets: `${VAR}` passthrough (promptdeploy change)

`ClaudeTarget` stops deploy-time expansion of MCP `env` values and passes
`${VAR}` references through verbatim; `headers` values already pass through
untouched today, so the end state is uniform verbatim passthrough for both.
Runtime expansion does the work (Evidence 3). This removes plaintext API keys
from every deployed `settings.json`, including remote ones. The deliberate
deploy-time expansion introduced by commit 5731599 was correct for its era
(the settings.json surface was dead, so nothing expanded anything); the
bridge changes that.

Consequences:

- Launch environments must export the referenced variables. Interactive shells
  already do; the wrapper may additionally source the standard env file as
  belt-and-braces.
- Because unset variables expand to empty on this surface (Evidence 3 caveat),
  `promptdeploy validate` gains a warning when an `mcp/*.yaml` references a
  variable not listed in `.env.example`. The scan covers `${VAR}` references
  in both `env` and `headers` values — the flagship migrated server (context7)
  keeps its key in `headers`, so an env-only check would miss exactly that
  variable.

### 3. One-time migration (manual, sessions closed)

- Remove the promptdeploy-managed server names from each profile's
  `.claude.json` top-level `mcpServers` (all hosts). They are duplicates once
  the bridge lands, and removing them eliminates precedence ambiguity between
  user scope and `--mcp-config`.
- **Headers-expansion probe first:** rerun the Evidence 3 probe with the
  `${VAR}` reference inside an HTTP server's `headers`. If headers do not
  expand on this surface, keep deploy-time expansion for `headers` only
  (`env` still passes through) and record the asymmetry in `mcp/schema.md`.
- Adopt the live context7 HTTP form into `mcp/context7.yaml` (`url:` +
  `headers: {CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}"}`); add the variable to
  `.env.example`. The repo's stdio-npx definition is the stale one.
- Rotate the API keys that have been sitting in plaintext in mode-0644 config
  files (Anthropic, Gemini, OpenAI, Perplexity, Context7), after the
  migration confirms `${VAR}` flow end to end.

### 4. Documentation

CLAUDE.md, PROMPTDEPLOY.md, and `mcp/schema.md` document: the read-surface
mechanics (what Claude Code reads and does not read), the wrapper dependency
(the bridge is load-bearing and lives outside this repo), the `${VAR}`
passthrough policy for claude targets, and the headless probe command
(Evidence 2) as the standard way to verify a profile's MCP deployment.

## Risks and open items

- **Wrapper dependency:** a Claude launch that bypasses `ai` gets no
  promptdeploy-managed servers. Accepted by owner ("everything goes through
  the wrapper"); the fallbacks section names the alternatives if that changes.
- **Precedence during the migration window:** a server defined in both
  `.claude.json` user scope and `--mcp-config` has undetermined precedence;
  the migration removes the overlap, and the window is short.
- **Upstream drift:** `--mcp-config` accepting files with extra keys is
  observed behavior, not documented contract. The probe command in the docs
  makes regressions cheap to detect; if it ever breaks, promptdeploy can emit
  a derived `mcp.json` next to `settings.json` and the wrapper points there
  instead (one-line change on each side).
- **Lenient expansion:** empty-string expansion of unset vars can produce a
  server that starts but fails auth, which is harder to diagnose than a parse
  error. The `validate` warning (Design 2) is the mitigation.

## Implementation outline

1. promptdeploy: `${VAR}` passthrough in `ClaudeTarget` MCP deployment +
   tests; `validate` warning for variables missing from `.env.example` +
   tests; `mcp/context7.yaml` update.
2. Wrapper patch (drafted here, applied by owner to `~/src/scripts/ai`).
3. Migration checklist execution per profile/host, then key rotation.
4. Documentation updates.

Items 1 and 4 land through the normal repo flow; item 2 is a one-line diff
owned by the owner; item 3 is an operational checklist, not code.
