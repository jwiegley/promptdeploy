---
description: Full categorical-vs-legacy retest — rebuild, unit tests, FPGA byte-identity
  for every model, and a perf-divergence pass
argument-hint: '[model-tag…] [--no-perf] [--no-semantic]'
---
Run the comprehensive retest of the categorical ingest pipeline against the legacy
ingest path, and confirm it is still a byte-for-byte drop-in with no performance
regression. Work the phases below in order, tracking each with your task/todo tool.
**Do not stop at the first failure** — finish the sweep and emit one consolidated result
table at the end.

# Arguments (`$ARGUMENTS`)

Parse `$ARGUMENTS` before starting:
- Bare model tags (e.g. `[phi-4] [mixtral-8x7b]`) → restrict Phases 3 & 5 to exactly those
  models. No tags → all eight supported models.
- `--no-perf` → skip Phase 5. `--no-semantic` → skip Phase 4.
- Set `MODELS` (the tag list) once and feed it to the Phase 3 runner and Phase 5 loop;
  never let the embedded script's default list override an explicit subset.

# Environment (read first)

- **Everything runs inside the Nix dev shell.** Wrap every build/test/ingest command as
  `nix develop --command bash -c '<cmd>'`. Outside it the toolchain (GHC 9.12, clang-19,
  the Python venvs) is missing and builds fail confusingly.
