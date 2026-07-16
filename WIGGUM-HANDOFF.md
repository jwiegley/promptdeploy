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

The first snapshot-only deployment sub-slice was committed as `b001f3c` (`Add
immutable skill snapshot materialization`), while the legacy imported-item
deploy guards remain fail-closed:

- public imported snapshots are revalidated for per-file, tree-byte, entry,
  link-expansion, normalized-mode, topology, digest, and link-payload
  invariants before any target write;
- a central catalog resolver binds every tree snapshot to its exact logical
  manifest provenance, and imported skills additionally bind the captured
  root `SKILL.md` bytes and digest to `SourceItem.content`;
- imported skill trees materialize only from captured nodes, preserving exact
  modes and empty directories, dereferencing captured links to regular files,
  and reusing the existing atomic swap/restore transaction without changing
  the primary path;
- installed imported trees are compared through an expectation-bounded,
  descriptor-held walk using `O_NOFOLLOW`, `O_NONBLOCK`, exact size/mode/kind
  checks, bounded reads, and pre/open/post/path identity audits.

The independent API and security reviews are
`/var/tmp/wg-ponytail-20260715/materializer-api-review/report.md` and
`/var/tmp/wg-ponytail-20260715/materializer-security-review/report.md`. Their
three findings are resolved: all tree-backed items now enforce logical-root
provenance, snapshot revalidation enforces the scanner's per-file ceiling, and
target verification no longer performs an unbounded path-racy walk. The clean
security remediation review is
`/var/tmp/wg-ponytail-20260715/materializer-security-review/remediation.md`;
the API reviewer also returned a clean remediation verdict. Final gates pass:
2,249 tests at 100% branch coverage, strict mypy over 79 source files, Ruff
format/lint, package build, Home Manager module and activation checks, and the
complete seven-check `nix flake check`.

Its required independent fess audit is
`/var/tmp/wg-ponytail-20260715/fess-b001f3c/report.md`. Production received a
clean verdict; the sole low finding was that the wrong-byte drift test also
changed file size. Fess-fix-only commit `1cac04b` (`Strengthen imported byte
drift coverage`) now uses an unequal same-length payload, so byte equality is
proved independently of the size guard. Per protocol, that narrow remediation
does not receive a recursive fess audit.

The target-owned static support slice was committed as `1bc7ef6` (`Add
target-owned bundle support`):

- every target owns an exact mode-stable support-v1 `LICENSE` tree beneath
  `.promptdeploy/bundles/ponytail`, with Codex correctly using the home-level
  root outside `.codex` and remote targets delegating to their staging root;
- all five remote allowlists include the complete hidden support subtree, and
  GPTel accepts the support bundle while continuing to reject non-prompt
  deployables;
- bundle names, selector/category/list plumbing, atomic replacement, exact
  matching, symlink/special-node handling, and leaf-only removal are covered;
- stale removal processes dependents before support and preserves a bundle
  whenever a type/exact-filtered `ManifestItem.source.bundle` dependent remains.

The independent support review is
`/var/tmp/wg-ponytail-20260715/support-target-review/report.md`. It reproduced
and then verified the fix for a filtered-dependent removal defect; its final
verdict is clean. Final gates pass: 2,294 tests at 100% branch coverage, strict
mypy over 80 source files, Ruff format/lint, package build, Home Manager module
and activation checks, and the complete seven-check `nix flake check`.

Its required independent audit is
`/var/tmp/wg-ponytail-20260715/fess-1bc7ef6/report.md`. Production received a
clean verdict; the sole low finding was that two `Path.mkdir` test doubles
used broad argument types and silenced the resulting checker errors.
Fess-fix-only commit `85cb966` (`Use exact Path.mkdir test doubles`) gives
both doubles the real signature and removes the suppressions. The 45 focused
cases and strict typing pass; per protocol, this narrow remediation does not
receive a recursive fess audit.

The dormant operation-catalog and imported-skill interface slice was committed
as `4917e25` (`Add pure operation catalog interfaces`):

- `src/promptdeploy/catalog.py` strictly composes one immutable catalog,
  preflights every configured target namespace, uses logical provenance labels,
  and keeps requested, applicable-requested, dependency-closed, and ordered
  selections distinct;
- target-type applicability is checked before filters or target behavior,
  dependency requirements bypass request filters without leaking support for a
  wrong-tier selector, and requested target predicates are memoized;
- Claude, Codex, Droid, OpenCode, GPTel, and Remote preserve the existing
  `deploy_skill(source_dir=...)` API while accepting either a primary path or
  an accepted `ImportedTreeSnapshot`;
- skill comparison prefers the accepted snapshot over the diagnostic source
  path and Remote forwards the same snapshot authority unchanged;
- `deploy`, `status`, `validate`, and `verify` remain on primary
  discovery, and both imported hashing/materialization guards remain
  fail-closed, so this seam does not activate bundle deployment.

