# Ponytail integration study and architecture

- Status: store-backed proportional static integration in progress; runtime design is optional
- Reference checkout: `/Users/johnw/Desktop/ponytail`
- Upstream: `https://github.com/DietrichGebert/ponytail`
- Reviewed revision: `16f29800fd2681bdf24f3eb4ccffe38be3baec6b`
- Declared version: `4.8.4`

> **Scope correction (2026-07-16):** `WIGGUM-SCOPE.md` supersedes every
> lifecycle, mode-persistence, plugin-runtime, transaction, rollback, recovery,
> and fleet-rollout requirement in this document. The current endpoint is the
> pinned six-skill family on native skill targets plus six GPTel prompt
> projections. Runtime sections remain below only as historical optional design
> material and are not completion gates.

## Decision

Ponytail is integrated as a named, allowlisted external bundle. During
development, the raw CLI may bind that logical bundle explicitly to the
Desktop checkout. Production, normal flake apps, and Home Manager activation
use one composed deployment derivation: this repository is copied to its root,
the pinned non-flake Ponytail input is copied to `sources/ponytail`, and the
binding descriptor names that in-tree store path.

The promptdeploy repository will contain the adapter manifest and the expected
upstream provenance, not a machine-local symlink, submodule, or hand-maintained
copy of the third-party payload.

`packages.deployment` is the inspectable store source. `apps.default` runs
`deploy` with its `deploy.yaml` and binding descriptor explicitly selected;
`apps.promptdeploy` exposes the other commands against the same tree. The raw
package/app remains separate for intentional mutable development. An explicit
config path is used instead of changing directory, so caller-relative output
paths retain their meaning.

This is the smallest design that preserves all of these properties at once:

- one reviewed Ponytail source for local and fleet deployment;
- the complete six-skill family, including future files inside each selected
  skill tree;
- target-specific adapters instead of false cross-client parity claims;
- no network fetch or dependency installation during Home Manager activation;
- source confinement, collision detection, manifest ownership, strict
  verification, and safe removal;
- explicit provenance and a repeatable pin update.

## What Ponytail contains

The inspected checkout has 156 meaningful non-`.git` files. It is not one
prompt or one skill: the `skills/` directory is the portable behavior, while
the rest is a mix of host adapters, runtime support, evidence, and publishing
material.

| Family | Files | Runtime classification |
|---|---:|---|
| Root files | 13 | Compact `AGENTS.md` rule, manifests, package metadata, license, and host runtime entry points |
| Hidden host adapters | 32 | Claude, Codex, Copilot, Cursor, Devin, Gemini/Antigravity, Kiro, OpenCode, Qoder, and Windsurf metadata/rules |
| `skills/` | 6 | Canonical portable capability family |
| `commands/` | 6 | Gemini-style TOML command adapters, not canonical behavior |
| `hooks/` | 11 | Shared Node mode/config/instruction/state runtime, event maps, and statusline scripts |
| `pi-extension/` | 4 | Native Pi adapter and tests |
| `ponytail-mcp/` | 5 | Optional user-invoked prompt/tool bridge, not always-on parity |
| `scripts/` | 5 | Consistency, generation, publication, and uninstall tooling |
| `docs/`, `examples/`, `assets/` | 25 | Reference, examples, and branding; not hidden runtime context |
| `benchmarks/` | 35 | Evaluation harnesses and historical results; never production payload |
| `tests/` | 14 | Upstream compatibility and parity tests; never production payload |

There are no custom subagent definitions. `.agents/` is a rule/marketplace
namespace. `ponytail-subagent.js` propagates instructions into host-created
subagents but does not define an agent roster.

### Canonical six-skill family