- **Never exceed 4 build jobs** (`-j 4`).
- **Executors:** dense models run on `tp1` (1 card); MoE / 32B models on `tp4` (4 cards).
- **FPGA availability is not provable by `ls /dev/vfio/`** (that only shows the device
  groups exist, not that they're free). Before tp4 cases, check nothing else holds the
  cards: `pgrep -af 'runtron|t_generate'` should show no other live run. The real safety
  net is the per-case lock-contention re-run in Phase 3 — rely on it.
- `/opt/positron/weights/...` is **read-only**; missing weights go to
  `/tmp/retest_weights/<repo>` and the C++ test harness's `resolve_weights()`
  picks them up automatically (no source edits required).
- Launch long builds/sweeps in the background and let the harness notify you; do not poll.

# Troubleshooting (read BEFORE concluding anything went wrong with the categorical pipeline)

When something fails in a way that looks like a categorical-vs-legacy
divergence, check these well-known false-positive sources first:

1. **Nix fetcher-cache corruption.** Any `disk I/O error` or `database disk
   image is malformed` from `nix develop` means
   `~/.cache/nix/fetcher-cache-v4.sqlite` is corrupt. Fix:
   `rm -f ~/.cache/nix/fetcher-cache-v4.sqlite` then retry.
2. **Stale generated plugins after rebase / restack.** `make build-categorical`
   does not always detect Haskell source changes that affect Plain plugin
   emission. If you suspect stale output, force re-emission:
   `rm -f gen/src/tron/h/tron/plugins/{categorical,ingested}_$MODEL.hpp &&
   make build-categorical -j 4` and verify the file mtime is post-rebase.
3. **Stale CMake graph after source-file deletion.** `ninja: error: missing
   and no known rule` for a deleted file means `gen/config/<target>` has
   the old graph cached. Fix: `rm -f gen/config/categorical` then rebuild.
4. **Stale FPGA HBM state across processes.** A SIGABRT with
   `start_addr 0x..multi-GB..` persisting across multiple fresh runtron
   processes after a multi-minute wait means a card needs an operator-level
   reset. Not actionable from `/retest`; report and stop.
5. **Wrong weights directory.** A tensor-by-name load failure like
   `unable to load tensor: model.layers.0.mlp.experts.0.gate_up_proj`
   almost always means the test points at a raw HF repo instead of the
   ingest-prepared one (e.g., `openai/gpt-oss-20b` vs
   `positron-ai/openai--gpt-oss-20b-ingest-best-gptq`). Verify the
   `ModelDef.weights` path against the existing 4-token TEST_CASE.
6. **Catch2 in-process state contamination.** A test FAIL inside a multi-
   case binary invocation but PASS in solo-process invocation is contamination,
   NOT a categorical regression. **Always re-run the single failing TEST_CASE
   by exact name in a fresh process before treating it as a bug.**
7. **Wrong `SYSTEM_CONFIG --instance`.** An HBM `ERROR allocating ...
   start_addr 0x48000000 ... DEBUG EXIT` is instance-budget overflow (GPT-OSS-120B
   at tp4 doesn't fit in `--instance 0,4`'s 1/4-of-machine share). Re-run
   with `SYSTEM_CONFIG="--instance 0,1"`.

# Phase 0 — Provision weights + pre-warm cache (mandatory)

Two distinct purposes:
- **Provision** missing weights from HuggingFace. The on-disk
  `/opt/positron/weights/huggingface/...` is read-only and some models
  (e.g. `hfl/chinese-alpaca-2-7b`) ship only PyTorch `.bin` upstream. Missing
  weights go to `/tmp/retest_weights/<repo>` where the test's
  `resolve_weights()` will pick them up.
- **Pre-warm** the FPGA weight cache (`/opt/positron/weights_cache/cached`)
  so the first Phase-3 invocation doesn't pay a multi-minute populate cost
  and TIMEOUT.

```bash
nix develop --command bash -c '
set -uo pipefail
WEIGHTS_ROOT=/opt/positron/weights/huggingface
SCRATCH_ROOT=/tmp/retest_weights
CACHE_ROOT=/opt/positron/weights_cache/cached

# Per-model: (huggingface-repo,  default-tp-slug)
# Keep aligned with ModelDef table in t/t_generate_categorical_fpga_real.cpp.
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

# Step A: provision missing weights.
for entry in "${MODELS[@]}"; do
  repo="${entry%% *}"
  if ls "$WEIGHTS_ROOT/$repo"/*.safetensors >/dev/null 2>&1; then
    echo "  provisioned (canonical): $repo"
  elif ls "$SCRATCH_ROOT/$repo"/*.safetensors >/dev/null 2>&1; then
    echo "  provisioned (scratch):   $repo"
  else
    echo "  downloading: $repo -> $SCRATCH_ROOT/$repo"
    python3 bin/get_model "$repo" --to "$SCRATCH_ROOT/$repo" || \
      echo "    FAILED to download $repo (may need HF token or be a private repo)"
  fi
done

# Step B: pre-warm the FPGA weight cache.
printf "x\n" > /tmp/retest_warmup_prompt.txt
for entry in "${MODELS[@]}"; do
  slug="${entry##* }"
  [ -d "$CACHE_ROOT" ] && find "$CACHE_ROOT" -maxdepth 3 -type d -name "$(basename "${entry%% *}")" \
    -print -quit 2>/dev/null | grep -q . && { echo "  warm: $slug"; continue; }
  echo "  prewarming: $slug"
  SYSTEM_CONFIG="--instance 0,1" timeout 600 gen/runtron stream-generate-text \
    --model "$slug" -f /tmp/retest_warmup_prompt.txt \
    --prompt-length 4 --length 1 --temperature 0 --pay-for-determinism --seed 42 \
    >/dev/null 2>&1 || echo "    PREWARM FAILED: $slug (continue, but expect slow Phase 3)"
done
'
```

**Auto-download details:** `bin/get_model REPO --to /tmp/retest_weights/REPO`
runs HuggingFace `snapshot_download` and (when needed) converts `.bin` →
`.safetensors`. Always pass `--to` explicitly — without it, `get_model`
defaults to `/opt/positron/weights/huggingface` which is read-only.
Positron-internal models (`positron-ai/...`) are not on public HuggingFace
and will fail download; their canonical paths must already be populated by
ops.

**The C++ test harness already knows about `/tmp/retest_weights/`** via
`resolve_weights()` in `t/t_generate_categorical_fpga_real.cpp`: if the
canonical path lacks safetensors, the test falls back to the scratch path
automatically. **You should not edit `kWeightsRoot` literals.**

**If pre-warm is skipped**, extend Phase-3 timeouts to ≥30 minutes (the
runner default below already does this); otherwise cold-cache first runs
show as TIMEOUT and look like regressions.

# Phase 1 — Rebuild

```
nix develop --command bash -c 'make build-categorical -j 4'
```
Regenerates every categorical + legacy plugin (`gen/.../plugins/*.hpp`), builds the C++
runtime/plugins, and `gen/t_generate_categorical_fpga_real`. Require exit 0 and zero
`error:` lines. If `gen/` is corrupt from a prior non-Nix `make`, `rm -rf gen` and rebuild.

**Stale CMake config after source-file deletion:** if a Haskell source file was
deleted (e.g., a benchmark like `ingest/bench/TimeRewrite.hs`) the ninja graph
inside `gen/config/categorical` may still reference the missing dependency and
the build will fail with `ninja: error: '…' missing and no known rule`. Fix by
`rm -f gen/config/categorical` to force CMake reconfigure, then rebuild.

**Plugin freshness after rebase:** `make build-categorical` re-emits plugins
only when its dependency graph sees Haskell source change. After a `gt restack`
or `git rebase` of the categorical-semantics-layer branch the ingest binary's
mtime can advance without the build graph noticing. If you suspect stale
plugins, force re-emission for the model under test:
`rm -f gen/src/tron/h/tron/plugins/categorical_$MODEL.hpp
gen/src/tron/h/tron/plugins/ingested_$MODEL.hpp && make build-categorical -j 4`.
Confirm the file mtimes are now post-rebase.

# Phase 2 — Unit tests

```
nix develop --command bash -c 'bin/ingest-cabal build && bin/ingest-cabal test'   # Haskell ingest suite
nix develop --command bash -c 'make build-test -j 4 && make test-host'             # C++ Catch2 host suite
```
All must pass, including the typed-pipeline byte-identity MD5 baselines. Never weaken or
skip a failing test to go green — fix the root cause. If a baseline digest legitimately
changed because emitter output changed, update it deliberately and say why.

# Phase 3 — FPGA byte-exact parity (headline gate)

`gen/t_generate_categorical_fpga_real` runs each **categorical** plugin and its
**legacy-ingest** counterpart on identical weights/prompt/seed/executor with
`pay_for_determinism=true`, asserting **bit-exact last-prompt-token logits**. This
deterministic last-token-logit equality is the accepted byte-level correctness gate
(generated-token-ID parity over a full decode is checked separately in Phase 5).

**Run each TEST_CASE in its own process** (one Catch2 tag-and-name per invocation,
NOT one whole binary invocation per model). The per-process runner below is the
canonical Phase-3 entrypoint. Two reasons:
- Catch2 in single-process mode runs all selected TEST_CASEs in one binary,
  and consecutive same-model cases share FPGA state (KV-cache page bindings,
  worker assignment, scratch HBM) — a later case can flip its logits even
  though each case passes in isolation. The 4-token oracle hid this by
  having only ONE TEST_CASE per model; multi-prompt-length tests expose it.
- A hardware `DEBUG EXIT` / SIGABRT in one model otherwise terminates the
  process and silently truncates the rest of the sweep. Per-process invocation
  contains that.

**NEVER** invoke the test binary as `gen/t_generate_categorical_fpga_real` (no
filter) or `gen/t_generate_categorical_fpga_real "[long]"` (selects many in
one process) as a way to "run /retest". That mode is for interactive debugging
only and will produce non-reproducible verdicts.

Model → executor matrix:

| Tag | Exec | Layers | | Tag | Exec | Layers |
|-----|------|--------|-|-----|------|--------|
| `[llama-3.2-1b]` | tp1 | 16 | | `[mixtral-8x7b]` | tp4 | 32 |
| `[llama-3.1-8b]` | tp1 | 32 | | `[gpt-oss-20b]` | tp4 | 24 |
| `[phi-4]` | tp1 | 40 | | `[gpt-oss-120b]` | tp4 | 36 |
| `[chinese-alpaca-2-7b]` | tp1 | 32 | | `[qwen-2.5-32b]` | tp4 | 64 |

**Isolated sweep runner — one TEST_CASE per process by exact name.**
Filters like `"$BIN" "[mixtral-8x7b][long]"` select MULTIPLE TEST_CASEs in
one process and reintroduce contamination. The runner below enumerates
exact names per filter via Catch2's `--list-tests`, then invokes each one
in its own process.

```bash
#!/usr/bin/env bash
set -uo pipefail
set -f   # noglob: the Catch2 tags are bracket-globs ([phi-4] etc.); without
         # this, unquoted filters expand against cwd dirs (h/, t/) and
         # silently mangle phi-4/chinese-alpaca/mixtral into single-char tags.
BIN=./gen/t_generate_categorical_fpga_real
LOGDIR=/tmp/retest_fpga_logs; mkdir -p "$LOGDIR"
[ -x "$BIN" ] || { echo "FATAL: $BIN missing — run Phase 1 first"; exit 2; }
"$BIN" --list-tests >/dev/null 2>&1 || { echo "FATAL: test binary won't list tests"; exit 2; }
if [ "$#" -gt 0 ]; then FILTERS="$*"; else
  FILTERS="[llama-3.2-1b] [llama-3.1-8b] [phi-4] [chinese-alpaca-2-7b] [mixtral-8x7b] [gpt-oss-20b] [gpt-oss-120b] [qwen-2.5-32b]"
fi

# Expand each filter to the list of matching exact TEST_CASE names.
ALL_TESTS=$("$BIN" --list-tests 2>/dev/null | awk '/^  Categorical/ {sub(/^  /,""); print}')
NAMES=()
for f in $FILTERS; do
  # Use Catch2's own filter to find matches, then list-tests filtered.
  matched=$("$BIN" --list-tests "$f" 2>/dev/null | awk '/^  Categorical/ {sub(/^  /,""); print}')
  [ -n "$matched" ] && while IFS= read -r line; do NAMES+=("$line"); done <<< "$matched"
done

# Deduplicate while preserving order.
declare -A SEEN; UNIQ=()
for n in "${NAMES[@]}"; do [ -z "${SEEN[$n]:-}" ] && { SEEN[$n]=1; UNIQ+=("$n"); }; done

for name in "${UNIQ[@]}"; do
  safe=$(printf '%s' "$name" | tr -cd 'a-zA-Z0-9-' | head -c 60)
  log="$LOGDIR/${safe}.log"
  start=$(date +%s); timeout 1800 "$BIN" "$name" > "$log" 2>&1; rc=$?; dur=$(( $(date +%s) - start ))
  # 30-min timeout: cold-cache 120B first-runs take ~5min just to populate
  # weights_cache; never tighten this below 1800s without Phase-0 pre-warm
  # or you will see false TIMEOUT verdicts.
  if   [ $rc -eq 0 ] && grep -q '0 failed' "$log"; then v=PASS
  elif [ $rc -eq 0 ] && grep -q 'SKIPPED' "$log";   then v=SKIPPED
  elif [ $rc -eq 0 ] && grep -q 'mayfail'  "$log";  then v=QUARANTINED-PASS
  elif grep -q '1 failed' "$log" && grep -q 'mayfail' "$log"; then v=QUARANTINED-FAIL
  elif [ $rc -eq 124 ] ; then v="TIMEOUT(30m)"
  elif grep -qiE "acquire lock|lock after|device busy|EBUSY" "$log"; then v="LOCK-CONTENTION"
  elif grep -q "first divergence at position" "$log";              then v="DIVERGE: $(grep -oE '[0-9]+/[0-9]+ positions match' "$log" | head -1)"
  elif grep -qiE "Failed to open file|No such file" "$log";        then v="WEIGHTS-MISSING"
  else v="FAIL(rc=$rc)"; fi
  printf '%-70s  %-18s  (%ds)\n' "$name" "$v" "$dur"
  sleep 15   # let tp4 device locks release between cases
done
```

**Verdict taxonomy (these are distinct report states; collapsing them into
"PASS" is dishonest):**

| Verdict | Meaning | Counts toward correctness gate? |
|---|---|---|
| `PASS` | byte-identical, no skips, no flake quarantine | yes |
| `SKIPPED` | weights not provisioned, test skipped cleanly | NO — INCOMPLETE coverage |
| `QUARANTINED-PASS` | `[!mayfail]` test happened to pass | yes (but note it's a flake-tolerant test) |
| `QUARANTINED-FAIL` | `[!mayfail]`-tagged test failed but is annotated as a known flake with an issue link (none currently active) | NO — does not block ship, but counts as a known-flake hit |
| `DIVERGE` | bit-level mismatch in last-prompt-token logits | **NO — real correctness regression** |
| `LOCK-CONTENTION` | FPGA device teardown lagged; re-run alone | re-run; if still fails alone, escalate |
| `WEIGHTS-MISSING` | canonical AND scratch paths empty — Phase 0 didn't provision | NO — INCOMPLETE; Phase 0 should have caught this |
| `TIMEOUT` | exceeded 30 min — likely cold cache or hang | re-run alone with Phase 0 done; escalate if it recurs |
| `FAIL(rc=…)` | other runtime abort (SIGABRT, SIGSEGV, etc.) | **investigate; not necessarily categorical** |

**Never report "8/N PASS" as "byte-identical drop-in for all models"** —
state which models PASSed, which were SKIPPED, which were QUARANTINED,
and which DIVERGED. Bind the claim to the *specific oracle* used (e.g.,
"byte-identical last-prompt-token logits at 4/32/64-token prompts, C++
generate(max_steps=0) API, per-process isolated").

**Long-prompt cases (mandatory, tagged `[long]`):** the default tags above run
the 4-token oracle (`kLlama3Prompt` / `kGenericPrompt`) which is too short to
expose seq-len-dependent emitter bugs (KV-cache stride, attention chunking,
visit-order). Per model, also run the 32-token and 64-token cases (and the
two realistic-text Llama-3 cases, tagged `[realistic]`):
`gen/t_generate_categorical_fpga_real "[<model-tag>][long]"`. **A `[long]`
DIVERGE on a model whose 4-token case passes is a real correctness bug — do
NOT silence it under decode-nondeterminism caveats below.**

**Test-state contamination across in-process TEST_CASEs:** Catch2 runs all
selected cases in a single process; consecutive cases for the same model can
leave residual FPGA state (KV-cache page bindings, worker assignment, scratch
HBM) that flips a later case's logits even though each case passes in
isolation. **Always invoke `[long]` cases ONE PER PROCESS** (the isolated
sweep runner above filters by Catch2 test name per invocation). If you see a
DIVERGE on a long-prompt case, re-run it alone (`gen/t_generate_categorical_fpga_real
"Categorical $MODEL matches legacy on N-token prompt"`) before treating it
as an emitter bug; an in-isolation PASS means the bug is in test-process
state, not the categorical pipeline.

**Realistic-prompt coverage is currently Llama-3-only:** `kRealLlama3Prompt42`
and `kRealLlama3Prompt64` are pre-tokenized real English text via
Llama-3.2-1B-Instruct's tokenizer. Synthetic incrementing IDs (`kMed*Prompt`,
`kLong*Prompt`) suffice for tokenizer-independent gates but may miss
content-sensitive emitter bugs. Adding realistic tokenizations for other
model families (Phi-4, Mixtral, GPT-OSS, Qwen, Chinese-Alpaca) would close
this gap; until then, those models' `[long]` cases are *seq-len* coverage
only, not *content-sensitivity* coverage.

**Plain vs Permuted plugin variants are distinct code paths.** `TargetExecutor.hs`
defines `Plain | Permuted`; `HypergraphToLoopy.hypergraphToLoopy` takes
`TargetExecutor` and only emits `Load2` + `BackPermute` when `isPermuted` returns
true. The slugs `categorical-<m>` / `ingested-<m>` use Plain; the slugs
`categorical-<m>-permuted` / `ingested-<m>-permuted` use Permuted. **A fix to
one rarely fixes the other.** When triaging a categorical-vs-legacy DIFF, first
verify from `config/models.yaml` which variant the failing slug is — do not
infer.

**Slug verification:** the Phase-3 test file pins exact slug pairs in
`t/t_generate_categorical_fpga_real.cpp` (the `ModelPair{categorical, ingested,
…}` literals). These are the authoritative byte-identity pairs; verify against
`config/models.yaml` if you add new test cases.

**Weights-path verification:** when adding new test cases, **copy the
`kWeightsRoot / "..."` literal verbatim from the existing TEST_CASE for the
same model**. Do NOT guess from `config/models.yaml` or HuggingFace org/repo
naming — some models live under repo-scoped subpaths (e.g., GPT-OSS-20B's
ingest-prepared GPTQ weights live at
`positron-ai/openai--gpt-oss-20b-ingest-best-gptq`, NOT `openai/gpt-oss-20b`).
A mismatched path manifests as `unable to load tensor: model.layers.0.mlp.experts.0.gate_up_proj`
or similar tensor-by-name load failure that LOOKS like a categorical lowering
bug but is actually a wrong-weights-directory mistake.

**GPT-OSS determinism caveat:** GPT-OSS on tp4 has documented run-to-run nondeterminism in
*multi-token decode*. The last-token-logit gate is deterministic under `pay_for_determinism`,
but if a GPT-OSS case looks flaky, run it **3×** and require it to pass every time before
calling it.

**Chinese-Alpaca-2-7B weights:** Phase 0's auto-download handles this —
`hfl/chinese-alpaca-2-7b` ships only PyTorch `.bin`, but `bin/get_model`
converts to safetensors and stages at `/tmp/retest_weights/hfl/chinese-alpaca-2-7b`,
and `resolve_weights()` picks up the scratch path automatically. **Do NOT edit
`kWeightsRoot` literals or `ModelDef.weights` to point at scratch** — that
ritual is obsolete and was a real source of "forgot to revert" mistakes.

**Mixtral dependency:** Mixtral byte-identity requires the legacy shared-RMSNorm fold
(`foldSharedRmsNormMul`, PR #2810) in this branch's base. If Mixtral diverges by ~1 ULP
across ~95% of logits, confirm that fix is present before treating it as a categorical bug.

**Known flakes ledger** (do not count as categorical regressions):

| Test | Failure | Tag | Issue | Notes |
|---|---|---|---|---|
| Mixtral `[long]` (synthetic prompt) | SIGABRT on `TRON_ASSERT(other_sp.m_star != -inf)` | RESOLVED — no longer quarantined | #2808 | **Root-caused, not a race.** Synthetic incrementing-id prompts (`kMed/kLongGenericPrompt` = {1..N}) drive Mixtral's MoE expert FFN to overflow at a deep layer → NaN via `residual_and_rmsnorm` (`wide.hpp:1278`: inf→ `1/√inf=0` → `inf*0=NaN`). The NaN query then makes `max(s_page)` (`self_attention.hpp:853`) launder NaN→−inf via x86 `_mm512_max_ps(data,−inf)` (returns 2nd operand on NaN), so the crash misreports as "−inf in a valid scratchpad" (sails past the NaN guard at :886). Deterministic, input-driven, NOT parallelism-dependent, NOT caused by PR #2685. **Fix in /retest: Mixtral tests use realistic Mixtral tokenizations (`kMixtralReal32/64`), which keep activations bounded — both pass byte-identical.** A real prompt never triggers it; the underlying runtime overflow→NaN hardening is a separate #2808 task (out of /retest scope). **Never use kMed/kLongGenericPrompt for an MoE model.** |
| GPT-OSS-20B/120B Phase-5 TOKID at tp4 | Token-ID stream diverges run-to-run | (advisory in retest.md) | (documented in `memory/fpga_120b_nondeterminism.md`) | Multi-token-decode nondeterminism at TP4; affects both pipelines identically — confounder, not categorical regression. |

**Adding a new entry:** include exact test name (or tag), failure signature
(error message regexp), issue link, and **removal condition** (when the
quarantine should lift). Do NOT add `[!mayfail]` without an issue link.

**Gate:** every supported model must hit `PASS` (or `QUARANTINED-PASS`) on
its 4-token AND `[long]` cases. `DIVERGE` is a failure. `SKIPPED` /
`WEIGHTS-MISSING` / `TIMEOUT` are INCOMPLETE — Phase 0 should have
provisioned. `QUARANTINED-FAIL` does not block ship but is reported as
such. See the verdict taxonomy above.

# Phase 4 — Semantic logit parity (host) — skip if `--no-semantic`

```
nix develop --command bash -c 'bin/ci/categorical_logit_matrix.sh'
```
Dumps a fresh categorical `.py` per model and runs `categorical_logit_test.py --strict-top1`
against the HuggingFace reference (allclose + top-1). This is the complementary *semantic*
gate to Phase 3's *byte-level* gate. (The matrix historically excludes MoE models; extend
only with verified MoE semantic coverage.)

# Phase 5 — Performance divergence pass — skip if `--no-perf`

For each model in `MODELS`, generate tokens through **both** the categorical and the
legacy-ingest plugin on the same executor and compare. This also doubles as a
**generated-token-ID parity** check: under `--temperature 0` the two pipelines should emit
the same token sequence.

**Important: Phase 3 and Phase 5 exercise different code paths.**
- **Phase 3** uses the in-process C++ API
  `generate(model, prompt, /*max_steps=*/0, opts)` with `LogitsMode::LAST`.
  Only the prompt-processing path runs; no decode-loop launch, no KV-cache
  allocation, no sampling. Cross-pipeline divergence here is an
  emitter/lowering bug.
- **Phase 5** uses the external `runtron stream-generate-text --length N`
  CLI, which launches the full decode loop, allocates KV cache, runs
  sampling, and writes generated tokens. A divergence visible in Phase 5
  but NOT Phase 3 indicates divergence in the decode-loop launch or in
  runtron's host harness — not in the emitter.

**TOKID failure taxonomy (apply BEFORE deciding pass/fail):**
1. **Phase 3 (long-prompt) PASS + Phase 5 TOKID DIFF on a GPT-OSS tp4 model**:
   advisory — matches the documented GPT-OSS TP4 multi-token decode
   nondeterminism (affects both pipelines equally). Mark as ADVISORY, not FAIL.
2. **Phase 3 (long-prompt) PASS + Phase 5 TOKID DIFF on any other model**:
   not an emitter bug — Phase 3 proves the prompt-processing path is
   byte-identical. The DIFF is in decode-loop launch / KV-cache layout /
   runtron host harness. Root-cause that path separately; do not block
   `/retest` on it.
3. **Phase 3 (long-prompt) DIFF**: real emitter bug. Phase 5 TOKID will
   also DIFF. Phase 3 is the gating signal; do not silence it under any
   Phase-5 caveat.

**HBM-exhaustion decision tree** (`runtron` log shows `ERROR allocating HBM
space ... DEBUG EXIT`):
- Error reports `start_addr 0x48000000` (≈1.125 GB into the card's HBM) →
  **instance-budget overflow**: the current `SYSTEM_CONFIG --instance i,n`
  gives this process only `1/n` of the machine's HBM, and the model exceeds
  that share (most often GPT-OSS-120B at tp4). Fix: rerun with
  `SYSTEM_CONFIG="--instance 0,1"` (whole machine).
- Error reports a large `start_addr` (multi-GB) **and persists across multiple
  fresh runtron processes after waiting** → genuine stale FPGA HBM state.
  Needs operator-level reset; not actionable from within `/retest`.

Fixed methodology (do not vary, or numbers aren't comparable):
- **Plugin slugs:** categorical = `categorical-$MODEL[-standalone][-tpN]`; legacy =
  `ingested-$MODEL[-tpN]` (confirm exact slugs in `config/models.yaml`). Same executor
  per side as the Phase-3 matrix.
- **Run:** 64-token prompt, 32-token greedy decode, `--temperature 0 --pay-for-determinism
  --seed 42`, via `runtron` (confirm flags with `runtron --help`; do **not** invent flags).
- **Instance config:** export `SYSTEM_CONFIG="--instance 0,1"` for every Phase-5 launch.
  `--instance 0,1` hands the whole machine to one process; `--instance 0,4` (the Phase-3
  ctest fallback) carves it into quarters and gives each instance only ~1/4 of the per-
  card HBM — enough for Mixtral/GPT-OSS-20B/Qwen-32B but **not for GPT-OSS-120B's tp4
  full-decode KV cache**, which DEBUG-EXITs at `start_addr 0x48000000`. Phase 3 dodges
  this because `LogitsMode::LAST` skips decode allocation entirely.
- **Trials:** 3 per side; report the **median**. Run legacy×3 then categorical×3.
- **Cold cache:** before *every* trial, attempt
  `sudo -n bash -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`. **First check `sudo -n true`**
  — if passwordless sudo is unavailable, DO NOT block on a password: skip cache-clearing,
  compare only gen-time/tok-s (not wall), and label wall-time **NON-COMPARABLE**. (Warm
  cache once produced a fake −18% wall "win" on GPT-OSS-120B that vanished cold.)
- **Metrics:** per model report Δ gen-time (ms/tok), Δ tok/s, Δ wall vs legacy
  (negative = categorical faster), and whether the 32 generated token-IDs matched.
- **Regression threshold:** flag any model whose **median gen-time/tok is >5% slower** than
  legacy (outside trial-to-trial noise). Watch-item: dense Llama-3.1-8B (eq-sat decode-loop
  per-token regression, wall breaks even); MoE (Mixtral) has historically been a win.

**Token-ID extraction (robust):** parse `--log-intermediates` output and diff
the `Logits of token N` lines pairwise (legacy vs categorical). ANSI green-text
span extraction (`\e\[38;2;000;128;000m([^\e]*)`) is fragile across runtron
versions and interleaved log lines, and yields decoded *text* rather than IDs;
the intermediates-file path is durable and produces vocab-sized vectors that
can be compared bit-for-bit. (For non-tp4 dense models the first divergent
`Logits of token N` row IS the first divergent decode position.)

If no clean `runtron` per-plugin harness is available, this is the spec to implement; do
not substitute an incomparable ad-hoc measurement.

# Final report

One consolidated summary. **Use the verdict taxonomy from Phase 3 — do NOT
collapse SKIPPED / QUARANTINED into PASS.**

- **Build:** clean / errors.
- **Unit tests:** Haskell N/N, C++ host pass/fail.
- **Phase 0:** weights provisioned (canonical / scratch / DOWNLOAD-FAILED per model);
  cache pre-warmed (yes / no per model).
- **Phase 3 byte-identity matrix:** for each `(model, prompt-length)` pair, report
  `PASS / SKIPPED / QUARANTINED-PASS / QUARANTINED-FAIL / DIVERGE / WEIGHTS-MISSING / TIMEOUT / FAIL`
  with executor and runtime.
- **Semantic gate (Phase 4):** per model allclose + top-1.
- **Perf (Phase 5):** per model Δ gen-time/tok-s/wall + TOKID match (apply the
  Phase-3-vs-Phase-5 code-path distinction above).
- **Overall verdict:** one of
  - `BYTE-EXACT DROP-IN` — every measured row PASS / QUARANTINED-PASS, no
    DIVERGE anywhere, no >5% perf regression. Quote the bound: *"byte-identical
    last-prompt-token logits on C++ generate(max_steps=0), across 4/32/64-token
    prompts + Llama-3 realistic, for {list of models that actually PASSed},
    per-process isolated execution."*
  - `INCOMPLETE` — SKIPPED / WEIGHTS-MISSING / TIMEOUT in any non-quarantined row.
  - `REGRESSION` — at least one DIVERGE or >5% perf slowdown.

**Anti-patterns to avoid in the final report** (call these out if you catch
yourself or a future session doing them):
- Reporting "8/8 byte-identical" when 2 were SKIPPED for missing weights and
  1 is in `[!mayfail]` quarantine — that's actually 5/8 + 2 SKIPPED + 1
  QUARANTINED-PASS.
- Claiming "byte-for-byte drop-in" from a single oracle (4-token kLlama3Prompt
  at last-prompt position). The right claim cites the prompt set AND the
  code path (C++ `generate(max_steps=0)`, NOT runtron decode loop).
- Treating Phase-5 TOKID DIFF as a Phase-3 regression. They're different
  code paths; Phase 3 governs emitter byte-identity.
- Treating a single in-process Catch2 failure as a categorical bug without
  the mandatory **rerun-by-exact-name in a fresh process** check.
- Silencing a real DIVERGE because "the test is flaky" — add to the
  quarantine ledger above with an issue link, or root-cause it.

Root-cause anything that genuinely diverged or regressed (file:line-level), rather than
papering over it.
