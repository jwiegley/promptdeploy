# Ponytail integration study and architecture

- Status: accepted design, implementation in progress
- Reference checkout: `/Users/johnw/Desktop/ponytail`
- Upstream: `https://github.com/DietrichGebert/ponytail`
- Reviewed revision: `16f29800fd2681bdf24f3eb4ccffe38be3baec6b`
- Declared version: `4.8.4`

## Decision

Ponytail will be integrated as a named, allowlisted external bundle. During
development, an explicit CLI binding may point that logical bundle at the
Desktop checkout. Production and Home Manager activation will bind the same
logical name to a pinned, non-flake Nix input in the immutable store.

The promptdeploy repository will contain the adapter manifest and the expected
upstream provenance, not a machine-local symlink, submodule, or hand-maintained
copy of the third-party payload.

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

The internal `review` mode is not a portable public intensity and will not be
accepted as a default. `ponytail-review` remains the portable one-shot review
surface.

## Target and fleet mapping

The current `deploy.yaml` describes 21 targets: eight Claude, four Codex,
seven OpenCode, one Factory Droid, and one GPTel. `RemoteTarget` is transport,
not a sixth semantic client; it must push the same complete target-specific
artifact set that a local target receives.

| Target | Required endpoint | Honest limitation |
|---|---|---|
| Claude Code | Six complete skills plus a target-local managed runtime and rendered lifecycle hooks; preserve profile-local `CLAUDE_CONFIG_DIR` behavior and optional statusline assets. | Hook execution requires Node. Native marketplace registration alone does not install content on a fresh host. |
| Codex CLI/Desktop/IDE | Six complete skills plus the same managed runtime, with an explicit writable `PLUGIN_DATA` binding so the shared code emits Codex JSON and stores state in the Codex-owned path. | Hook definitions remain subject to the user's Codex trust review. Promptdeploy must not edit trust state. |
| OpenCode | Preserve the upstream native plugin's relative `.opencode/command`, `hooks/`, and `skills/` layout and manage exactly one plugin entry in `opencode.json`. | The native plugin owns its bundled skills; generic Ponytail skill deployment must be disabled there to avoid duplicates. |
| Factory Droid | Six complete skills through the existing atomic skill-tree target path. | No proven always-on hook/instruction surface; claim callable skill tier only until a live client proves more. |
| GPTel | Six named prompt projections made by stripping each `SKILL.md` frontmatter and retaining the body. | Prompt presets are not native skills, lifecycle hooks, or persistent modes. |

The optional MCP package is not part of the minimum endpoint. Upstream states
that it is user-invoked rather than always-on, and its uninstalled SDK/Zod
dependencies require separate reproducible packaging. It may be added later as
an explicitly weaker bridge, never as evidence of lifecycle parity.

## Source-reference alternatives

| Model | Decision |
|---|---|
| Absolute Desktop path | Development override only. It is mutable and absent on other hosts. |
| External symlink forest | Rejected. It violates current source boundaries and fails in immutable/remote deployments. |
| Git submodule | Rejected as canonical. Flake sources omit submodule contents unless every consumer opts in, and an adapter manifest is still required. |
| Vendored or generated committed copy | Rejected as canonical. It duplicates ownership, imports irrelevant project material, and invites silent drift. |
| Client marketplaces/packages only | Optional supplement. They are network/trust/client-state dependent and cannot prove a common fleet pin. |
| Named bundle manifest + explicit source binding | Accepted control plane. |
| Named bundle + pinned non-flake Nix input | Accepted production binding. |

## Bundle model

The committed manifest will name only logical source paths and selected
exports. The exact schema may evolve during implementation, but it must retain
these semantics:

```yaml
schema: 1
name: ponytail
version: 4.8.4
revision: 16f29800fd2681bdf24f3eb4ccffe38be3baec6b
version_file: package.json
license: LICENSE

exports:
  skills:
    - skills/ponytail
    - skills/ponytail-review
    - skills/ponytail-audit
    - skills/ponytail-debt
    - skills/ponytail-gain
    - skills/ponytail-help
  gptel_prompts:
    from_skills: true
    strip_frontmatter: true
  claude_codex_runtime:
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
      - LICENSE
  opencode_plugin:
    include:
      - .opencode/command
      - .opencode/plugins/ponytail.mjs
      - .opencode/plugins/ponytail-frontmatter.cjs
      - hooks
      - skills
      - LICENSE
```

Every include path must be canonical and relative. Discovery must reject
absolute paths, `.`/`..`, broken or external links, directory links, special
files, missing expected version/license files, duplicate names, and any
unlisted new runtime file. `.git`, `.env`, `node_modules`, benchmarks, tests,
publishing scripts, and marketing assets can never enter the payload by
implicit recursion from the repository root.

Each emitted item will carry bundle name, revision, logical relative path,
and mutable/immutable source status. Those values participate in diagnostics,
hashing, and manifest provenance. `(item_type, name)` remains the ownership key
and must be globally unique across the primary source and every bundle.

### Bindings

The planned development interface is explicit and visibly mutable:

```text
promptdeploy validate \
  --bundle-source ponytail=/Users/johnw/Desktop/ponytail
```

