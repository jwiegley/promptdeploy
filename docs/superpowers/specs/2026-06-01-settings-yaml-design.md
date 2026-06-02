# Single-Source Claude Code `settings.json` via `settings.yaml`

**Date:** 2026-06-01
**Status:** Approved (pending implementation plan)

## Background

Claude Code reads a `settings.json` from each configuration directory. The user
maintains ten such files across hosts and profiles:

| Host       | Path                                       | promptdeploy target   |
|------------|--------------------------------------------|-----------------------|
| hera       | `~/.config/claude/personal/settings.json`  | `claude-personal`     |
| hera       | `~/.config/claude/positron/settings.json`  | `claude-positron`     |
| hera       | `~/.config/claude/git-ai/settings.json`    | `claude-git-ai-local` |
| clio       | `~/.config/claude/personal/settings.json`  | `claude-personal`     |
| clio       | `~/.config/claude/positron/settings.json`  | `claude-positron`     |
| clio       | `~/.config/claude/git-ai/settings.json`    | `claude-git-ai-local` |
| vulcan     | `~/.claude/settings.json`                   | `claude-vulcan`       |
| andoria-08 | `~/.claude/settings.json`                   | `claude-andoria`      |
| vps        | `~/.claude/settings.json`                   | `claude-vps`          |
| git-ai     | `~/.claude/settings.json`                   | `claude-git-ai-remote`|

These ten files map to **seven** logically-distinct promptdeploy targets
(`claude-personal`, `claude-positron`, and `claude-git-ai-local` each deploy to
both hera and clio; the four remote `~/.claude` files are one target each). They
share most of their content but drift in host-specific ways. Editing a common
setting today means editing the same value in up to ten files by hand. The user
wants a single master file in this repository, deployed everywhere, with
host-specific deltas expressed in one place, plus a way to pull host-side changes
back into the master.

### Current managed boundary (verified by reading the live files)

Each `settings.json` contains three categories of content:

1. **promptdeploy-managed today** — the `mcpServers` block (from `mcp/*.yaml`) and
   two hook groups: the iTerm bell (`hooks/claude-code.yaml`) and `claude-vault`
   (`hooks/claude-vault.yaml`), both tagged with `_source` in the deployed JSON.
2. **Externally managed** — `git-ai checkpoint` hooks and `cozempic` hooks, each
   self-installed by those tools with per-host nix-store paths. These are **not**
   promptdeploy's and must be preserved untouched.
3. **Unmanaged — the subject of this work** — every other top-level key: `env`,
   `statusLine`, `enabledPlugins`, `sandbox`, `effortLevel`, `model`,
   `showThinkingSummaries`, `skipDangerousModePermissionPrompt`,
   `preferredNotifChannel`, `agentPushNotifEnabled`, `remoteControlAtStartup`,
   `verbose`, `extraKnownMarketplaces`.

