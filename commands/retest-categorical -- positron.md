---
description: Full categorical-vs-legacy retest — rebuild, unit tests, FPGA byte-identity
  for every model, and a perf-divergence pass
argument-hint: '[model-tag…] [--no-perf] [--no-semantic]'
---
> **Branch precondition.** This command targets the **categorical ingest pipeline**, which
> lives on the `categorical-semantics-layer` branch (or a branch that has merged it). On
> `main` it does **not** apply: `make build-categorical` is not a make target,
> `t/t_generate_categorical_fpga_real.cpp`, `bin/ci/categorical_logit_matrix.sh`,
> `categorical_logit_test.py`, the `--strict-top1` flag, and the `categorical-*` slugs are all
> absent. On `main`, use **`/retest-this`** (the HF-oracle general battery) instead. Every
> target/binary/flag below is named relative to the categorical branch; `/retest-this` line
> "`make build-categorical` is not a target in main" is correct *for main* and not in conflict
> once this precondition is honored. Phase 1 should assert the target exists and abort with
> this message otherwise (e.g. `make -n build-categorical >/dev/null 2>&1 || { echo "not on
> the categorical branch — use /retest-this"; exit 2; }`).

Confirm the categorical ingest pipeline is still a byte-for-byte drop-in for the legacy
ingest path with no performance regression. Work the phases in order, tracking each with
your task tool. **Do not stop at the first failure** — finish the sweep and emit one
consolidated result table at the end.

# This is the categorical specialization of `/retest-this`

`/retest` is the **categorical specialization** of `/retest-this`. Run the shared grunt-work
via `/retest-this` (it owns the general operating rules, the generic Known-traps, the
weights-provisioning machinery, the three unit-test layers, the isolated one-TEST_CASE
runner, the generic perf methodology, and the review/comment phases), then apply the
categorical overrides below. This is a **documentation-style delegation** — there is no
machine handoff or argument forwarding; read both docs and apply the overrides by hand.
**Where `/retest-this` and this doc conflict, this doc wins for categorical runs.**

| `/retest-this` general | `/retest` categorical override |
|---|---|
| Source of truth = HF transformers forward pass (TVD/top-K/decoded-tokens) | **Legacy ingest path; bit-exact last-prompt-token logits** (`require_byte_identical`, 100%) |
| Comparison binary chosen per derived set | **`gen/t_generate_categorical_fpga_real`** |
| Build = `make build` / `build-test` / `build-ingest` per diff | **`make build-categorical -j 4`** (regenerates categorical + legacy plugins; categorical-branch target only) |
| Model set derived from branch diff | **Fixed eight-model Phase-0 table** (Phase 0 below) |
| Phase-3 sweep filter generic | **`awk '/^  Categorical/'` + the fixed eight-model default `FILTERS`** |
| Phase 4 = code review | **Phase 4 = `bin/ci/categorical_logit_matrix.sh`** semantic gate (`--strict-top1` vs HF, MoE excluded) |
| Slugs from registry | **`categorical-$MODEL[-standalone][-tpN]` / `ingested-$MODEL[-tpN]`** pairing |
| Overall verdict `HF-CORRECT` | **`BYTE-EXACT DROP-IN`** |

The two docs deliberately differ in two places that bite an operator who read one and acts in
the other:

- **Phase numbering is off by one for perf.** In `/retest-this`, code review is Phase 4 and
  perf is Phase 6. Here, Phase 4 is the categorical semantic gate and **Phase 5 is perf** —
  so when this doc says "use the `/retest-this` Phase-6 perf methodology," that maps onto
  **this doc's Phase 5**. And "Phase 4" means code-review in `/retest-this` but the semantic
  logit matrix here.
- **`--no-semantic` means different things.** In `/retest-this` it skips only a *sub-check
  inside Phase 3* (the Python equivalence run). Here it skips **all of Phase 4** (the entire
  categorical semantic matrix). They gate structurally different work; do not treat them as
  the same flag.

# Arguments (`$ARGUMENTS`)

