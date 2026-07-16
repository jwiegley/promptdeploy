# Ponytail Wiggum handoff

## Goal and frozen criteria

The active goal is the full ponytail-to-promptdeploy integration recorded in
`WIGGUM-PLAN.md`. Read that file in full before resuming. Do not reduce the
scope to copying one skill or configuring one client.

## Current state

- Branch: `codex/ponytail-integration`, created from `origin/main` at
  `c308988401fe9a7087aedfeba38bd59143f4cc7d` after a zero-ahead/zero-behind
  check.
- Planning/study commit: `99ece3134b4364410d10aa533c412bb84631d102`
  (`Document Ponytail integration plan`). Its required independent fess audit
  is recorded below.
- Audit-remediation commit: `3328e2b` (`Resolve Ponytail design audit
  findings`). Per the Wiggum protocol, this fess-fix-only commit does not need
  a recursive audit.
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
- Official Codex documentation establishes the plugin root/data environment
  and human hook-trust boundary. Separate isolated temporary HOME/CODEX_HOME
  tests proved only host-local cache and marketplace/plugin config behavior.
  The promptdeploy endpoint will exercise its rendered environment directly
  and use a managed runtime plus rendered hooks rather than pretend static
  marketplace config is a complete installation.
- The upstream `ponytail-gain` skill retains superseded single-shot headline
  figures. The integration will preserve the pinned upstream bytes and disclose
  the inconsistency instead of silently editing or omitting one of six skills.

## Independent planning audit and resolved contracts

The required fess audit for `99ece31` is
`/var/tmp/wg-ponytail-20260715/fess-99ece31/report.md`. It found five genuine
implementation-blocking contradictions. The frozen plan and ADR now resolve
them as follows:

- remove the embedded instruction fallback through a digest-guarded runtime
  transform; a missing canonical main skill is a health failure;
- render commands from an explicit remote live path and use a two-phase
  runtime-before-registration transaction;
- generate honest `gptel-preset-v1` adapters instead of byte-equal stripped
  bodies;
- keep `ponytail-review` one-shot by preventing the tracker from persisting
  `review`;
- enumerate the six OpenCode commands and required shared modules instead of
  recursively admitting adapter directories.

The remediation also makes `verify --target-root`, mode/empty-directory-aware
tree digests, an owned cross-target MIT notice, health probes, accurate 81/82
baseline wording, and the implementation-versus-live-rollout authority split
explicit.

All implementation-seam reports are complete:

- `/var/tmp/wg-ponytail-20260715/bundle-api/report.md`: source composition,
  schema, provenance, collision, and test design;
- `/var/tmp/wg-ponytail-20260715/runtime-adapter/report.md`: managed runtime,
  Claude/Codex hooks, OpenCode layout, rollback, and remote behavior;
- `/var/tmp/wg-ponytail-20260715/nix-binding/report.md`: pinned flake input,
  package passthru, Home Manager assertions, and activation binding.

Where a seam report conflicts with the audited contracts above, the frozen
plan and durable ADR win. In particular, raw stripped GPTel bodies, persistent
review mode, recursive OpenCode adapter directories, and upstream's fallback
copy are prohibited.

## Next actions

The first Phase-1 implementation slice is implemented and verified:

- `src/promptdeploy/bundles.py` adds closed schema-1 binding descriptors,
  confined declarations, explicit mutable overrides, and the immutable
  `/nix/store` gate;
- `Config.bundles` is defaulted for compatibility, config loading performs no
  bundle I/O when no bundle is declared, and target-root remapping preserves
  the resolved tuple;
- `tests/test_bundles.py` covers every binding/declaration branch. The Nix
  pytest check passed 1,870 tests at 100% branch coverage, and the Nix mypy
  check passed 68 source files.

Its required independent audit is
`/var/tmp/wg-ponytail-20260715/fess-3113fcc/report.md`. The audit reproduced
three defects, all resolved by the current remediation: explicit overrides no
longer disappear when no declaration exists; declarations retain the resolved
manifest path rather than a swappable lexical symlink; and unknown `~user`
expansion is normalized to a clean bundle error for both overrides and the
ambient descriptor path. This is a fess-fix-only unit and therefore does not
receive a recursive audit.

After committing this remediation:

The second Phase-1 slice is implemented and focused gates are green:

- deployment manifests now write schema v2 while strict-reading both legacy
  v1 and v2;
- imported entries store only closed logical provenance (bundle, path,
  version, revision/NAR or mutable status, transform, and license), never a
  source-root path;
- unsafe names/paths remain fail-closed in both strict and rebuildable-cache
  readers, exact partial deployment preserves unselected provenance, and v1
  primary hash semantics remain current;
- 1,963 tests pass at 100% branch coverage and strict mypy passes 69 source
  files.

