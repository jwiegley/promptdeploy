# Ponytail Wiggum handoff

## Goal and frozen criteria

The active goal is the full ponytail-to-promptdeploy integration recorded in
`WIGGUM-PLAN.md`. Read that file in full before resuming. Do not reduce the
scope to copying one skill or configuring one client.

## Current state

- Branch: `codex/ponytail-integration`, created from `origin/main` at
  `c308988401fe9a7087aedfeba38bd59143f4cc7d` after a zero-ahead/zero-behind
  check.
- Working tree was clean before these durable-state files were added.
- Reference checkout is clean on `main` at
  `16f29800fd2681bdf24f3eb4ccffe38be3baec6b` (`package.json` version 4.8.4).
- Promptdeploy has no `.envrc`; `direnv status` reported no environment loaded.
- Anvil is available through a dedicated Emacs daemon
  (`ANVIL_EMACS_STATE_DIR=/var/tmp/anvil-emacs-501/hera/agents/83b3b516ac22f2d21bb2b86ae0ac634b`).
  Its modified-buffer list was empty, but that isolated daemon cannot certify
  the state of a separate interactive Emacs. Re-run the Anvil checkpoint after
  interruption and before each edit/commit batch.
- PAL tools are not advertised. Independent read-only subagents are used for
  inventory/design consensus instead.
- Existing source confinement allows top-level `skills/NAME` links only when
  they resolve inside the promptdeploy repository. The existing
  `skills/translate-en` link demonstrates the pattern, while its submodule also
  exposes the important Nix-source portability risk that the design must solve.

## Completed study and decision

Three bounded read-only reports were completed and independently reconciled
against primary files:

- `/var/tmp/wg-ponytail-20260715/inventory/report.md`: 156-file Ponytail
  inventory, host/runtime map, risks, and parity checklist.
- `/var/tmp/wg-ponytail-20260715/promptdeploy/report.md`: promptdeploy source,
  target, manifest, Nix/Home Manager, and verification architecture.
- `/var/tmp/wg-ponytail-20260715/matrix/report.md`: independent artifact/target
  matrix and source-reference trade-off decision.

Their durable synthesis is `docs/ponytail-integration.md`. The accepted design
is a named, allowlisted bundle manifest, an explicit mutable Desktop binding
for development only, and a pinned non-flake Nix input for production. Direct
external links, a submodule, committed generated copies, and marketplace-only
installation are rejected as the canonical cross-agent source.

Evidence established:

- Ponytail defines six portable skills, compact `AGENTS.md` instructions,
  Claude/Codex lifecycle hooks requiring Node, OpenCode/Pi/Hermes runtimes,
  host-specific rule copies, and plugin manifests for multiple clients.
- Promptdeploy currently deploys complete skill trees natively to Claude,
  Codex, Droid, and OpenCode. GPTel deliberately accepts only prompts, so the
  full objective requires a faithful skill-to-prompt fallback or equivalent.
- A plain external symlink to `/Users/johnw/Desktop/ponytail` is not acceptable:
  it would fail on remote hosts and immutable Nix/Home Manager sources.
- Official Codex documentation and an isolated temporary HOME/CODEX_HOME test
  proved that plugin installation supplies the required `PLUGIN_DATA` and root
  variables, caches host-local content, writes marketplace/plugin config, and
  preserves a human hook-trust boundary. The promptdeploy endpoint will use a
  managed runtime plus rendered hooks rather than pretend static marketplace
  config is a complete installation.
- The upstream `ponytail-gain` skill retains superseded single-shot headline
  figures. The integration will preserve the pinned upstream bytes and disclose
  the inconsistency instead of silently editing or omitting one of six skills.

## Active work

Three implementation-seam reports are in flight:

- `/var/tmp/wg-ponytail-20260715/bundle-api/report.md`: exact Phase-1 Python,
  schema, provenance, collision, and test design.
- `/var/tmp/wg-ponytail-20260715/runtime-adapters/report.md`: managed runtime,
  Claude/Codex hooks, OpenCode layout, rollback, and remote behavior.
- `/var/tmp/wg-ponytail-20260715/nix-binding/report.md`: pinned flake input,
  package passthru, Home Manager assertions, and activation binding.

## Next actions

1. Review the three implementation-seam reports against the accepted ADR.
2. Commit this durable planning/study work as the first coherent unit and run
   its context-complete fess audit.
3. Implement Phase 1: bundle schema/bindings, composite discovery,
   provenance, six skill imports, GPTel projections, and focused tests.
4. Run focused and full gates, commit the unit, and dispatch its fess audit.

## Gate attempt counts

- Promptdeploy baseline: passed on attempt 1 (`nix flake check`, all 7 checks).
- Ponytail reference baseline: attempt 1 reached 81/82; sole failure signature
  is missing optional `pandas` for the CSV benchmark. This dependency does not
  block source parity or promptdeploy implementation and was not installed.
- Implementation/full-suite gate: 0 consecutive failures.
- Rebase/restack gate: 0 consecutive failures.

Reset a gate count when it passes or when its underlying failure signature
demonstrably changes. Stop and escalate after three consecutive attempts at the
same signature without progress.

## Prohibited resume shortcuts

- Do not point production deployment at the Desktop checkout.
- Do not claim GPTel or another instruction-only host has native lifecycle-hook
  parity.
- Do not copy six `SKILL.md` files by hand and call that a maintained reference.
- Do not mutate live target directories while isolated preview paths can prove
  the behavior.