Observed host variation in category 3 includes additions, changes, **and**
removals reaching into nested blocks — e.g. `effortLevel` is `low`/`high`/absent;
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` is `64000` vs `32000`; `MCP_TIMEOUT` is
`1800000` vs `300000`; `claude-positron` drops `sandbox`, `ENABLE_LSP_TOOL`,
`ENABLE_TOOL_SEARCH`, and `showThinkingSummaries`, and adds `model: sonnet`; each
`statusLine.command` path differs.

No secrets live in category 3 — the API keys are all inside `mcpServers`, which
stays owned by `mcp/`. `settings.yaml` is therefore secret-free.

## Goals

1. A single `settings.yaml` in the repo holding the common (category 3) settings,
   with host-specific deltas expressed via per-target/group overrides.
2. `promptdeploy deploy` renders `settings.yaml` per target and merges the result
   into each target's `settings.json`, preserving categories 1 and 2 and any other
   key already present.
3. `promptdeploy settings init` bootstraps `settings.yaml` from the live files
   across all targets (request to "consolidate everything").
4. `promptdeploy settings reconcile` surfaces host-side drift and, on `--apply`,
   writes it back into the appropriate override block.

## Non-goals

- **No change to hook or MCP management.** `hooks/` and `mcp/` remain the sources
  of truth for categories 1; `settings.yaml` never carries `hooks`/`mcpServers`.
- **Not bringing git-ai/cozempic hooks under management.** They stay external and
  preserved.
- **Claude-only.** Droid (`settings.json` with `customModels`) and OpenCode
  (`opencode.json`) have different schemas; `settings.yaml` does not apply to them.
- **No per-sub-key ownership.** Ownership granularity is the top-level key (see
  §6.7). promptdeploy owns each top-level key it renders, as a whole.

## Design

### 6.1 `settings.yaml` schema

Two top-level keys, mirroring the `overrides:` pattern already used in
`models.yaml`:

```yaml
base:                      # applied to every claude target; excludes hooks/mcpServers
  env:
    ANTHROPIC_DEFAULT_HAIKU_MODEL: claude-sonnet-4-6
    CLAUDE_CODE_MAX_OUTPUT_TOKENS: '64000'
    MCP_TIMEOUT: '1800000'
    MCP_TOOL_TIMEOUT: '1800000'
  effortLevel: low
  skipDangerousModePermissionPrompt: true
  showThinkingSummaries: true
  sandbox:
    enabled: false
    autoAllowBashIfSandboxed: true
    filesystem:
      allowWrite: [/private/tmp, /var/folders]
      allowRead: [/private/tmp, /var/folders, /Users/johnw/Products]
  enabledPlugins:
    'superpowers@claude-plugins-official': true
    'claude-mem@thedotmack': true
  statusLine:
    type: command
    command: bash /Users/johnw/.config/claude/personal/statusline-command.sh

overrides:                 # key = target id OR label/group from deploy.yaml
  claude-positron:
    effortLevel: null               # null deletes the key
    model: sonnet                   # add
    env:
      CLAUDE_CODE_MAX_OUTPUT_TOKENS: '32000'   # change (deep merge)
      MCP_TIMEOUT: '300000'
      MCP_TOOL_TIMEOUT: '300000'
      ENABLE_LSP_TOOL: null                    # delete one nested key
    sandbox: null                              # delete the whole block
    showThinkingSummaries: null
  claude-git-ai-local:
    effortLevel: high
