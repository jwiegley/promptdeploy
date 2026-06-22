# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Repository Purpose

A single-source repository of AI coding prompts (agents, commands, skills, prompts, MCP servers, hooks, models, marketplaces, settings) deployed to five target environments -- Claude Code, OpenAI Codex, Factory Droid, OpenCode, and gptel (Emacs) -- using the included `promptdeploy` Python CLI tool.

## Content Types

| Type | Location | Format | Notes |
|------|----------|--------|-------|
| Agents | `agents/*.md` | Markdown + YAML frontmatter (`name`, `description`) | Pro agents, reviewers, specialists |
| Commands | `commands/*.md` | Plain Markdown, `$ARGUMENTS` placeholder | Some have frontmatter for filtering |
| Skills | `skills/*/SKILL.md` | Directory with `SKILL.md` (YAML frontmatter) + optional files | `translate-en` symlinks into the `translate-tool` git submodule (run `git submodule update --init` after cloning) |
| Prompts | `prompts/*.{poet,j2,jinja,jinja2,txt,md,org,json}` | Poet files: YAML list of role/content turns + Jinja2, optional leading `# key: value` comment frontmatter; plain files become a single system turn | Rendered per target by `poet.py`; gptel copies `.poet` files directly and consumes only prompts |
| MCP Servers | `mcp/*.yaml` | YAML with `name`, transport (`command`+`args` or `url`+`headers`), `env`, `scope` | Schema in `mcp/schema.md`; on claude targets deployed into `.claude.json` (local profiles only -- see Key Patterns) |
| Hooks | `hooks/*.yaml` | YAML with `name`, event handlers, matchers | Claude Code and Codex |
| Marketplaces | `marketplaces/*.yaml` | YAML with `name`, optional `source`, `plugins` map | Claude-only; drives top-level `extraKnownMarketplaces` + `enabledPlugins` in `settings.json`. Schema in `marketplaces/schema.md` |
| Models | `models.yaml` | Single YAML file, providers with nested models | Droid and OpenCode consume the full config; Codex consumes providers with `codex:` config; claude targets read only `providers.anthropic.claude.default_model` for model injection -- currently absent, so injection is OFF (see Key Patterns) |
| Settings | `settings.yaml` | Single YAML, `base:` + `overrides:` (per target/group) | Claude-only; rendered per target and gently merged into `settings.json` (managed top-level keys only; the four MANAGED_ELSEWHERE keys hooks/mcpServers/extraKnownMarketplaces/enabledPlugins untouched) |

All content items support `only`/`except` filtering by target or group name (defined in `deploy.yaml`). Items can also embed *filetags* in the filename (or skill directory name) after a ` -- ` separator, e.g. `heavy -- positron local.md` (`filetags.py`): each tag must match the target (AND semantics), evaluated before and composed with `only`/`except`.

## promptdeploy Architecture

`src/promptdeploy/` -- Pure Python, dependencies PyYAML + Jinja2 + ruamel.yaml. `src` layout with setuptools.

### Pipeline

