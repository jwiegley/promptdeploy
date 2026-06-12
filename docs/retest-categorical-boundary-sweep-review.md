# Retest Categorical Boundary Sweep Review

> **Resolved.** All three P2 issues below were fixed by commits `2c32b19`
> (adds the generic "Token fidelity across context-length boundaries"
> procedure to `/retest`, resolving Issue 1) and `3115483` (reworks the
> `/retest-categorical` Phase-3 gate: splits `NOT-REACHABLE` into
> `OUT-OF-SCOPE` and `REACHABILITY-REGRESSION`, enumerates the boundary
> matrix directly, and maps every Phase-3 status into the final verdict
> taxonomy, resolving Issues 2 and 3). This document is kept as a historical
> record of the review; deleting it is the maintainer's alternative (see the
> vendored/leftover-files decision).

## Scope

This report documents three P2 review findings in
`commands/retest-categorical -- positron.md` related to the new mandatory
token-fidelity boundary sweep. The issues are connected: the categorical command
declares a stricter boundary gate, but the base procedure it references is
missing and the new reachability verdict is not mapped consistently through the
gate and final report.

Relevant files:

- `commands/retest-categorical -- positron.md`
- `commands/retest -- positron.md`

The affected categorical behavior is Phase 3, the FPGA byte-exact parity gate
that compares the categorical ingest path against the legacy ingest path.

## Issue 1: Missing Base Boundary-Sweep Procedure

### Definition

`/retest-categorical` now delegates the token-fidelity boundary sweep to
`/retest`, then says the categorical command overrides the oracle from
HuggingFace top-K inclusion to categorical-vs-legacy byte identity.

The problem is that `/retest` does not currently define the inherited boundary
sweep. It has no `Token fidelity across context-length boundaries` section, and
its existing Phase-3 text treats longer sequence-length coverage as a known
limitation rather than as a mandatory gate.

The categorical document references the missing procedure in two places:

- `commands/retest-categorical -- positron.md:36` says the `/retest` general
  behavior is `Token-fidelity boundary sweep {8,64,256,4096,16384} gated vs HF
  top-K inclusion`.
- `commands/retest-categorical -- positron.md:202-205` says to see the `/retest`
  `Token fidelity across context-length boundaries` section for the page-boundary
  rationale and realistic-prompt / NaN rule.

But the base `/retest` document says the opposite current state:

- `commands/retest -- positron.md:418-424` says `t_generate_ingest_1` currently
  registers only a single prompt per model, has no `[long]` or `[realistic]`
  tags in any `t/` binary, and that sequence-length coverage should be treated
  as a known limitation rather than a gate.

### How It Was Found

The reviewer compared the new categorical delegation table and Phase-3 boundary
text against the current `/retest` command document. A search for `Token fidelity
across context-length boundaries` found the categorical reference but no base
section in `/retest`.

The reviewer also checked the categorical Phase-3 runner. The runner still
enumerates the oracle case plus existing `[long]` / `[realistic]` cases from
`gen/t_generate_categorical_fpga_real`; it does not generate or enforce the full
boundary matrix `{8, 64, 256, 4096, 16384}`.

### Why It Matters

Operators can read the categorical command and conclude that the boundary sweep
is mandatory, while having no concrete inherited HuggingFace procedure to apply
or override. That creates several failure modes:

- The boundary cells can be skipped because no runner enumerates them.
- The operator can guess a procedure, causing inconsistent retest evidence.
- A final report can claim boundary coverage that was never actually run.
- The categorical byte-exact gate can appear stricter on paper without changing
  the executable retest path.

This is especially risky because the new categorical text makes boundary
coverage part of the headline Phase-3 gate.

### Proposed Correction

Fix this by either adding a real base procedure to `/retest` or making the
categorical command fully standalone.

The preferred correction is to add a generic `/retest` section named
`Token fidelity across context-length boundaries`, then keep
`/retest-categorical` as the oracle-specific override.

The base `/retest` section should define:

- The boundary set: `{8, 64, 256, 4096, 16384}`.
- The rationale: KV cache page size is 64, so these lengths exercise 1, 4, 64,
  and 256-page regimes.
- The exact HuggingFace oracle for the generic retest path, including
  per-position top-K inclusion and any tolerance rules.
- How to determine whether a length is in scope for a model before running it.
- How to source realistic token streams for long prompts, especially 256, 4096,
  and 16384-token cases.
- How MoE models avoid synthetic incrementing-token prompts that can trigger the
  known NaN issue.