```

`hooks` and `mcpServers` are illegal here: `validate` warns if they appear, and
the renderer strips them defensively so they can never reach `deploy_settings`.

### 6.2 Merge-patch semantics — `src/promptdeploy/settings.py` (new module)

Two pure, independently-testable helpers implementing **RFC 7386 JSON Merge
Patch**:

- `apply_merge_patch(base: dict, patch: dict) -> dict` — returns a new dict;
  for each `k, v` in `patch`: if `v is None`, delete `k`; elif both `base[k]` and
  `v` are dicts, recurse; else set `base[k] = v`. Deep-copies so inputs are never
  mutated.
- `generate_merge_patch(base: dict, target: dict) -> dict` — the inverse: the
  minimal patch `P` with `apply_merge_patch(base, P) == target`. For `k` in
  `base` not in `target`, `P[k] = None`; for differing dict values, recurse; for
  other differences and keys only in `target`, `P[k] = target[k]`.

### 6.3 Rendering — `render_settings(doc, target_id, config) -> dict`

1. Deep-copy `doc["base"]` (default `{}`).
2. Collect every `overrides` entry whose key matches the target: either the exact
   `target_id`, or a group/label key whose expansion (`config.groups`) contains
   `target_id`.
3. Apply matches via `apply_merge_patch` **in `settings.yaml` file order**
   (mapping order is preserved by the YAML loader), **except** an override keyed by
   the exact `target_id`, which is always applied last so the most specific
   override wins. (File order lets the user control precedence among overlapping
   group/label overrides.)
4. Strip `hooks` and `mcpServers` from the result.
5. Recursively strip any remaining `null` values (and now-empty dicts they
   leave behind). Override-driven `null`s are already resolved as deletes in step
   3, but a literal `null` written directly in `base` would otherwise survive into
   `rendered` and be emitted to `settings.json` as a JSON `null`. This final
   sweep guarantees the invariant below; `validate` additionally warns on `null`
   values in `base` so the author notices an unintended one. The sweep targets
   dict values and top-level keys; per RFC 7386, **list elements are atomic** — a
   `null` inside a list value is left as-is (no settings value here is a list of
   nullable elements, so this is a documentation caveat, not a live case).

The returned dict has all `null`s resolved; it is the concrete set of managed
top-level keys for that target. **No `null`s reach the target.**

### 6.4 Discovery — `src/promptdeploy/source.py`

`SourceDiscovery.discover_settings()` yields a single
`SourceItem(item_type="settings", name="settings", path=<settings.yaml>,
metadata=<parsed dict>, content=<raw bytes>)` when `settings.yaml` exists (read
via PyYAML — deploy-time reads do not need round-trip fidelity). Added to
`discover_all()`. Registered in `deploy.py`'s `_TYPE_TO_CATEGORY` and
`_CLI_TYPE_TO_ITEM_TYPE` as `"settings" -> "settings"`.

The item's `content` is the whole file's bytes, so `_compute_hash` (which hashes
`item.content`) changes whenever any part of `settings.yaml` changes — every
claude target re-renders and re-asserts. This over-deploys slightly (an unrelated
override edit re-touches all targets, idempotently) exactly as the existing
single-file `models` item does; acceptable.

### 6.5 Deploy integration

- **`base.Target`** gains two methods with **default no-op implementations**
  (not `@abstractmethod`, matching `prepare`/`finalize`/`rsync_includes`), so the
  three non-Claude targets need no changes:
  - `deploy_settings(self, rendered: dict, previous_keys: list[str]) -> None`
  - `remove_settings(self, previous_keys: list[str]) -> None`

- **`ClaudeTarget.deploy_settings`**:
  1. Load `settings.json` (`{}` if absent) via the existing `_load_json`.
  2. For each `k` in `previous_keys` not in `rendered`: `settings.pop(k, None)`
     (a key promptdeploy managed before but no longer renders).
  3. For each `k, v` in `rendered`: `settings[k] = v` (assert the managed key,
     wholesale).
  4. `_save_json` (atomic temp-file + `os.replace`, as everywhere else).
  It never reads or writes `hooks`, `mcpServers`, or any key not in `rendered`/
  `previous_keys`. Ordering relative to the hook/MCP deploy is irrelevant because
  they touch disjoint top-level keys.

- **`ClaudeTarget.remove_settings`**: load, pop every key in `previous_keys`,
  save. Called by stale-removal when `settings.yaml` is deleted from the repo.

- **`ClaudeTarget.should_skip`** continues returning `False` for `settings` (it is
  handled). **`droid` and `opencode` `should_skip`** add `item_type == "settings"`
  to their skip conditions. **`gptel` needs no change** — its `should_skip` already
  returns `item_type != "prompt"`, which skips `settings`.

- **Dedicated settings branch in `deploy.py`'s deploy loop.** `settings` does
  **not** fit the generic existence/pre-existing/drift machinery, because (unlike
  `mcp`/`models`) it has no dedicated marker key: a target's `settings.json`
  effectively always exists (the hook/MCP deploy creates it), so a generic
  `item_exists`-based path would either mis-report the first settings deploy as
  `pre-existing` (and skip it) or, if `item_exists` returned `False`, re-deploy on
  every run. So the loop gets an explicit early branch, evaluated like the existing
  `models` special-case but covering existence/skip too:

  ```
  if item.item_type == "settings":
      rendered = render_settings(doc, target_id, config)
      is_update = "settings" in manifest.items.get("settings", {})
      previous_keys = (manifest.items.get("settings", {})
                       .get("settings", ManifestItem("")).managed_keys or [])
      if force or changed:                      # changed = manifest-hash differs
          if not dry_run:
              target.deploy_settings(rendered, previous_keys)
          actions.append(DeployAction("update" if is_update else "create",
                                      "settings", "settings", target_id, ...))
      else:
          actions.append(DeployAction("skip", "settings", "settings", target_id, ...))
      deployed_names.add(("settings", "settings"))
      new_manifest.items.setdefault("settings", {})["settings"] = ManifestItem(
          source_hash=current_hash, managed_keys=list(rendered.keys()))
      continue
  ```

  This gives correct `create`/`skip`/`update` semantics off the manifest hash
  alone, never triggers pre-existing detection, records `managed_keys` at the one
  write site (no interaction with the generic recording block), and registers the
  item in `deployed_names` so stale-detection behaves. `ClaudeTarget.item_exists`
  may simply return `False` for `settings` (unused on this path, defined only for
  interface completeness). The branch must be inserted **after** `category`,
  `current_hash`, and `changed` are computed (deploy.py ~lines 291–295), all of
  which it reads. Each `DeployAction` it appends also sets `source_path=str(item.path)`
  (elided as `...` above) to match every other item type, and `warnings=[]`
  (`deploy_settings` renders no templates).

- **`_remove_item`** routes category `settings` to `target.remove_settings`,
  passing the prior `managed_keys` read from the stale manifest entry. (When
  `--only-type` is active, the category↔type inversion in the stale loop must map
  `settings`→`settings`; see §6.8.)

### 6.6 Manifest extension — `src/promptdeploy/manifest.py`

`ManifestItem` gains `managed_keys: Optional[list[str]] = None`.
`save_manifest` serializes it only when present (matching how `target_path`/
`config_key` are handled). `load_manifest` already passes `**vals`, so the new
field round-trips once the dataclass has it. Existing manifests deserialize
unchanged (the field defaults to `None`). The `settings` category is created on
demand via `setdefault`. `managed_keys` is written **only** by the §6.5 settings
branch — the generic per-item recording block is untouched.

This is the per-target memory that makes "gentle, manage tracked keys only"
precise: removal touches only keys promptdeploy itself last wrote.

### 6.6a `status.py` integration — REQUIRED

`status.py` maintains its **own** `_TYPE_TO_CATEGORY` (separate from `deploy.py`'s)
and `get_status` does `category = _TYPE_TO_CATEGORY[item.item_type]` for every item
from `discover_all()`. Adding the `settings` source item therefore makes
`promptdeploy status` raise `KeyError: 'settings'` unless `status.py`'s map gains
`"settings": "settings"`. Add it. (The `list` command's `category_labels`/
iteration entries live in `cli.py` and are covered separately by §6.8.) The status
hash for `settings` is the plain `compute_file_hash(item.content)` already used
there — sufficient for new/changed/current classification.

> **Adjacent latent bug:** `status.py`'s `_TYPE_TO_CATEGORY` is *already* missing
> `"prompt"`, so `promptdeploy status` `KeyError`s today whenever a `prompts/` item
> exists (the repo has two). Since this work edits that exact dict, add
> `"prompt": "prompts"` in the same change to fix it.

### 6.7 Ownership & gentle merge — worked example

Granularity is the **top-level key**. Suppose `claude-positron`'s prior deploy
managed `{env, effortLevel, model, statusLine, ...}` and the host's
`settings.json` also contains externally-installed `hooks`, a `mcpServers` block,
and a `feedbackSurveyState` key Claude Code wrote itself.

A new deploy where the positron override no longer sets `model`:

- `previous_keys` includes `model`; `rendered` does not → `model` is removed.
- `env`, `effortLevel`, `statusLine`, … are re-asserted from `rendered`.
- `hooks`, `mcpServers`, `feedbackSurveyState` are **never referenced** → preserved.

A runtime change (`/config` writes `model: opus` into the host file) is **not**
auto-clobbered silently — it is re-asserted to the rendered value on the next
deploy because `model` is a managed key, and `reconcile` is the tool to pull such
a change back into `settings.yaml` first if desired. Keys promptdeploy has never
managed are invisible to it.

### 6.8 CLI surface — `src/promptdeploy/cli.py`

- `deploy --only-type` gains `settings` in its `choices` (and `_CLI_TYPE_TO_ITEM_TYPE`
  gains `"settings": "settings"`). The stale-removal loop in `deploy.py` inverts
  `_TYPE_TO_CATEGORY` to map category→type for the `--only-type` filter; the
  symmetric `settings`↔`settings` entry makes this work with no special case.
- `list` adds `settings` to **both** its `category_labels` map and the explicit
  category iteration tuple.
- New `settings` subcommand group with two actions:
  - `promptdeploy settings init [--from REF] [--target …] [--force]`
  - `promptdeploy settings reconcile [--target …] [--apply]`
  Both honor `--target` group/id expansion via the existing `expand_target_arg`,
  filtered to claude-type targets.

### 6.9 `settings init`

Bootstraps `settings.yaml` (request #1). Refuses to overwrite an existing
`settings.yaml` without `--force`.

1. For each selected claude target, obtain its live `settings.json` (see §6.11 for
   the local/remote read path) and strip `hooks` + `mcpServers`.
2. `--from REF` chooses the reference target whose stripped settings become
   `base` (default: the first claude target in `deploy.yaml`, i.e.
   `claude-personal`).
3. For every other target T, `overrides[T] = generate_merge_patch(base,
   stripped[T])`. Targets identical to `base` get no entry.
4. Write `settings.yaml` via ruamel.yaml (§6.12), emitting `base` then
   `overrides`.

The user then refactors by hand — e.g. collapsing several identical per-target
overrides into a shared label/group override, or adding comments.

### 6.10 `settings reconcile`

The ongoing sync (request #2's reconciliation). Requires an existing
`settings.yaml` (else suggests `init`).

1. For each selected claude target, read live `settings.json`, strip `hooks` +
   `mcpServers` → `host`.
2. Compute `rendered = render_settings(doc, target_id, config)`.
3. Diff `host` vs `rendered` and print, per target:
   - `+ key` present on host, absent from `rendered` (host-only, candidate to pull),
   - `~ key` present in both but differing,
   - `- key` rendered but absent on host (settings.yaml would add it on next
     deploy — informational only; nothing to pull).
4. With `--apply`, for each `+`/`~` **top-level** key, set the override for that
   target to reproduce the host value: assign `overrides[target_id][key] =
   generate_merge_patch(base, host)[key]` (a `null` when the host lacks a key that
   `base` has). Writes are per-key into the existing ruamel override map, so
   comments on untouched override keys are preserved; regenerated keys may lose
   inner-nested comments (best-effort, documented). Never touches other targets'
   overrides.

Per-key correctness: override application and merge-patch both operate on each
top-level key independently, so reproducing the host value key-by-key yields
`apply_merge_patch(base, overrides[target_id]) == host` for the reconciled keys
while leaving siblings as they were.

### 6.11 Local vs remote read path (shared by init/reconcile)

Reuse the existing transport. `create_target(tc)` builds either a local
`ClaudeTarget` or a `RemoteTarget` wrapping one. `RemoteTarget.prepare()` already
rsyncs the target's `rsync_includes()` (which lists `settings.json`) into a local
staging dir. So:

```
target = create_target(tc)
target.prepare()                      # no-op locally; rsync-pull for remote
data = read settings.json under the (staged or local) config path
target.cleanup()
```

A small public accessor on `ClaudeTarget` (e.g. `read_settings_json() -> dict`)
exposes the staged/local file so init/reconcile need not reach into the private
`_settings_path`. **`RemoteTarget` exposes no config-path or settings accessor of
its own** (it wraps an inner `ClaudeTarget`), so the same accessor must be added
to `RemoteTarget`'s delegation surface (forwarding to `self._inner`); init/
reconcile then call `target.read_settings_json()` uniformly for local and remote.
No new SSH code — `ssh_pull` via `prepare()` suffices.

### 6.12 Dependency: ruamel.yaml

To preserve comments, key order, and formatting when `init`/`reconcile --apply`
write `settings.yaml`, add **ruamel.yaml** as a runtime dependency. It is used
only on the write-back path (round-trip load → edit `CommentedMap` → dump);
deploy-time rendering reads `settings.yaml` with the existing PyYAML loader.

- `pyproject.toml`: append `"ruamel.yaml>=0.18"` to `dependencies` (currently
  `["PyYAML>=6.0", "Jinja2>=3.1"]` — already two deps, so the "single dependency"
  framing is updated, not newly broken).
- `flake.nix`: add the nixpkgs `ruamel-yaml` package to **both** the dev-shell
  `pythonWithDeps` list (as `ps.ruamel-yaml`, alongside `pyyaml`/`jinja2`) and the
  `buildPythonApplication` `dependencies` list (as `python.pkgs.ruamel-yaml`).

### 6.13 Validation — `src/promptdeploy/validate.py`

When `settings.yaml` exists:
- `base` (if present) and each `overrides` value are mappings; `overrides` is a
  mapping.
- Every `overrides` key resolves to a known target id or group/label. **Note:**
  `config.py` auto-registers each declared `hosts:` name as a group (populated
  with the host-less targets on the current machine), so an override keyed by a
  hostname (e.g. `hera`) is valid and matches every host-less target — a powerful
  but easily-surprising surface. Validation accepts it; a test pins the behavior.
- Warn if `hooks` or `mcpServers` appears in `base` or any override (it will be
  stripped and is therefore inert — point the user at `hooks/` / `mcp/`).
- Warn on a literal `null` value inside `base` (overrides legitimately use `null`
  to delete; a `null` in `base` is almost always an accident and is stripped by
  the renderer — see §6.3 step 5).
- Values are JSON-representable (scalars/dicts/lists); reject YAML-only types
  (dates, sets, tuples) since the output is JSON.

### 6.14 Docs

- `CLAUDE.md`: add a `Settings` row to the content-types table; document the
  `settings.yaml` mechanism, the gentle-merge boundary, and the `settings`
  subcommand; correct the stale "single dependency (PyYAML)" phrasing.
- `README.md` / `PROMPTDEPLOY.md`: document `settings.yaml`, `settings init`,
  and `settings reconcile`; update the dependency note.

## Data flow

**Deploy** (`promptdeploy deploy --target positron`):

```
discover settings.yaml ─► SourceItem(settings)
   └► deploy.py: render_settings(doc, "claude-positron", config) ─► rendered dict
       └► ClaudeTarget.deploy_settings(rendered, previous_keys from manifest)
           ├─ load settings.json (staged copy if remote)
           ├─ drop previous_keys − rendered
           ├─ set rendered keys (wholesale, top-level)
           └─ save; hooks/mcpServers/other keys untouched
       └► manifest.items["settings"]["settings"].managed_keys = rendered.keys()
   (remote) RemoteTarget.finalize() rsync-pushes settings.json back