| Skill | Contract |
|---|---|
| `ponytail` | Persistent lazy-senior coding mode: understand the real flow, then prefer YAGNI, existing code, stdlib, native platform features, installed dependencies, and the smallest correct change. Public levels are `lite`, `full`, `ultra`, and `off`; `full` is the default. |
| `ponytail-review` | Read-only diff review for over-engineering; reports what to delete or replace and does not apply fixes. |
| `ponytail-audit` | Read-only whole-repository over-engineering audit, ranked by the largest removable complexity. |
| `ponytail-debt` | Read-only harvest of `ponytail:` ceiling/upgrade markers into a ledger. |
| `ponytail-gain` | One-shot benchmark card; does not alter mode or repository state. |
| `ponytail-help` | One-shot mode, command, configuration, and update reference. |

All six are currently single-file skills, but promptdeploy must import and hash
each complete directory. That preserves future references, scripts, assets,
empty directories, executable modes, and confined file links without changing
the bundle schema.

The load-bearing main-skill rules must remain verbatim: trace the real flow
before minimizing; reuse the existing codebase before adding code; fix shared
root causes; preserve validation, data-loss protection, security,
accessibility, and real-hardware calibration; leave one runnable check for
non-trivial logic; and mark deliberate ceilings with a `ponytail:` upgrade
trigger.

### Always-on and mode behavior

`AGENTS.md` is the compact instruction-only fallback. It retains the ladder,
root-cause rule, safety boundaries, and runnable-check rule, but it does not
provide mode switching, state, command cards, or subagent scoping.

The full runtime uses `hooks/ponytail-instructions.js` to read the canonical
main skill relative to the shared `hooks/` and `skills/` layout. The lifecycle
map installs:

- `SessionStart` → `ponytail-activate.js`;
- `UserPromptSubmit` → `ponytail-mode-tracker.js`;
- `SubagentStart` → `ponytail-subagent.js`.

The shared code resolves `PONYTAIL_DEFAULT_MODE`, XDG/Windows config paths,
exact deactivation phrases, optional subagent matching, state, and each host's
output format. Node must be present on the non-interactive hook path. Static
skills remain usable when it is absent, but a target claiming the full tier
must report that missing capability rather than silently claiming success.

Two upstream behaviors are deliberately corrected by versioned adapter
transforms. `ponytail-instructions.js` contains an embedded fallback copy of
the main rules; the managed transform removes that fallback and makes an
unreadable selected `skills/ponytail/SKILL.md` fail the rendered-snapshot
conformance probe.
`ponytail-mode-tracker.js` persists the internal `review` mode; the managed
transform recognizes the one-shot review invocation without changing the
ambient `lite`/`full`/`ultra`/`off` state. Both transforms require the exact
reviewed input digest and have semantic golden tests, so a pin update cannot
silently carry the patch onto changed upstream code.

Malformed client events retain upstream's fail-open/no-session-block behavior.
That recovery is distinct from source or capability conformance. The dormant
snapshot probe executes the selected instruction read, state write/read,
event-output, and relative-module paths only against private copies of the
pinned retained artifact. It does not inspect deployed paths, registration,
target Node, trust state, or reachability. The future installed-target
transaction must repeat those checks through the target's real execution path
and fail if any required capability is unavailable. The snapshot sequence
checks default subagent injection; separate runtime goldens cover matcher
match, mismatch, invalid-regex, and malformed-input behavior.

## Target and fleet mapping

The current `deploy.yaml` describes 21 targets: eight Claude, four Codex,
seven OpenCode, one Factory Droid, and one GPTel. `RemoteTarget` is transport,
not a sixth semantic client; it must push the same complete target-specific
artifact set that a local target receives.

| Target | Current endpoint | Honest limitation |
|---|---|---|
| Claude Code | Six complete skills through the existing skill-tree target path. | No lifecycle hooks or persistent Ponytail mode are installed. |
| Codex CLI/Desktop/IDE | Six complete skills through the existing skill-tree target path. | No hooks, trust changes, or persistent Ponytail mode are installed. |
| OpenCode | Six complete skills through the existing skill-tree target path. | No plugin registration, commands, hooks, or persistent mode are installed. |
| Factory Droid | Six complete skills through the existing skill-tree target path. | No proven always-on hook/instruction surface; claim callable skill tier only. |
| GPTel | Six named, target-aware preset projections derived from the six skills. Each projection keeps the substantive task/rules while replacing host-specific activation, persistence, slash-command, subagent, and update claims with an explicit one-invocation preset contract. | Prompt presets are not native skills, lifecycle hooks, commands, or persistent modes. |