The production flake input is non-flake and pinned. The packaged executable
binds `ponytail` to that immutable store path and exposes the same source,
revision, and digest through package passthru. Home Manager asserts the binding
is store-backed and matches the package metadata before activation. Activation
performs no fetch, npm install, or plugin-manager mutation.

## Managed runtime design

Executable runtime code is a first-class, hashed item rather than an incidental
hook side effect. A target stages a complete allowlisted tree under a managed,
content-addressed directory, verifies it, then switches hook/plugin
configuration to that exact digest. Only after the new configuration verifies
may an unreferenced old digest be removed. Failure leaves either the complete
old state or the complete new state—never hooks pointing at a partial tree.

Claude and Codex hook commands are rendered from the upstream event map. They
must not retain `${CLAUDE_PLUGIN_ROOT}` outside a plugin host. Commands point at
the managed runtime; Codex commands additionally set a writable managed
`PLUGIN_DATA` path. POSIX and PowerShell forms, timeouts, matchers, status
messages, BOM/CRLF handling, and the non-closing-stdin fail-safe are preserved.

OpenCode receives the native relative layout and one managed plugin path. The
target's remote rsync includes must cover the runtime directory. Removing the
bundle removes only promptdeploy-owned configuration/runtime paths and retains
the user's mode/default state unless an explicit purge is requested.

## Safety, trust, and ownership

- Detect an existing native Ponytail plugin or unmanaged same-name skills,
  commands, hooks, or runtime before mutation. Normal deployment stops;
  explicit migration/force establishes one owner.
- Never install promptdeploy-managed generic Ponytail skills alongside an
  OpenCode plugin that already exposes the same skill directory.
- Never pre-approve Codex hooks or edit its trust database. “Installed but
  untrusted” is a visible incomplete capability, not success.
- Preserve existing unmanaged artifacts through the normal adoption/force
  rules and remove only manifest-owned paths.
- Keep user mode/default files on ordinary uninstall; document a separate
  explicit state purge.
- Quote generated paths safely and never interpolate a mutable checkout path
  into a production hook command.
- Verify Node through the same local or SSH non-interactive environment that
  will execute the hooks.

## Known upstream risks retained with provenance

The imported bytes remain upstream-owned. Promptdeploy will not silently
rewrite them.

- `ponytail-gain` still presents the older single-shot 80–94% line reduction,
  47–77% cost reduction, and 3–6× speed card, while the current README leads
  with corrected 12-task agentic figures of roughly 54% less code, 20% lower
  cost, and 27% faster. The full skill is retained verbatim and this mismatch
  is disclosed until fixed upstream.
- Some hosts differ on bare `/ponytail`, hidden `review` state, and default
  reporting. The portable public contract is the six skills and four public
  levels; target-native extras are not generalized without evidence.
- Instruction-only rules are deliberately weaker than lifecycle injection.
  Their availability must not be reported as full mode parity.

## Verification and rollout

Implementation proceeds in independently audited work units:

1. bundle schema, immutable/mutable bindings, composite discovery,
   provenance, duplicate rejection, six skills, and GPTel projections;
2. managed runtime item and Claude/Codex rendered hooks;
3. native OpenCode plugin ownership and remote transport;
4. pinned Nix input, package passthru, Home Manager assertions, and exact
   activation selection;
5. documentation, isolated target-root parity, full CI, live trust/capability
   probes, and fleet rollout.

Completion requires more than file presence:

- full `nix flake check` (format, lint, strict mypy, 100% branch coverage,
  Home Manager evaluation, activation driver, and package build);
- isolated target-root deployment and strict verification for all six names on
  all five target types at their declared tier;
- source/version/revision and selected-tree equality with the pinned Ponytail
  input;
- first deploy, no-op redeploy, pin update, rollback, drift, removal, and
  failure-injection coverage;
- actual session start, resume/compact, mode switch, off, and subagent behavior
  on Claude/Codex; actual full/ultra/off and six commands on OpenCode; live
  skill selection on Droid; live prompt loading on GPTel;
- Codex trust reviewed by the user, never automated;
- every configured local and remote target verified before claiming
  “everywhere”.

## Baseline evidence

- Promptdeploy was clean at `c308988401fe9a7087aedfeba38bd59143f4cc7d`;
  `direnv exec . nix flake check` passed all seven checks before implementation.
- Ponytail was clean at the reviewed revision. Its root suite passed 81 of 82
  tests; the only failure was the documented CSV correctness benchmark because
  this host's `python3` lacks the optional `pandas` dependency. No dependency
  was installed, and the generated Python bytecode cache from that run was
  removed; the Ponytail worktree was clean afterward.
- An isolated temporary Codex home proved the official native plugin path:
  marketplace registration wrote a `[marketplaces.ponytail]` config block;
  installation cached version 4.8.4 and wrote
  `[plugins."ponytail@ponytail"] enabled = true`; plugin hooks receive
  `PLUGIN_DATA` and compatibility root variables. This also proved why native
  plugin-manager state is not a pure static promptdeploy deployment and why
  the managed-hook adapter must preserve the trust boundary.

Official Codex references used for this design: [build plugins](https://learn.chatgpt.com/docs/build-plugins),
[lifecycle hooks](https://learn.chatgpt.com/docs/hooks), and
[developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