The independent interface review and clean diff audit are
`/var/tmp/wg-ponytail-20260715/catalog-interface-review/report.md` and
`/var/tmp/wg-ponytail-20260715/catalog-diff-audit/report.md`. Their minor
observations are resolved by exactly-once requested predicates, explicit
Claude closure coverage, explicit OpenCode support-only full-selection
coverage, no-authority target coverage, and preserving the public keyword
name. Final gates pass: 2,312 tests at 100% branch coverage, strict mypy over
82 source files, Ruff format/lint, package build, Home Manager module and
activation checks, and the complete seven-check `nix flake check`.

Its required independent audit is
`/var/tmp/wg-ponytail-20260715/fess-4917e25/report.md`. Production received a
clean verdict; the sole low finding was that four positive-only imported-match
tests would also accept an unconditional `True`. Fess-fix-only commit
`5cefa94` (`Test imported snapshot mismatches`) adds unequal accepted snapshots
for Claude, Codex, Droid, OpenCode, and Remote. The five focused cases pass;
per protocol, this narrow remediation does not receive a recursive fess audit.

The composed-catalog activation slice is implemented in the current work unit:

- deploy, status, and strict verify each capture one immutable composed catalog,
  close target-specific dependencies in stable topological order, hash exact
  logical provenance, and compare imported bytes only through accepted
  snapshots;
- deploy preflights protected unmanaged Ponytail collisions and dependency
  prerequisites before mutation; other ownership collisions remain per-item
  checks before that item writes. It refuses unmanaged Ponytail skills even
  under `--force`, revalidates required support before each dependent and
  manifest commit, and keeps dependent removal ahead of bundle cleanup;
- manifest v2 source provenance, target-rendered MCP/model hashes, exact GPTel
  adopted paths, and logical diagnostics make convergence, rotation, adoption,
  drift, and stale removal agree across deploy/status/verify without persisting
  secret-derived hashes for stripped or runtime-indirect values;
- global `--bundle-bindings-file`, repeatable `--bundle-source`, and
  `--require-immutable-bundles` flags feed every command through the same
  binding authority, while `verify --target-root` uses the same isolated
  preview mapping as deploy/status/list;
- target-root previews reject noncanonical IDs, lexical root/ancestor/leaf/
  nested symlinks, hard links, special files, and unknown-home paths before
  target access; all four CLI paths report those failures cleanly, and preview
  hashes never incorporate secret values that remain literal;
- validation remains lenient per bundle, reports logical bundle paths, catches
  effective imported namespace/dependency failures, and never lets one broken
  binding hide primary or sibling diagnostics;
- isolated end-to-end coverage proves all five local target types: Claude,
  Codex, and Droid receive support plus six immutable skill trees; GPTel
  receives support plus six `gptel-preset-v1` prompt projections; OpenCode
  receives support only until the native runtime slice lands. First deploy,
  no-op convergence, provenance drift, source deletion after composition,
  strict verification, exact selection, and original-target non-mutation all
  pass.

The independent activation reviews are
`/var/tmp/wg-ponytail-20260715/activation-correctness-review/report.md` and
`/var/tmp/wg-ponytail-20260715/activation-security-review/report.md`; the two
isolated implementation prototypes are
`/var/tmp/wg-ponytail-20260715/activation-core-proto/report.md` and
`/var/tmp/wg-ponytail-20260715/activation-cli-proto/report.md`. Every finding
was fixed and both final review verdicts are clean under the documented
non-concurrent ordinary-POSIX preview-tree threat model. Final gates pass:
2,397 tests at 100% statement and branch coverage, strict mypy over 83 source
files, Ruff format/lint, package build, Home Manager module and activation
checks, and the complete seven-check `nix flake check`.

Next:

1. add the managed runtime and native OpenCode plugin adapters, including
   live-path rendering, collision detection, transactional registration, and
   rollback/fault-injection coverage;
2. bind the pinned flake source through the package and Home Manager activation
   path, prove immutable metadata parity, and only then enable the root bundle
   declaration;
3. finish operator/update documentation, reference parity, observation drain,
   restack, final audits, and final fess before any separately authorized live
   rollout.

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
- Snapshot materializer and support-target slices: coverage-only and review
  findings changed on each attempt; both final 100%-branch pytest gates, strict
  mypy gates, and seven-check flake gates are green.
- Dormant catalog/interface slice: the first Nix mypy attempt found only a
  test-factory annotation mismatch; the corrected 82-file strict mypy gate,
  2,312-test 100%-branch pytest gate, and full seven-check flake gate are green.
- Composed-catalog activation slice: focused failures changed across typing,
  stale expectations, coverage-only branches, and independent review findings;
  each was corrected before the final gate. The final 2,397-test 100%-branch
  pytest derivation, strict 83-file mypy derivation, and all seven flake checks
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