Claude, Codex, Droid, and OpenCode select `bundle:ponytail` plus the six
`skill:*` items. GPTel selects the bundle plus six `prompt:*` projections. The
active bundle materializes only the owned MIT notice/provenance support tree;
the captured runtime payloads remain dormant. This same mapping is applied by
local targets and by each remote target's ordinary transport wrapper.

The optional MCP package is not part of the minimum endpoint. Upstream states
that it is user-invoked rather than always-on, and its uninstalled SDK/Zod
dependencies require separate reproducible packaging. It may be added later as
an explicitly weaker bridge, never as evidence of lifecycle parity.

GPTel uses the versioned `gptel-preset-v1` transform, not raw frontmatter
stripping. For the main skill it preserves the coding ladder, boundaries,
output discipline, and full-intensity rules while replacing persistence and
mode-switch sections with a one-invocation preset statement. Review, audit,
debt, and gain retain their substantive one-shot tasks with a host-neutral
invocation header. Help becomes an accurate catalog of the six installed GPTel
presets and explicitly states that lifecycle activation, slash commands,
subagent propagation, persistent modes, plugin configuration, and plugin
updates are unavailable. Semantic goldens assert those retained and forbidden
claims; byte equality with a stripped `SKILL.md` is intentionally not an
acceptance rule.

## Source-reference alternatives

| Model | Decision |
|---|---|
| Absolute Desktop path | Development override only. It is mutable and absent on other hosts. |
| External symlink forest | Rejected. It violates current source boundaries and fails in immutable/remote deployments. |
| Git submodule | Rejected as canonical. Flake sources omit submodule contents unless every consumer opts in, and an adapter manifest is still required. |
| Vendored or generated committed copy | Rejected as canonical. It duplicates ownership, imports irrelevant project material, and invites silent drift. |
| Client marketplaces/packages only | Optional supplement. They are network/trust/client-state dependent and cannot prove a common fleet pin. |
| Named bundle manifest + copied store deployment mapping | Accepted control plane. The flake maps each external input into `packages.deployment`; the manifest maps its selected files to agent surfaces. |
| Named bundle + pinned non-flake Nix input | Accepted production binding. |

## Bundle model

The committed `bundles/ponytail.yaml` manifest is a closed schema 2 document.
It names only logical source paths, exact directory inventories, selected
exports, and transformed payload digests. Its abridged shape is:

