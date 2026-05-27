---
description: Full categorical-vs-legacy retest — rebuild, unit tests, FPGA byte-identity for every model, and a perf-divergence pass
argument-hint: "[model-tag…] [--no-perf] [--no-semantic]"
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
- `/opt/positron/weights/...` is **read-only**; provision missing weights to a scratch dir.
- Launch long builds/sweeps in the background and let the harness notify you; do not poll.

# Phase 1 — Rebuild

```
nix develop --command bash -c 'make build-categorical -j 4'
```
Regenerates every categorical + legacy plugin (`gen/.../plugins/*.hpp`), builds the C++
runtime/plugins, and `gen/t_generate_categorical_fpga_real`. Require exit 0 and zero
`error:` lines. If `gen/` is corrupt from a prior non-Nix `make`, `rm -rf gen` and rebuild.

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

**Run each model in its own process** (one Catch2 tag per invocation). Running the whole
binary at once lets a hardware `DEBUG EXIT` / HBM abort in one model terminate the process
and silently truncate the rest of the sweep; per-case isolation contains that.

Model → executor matrix:

| Tag | Exec | Layers | | Tag | Exec | Layers |
|-----|------|--------|-|-----|------|--------|
| `[llama-3.2-1b]` | tp1 | 16 | | `[mixtral-8x7b]` | tp4 | 32 |
| `[llama-3.1-8b]` | tp1 | 32 | | `[gpt-oss-20b]` | tp4 | 24 |
| `[phi-4]` | tp1 | 40 | | `[gpt-oss-120b]` | tp4 | 36 |
| `[chinese-alpaca-2-7b]` | tp1 | 32 | | `[qwen-2.5-32b]` | tp4 | 64 |

Isolated sweep runner — pass the tag subset as args (defaults to all eight):

```bash
#!/usr/bin/env bash
set -uo pipefail
BIN=./gen/t_generate_categorical_fpga_real
LOGDIR=/tmp/retest_fpga_logs; mkdir -p "$LOGDIR"
[ -x "$BIN" ] || { echo "FATAL: $BIN missing — run Phase 1 first"; exit 2; }
"$BIN" --list-tests >/dev/null 2>&1 || { echo "FATAL: test binary won't list tests"; exit 2; }
if [ "$#" -gt 0 ]; then TAGS="$*"; else
  TAGS="[llama-3.2-1b] [llama-3.1-8b] [phi-4] [chinese-alpaca-2-7b] [mixtral-8x7b] [gpt-oss-20b] [gpt-oss-120b] [qwen-2.5-32b]"
fi
for tag in $TAGS; do
  safe=$(printf '%s' "$tag" | tr -cd 'a-z0-9-'); log="$LOGDIR/$safe.log"
  start=$(date +%s); timeout 1800 "$BIN" "$tag" > "$log" 2>&1; rc=$?; dur=$(( $(date +%s) - start ))
  if   [ $rc -eq 0 ]; then v=PASS
  elif [ $rc -eq 124 ]; then v="TIMEOUT(30m)"
  elif grep -qiE "acquire lock|lock after|device busy|EBUSY" "$log"; then v="LOCK-CONTENTION (re-run alone)"
  elif grep -q "first divergence at position" "$log"; then v="DIVERGE: $(grep -oE '[0-9]+/[0-9]+ positions match' "$log" | head -1)"
  elif grep -qiE "Failed to open file|No such file|not in cache.*loading.*\.safetensors" "$log"; then v="WEIGHTS-MISSING"
  else v="FAIL(rc=$rc)"; fi
  echo "  $v | $tag (${dur}s)"
  sleep 15   # let tp4 device locks release between cases
done
```

Interpret the four outcomes:
1. **DIVERGE** — a genuine numerical mismatch. Triage by diffing the generated plugins
   (`gen/.../plugins/categorical_<m>.hpp` vs `ingested_<m>.hpp`): compare the `tron::` op
   multiset and kernel bodies; the cause is a kernel/op/normalization/dtype difference.
2. **LOCK-CONTENTION** — not a correctness failure; a prior tp4 case's device teardown
   lagged. **Re-run that model alone** with the FPGA idle and trust that result.
3. **WEIGHTS-MISSING** — see the Chinese-Alpaca note; until provisioned, that model is
   **INCOMPLETE**, which makes the overall verdict INCOMPLETE (not PASS).
4. **TIMEOUT** — investigate a hang (rare); re-run alone.

**GPT-OSS determinism caveat:** GPT-OSS on tp4 has documented run-to-run nondeterminism in
*multi-token decode*. The last-token-logit gate is deterministic under `pay_for_determinism`,
but if a GPT-OSS case looks flaky, run it **3×** and require it to pass every time before
calling it.

**Chinese-Alpaca-2-7B weights:** the HF repo `hfl/chinese-alpaca-2-7b` ships only PyTorch
`.bin`. If its safetensors are absent under `/opt/positron/...`, download + convert:
```
nix develop --command bash -c 'python3 bin/get_model hfl/chinese-alpaca-2-7b --to /tmp/caweights'
```
Then, **only for this run**, edit the Chinese-Alpaca `ModelPair` weights path in
`t/t_generate_categorical_fpga_real.cpp` (the `kWeightsRoot / "hfl/chinese-alpaca-2-7b"`
literal in its `TEST_CASE`) to `/tmp/caweights/hfl/chinese-alpaca-2-7b`, rebuild the test
binary, run `[chinese-alpaca-2-7b]`, then **revert the edit** (the committed test must keep
the canonical path). If you don't provision the weights, mark this model INCOMPLETE.

**Mixtral dependency:** Mixtral byte-identity requires the legacy shared-RMSNorm fold
(`foldSharedRmsNormMul`, PR #2810) in this branch's base. If Mixtral diverges by ~1 ULP
across ~95% of logits, confirm that fix is present before treating it as a categorical bug.

**Gate:** every supported model must be byte-identical; any DIVERGE = fail, any
WEIGHTS-MISSING/TIMEOUT = INCOMPLETE.

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
**generated-token-ID parity** check: under `--temperature 0` the two pipelines must emit
the **identical token sequence** (a stricter, decode-level companion to Phase 3).

Fixed methodology (do not vary, or numbers aren't comparable):
- **Plugin slugs:** categorical = `categorical-<model>[-standalone][-tpN]`; legacy =
  `ingested-<model>[-tpN]` (confirm exact slugs in `config/models.yaml`). Same executor
  per side as the Phase-3 matrix.
- **Run:** 64-token prompt, 32-token greedy decode, `--temperature 0 --pay-for-determinism
  --seed 42`, via `runtron` (confirm flags with `runtron --help`; do **not** invent flags).
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

If no clean `runtron` per-plugin harness is available, this is the spec to implement; do
not substitute an incomparable ad-hoc measurement.

# Final report

One consolidated summary:
- **Build:** clean / errors.
- **Unit tests:** Haskell N/N, C++ host pass/fail.
- **Byte-identity matrix:** per model PASS / DIVERGE(x/y) / LOCK / WEIGHTS-MISSING / TIMEOUT + executor.
- **Semantic gate:** per model allclose + top-1.
- **Perf:** per model Δ gen-time/tok-s/wall + token-ID match; overall within-noise / regression / win.
- **Overall verdict:** PASS (byte-exact, non-regressing drop-in) / FAIL (a real DIVERGE or
  >5% regression) / INCOMPLETE (missing weights, timeout, or NON-COMPARABLE perf).

Root-cause anything that genuinely diverged or regressed (file:line-level), rather than
papering over it.