- How each `(model, length)` cell is run in an isolated process.
- How `NO-COVERAGE`, environment failures, true divergences, and out-of-scope
  cells are reported.

The categorical override should then state only what differs:

- The oracle is legacy ingest, not HuggingFace.
- The comparator is byte-exact last-prompt-token logits, not top-K slack.
- Every in-scope `(model, length)` boundary cell must agree exactly.

The Phase-3 runner also needs to enumerate the boundary cells directly. It should
not rely solely on existing `[long]` or `[realistic]` tags unless those tags are
expanded to cover the full boundary set and are verified in the test listing.

### Background for Diagnosis

This issue is a documentation/procedure mismatch, not necessarily a model bug.
The categorical branch may already contain some long cases, but the document now
requires a specific five-length boundary matrix. Another agent should inspect
both the command document and the actual `t_generate_categorical_fpga_real`
test registrations to verify whether the required lengths exist and whether the
runner selects them one process at a time.

If the test binary does not register the full boundary set, the correction is
not only a Markdown edit. The test data and runner must be extended so the
documented gate is executable.

## Issue 2: `NOT-REACHABLE` Conflates Out-of-Scope Cells With Regressions

### Definition

The categorical document introduces `NOT-REACHABLE`, but it uses that single
status for two different conditions:

- A length exceeds a model's declared support, such as
  `max_position_embeddings` or a real KV-cache capacity limit.
- A length is within the model's supported range, but only one of the two
  compared pipelines can run it.

Those cases must not have the same verdict.

The problematic text is:

- `commands/retest-categorical -- positron.md:210-212`: both pipelines must
  reach the length; a length only one side can run is `NOT-REACHABLE`; the same
  sentence also references `max_position_embeddings` and KV-cache HBM limits.
- `commands/retest-categorical -- positron.md:292`: the verdict table defines
  `NOT-REACHABLE` as incomplete for either one-sided reachability or exceeding
  model limits.

### How It Was Found

The reviewer checked whether the new verdict taxonomy matches the categorical
command's stated contract. That contract is drop-in replacement behavior:
categorical ingest must match legacy ingest at every supported, reachable
boundary length.

Under that contract, a model-limit exclusion and a one-sided pipeline failure
have different meanings:

- A boundary length beyond the model's maximum context is outside the model's
  contract.
- A reachable length that only legacy or only categorical can execute is a
  categorical drop-in failure.

### Why It Matters

The current `NOT-REACHABLE` definition can produce both false failures and false
non-failures.

False failure:

- A model has `max_position_embeddings = 4096`.
- The 16384-token boundary cell is impossible by model definition.
- Marking that cell `INCOMPLETE` makes the whole run look unfinished even though
  the cell is legitimately out of scope.

False non-failure:

- A model supports 4096 tokens.
- The legacy pipeline runs the 4096-token case, but the categorical pipeline
  cannot.
- Marking that cell `NOT-REACHABLE` / `INCOMPLETE` hides a real regression. The
  categorical path is not a drop-in replacement at a supported length.

### Proposed Correction

Split `NOT-REACHABLE` into separate verdicts with distinct gate semantics.

Recommended statuses:

- `OUT-OF-SCOPE` or `N/A-MAX-POSITION`: the requested length exceeds the model's
  declared context limit or a documented hard capacity limit. This does not
  count as pass, incomplete, or regression, but it must be reported with the
  exact reason and limit.
- `REACHABILITY-REGRESSION`: the length is inside the model's supported
  contract, but only one pipeline can execute it. This counts as `REGRESSION`.
- Existing setup or coverage statuses remain separate: `NO-COVERAGE`, `SKIPPED`,
  `WEIGHTS-MISSING`, `TIMEOUT`, unresolved `LOCK-CONTENTION`, and selector
  failures should continue to make the run `INCOMPLETE`.

The gate should be rewritten around in-scope cells:

- Every in-scope boundary cell must run both pipelines.
- Every in-scope boundary cell must pass byte-exact categorical-vs-legacy parity.
- Any byte divergence is `REGRESSION`.
- Any one-sided reachability at an in-scope length is `REGRESSION`.
- Any missing test coverage or environmental inability to run an in-scope cell is
  `INCOMPLETE`.
- Any out-of-scope length is reported as `N/A` with the model limit and is not
  allowed to be counted as `PASS`.

### Background for Diagnosis

The agent fixing this should verify how model context limits are represented in
the project. The document mentions `max_position_embeddings`, but some models or
executors may have additional effective limits from KV-cache HBM capacity,
executor topology, or test harness constraints.