```yaml
schema: 2
name: ponytail
revision: 16f29800fd2681bdf24f3eb4ccffe38be3baec6b
version: {value: "4.8.4", file: package.json, key: version}
license:
  spdx: MIT
  file: LICENSE
  sha256: sha256:fb1bc6909ac3ef82d5c22106e32ef682b0cff66788fa915fb9b53b15c9d2f3ab

exports:
  - type: skill
    name: ponytail
    path: skills/ponytail
    tree_sha256: sha256:c8a4e819082fc6fe7eed764e8114e7cbc2b259dba7293b63e53e1aaa7f0682e6
    skill_md_sha256: sha256:1316a2f3f95741d2300b116fe0c2d81ce4a9568656ed0a62643f54aaf09957f2
    target_types: [claude, codex, droid, opencode]
    projections:
      - {type: prompt, name: ponytail, target_types: [gptel], transform: gptel-preset-v1}
  # Five more exact skill/projection rows, in canonical Ponytail order.

runtime:
  inventory:
    hooks:
      - claude-codex-hooks.json
      - copilot-hooks.json
      - ponytail-activate.js
      - ponytail-config.js
      - ponytail-instructions.js
      - ponytail-mode-tracker.js
      - ponytail-runtime.js
      - ponytail-statusline.ps1
      - ponytail-statusline.sh
      - ponytail-subagent.js
      - qoder-hooks.json
    .opencode: [command, plugins]
    .opencode/command: [ponytail-audit.md, ponytail-debt.md, ponytail-gain.md, ponytail-help.md, ponytail-review.md, ponytail.md]
    .opencode/plugins: [ponytail-frontmatter.cjs, ponytail.mjs]
    skills: [ponytail, ponytail-audit, ponytail-debt, ponytail-gain, ponytail-help, ponytail-review]
  payloads:
    - name: claude-codex-runtime-v1
      target_types: [claude, codex]
      tree_sha256: sha256:a2f4bbac93ba0359f7325621b1a7c7fb049c5b1244c21d9c0c37a89b47bc9894
      transforms:
        hooks/ponytail-instructions.js: strict-canonical-instructions-v1
        hooks/ponytail-mode-tracker.js: one-shot-review-v1
      include:
        - hooks/claude-codex-hooks.json
        - hooks/ponytail-activate.js
        - hooks/ponytail-config.js
        - hooks/ponytail-instructions.js
        - hooks/ponytail-mode-tracker.js
        - hooks/ponytail-runtime.js
        - hooks/ponytail-statusline.sh
        - hooks/ponytail-statusline.ps1
        - hooks/ponytail-subagent.js
        - skills/ponytail/SKILL.md
    - name: opencode-plugin-v1
      target_types: [opencode]
      tree_sha256: sha256:70becde0867bbe3f293b28a56744e60950c62b8758cf837dfeb82f780d29a15b
      transforms:
        hooks/ponytail-instructions.js: strict-canonical-instructions-v1
      include:
        - .opencode/command/ponytail.md
        - .opencode/command/ponytail-review.md
        - .opencode/command/ponytail-audit.md
        - .opencode/command/ponytail-debt.md
        - .opencode/command/ponytail-gain.md
        - .opencode/command/ponytail-help.md
        - .opencode/plugins/ponytail.mjs
        - .opencode/plugins/ponytail-frontmatter.cjs
        - hooks/ponytail-config.js
        - hooks/ponytail-instructions.js
        - skills/ponytail
        - skills/ponytail-review
        - skills/ponytail-audit
        - skills/ponytail-debt
        - skills/ponytail-gain
        - skills/ponytail-help
```

Schema 2 capture is deliberately dormant at the current work-unit boundary:
the two immutable payload snapshots are retained on `bundle:ponytail`, but
deploy/status/verify still materialize and compare only the existing LICENSE
support tree. Native runtime installation and registration begin only after
the target transaction and remote live-path slice lands. The pure renderer is
also present. Deployment manifest v3 now has a closed, optional local-Claude
runtime receipt, while v1/v2 entries migrate with no invented receipt. Active
deploy/status/verify paths do not emit that receipt or substitute the
target-effective candidate hash until the transaction boundary lands.

Every include path must be canonical and relative. Discovery must reject
absolute paths, `.`/`..`, broken or external links, directory links, special
files, missing expected version/license files, duplicate names, and any
unlisted new runtime file. `.git`, `.env`, `node_modules`, benchmarks, tests,
publishing scripts, and marketing assets can never enter the payload by
implicit recursion from the repository root.

Directory recursion is allowed only for one of the six selected canonical
skill trees, where future auxiliary files are part of the contract. Adapter
directories such as `.opencode/command` and `hooks` are enumerated file by file.
Every selected Ponytail item depends on the owned `bundle:ponytail`
support artifact, installed under the target's hidden promptdeploy bundle
area. It is verified and retained while any Ponytail item remains, including
skill-only Droid and prompt-only GPTel deployments.

Imported tree digests frame directory entries (including empty directories),
node kinds, canonical relative paths, normalized permission/execute modes, and
file bytes. A mode-only or empty-directory change therefore changes provenance
and cannot hide behind an unchanged file-content hash.