```

**Reconcile** (`promptdeploy settings reconcile --target positron --apply`):

```
for target in selected claude targets:
   prepare() ─► read settings.json ─► strip hooks/mcpServers ─► host
   rendered = render_settings(doc, target, config)
   diff(host, rendered) ─► print +/~/-
   if --apply: for each +/~ key: overrides[target][key] = patch(base,host)[key]
write settings.yaml via ruamel (comments preserved on untouched keys)
```

## Error handling

- Missing `settings.yaml`: `deploy` silently skips the settings item (no source);
  `reconcile` errors and suggests `init`; `init` proceeds.
- Malformed `settings.yaml` (non-dict `base`/`overrides`, unknown override key,
  illegal `hooks`/`mcpServers`): surfaced by `validate`; `deploy` fails fast with
  a clear message rather than writing a half-rendered file.
- Remote unreachable during init/reconcile: the existing `ssh_pull` error path in
  `prepare()` propagates; the offending target is reported and skipped (init/
  reconcile process the others).
- `init` without `--force` when `settings.yaml` exists: refuse and instruct.
- Atomic writes throughout (`tempfile.mkstemp` + `os.replace`) so an interrupted
  deploy or reconcile never leaves a truncated `settings.json` or `settings.yaml`.

## Testing strategy

- **`tests/test_settings.py`** (new): `apply_merge_patch`/`generate_merge_patch`
  round-trips (scalars, nested dicts, null-delete, add, key-only-in-target);
  `render_settings` precedence (group vs exact-id, file order, deep env merge,
  null delete, hooks/mcpServers strip).
- **`tests/test_claude_target.py`**: `deploy_settings` gentle merge — asserts
  managed-key set/replace, removal of dropped `previous_keys`, and preservation of
  `hooks`, `mcpServers`, and an unrelated key; `remove_settings`.
- **`tests/test_deploy.py`**: end-to-end settings item — create/update/skip/remove
  actions, `managed_keys` manifest round-trip, stale removal on deleted
  `settings.yaml`, and `--only-type settings`.
- **Non-claude skip**: `test_droid_target.py`/`test_opencode_target.py`/
  `test_gptel_target.py` assert `should_skip("settings", …)` is `True`.
- **`tests/test_settings_cli.py`** (new): `init` factoring (base from reference,
  per-target override patches), `--force` guard; `reconcile` diff classification
  and `--apply` write-back into overrides with comment preservation (ruamel
  round-trip), over fixture `settings.json` files; remote path exercised with a
  fake/staged target.
- **`tests/test_validate.py`**: settings.yaml validation cases.
- 100% coverage gate in `pyproject.toml` applies to all new code.

## Verification

- `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing` — pass
  with the 100% coverage gate.
- `nix flake check` — pass (`ruff format --check`, `ruff check`, `mypy`, `pytest`,
  `nix build`); the build now resolves `ruamel-yaml`.
- Sanity: `promptdeploy validate`; `promptdeploy deploy --only-type settings
  --dry-run --target-root /tmp/preview` renders settings.json for every claude
  target into the scratch tree; `promptdeploy settings reconcile --target
  claude-personal` reports a clean diff after a deploy.

## Risks

- **Clobbering host state.** Mitigated by top-level-key ownership + manifest
  `managed_keys` (never removes a key it did not write) and by keeping
  `hooks`/`mcpServers`/unknown keys strictly untouched. `reconcile` exists to pull
  intentional host changes back before a deploy re-asserts.
- **ruamel.yaml dependency.** Accepted trade-off for comment-preserving
  write-back; confined to the init/reconcile write path so deploy stays on PyYAML.
- **Override precedence confusion** among overlapping group/label keys. Mitigated
  by the documented rule (file order, exact-target-id last) and `validate`.
- **Comment loss on regenerated nested keys** in `reconcile --apply`. Documented
  as best-effort; top-level/untouched-key comments are preserved.

## Rollout / migration

1. Land the code (render/deploy/manifest/CLI/validate/deps) with no `settings.yaml`
   present — a no-op for all existing deploys.
2. Run `promptdeploy settings init` (pulling all seven claude targets) to generate
   the first `settings.yaml`; hand-refactor per-target overrides into shared
   label/group overrides where natural; commit.
3. `promptdeploy deploy --only-type settings --dry-run` (optionally with
   `--target-root`) to preview, then deploy for real. Existing hook/MCP entries
   and external hooks remain in place.
4. Thereafter edit `settings.yaml` centrally; use `reconcile` to fold in host-side
   changes.

## Rollback

Delete `settings.yaml` (deploy then removes the managed keys via `remove_settings`
on the next run, or leave them — they are the last-good values) and revert the
code changes. `hooks`/`mcpServers`/external content is never altered by this
feature, so rollback cannot damage categories 1 or 2.