1. **CLI** (`cli.py`) -- argparse, 5 subcommands: `deploy`, `validate`, `status`, `list`, `settings` (with `init`/`reconcile`)
2. **Config** (`config.py`) -- Loads `deploy.yaml` from CWD or ancestors. `Config`/`TargetConfig` dataclasses. `remap_targets_to_root()` for `--target-root` preview
3. **Discovery** (`source.py`) -- `SourceDiscovery` scans all item types. `SourceItem` uses singular `item_type` (`agent`, `command`, `skill`, `mcp`, `models`, `hook`, `marketplace`, `prompt`, `settings`). `discover_all` yields `marketplace` *after* `settings`: during migration the settings item must deploy first to pop the formerly settings.yaml-managed `extraKnownMarketplaces`/`enabledPlugins` keys (tracked in the settings manifest's `managed_keys`) before marketplace items re-add their entries in the same run.
4. **Poet rendering** (`poet.py`) -- `parse_poet` parses `.poet`/Jinja prompt files (YAML list of role/content turns, optional `# key: value` comment frontmatter); `render_for_command` produces slash-command Markdown (claude/opencode→`commands/{name}.md`, droid→`skills/{name}/SKILL.md`); `render_for_gptel` produces the JSON array used for Jinja-flavoured gptel prompt sources. Native `.poet` files are copied directly by the gptel target. Undefined Jinja variables degrade to literal `{{ name }}` placeholders and surface as deploy warnings instead of raising.
5. **Filtering** (`filters.py`) -- `should_deploy_to()` evaluates filetags first (AND semantics), then `only`/`except` with group expansion
6. **Deploy** (`deploy.py`) -- Orchestrates targets × items, computes SHA256 hashes, returns `List[DeployAction]`. Maps between naming conventions: `_TYPE_TO_CATEGORY` (singular→plural for manifests), `_CLI_TYPE_TO_ITEM_TYPE` (CLI plural→singular)
7. **Targets** (`targets/`) -- Abstract `Target` ABC in `base.py`, five local implementations + remote wrapper:
   - `claude.py` -- Writes `.md` files; merges MCP into `.claude.json` `mcpServers` (surgically, only the named keys; *local* claude targets via the `manage_mcp` flag, *remote* claude targets via the `RemoteTarget` SSH-stdin direct-merge intercept -- never via `.claude.json` rsync and never via the `claude` CLI); merges hooks with `_source` tagging for independent group updates; merges marketplaces into top-level `extraKnownMarketplaces`/`enabledPlugins` (ownership derived from the `@<marketplace>` key suffix). `deploy_settings` strips four keys (see settings.yaml)
   - `codex.py` -- Writes agents to `.codex/agents/*.toml`; commands to `.agents/skills/command-{name}/` generated skills; prompts to `.agents/skills/prompt-{name}/` generated skills; skills to `.agents/skills/{name}/`; MCP/model providers to managed `.codex/config.toml` blocks; hooks to `.codex/hooks.json`; settings and marketplaces skipped
   - `droid.py` -- Agents→`droids/`; commands skipped unless `droid_deploy: skill` in frontmatter; MCP→`mcp.json` with `type` field; models→`settings.json` `customModels` with provider-type formatting
   - `opencode.py` -- Standard layout; MCP→`opencode.json` with `command` as array, `environment` instead of `env`; models→`opencode.json` under `provider` key
   - `gptel.py` -- Prompts only: native `.poet` sources copy to `{name}.poet`; Jinja-flavoured Poet sources render to `{name}.json` (via `render_for_gptel`) for `gptel-prompts.el`; plain prompts are copied verbatim; all other item types are silently skipped
   - `remote.py` -- `RemoteTarget` wrapper; delegates to inner target operating on a local staging dir; syncs via rsync over SSH. For a claude inner (`remote_mcp=True`) it intercepts MCP: each set/pop is accumulated and flushed as one SSH-stdin direct merge into the remote `.claude.json` (`flush_remote_mcp`, called from the deploy loop *before* `save_manifest`), strict-expanding `env`/`headers` secrets at deploy time
8. **SSH Transport** (`ssh.py`) -- `ssh_pull`/`ssh_push`/`ssh_exists` via `rsync -az --delete` and `ssh`; `build_claude_merge_script` + `ssh_stdin` for the remote-MCP direct merge (base64-embedded ops piped to `python3 -`). `_SSH_OPTS` uses `StrictHostKeyChecking=yes` (fail closed). No Python SSH dependencies.
9. **Manifest** (`manifest.py`) -- SHA256 change detection. Atomic writes via `tempfile.mkstemp()` + `os.replace()`

### Key Patterns

- **Atomic file writes everywhere** -- All JSON/manifest writes use temp file + `os.replace()`. New code writing files must follow this pattern.
- **Manifest tracks managed items** -- Only manifest-tracked items are updated/removed; unmanaged items in target directories are never touched.
- **`_source` tagging on hooks** -- Each hook entry in `settings.json` gets `_source: <group-name>` so multiple groups can coexist on the same event type without interference.
- **Marketplace ownership via `@<marketplace>` key suffix** -- Unlike hooks, marketplace entries need no `_source` tag: each `enabledPlugins` key is `"<plugin>@<marketplace>"`, so ownership is derivable. Reclamation matches the marketplace part exactly via `key.rsplit("@", 1)[-1] == name` (a marketplace named `official` must not claim `x@plugins-official`). `extraKnownMarketplaces[name]` is keyed directly. Source-less marketplaces (built-ins like `claude-plugins-official`) write only `enabledPlugins`.
- **MCP deployment to `.claude.json` (claude targets)** -- Claude Code reads user-scope MCP servers from `$CLAUDE_CONFIG_DIR/.claude.json` (top-level `mcpServers`), never from `settings.json`. *Local* claude: `ClaudeTarget.deploy_mcp_server` merges each server into `.claude.json` -- surgically, touching only its named key and preserving all other app-owned state (OAuth, caches, per-project history) -- so plain `claude` picks them up with no wrapper; `${VAR}` in `env`/`headers` is written verbatim and expands at runtime. *Remote* claude (`RemoteTarget`, `remote_mcp=True`): since `.claude.json` is machine-specific and must never be rsynced, MCP is deployed by an **SSH-stdin direct surgical merge into `<remote_path>/.claude.json`** -- not the `claude` CLI, not rsync. Ops are accumulated (set/pop) and flushed by `flush_remote_mcp` in the deploy loop **before `save_manifest`**, so a failed flush leaves the manifest untouched and the next run self-heals; `finalize`/`cleanup` (dry-run + error) discard the ops with no SSH. The flush builds a `python3` merge program with the ops embedded as `base64(json(ops))` and pipes it to `ssh <host> python3 -` on **stdin** (the secret is never an argv token, never in `ps`/`/proc/<pid>/cmdline`); the program loads-or-empties the file, sets/pops `mcpServers[name]`, drops an empty `mcpServers`, and writes atomically (`mkstemp` mode `0600` + `os.replace`, never widened), with its whole body wrapped in an outer try/except so any error prints only a fixed diagnostic (never the payload/values). `${VAR}` in `env`/`headers` is **strict-expanded at deploy time** (baked into the remote file). `--dry-run` does no SSH merge and no write (it still `ssh_pull`s in `prepare`); `--target-root` (host stripped) previews a remote-MCP target as a **local `.claude.json` write with verbatim `${VAR}`**, NOT the remote baked form. Operational note: `.claude.json` is rewritten wholesale by any live `claude` session (local or remote), so deploy MCP with sessions closed (the same constraint `claude mcp add` has). Standard per-profile verification probe: `claude -p "Say ok" --output-format json --max-turns 1` and inspect the init event's `mcp_servers`. Design history: `docs/superpowers/specs/2026-06-15-remote-mcp-ssh-stdin-direct-merge.md` (remote-MCP final), and the rejected `--mcp-config` launcher bridge in `docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md`.
- **Environment variable handling (`${VAR}` policy per target)** -- `envsubst.py` expands `${VAR}` from `os.environ` (after `.env` auto-load). Claude (local): MCP `env` and `headers` pass through VERBATIM -- `${VAR}` expands at runtime when Claude Code reads `.claude.json`, so secrets never land in deployed config; do NOT reintroduce deploy-time expansion on the local path. The local mcp manifest hash is source-bytes only, so a rotated secret does NOT redeploy. Caveat: an unset variable expands to EMPTY at runtime there, hence the validate warning below. Claude (remote): the opposite -- `env`/`headers` `${VAR}` are **strict-expanded at deploy time** and baked into the remote `.claude.json`, transported only over SSH stdin (so the secret is never an argv token); a missing var raises `EnvVarError` -> exit 1. The remote-MCP manifest hash **folds env values** (mirroring `models` via `_expand_env_for_hash`, gated on the `target.remote_mcp_hash` capability), so a rotated secret triggers a redeploy and running `status`/`deploy` without the secret exported reports the server as `changed` (`deploy` auto-loads `.env`; `status` does not). `${VAR}` is only honored in `env`/`headers` for both paths. Droid: MCP `env`/`headers` verbatim; only the models provider `api_key` expands, via lenient `expand_env_vars` (unset vars stay as literal `${VAR}` with a stderr warning). OpenCode: strict deploy-time expansion via `expand_env_vars_strict` for `models.providers.*.api_key`, `mcp.*.env.*`, and `mcp.*.headers.*` -- a missing var raises `EnvVarError` and the CLI exits 1, since OpenCode runs from a directory that won't have those vars set. `promptdeploy validate` warns when an MCP `env`/`headers` value references a `${VAR}` not declared in `.env.example` (no `.env.example` disables the check).
- **Frontmatter transformation** -- `frontmatter.py` `transform_for_target()` strips deployment metadata (`only`/`except`) before writing to targets.
- **Models filtering** -- `only`/`except` applies at both provider and individual model level.
- **Provider overrides** -- A provider may define an `overrides:` mapping of target ID or group → partial provider config. During deploy, `_filter_models_config` shallow-merges the matching override (group keys are expanded via `Config.groups`) over the provider's defaults; the `overrides` key is stripped from the result and `models`/`overrides` keys inside an entry are ignored. Use this to vary per-target fields like `base_url` without forking providers.
- **Remote deployment** -- Targets with `host:` in `deploy.yaml` are deployed via rsync over SSH. The `Target` ABC has `prepare()`/`finalize()`/`cleanup()` lifecycle hooks (no-ops for local targets). `RemoteTarget` wraps any inner target, using a local staging dir: `prepare()` pulls remote state, `finalize()` pushes back. Path `~` expansion is skipped for remote targets (rsync expands `~` on the remote). `--target-root` strips `host` to force local preview.
- **Claude model injection (OFF by default; opt-in per target)** -- The mechanism exists but is dormant: `ClaudeTarget` injects a `model:` field into deployed agent/skill frontmatter only when an effective model resolves. `create_target` resolves it from `TargetConfig.model` (per-target override in `deploy.yaml`) with fallback to `load_anthropic_default_model` (`providers.anthropic.claude.default_model` in `models.yaml`); `None` skips injection. There is intentionally **no global default** -- the `anthropic` provider was removed (2026-06-13) because injecting a model everywhere overrides Claude Code's `inherit` default (so agents stop following the user's `/model` choice) and, for skills, `model:` is only a *per-turn* override that force-switches the model whenever the skill runs. **If you do set a per-target `model:`, use an alias (`fable`/`opus`/`sonnet`/`haiku`/`inherit`), not a full dated ID** -- aliases track the recommended version and survive model retirements (per `code.claude.com/docs/en/model-config`). Commands, MCP, hooks, and models are not touched.