Each emitted item will carry bundle name, revision, logical relative path,
and mutable/immutable source status. Those values participate in diagnostics,
hashing, and manifest provenance. `(item_type, name)` remains the ownership key
and must be globally unique across the primary source and every bundle.

### Bindings

The development interface is explicit and visibly mutable:

```text
promptdeploy \
  --bundle-source ponytail=/Users/johnw/Desktop/ponytail \
  validate
```

The production flake input is non-flake and pinned. The packaged executable
defaults `PROMPTDEPLOY_BUNDLE_BINDINGS_FILE` to a generated descriptor for that
immutable store path and exposes the source, revision, NAR hash, version, and
descriptor through package passthru. Home Manager uses the same packaged
executable. Neither path fetches, runs npm, or mutates a plugin manager.

## Managed runtime design (historical optional follow-up)

Executable runtime code is part of the first-class, hashed
`bundle:ponytail` item rather than an incidental hook side effect. A target
stages a complete allowlisted tree under a managed, content-addressed
directory, verifies it, then switches hook/plugin configuration to that exact
digest. Only after the new configuration verifies may an unreferenced old
digest be removed. Failure leaves either the complete old state or the
complete new state—never hooks pointing at a partial tree. On Droid and GPTel,
the same bundle item owns only the license/provenance support tree.

Claude and Codex hook commands are rendered from the upstream event map. They
must not retain `${CLAUDE_PLUGIN_ROOT}` outside a plugin host. Commands point at
the managed runtime; Codex commands additionally set a writable managed
`PLUGIN_DATA` path. POSIX and PowerShell forms, timeouts, matchers, status
messages, BOM/CRLF handling, and the non-closing-stdin fail-safe are preserved.
The rendered instruction builder and mode tracker apply the two digest-guarded
corrections above. The dormant rendered-snapshot probe proves canonical
instruction reads and that `ponytail-review` leaves its seeded `full` mode
unchanged in a private local copy; installed-target verification remains part
of the future transaction layer. Broader semantic goldens cover every prior
ambient mode.

OpenCode receives the native relative layout and one managed plugin path. The
target's remote rsync includes must cover the runtime directory. Removing the
bundle removes only promptdeploy-owned configuration/runtime paths and retains
the user's mode/default state unless an explicit purge is requested.

The implemented pure renderer now freezes the desired-state side of this
contract without activating it. It selects exactly one of
`claude-codex-runtime-v1`, `opencode-plugin-v1`, or synthesized `support-v1`
for each of the five semantic target types; converts captured links to regular
installed files; excludes the Claude/Codex hook-map render input; adds the MIT
notice to the OpenCode runtime; and hashes the resulting kind/path/mode/byte
tree with `promptdeploy-installed-tree-v1`. The reviewed snapshots currently
produce these installed runtime digests:

- Claude/Codex:
  `sha256:46bd65bad6023d631340e3262418866206e95ea5afb38d9bab8dbd567fc32d24`;
- OpenCode:
  `sha256:897de1f6cdc260d6243a6920c20773407e3b654cd4e0d47681fb5d90472adfc0`;
- Droid/GPTel support tree:
  `sha256:5dd1e01459a1ae1f5b5fa5bdf181905ba8dbecfb4585d400a4622f5b4842ec83`.