The important distinction is whether the length is outside the model's supported
contract or inside the contract but unreachable because one pipeline fails. The
former is a reported exclusion. The latter is a regression in the categorical
drop-in claim.

## Issue 3: Final Verdict Omits `NOT-REACHABLE`

### Definition

The Phase-3 gate says `NOT-REACHABLE` makes the run incomplete, but the final
overall verdict does not include `NOT-REACHABLE` in the `INCOMPLETE` cases.

The inconsistent text is:

- `commands/retest-categorical -- positron.md:298-302`: the Phase-3 gate says
  any `NO-COVERAGE` / `NOT-REACHABLE` / `SKIPPED` / `WEIGHTS-MISSING` /
  `TIMEOUT` makes the run `INCOMPLETE`.
- `commands/retest-categorical -- positron.md:382-384`: the final report asks
  Phase 3 to include boundary rows with `PASS`, `DIVERGE`, and `NOT-REACHABLE`.
- `commands/retest-categorical -- positron.md:387-390`: the overall verdict
  defines `INCOMPLETE` as any `NO-COVERAGE` / `SKIPPED` / `WEIGHTS-MISSING` /
  `TIMEOUT`, omitting `NOT-REACHABLE`.

### How It Was Found

The reviewer compared the Phase-3 verdict table and gate text against the final
report's overall verdict taxonomy. The same status is gate-relevant in Phase 3
but missing from the summary-level verdict mapping.

### Why It Matters

A run can report a boundary cell as `NOT-REACHABLE` while the final summary does
not know how to classify it. This can produce contradictory output, such as:

- Phase 3 says the run is incomplete because a boundary cell is `NOT-REACHABLE`.
- The final verdict omits that condition and can still appear to allow
  `BYTE-EXACT DROP-IN`.

That makes the retest result ambiguous and can mislead reviewers or release
operators.

### Proposed Correction

If `NOT-REACHABLE` remains as a temporary status, add it to the final
`INCOMPLETE` definition.

The better fix is to apply Issue 2 first and then update the final verdict
taxonomy to match the split statuses:

- `BYTE-EXACT DROP-IN`: all in-scope measured rows pass or quarantined-pass; no
  divergence; no reachability regression; no missing required coverage; no
  setup failure; no performance regression above the allowed threshold.
- `INCOMPLETE`: any in-scope cell has `NO-COVERAGE`, `SKIPPED`,
  `WEIGHTS-MISSING`, `TIMEOUT`, unresolved lock contention, selector failure, or
  other environmental/test-harness failure that prevents a required verdict.
- `REGRESSION`: any byte divergence, any in-scope one-sided pipeline
  reachability, or any performance regression above the allowed threshold.
- `OUT-OF-SCOPE` / `N/A`: reported per cell for lengths beyond the model's
  declared support, never counted as `PASS`, `INCOMPLETE`, or `REGRESSION`
  unless the document intentionally chooses stricter semantics.

### Background for Diagnosis

This is a consistency bug in the reporting contract. It can be fixed in Markdown
once the verdict taxonomy is settled, but the final wording should be updated
only after resolving Issue 2. Otherwise the document may preserve the wrong
semantics by consistently treating all reachability cases as incomplete.

## Recommended Fix Order

1. Split reachability semantics first. Define separate statuses for out-of-scope
   cells and in-scope one-sided pipeline failures.
2. Add the missing generic `/retest` boundary-sweep procedure, or inline a fully
   standalone categorical procedure that no longer references missing base text.
3. Update the categorical Phase-3 runner instructions so the boundary matrix is
   actually enumerated and executed.
4. Update the final overall verdict so every Phase-3 status maps cleanly to
   `BYTE-EXACT DROP-IN`, `INCOMPLETE`, `REGRESSION`, or reported `N/A`.

## Acceptance Criteria for the Fix

A complete fix should satisfy all of the following:

- `/retest` contains a real boundary-sweep procedure if
  `/retest-categorical` continues to delegate to it.
- `/retest-categorical` states the categorical override in terms of byte-exact
  categorical-vs-legacy parity, without relying on missing base procedure text.
- The runner instructions explicitly cover `{8, 64, 256, 4096, 16384}`.
- Out-of-scope model lengths are reported separately from failed in-scope
  pipeline reachability.
- One-sided reachability at an in-scope length is a regression, not merely
  incomplete.
- The final verdict taxonomy includes every status that Phase 3 can emit.
- A report cannot claim `BYTE-EXACT DROP-IN` if any required in-scope boundary
  cell was skipped, missing, divergent, timed out, or reachable by only one
  pipeline.