### settings.yaml

A repo-level `settings.yaml` is a single-source master for Claude Code `settings.json`, rendered per target and deployed as a first-class item (claude targets only; droid/opencode/gptel skip it).

- **Render** (`settings.py`, pure, no I/O) -- `render_settings(doc, target_id, config)` starts from `base:` and applies each matching `overrides:` entry as an RFC 7396 JSON Merge Patch (`null` deletes a key). Group/label overrides apply first in file order; the exact `target_id` override applies last, so an exact match wins over any group. The four `MANAGED_ELSEWHERE` keys (`hooks`/`mcpServers`/`extraKnownMarketplaces`/`enabledPlugins`) and any remaining `null` values are stripped, so only plain managed keys reach the deploy layer — settings.yaml never fights the `hooks/`, `mcp/`, or `marketplaces/` item types over those keys. `settings_sync.py` reuses the same `MANAGED_ELSEWHERE` constant. `apply_merge_patch`/`generate_merge_patch` are the forward/inverse merge-patch primitives.
- **Deploy** (dedicated `settings` branch in `deploy.py`) -- `ClaudeTarget.deploy_settings` gently merges the rendered top-level keys into `settings.json`: it sets each rendered key and removes only keys that were previously managed (tracked per-target in the manifest's `managed_keys`) but are no longer rendered. Unmanaged keys plus the `MANAGED_ELSEWHERE` keys and external/unknown keys are left untouched. Removing `settings.yaml` (or filtering a target out) removes exactly the previously-managed keys via `remove_settings`. `RemoteTarget` delegates `deploy_settings`/`remove_settings`/`read_settings_json` to its inner target, so remote claude hosts are covered.
- **CLI** -- `settings init [--from REF] [--target T] [--force]` bootstraps `settings.yaml` from live hosts (factoring a `base` plus per-target `overrides`); `settings reconcile [--target T] [--apply]` reports host drift relative to `settings.yaml` and, with `--apply`, folds that drift back into `overrides` (a key the host deleted but `base` has becomes a `null` override; ruamel.yaml write-back preserves comments). Both read live state through a target's `read_settings_json` lifecycle.
- **statusline-command.sh** -- host-managed: the `statusLine` entries in `settings.yaml` point each host at its own local copy, and promptdeploy does not deploy the script itself. The repo-root `statusline-command.sh` is a reference master only (the former `statusline-debug.sh` was removed).

## Development Commands

```bash
# Enter dev shell (Python 3.12 + all deps via Nix) -- reproducible entry point
nix develop

# Alternative: direnv (note: .envrc is untracked/machine-local, not reproducible)
direnv allow

# Run deployment through the flake
nix run . -- --dry-run
nix run .

# Run all tests (100% line+branch coverage enforced in pyproject.toml)
PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing

# Run a single test file
PYTHONPATH=src python -m pytest tests/test_deploy.py -v

# Run a single test
PYTHONPATH=src python -m pytest tests/test_deploy.py::TestDeploy::test_name -v

# Run all Nix flake checks (ruff, mypy, pytest, build)
nix flake check

# Build Nix package only
nix build

# Install editable with pip (alternative to Nix)
pip install -e ".[dev]"
```

The Nix dev shell also provides `mypy` and `ruff`. The gates are strict: mypy runs in `strict` mode (relaxed only for `tests.*`), ruff enforces a curated lint baseline (`E`, `F`, `W`, `I`, `UP`, `B`, `SIM`, `C4`, `RUF`), and coverage measures branches as well as lines (`branch = true`, `fail_under = 100`) -- all configured in `pyproject.toml` and enforced via `nix flake check`.

The flake also exports `homeManagerModules.default` (`nix/hm-module.nix`): with `programs.promptdeploy.enable` set, it runs `promptdeploy deploy --quiet` from `sourceDir` on every home-manager activation. With `targets` unset it deploys `--target local` only (the `local`-labelled targets), so activation never reaches remote hosts over SSH unless targets are named explicitly. A failed deploy does not abort activation, but it is not silent: all promptdeploy output is captured to `$XDG_STATE_HOME/promptdeploy/deploy.log` (default `~/.local/state/promptdeploy/deploy.log`) and a warning naming that log is printed on failure.

## CI

`.github/workflows/ci.yml` runs `nix flake check` which executes all 5 checks defined in `flake.nix`: `ruff format --check`, `ruff check` (curated `select` baseline), `mypy` (strict), `pytest` with 100% line+branch coverage gate, and `nix build`. `lefthook.yml` mirrors those as pre-commit checks with fast staged-file feedback, plus `nix flake check` as the authoritative full-tree gate, plus an `agnix` lint pass over Markdown/YAML/JSON/TOML files -- a sixth gate that is not part of the flake checks.

## deploy.yaml

Defines 15 targets classified by labels: `claude`, `codex`, `personal`, `positron`, `git-ai`, `gptel`, `local`, `remote`. Labels on targets auto-generate groups (merged with explicit groups). `--target positron` expands to `claude-positron` + `claude-andoria` + `codex-andoria`. Target types: `claude`, `codex`, `droid`, `opencode`, `gptel`. Remote targets add `host:` field.

The top-level `hosts:` key registers each listed hostname as a group containing every target whose `host:` matches it; the current machine's short hostname (override with `PROMPTDEPLOY_HOST`) also becomes a group containing all host-less targets. This is what `only: [hera]` / `only: [clio]` filters in `models.yaml` rely on.

## Environment Variables

API keys required for deployment (not dry-run). See `.env.example` for the full list. Export them in the shell, or put them in a repo-root `.env` file -- `promptdeploy deploy` auto-loads it (variables already set in the real environment take precedence over `.env` values).