`EmittedHostPath` is a nominal live-target value, distinct from a transaction's
staging `Path`, with explicit local/remote origin, strict UTF-8 components,
depth and byte budgets, and root-path rejection. It renders quoted POSIX or
PowerShell expressions, fails closed when a home anchor lacks `HOME`, and
escapes every quote character that PowerShell treats as a delimiter. The pure
API makes the authority distinction explicit; the future target adapter must
prove that it mints this value from its configured live namespace while the
staging `Path` remains a separate transaction value.
The Claude/Codex renderer accepts the upstream hook map only when its exact
three events, order, matcher, scripts, timeouts, and status messages match the
reviewed contract. It then emits the complete promptdeploy-owned fragment,
clears incompatible host variables, sets the managed plugin root, preserves
Claude's profile-local configuration root, and gives Codex its stable writable
plugin-data path. OpenCode derives one relative managed plugin identity from
the installed-tree digest. The resulting target-effective value binds source
provenance, payload name/root/digest, adapter ABI, installed tree, runtime
identity, and registration digest under `promptdeploy-rendered-bundle-v1`.
Those inputs live in one frozen effective-state descriptor; the candidate hash
is derived rather than caller supplied, and the compact receipt is derived and
cross-checked from that same descriptor. Hook registration values rehash their
complete semantic JSON, while the OpenCode plugin identity rehashes its exact
relative path beneath the owned runtime.

These values remain candidates, not observed or committed target state. A
target must recompute the complete render immediately before mutation and use
an atomic transaction that preseeds and probes the runtime, compares the
registration baseline, switches registration as the activation barrier,
commits manifest ownership, and only then garbage-collects unreferenced
content. Until that layer lands, the manifest-v3 receipt remains absent; the
existing LICENSE-only bundle hash and deploy/status/verify semantics remain
authoritative. A future-version receipt is never interpreted as current
ownership by the v3 reader.

Remote adapters receive both a local staging path and a distinct emitted live
host path; generated commands may use only the latter. Remote updates are a
two-phase transaction: preseed and verify the unreferenced digest tree first,
then compare the live-config baseline and atomically switch the registration
and manifest. Removal unregisters before garbage collection. Fault injection
between transfer, config switch, manifest switch, and cleanup must always
observe either the old complete pair or the new complete pair, never a staging
path or a registration pointing at partial content.

## Safety, trust, and ownership

- Detect an existing native Ponytail plugin or unmanaged same-name skills,
  commands, hooks, or runtime before mutation. Deployment stops even under
  `--force`; the operator must remove the native installation with that
  client's native command before promptdeploy can establish ownership.
- Never install promptdeploy-managed generic Ponytail skills alongside an
  OpenCode plugin that already exposes the same skill directory.
- Never pre-approve Codex hooks or edit its trust database. “Installed but
  untrusted” is a visible incomplete capability, not success.
- Preserve existing unmanaged artifacts through the normal adoption/force
  rules and remove only manifest-owned paths.
- Preview roots accept only lowercase ASCII target IDs and ordinary,
  single-link files/directories; symlinks, hard links, and special files are
  rejected before target access. Required support is revalidated before each
  dependent and again before manifest commit. A same-user filesystem change
  after that final check is ordinary post-deploy drift and must fail the next
  strict verification.
- Keep user mode/default files on ordinary uninstall; document a separate
  explicit state purge.
- Quote generated paths safely and never interpolate a mutable checkout path
  into a production hook command.
- Verify Node through the same local or SSH non-interactive environment that
  will execute the hooks.
- Verify the canonical skill read, relative module graph, representative hook
  output, state write/read, and exact registration. Upstream fail-open handling
  of a malformed individual event does not turn a failed installed-target
  capability probe into a successful full tier.

## Known upstream risks retained with provenance

The imported bytes remain upstream-owned. Complete skill trees stay verbatim;
the three disclosed target transforms are named, digest-guarded, versioned,
and verified rather than silent rewrites.

- `ponytail-gain` still presents the older single-shot 80–94% line reduction,
  47–77% cost reduction, and 3–6× speed card, while the current README leads
  with corrected 12-task agentic figures of roughly 54% less code, 20% lower
  cost, and 27% faster. The full skill is retained verbatim and this mismatch
  is disclosed until fixed upstream.
- The native `ponytail` and `ponytail-help` text still describes persistent
  modes, plugin configuration and updates, and host-specific slash-command
  behavior. The proportional static tier preserves those upstream bytes but
  installs none of that lifecycle machinery; users invoke the six skills
  through each host's ordinary skill surface. GPTel's guarded projections
  replace the claims with its one-shot prompt contract.