- Bare model tags (e.g. `[phi-4] [mixtral-8x7b]`) → restrict Phases 3 & 5 to those models;
  no tags → all eight. Set `MODELS` once and feed it to both phases. **Phase 4 (the semantic
  logit matrix) is NOT narrowed by a tag filter** — it always runs the full non-MoE matrix
  regardless of which tags you pass.
- `--no-perf` → skip Phase 5. `--no-semantic` → skip Phase 4 (the whole categorical semantic
  matrix; see the note above on how this differs from `/retest-this`'s `--no-semantic`).

# Operating rules

Use the `/retest-this` **Operating rules** verbatim (Nix shell wrapper, `-j 4` max, executor
selection, the `/opt/positron/weights/...` read-only / `/tmp/retest_weights/<repo>` fallback,
one-TEST_CASE-per-process for FPGA, background long jobs, the distinct
`PASS / SKIPPED / QUARANTINED / DIVERGE` states), with two categorical specializations:

- **The Phase-3 runner enforces one-TEST_CASE-per-process** (the sweep runner below).
- **Claim discipline (categorical example).** Bind every "byte-identical" statement to its
  oracle and code path: "last-prompt-token logits, C++ `generate(max_steps=0)`,
  4/32/64-token prompts, per-process isolated."

`ls /dev/vfio/` does not prove the cards are free — check `pgrep -af 'runtron\|t_generate'`.

# Known traps

Use the `/retest-this` **Known traps** for the generic rows (fetcher-cache, stale CMake
graph, wrong-weights-dir, multi-case-vs-solo, HBM instance overflow, stale-HBM operator
reset, the #2808 MoE-synthetic-prompt SIGABRT). Two categorical-specific traps (categorical
branch only — these paths/targets do not exist on main):

| Symptom | Cause → fix |
|---|---|
| `ninja: error: '…' missing and no known rule` (deleted source) | stale CMake graph → `rm -f gen/config/categorical`, rebuild |
| plugin output looks stale after a rebase/restack | `make build-categorical` missed a Haskell change → `rm -f gen/src/tron/h/tron/plugins/{categorical,ingested}_$MODEL.hpp && make build-categorical -j 4`; confirm mtime is post-rebase |

# Phase 0 — Provision weights + pre-warm cache (mandatory)

Use the `/retest-this` Phase-0 machinery (the `WEIGHTS_ROOT`/`SCRATCH_ROOT`/`CACHE_ROOT`
fallback, `bin/get_model REPO --to DIR`, the pre-warm loop, the ≥30-min Phase-3 timeout note)
but with the **fixed eight-model categorical roster**, not a branch-derived set:

```bash
nix develop --command bash -c '
set -uo pipefail
WEIGHTS_ROOT=/opt/positron/weights/huggingface
SCRATCH_ROOT=/tmp/retest_weights
CACHE_ROOT=/opt/positron/weights_cache/cached
# (huggingface-repo, default-tp-slug) — keep aligned with the ModelDef table in
# t/t_generate_categorical_fpga_real.cpp.
MODELS=(
  "shuyuej/Llama-3.2-1B-Instruct-GPTQ            ingested-llama-3.2-1b"
  "thesven/Meta-Llama-3.1-8B-Instruct-GPTQ       ingested-llama-3.1-8b"
  "microsoft/phi-4                               ingested-phi-4"
  "hfl/chinese-alpaca-2-7b                       ingested-chinese-alpaca-2-7b"
  "mistralai/Mixtral-8x7B-Instruct-v0.1          ingested-mixtral-8x7b-instruct-v0.1-tp4"
  "positron-ai/openai--gpt-oss-20b-ingest-best-gptq  ingested-gpt-oss-20b-tp4"
  "positron-ai/openai--gpt-oss-120b-ingest-best-gptq ingested-gpt-oss-120b-tp4"
  "Qwen/Qwen2.5-32B-Instruct-GPTQ-Int4           ingested-qwen-2.5-32b-tp4"
)
for entry in "${MODELS[@]}"; do
  repo="${entry%% *}"
  if   ls "$WEIGHTS_ROOT/$repo"/*.safetensors >/dev/null 2>&1; then echo "  have (canonical): $repo"
  elif ls "$SCRATCH_ROOT/$repo"/*.safetensors >/dev/null 2>&1; then echo "  have (scratch):   $repo"
  else echo "  downloading: $repo"; python3 bin/get_model "$repo" --to "$SCRATCH_ROOT/$repo" \
         || echo "    DOWNLOAD-FAILED $repo (private/positron-internal repos must be pre-populated by ops)"; fi
done
printf "x\n" > /tmp/retest_warmup_prompt.txt
for entry in "${MODELS[@]}"; do
  slug="${entry##* }"
  find "$CACHE_ROOT" -maxdepth 3 -type d -name "$(basename "${entry%% *}")" -print -quit 2>/dev/null | grep -q . \
    && { echo "  warm: $slug"; continue; }
  echo "  prewarming: $slug"
  SYSTEM_CONFIG="--instance 0,1" timeout 600 gen/runtron stream-generate-text \
    --model "$slug" -f /tmp/retest_warmup_prompt.txt \
    --prompt-length 4 --length 1 --temperature 0 --pay-for-determinism --seed 42 \
    >/dev/null 2>&1 || echo "    PREWARM-FAILED: $slug (Phase 3 will be slow)"
done
'
```

`bin/get_model REPO --to DIR` runs HF `snapshot_download` and converts `.bin`→`.safetensors`;
**always pass `--to`** (without it, it defaults to the read-only canonical root). If pre-warm
is skipped, keep the Phase-3 timeout ≥30 min or cold-cache first runs read as TIMEOUT.

# Phase 1 — Rebuild

```
nix develop --command bash -c 'make build-categorical -j 4'
```
Regenerates every categorical + legacy plugin and `gen/t_generate_categorical_fpga_real`.
**Categorical-branch target only** — on main this target does not exist (see the branch
precondition at the top). Assert it before building:
```
nix develop --command bash -c 'make -n build-categorical >/dev/null 2>&1' \
  || { echo "make build-categorical absent — not on the categorical branch; use /retest-this"; exit 2; }
```
Require exit 0 and zero `error:` lines. If `gen/` is corrupt from a prior non-Nix `make`,
`rm -rf gen` and rebuild. (Stale-graph and stale-plugin recovery: see Known traps.)

# Phase 2 — Unit tests

```
nix develop --command bash -c 'bin/ingest-cabal build && bin/ingest-cabal test'   # Haskell ingest
nix develop --command bash -c 'make build-test -j 4 && make test-host'             # C++ Catch2 host
```
All must pass, including the typed-pipeline byte-identity MD5 baselines. Never weaken or skip
a failing test — fix the root cause. If a baseline digest legitimately changed because emitter
output changed, update it deliberately and say why. (See `/retest-this` Phase 2 for the
shared discipline; the categorical run does not need the FPGA `make test-ingest` layer.)

# Phase 3 — FPGA byte-exact parity (headline gate)

`gen/t_generate_categorical_fpga_real` runs each categorical plugin and its legacy-ingest
counterpart on identical weights/prompt/seed/executor with `pay_for_determinism=true`, via the
in-process C++ API `generate(model, prompt, max_steps=0, …)` with `LogitsMode::LAST`, asserting
**bit-exact last-prompt-token logits**. (Generated-token parity over a full decode is Phase 5.)
The oracle is the **legacy ingest pipeline**, not HuggingFace — this is where `/retest`
diverges from `/retest-this`, whose Phase 3 gates against HF.

Model → executor: `[llama-3.2-1b]` tp1·16L, `[llama-3.1-8b]` tp1·32L, `[phi-4]` tp1·40L,
`[chinese-alpaca-2-7b]` tp1·32L, `[mixtral-8x7b]` tp4·32L, `[gpt-oss-20b]` tp4·24L,
`[gpt-oss-120b]` tp4·36L, `[qwen-2.5-32b]` tp4·64L.

Each model has a 4-token oracle plus `[long]` 32/64-token cases (and, for Llama-3, `[realistic]`
real-text cases). The `[long]` cases catch seq-len-dependent emitter bugs (KV-cache stride,
attention chunking, visit-order) the 4-token oracle misses. **A `[long]` DIVERGE on a model whose
4-token case passes is a real bug** — do not excuse it. (These tags exist in
`t_generate_categorical_fpga_real` on the categorical branch; they do **not** exist in main's
`t_generate_ingest_1`, which is why `/retest-this` does not gate on `[long]`.)

**Isolated sweep runner — one TEST_CASE per process by exact name** (enumerates exact names per
filter via `--list-tests`, so a `[long]` filter can't pull many cases into one process):

```bash
#!/usr/bin/env bash
set -uo pipefail
set -f   # noglob: Catch2 tags are bracket-globs ([phi-4]); unquoted they expand against cwd
         # dirs (h/, t/) and mangle phi-4/chinese-alpaca/mixtral into single-char tags.
BIN=./gen/t_generate_categorical_fpga_real
LOGDIR=/tmp/retest_fpga_logs; mkdir -p "$LOGDIR"
[ -x "$BIN" ] || { echo "FATAL: $BIN missing — run Phase 1 first (categorical branch only)"; exit 2; }
"$BIN" --list-tests >/dev/null 2>&1 || { echo "FATAL: test binary won't list tests"; exit 2; }
if [ "$#" -gt 0 ]; then FILTERS="$*"; else
  FILTERS="[llama-3.2-1b] [llama-3.1-8b] [phi-4] [chinese-alpaca-2-7b] [mixtral-8x7b] [gpt-oss-20b] [gpt-oss-120b] [qwen-2.5-32b]"
fi
NAMES=()
for f in $FILTERS; do
  matched=$("$BIN" --list-tests "$f" 2>/dev/null | awk '/^  Categorical/ {sub(/^  /,""); print}')
  [ -n "$matched" ] && while IFS= read -r line; do NAMES+=("$line"); done <<< "$matched"
done
if [ "$#" -gt 0 ] && [ "${#NAMES[@]}" -eq 0 ]; then
  echo "NO-COVERAGE: filters [$FILTERS] matched zero Categorical TEST_CASEs. INCOMPLETE."; exit 3
fi
declare -A SEEN; UNIQ=()
for n in "${NAMES[@]}"; do [ -z "${SEEN[$n]:-}" ] && { SEEN[$n]=1; UNIQ+=("$n"); }; done
for name in "${UNIQ[@]}"; do
  safe=$(printf '%s' "$name" | tr -cd 'a-zA-Z0-9-' | head -c 60); log="$LOGDIR/${safe}.log"
  start=$(date +%s); timeout 1800 "$BIN" "$name" > "$log" 2>&1; rc=$?; dur=$(( $(date +%s) - start ))
  # 30-min timeout: cold-cache 120B first-runs spend ~5min populating weights_cache.
  if   [ $rc -eq 0 ] && grep -q 'SKIPPED' "$log"; then v=SKIPPED
  elif [ $rc -eq 0 ]; then v=PASS
  elif [ $rc -eq 124 ]; then v="TIMEOUT(30m)"
  elif grep -qiE "acquire lock|lock after|device busy|EBUSY" "$log"; then v="LOCK-CONTENTION"
  elif grep -q "first divergence at position" "$log"; then v="DIVERGE: $(grep -oE '[0-9]+/[0-9]+ positions match' "$log" | head -1)"
  elif grep -qiE "Failed to open file|No such file" "$log"; then v="WEIGHTS-MISSING"
  else v="FAIL(rc=$rc)"; fi
  printf '%-70s  %-18s  (%ds)\n' "$name" "$v" "$dur"
  sleep 15   # let tp4 device locks release between cases
done
```

(The categorical binary asserts bit-exact equality and prints `first divergence at position …`
/ `N/N positions match` on mismatch — hence the DIVERGE regex above. It emits no Catch2
`mayfail` marker, so QUARANTINED-PASS/FAIL do not arise from this binary today; the
QUARANTINED rows below apply only if a `mayfail`-tagged case is later added.)

**Verdict taxonomy** (report these distinctly):

| Verdict | Counts toward the gate? |
|---|---|
| `PASS` / `QUARANTINED-PASS` | yes |
| `DIVERGE` | **NO — real correctness regression** |
| `NO-COVERAGE` | NO — INCOMPLETE; a passed tag matched zero TEST_CASEs |
| `SKIPPED` / `WEIGHTS-MISSING` / `TIMEOUT` | NO — INCOMPLETE (Phase 0 should have provisioned/pre-warmed) |
| `QUARANTINED-FAIL` | does not block ship, but report as a known-flake hit (only if a `mayfail` case exists) |
| `LOCK-CONTENTION` | re-run alone; if it still fails alone, escalate |
| `FAIL(rc=…)` | investigate (SIGABRT/SIGSEGV); not necessarily categorical — see Known traps |

**Gate:** every supported model must `PASS`/`QUARANTINED-PASS` on its 4-token and `[long]`
cases. Any `DIVERGE` fails. Any `NO-COVERAGE`/`SKIPPED`/`WEIGHTS-MISSING`/`TIMEOUT` makes the
run INCOMPLETE.

**MoE models must use realistic prompts.** Synthetic incrementing-id prompts overflow an MoE
expert FFN to NaN at depth (issue #2808). Both MoE families now use realistic tokenizations —
Mixtral via `kMixtralReal32/64`, GPT-OSS via `kGptOssReal32/64`. The dense models (Llama, Phi-4,
Qwen, Chinese-Alpaca) keep the synthetic `kMed/kLongGenericPrompt` (no expert-FFN path to
overflow). Any new MoE model must get a realistic tokenization, never the synthetic prompts.

**Triage notes when a DIVERGE is genuine:**
- **Plain vs Permuted are distinct codepaths.** `categorical-<m>`/`ingested-<m>` are Plain;
  `…-permuted` are Permuted (emit `Load2`+`BackPermute`, gated on `TargetExecutor.isPermuted`
  in `ingest/src/Categorical/HypergraphToLoopy.hs` — `assignPermutedWeightPerms` /
  `buildBackPermuteSites`; the `Plain`/`Permuted` enum is `ingest/src/TargetExecutor.hs:21-23`).
  A fix to one rarely fixes the other — confirm the failing slug's variant from
  `config/models.yaml`.
- **Mixtral byte-identity** requires the legacy shared-RMSNorm fold (`foldSharedRmsNormMul`,
  PR #2810) in the base. A ~1-ULP diff across ~95% of logits → confirm that fold is present
  before treating it as categorical. (`foldSharedRmsNormMul` lands with PR #2810; on a base
  that predates it the symbol is absent, so verify it is in the base first.)
- **GPT-OSS tp4** has run-to-run nondeterminism in *multi-token decode* (not the last-token
  gate); if a GPT-OSS case looks flaky, run it 3× and require every run to pass.

# Phase 4 — Semantic logit parity (host) — skip if `--no-semantic`

```
nix develop --command bash -c 'bin/ci/categorical_logit_matrix.sh'
```
Dumps a categorical `.py` per model and runs `categorical_logit_test.py --strict-top1` against
the HuggingFace reference (allclose + top-1) — the *semantic* gate complementing Phase 3's
*byte-level* gate. `--strict-top1` is a flag of the **categorical** `categorical_logit_test.py`
specifically; it is categorical-pipeline-only and does not exist on the legacy/HF reference
path (which is why `/retest-this` forbids inventing it — confirm its surface with
`categorical_logit_test.py --help` on the categorical branch). **The matrix excludes all MoE
models** (Mixtral, GPT-OSS), so MoE currently has no ground-truth check — only
categorical-vs-legacy byte-identity, which a bug shared by both pipelines would pass. Extend
with verified MoE coverage to close that gap. **This phase runs the full non-MoE matrix
regardless of any tag filter** passed in `$ARGUMENTS`.

# Phase 5 — Performance + decode-token parity — skip if `--no-perf`

Generate through both plugins on the same executor; report perf, and (under `--temperature 0`)
whether the decoded token sequences match. Use the `/retest-this` **Phase-6** perf methodology
(the Phase 3 ≠ Phase-5-here codepath distinction, the TOKID taxonomy, `--instance 0,1`, the
deterministic flags, the benchmark hygiene, the 5% threshold, `--log-intermediates`
extraction) — note that `/retest-this`'s Phase 6 is **this doc's Phase 5**; the categorical
specializations are the dual-slug pairing and the watch-list below.

**Phase 3 ≠ Phase 5 codepath.** Phase 3 is the in-process C++ `generate(max_steps=0)` —
prompt-processing only. Phase 5 is the external `runtron stream-generate-text --length N` —
full decode loop, KV-cache, sampling. A divergence in Phase 5 but **not** Phase 3 is in the
decode-loop/runtron host path, **not** the emitter.

**TOKID taxonomy:** (1) Phase-3 PASS + Phase-5 TOKID DIFF on GPT-OSS tp4 → advisory (documented
multi-token-decode nondeterminism, both pipelines equally). (2) same on any other model → not an
emitter bug (Phase 3 already proved the prompt path identical); root-cause the decode path
separately, don't block `/retest`. (3) Phase-3 DIFF → real emitter bug; Phase 3 is the gate.

Fixed methodology (don't vary, or numbers aren't comparable):
- Slugs: categorical = `categorical-$MODEL[-standalone][-tpN]`, legacy = `ingested-$MODEL[-tpN]`
  (confirm in `config/models.yaml`); same executor as Phase 3.
- `SYSTEM_CONFIG="--instance 0,1"` for every launch (whole machine; `--instance 0,4` starves
  GPT-OSS-120B's decode KV cache → DEBUG EXIT).
- 64-token prompt, 32-token greedy decode, `--temperature 0 --pay-for-determinism --seed 42`
  (confirm flags with `runtron --help`; don't invent flags).
- 3 trials/side, report the **median**. Cold cache: before each trial try
  `sudo -n bash -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`; if passwordless sudo is
  unavailable, skip it, compare gen-time/tok-s only, and label wall **NON-COMPARABLE** (warm
  cache once produced a fake −18% wall "win" on 120B).
- Metrics per model: Δ gen-time (ms/tok), Δ tok/s, Δ wall vs legacy, and whether the 32
  token-IDs matched. **Flag >5% median gen-time/tok regression.** Watch dense Llama-3.1-8B
  (eq-sat per-token decode regression, wall breaks even); Mixtral has historically been a win.
- **Token-ID extraction:** diff the `Logits of token N` lines from `--log-intermediates`
  pairwise, not ANSI green-text spans (fragile across runtron versions).

# Final report

One consolidated summary, using the verdict taxonomy verbatim (do not collapse SKIPPED /
QUARANTINED / NO-COVERAGE into PASS):
- **Build / Unit tests:** clean? Haskell N/N, C++ host pass/fail.
- **Phase 0:** per model — weights canonical/scratch/DOWNLOAD-FAILED; cache pre-warmed y/n.
- **Phase 3:** per `(model, prompt-length)` — the verdict + executor + runtime.
- **Phase 4:** per model allclose + top-1 (MoE excluded).
- **Phase 5:** per model Δ gen-time/tok-s/wall + TOKID match.
- **Overall verdict:** `BYTE-EXACT DROP-IN` (all measured rows PASS/QUARANTINED-PASS, no
  DIVERGE, no NO-COVERAGE, no >5% regression — and quote the oracle+codepath+prompt-set bound) /
  `INCOMPLETE` (any NO-COVERAGE/SKIPPED/WEIGHTS-MISSING/TIMEOUT) / `REGRESSION` (any DIVERGE or
  >5% slowdown).

Root-cause anything that genuinely diverged or regressed at file:line level — don't paper over
it, and don't silence a real DIVERGE as "flaky" (quarantine with an issue link + removal
condition, or fix it).