Its required independent audit is
`/var/tmp/wg-ponytail-20260715/fess-88ee2fe/report.md`. The audit reproduced
two exact-state defects: strict loading accepted existing but incomplete
manifests, and both readers silently accepted duplicate JSON keys. The
current remediation rejects an existing exact manifest without an `items`
object or an explicit per-item `source_hash`, rejects duplicate keys at every
JSON object depth, and preserves the documented missing-version legacy-v1
migration case. The Nix pytest gate now passes 1,976 tests at 100% branch
coverage, strict mypy passes 69 source files, and the full seven-check flake
gate is green. This is a fess-fix-only unit and therefore does not receive a
recursive audit.

The third Phase-1 source-catalog slice is implemented and verified in the
current work unit:

- `flake.lock` and `flake.nix` pin Ponytail as a non-flake input at
  `16f29800fd2681bdf24f3eb4ccffe38be3baec6b`; the Nix test derivation uses
  that store source rather than silently skipping when the Desktop checkout
  is absent;
- `bundles/ponytail.yaml` is a closed, reviewed manifest for version 4.8.4,
  the MIT notice, six complete skill trees, and six named
  `gptel-preset-v1` projections;
- descriptor-held imported-tree capture freezes node kind, canonical path,
  normalized mode, empty directories, link identity, and bytes before the
  checkout can change; all six pinned tree and `SKILL.md` digests match both
  the Desktop checkout and Nix source;
- source items now carry logical provenance, applicability, dependencies,
  and an immutable tree snapshot. Composition rejects ambiguous identities,
  dependency cycles/gaps, applicability gaps, and effective target-specific
  slash-name collisions;
- all six GPTel transforms are byte-, heading-, frontmatter-, and
  substitution-guarded. They preserve the reviewed task semantics while
  making one-invocation scope and absent lifecycle/update capabilities
  explicit;
- the legacy path-based deployment functions reject imported items before
  hashing or materialization. The next unit will replace that temporary
  fail-closed boundary with successful snapshot-only deployment; the retained
  checkout path is diagnostic and cannot currently become deployment
  authority.

The pre-commit reviews are
`/var/tmp/wg-ponytail-20260715/catalog-slice-api-review/report.md` and
`/var/tmp/wg-ponytail-20260715/catalog-slice-security-review/report.md`; the
remediation verification is
`/var/tmp/wg-ponytail-20260715/catalog-fix-review/report.md`. Their findings are
resolved in this work unit: tree-backed hashing is item-type independent;
composition cannot omit collision preflight; literal duplicate YAML keys,
including keys inside inline and sequence merge sources, remain rejected
without breaking standard merge precedence; source directory enumeration is
bounded before allocation; Windows device/alias paths are rejected; link
metadata is audited before and after capture; and filesystem tests set modes
explicitly instead of depending on process umask.

The final source-slice gates pass: 2,210 tests at 100% branch coverage, strict
mypy over 78 source files, Ruff format/lint, package build, Home Manager module
and activation checks, and the complete seven-check `nix flake check`.

The source-catalog slice was committed as `61c2c4b` (`Add pinned Ponytail
source catalog`). Its required independent audit is
`/var/tmp/wg-ponytail-20260715/fess-61c2c4b/report.md`. The audit confirmed that
canonical root-relative immediate link targets match the frozen scanner
contract, and found two descriptor-cleanup defects: a failed session-root
identity `fstat` and a failed selected-tree `fstat` could leak their open
descriptors and expose raw `OSError`. This fess-fix-only work unit closes both
descriptors, normalizes both failures to `ImportedSourceError`, and adds
deterministic fault-injection coverage. Per protocol, it does not receive a
recursive fess audit. Its final gates pass: 2,212 tests at 100% branch coverage,
strict mypy over 78 source files, Ruff format/lint, package build, Home Manager
module and activation checks, and the complete seven-check `nix flake check`.

Next:

1. integrate the composed catalog with deploy/status/validate/verify using
   snapshot-only imported-tree hashing and materialization, target-specific
   dependency closure, manifest provenance, and `verify --target-root`;
2. prove first deploy, no-op convergence, pin drift, source mutation/deletion,
   exact removal, and rollback in isolated target roots before enabling the
   root declaration.

## Gate attempt counts

- Promptdeploy baseline: passed on attempt 1 (`nix flake check`, all 7 checks).
- Ponytail reference baseline: attempt 1 reached 81/82; sole failure signature
  is missing `pandas`, which upstream installs as a CSV-test dependency but the
  production runtime does not need. The baseline is non-green; the dependency
  was not installed under this run's constraints.
- Binding-slice pytest/mypy gates: passed on attempt 1; failure count reset.
- Binding slice and audit remediation full `nix flake check`: each passed on
  attempt 1 (all 7 checks); failure count reset.
- Manifest-slice pytest/mypy gates: passed after one mypy-only test-fixture
  correction; failure signature changed and the count reset.
- Manifest-slice full `nix flake check`: passed on attempt 1 (all 7 checks).
- Source-catalog pre-review pytest/mypy and full-flake gates: passed on attempt
  1; the review remediation then changed the implementation and reset the
  count.
- Source-catalog remediation: a direct host pytest attempt was inapplicable
  because that interpreter lacks project dependencies; the Nix pytest gate
  first exposed only new coverage branches, then passed on the next changed
  test signature. The final 2,210-test, mypy, and full seven-check flake gates
  are green.
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