- Upstream's tracker persists a hidden `review` state and its instruction
  builder can substitute a hardcoded rules copy. The dormant runtime transforms
  contain pinned corrections, but the current static integration invokes
  neither runtime behavior; its portable contract is the six skills.
- Instruction-only rules are deliberately weaker than lifecycle injection.
  Their availability must not be reported as full mode parity.

## Runtime verification and rollout (historical optional follow-up)

Implementation proceeds in independently audited work units:

1. bundle schema, immutable/mutable bindings, composite discovery,
   provenance, duplicate rejection, six skills, and GPTel projections;
2. managed runtime item and Claude/Codex rendered hooks;
3. native OpenCode plugin ownership and remote transport;
4. pinned Nix input, package passthru, Home Manager assertions, and exact
   activation selection;
5. documentation, isolated target-root parity, full CI, non-mutating
   capability probes, and closeout audits.

Completion requires more than file presence:

- full `nix flake check` (format, lint, strict mypy, 100% branch coverage,
  Home Manager evaluation, activation driver, and package build);
- isolated target-root deployment and `verify --target-root` strict
  verification for all six names, the support bundle, and every target type at
  its declared tier;
- source/version/revision and selected-tree equality with the pinned Ponytail
  input;
- first deploy, no-op redeploy, pin update, rollback, drift, removal, and
  failure-injection coverage;
- representative Node and OpenCode runtime probes against the isolated
  materialization, including canonical-read failure, state round trips,
  one-shot review, and target-aware GPTel semantic goldens.

Fleet rollout is a separately authorized operational phase. Only after that
authorization may promptdeploy change the 21 live target configurations or
remove an existing native Ponytail installation. The rollout acceptance is:
actual session-start/resume/compact/mode/off/subagent behavior on Claude and
Codex; actual full/ultra/off and six-command behavior on OpenCode; live skill
selection on Droid; live prompt loading on GPTel; every configured remote
transport path verified; and Codex hook trust reviewed by the user. Until
those checks are completed, the implementation may be complete and deployable
without claiming that the live fleet has already reached “everywhere.”

## Baseline evidence

- Promptdeploy was clean at `c308988401fe9a7087aedfeba38bd59143f4cc7d`;
  `direnv exec . nix flake check` passed all seven checks before implementation.
- Ponytail was clean at the reviewed revision. Its root suite passed 81 of 82
  tests; the only failure was the documented CSV correctness benchmark because
  this host's `python3` lacks `pandas`, which upstream CI installs as a required
  CSV-test dependency even though production runtime does not require it. No
  dependency was installed, the baseline remains visibly non-green at 81/82,
  and the generated Python bytecode cache was removed afterward.
- In isolated `HOME`/`CODEX_HOME` directories, marketplace registration created
  `[marketplaces.ponytail]`; plugin installation cached the 4.8.4 tree and
  created `[plugins."ponytail@ponytail"] enabled = true`. A second isolated
  registration recorded the exact Git URL and reviewed `ref`. Those filesystem
  observations prove marketplace/config/cache behavior only. The official
  Codex hook contract—not the installation experiment—establishes
  `PLUGIN_ROOT`, `PLUGIN_DATA`, and compatibility environment variables; the
  managed adapter still exercises its rendered environment directly.

The isolated evidence roots were
`/var/tmp/promptdeploy-ponytail-codex.EXzur6` (local marketplace plus installed
cache) and `/var/tmp/promptdeploy-ponytail-codex-git.J5UwSW` (Git marketplace at
the exact reviewed ref). Their relevant `config.toml` and cache paths were
inspected without touching the live Codex home. These temporary roots are
diagnostic evidence, not production inputs, and are removed at closeout.

Official Codex references used for this design: [build plugins](https://learn.chatgpt.com/docs/build-plugins),
[lifecycle hooks](https://learn.chatgpt.com/docs/hooks), and
[developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
