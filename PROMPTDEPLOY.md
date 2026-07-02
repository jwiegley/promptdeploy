# promptdeploy

Deploy agents, commands, skills, prompts, MCP servers, hooks, models, marketplaces, and settings from a single source repository to multiple environments: Claude Code, OpenAI Codex, Factory Droid, OpenCode, and Emacs gptel.

## Targets

| Target | Type | Path |
|--------|------|------|
| claude-personal | Claude Code | ~/.config/claude/personal |
| claude-positron | Claude Code | ~/.config/claude/positron |
| droid | Factory Droid | ~/.factory |
| codex-hera | OpenAI Codex | hera:~ |
| opencode-vulcan | OpenCode | vulcan:~/.config/opencode |
| opencode-hera | OpenCode | hera:~/.config/opencode |
| opencode-clio | OpenCode | clio:~/.config/opencode |
| codex-clio | OpenAI Codex | clio:~ |
| claude-vulcan | Claude Code | vulcan:~/.claude |
| claude-vps | Claude Code | vps:~/.claude |
| claude-andoria | Claude Code | andoria-08:~/.claude |
| codex-andoria | OpenAI Codex | andoria-08:~ |
| gptel-emacs | gptel (Emacs) | ~/.emacs.d/prompts |

Targets and groups are defined in `deploy.yaml` at the repository root -- that file is the source of truth; this table mirrors it. Host-qualified paths are remote targets, deployed via rsync over SSH. Run `promptdeploy list` to see what is currently managed on each target.

## Commands

```
promptdeploy deploy [--dry-run] [--force] [--target TARGET] [--target-root DIR] [--only-type TYPE] [--verbose|--quiet]
promptdeploy validate
promptdeploy status [--target TARGET] [--target-root DIR]
promptdeploy list [--target TARGET] [--target-root DIR]
promptdeploy settings init [--from REF] [--target TARGET] [--force]
promptdeploy settings reconcile [--target TARGET] [--apply]
```

- **deploy** -- Copy every managed item type (agents, commands, skills, prompts, MCP servers, models, hooks, marketplaces, settings) to target environments. Items unchanged since the last deploy are skipped. Items removed from the source are cleaned up from targets.
- **validate** -- Check all source items for YAML errors, invalid environment IDs, and missing required fields. Also warns when an MCP server's `env` or `headers` references a `${VAR}` not declared in `.env.example` (the check is skipped when no `.env.example` exists).
- **status** -- Compare source items against deployed manifests. Shows new (A), modified (M), deleted (D), and current items.
- **list** -- Show all items currently managed by promptdeploy in each target.
- **settings init** -- Bootstrap `settings.yaml` from live Claude hosts: shared values become `base`, per-host differences become `overrides`.
- **settings reconcile** -- Report where live hosts have drifted from `settings.yaml`; with `--apply`, fold that drift back into `overrides`.

### Flags

- `--dry-run` -- Show what would happen without making changes.
- `--force` -- Deploy items even when unchanged since the last run, and overwrite pre-existing unmanaged items.
- `--target TARGET` -- Limit to specific targets. Repeatable. Accepts group names (e.g., `claude`).
- `--target-root DIR` -- Redirect all deployment output under `DIR`, one subdirectory per target id. Strips `host:` so remote targets are previewed locally. Also accepted by `status` and `list`.
- `--only-type TYPE` -- Limit to `agents`, `commands`, `skills`, `mcp`, `models`, `hooks`, `marketplaces`, `prompts`, or `settings`. Repeatable.
- `--verbose` -- Show diffs and timing.
- `--quiet` -- Suppress output except errors and change counts.

## Environment Filtering

Items can be restricted to specific targets using YAML frontmatter:

```yaml
---
name: my-agent
only:
  - claude
---
```

- `only: [claude]` -- Deploy only to the `claude` group.
- `except: [droid]` -- Deploy everywhere except Factory Droid.
- Both cannot be used on the same item.
- Group names (defined in `deploy.yaml`) expand to their members.

### Filetags

Labels can also be embedded in the filename itself using the filetags convention: `basename -- tag1 tag2.md`. The separator is ` -- ` (space-dash-dash-space); if it appears more than once, only the rightmost occurrence splits tags from the basename. The item deploys under `basename` (unless a `name:` in its metadata overrides it), and each tag acts as an implicit `only` label with AND semantics -- the target must match *every* tag. Filetags compose with frontmatter `only`/`except`: both filters must pass. They work on agents, commands, prompts, MCP servers, hooks, and marketplaces (file stems) as well as skills (directory names). `promptdeploy validate` rejects tags that are not valid target, group, or label names.

## MCP Servers (Claude targets): `.claude.json`

`promptdeploy` merges MCP server definitions into each local Claude target's `$CLAUDE_CONFIG_DIR/.claude.json` under the top-level `mcpServers` key -- the user-scope surface Claude Code reads natively. (Claude Code does **not** read `mcpServers` from `settings.json`; its MCP read surfaces are `.claude.json`, project `.mcp.json`, plugin-provided servers, claude.ai connectors, enterprise `managed-mcp.json`, and the per-invocation `--mcp-config` flag.) The merge is surgical -- only the named server keys are written, and every other key in the app-owned file (OAuth session, caches, per-project state) is preserved -- so plain `claude` picks the servers up with no wrapper or flags.

Two consequences shape the deploy model:

- **`.claude.json` is never rsynced.** Local Claude targets merge directly into their profile's `.claude.json`. Remote Claude targets perform a surgical SSH-stdin merge into the remote `.claude.json` so OAuth state, caches, and session history stay on that host.
- **Deploy with sessions closed.** A live `claude` session rewrites `.claude.json` wholesale from memory with no locking, so an MCP deploy run alongside an open session can be lost (or clobber the session's own changes). This is exactly the constraint `claude mcp add` operates under.

`${VAR}` references in `env` and `headers` are strict-expanded at deploy time and the resolved values are baked into `.claude.json` (mode `0600`). A missing variable fails the deploy with `EnvVarError`, and the manifest hash includes the current values so secret rotation triggers a refresh.

To verify a profile's MCP servers end to end, run a headless probe and inspect the init event's `mcp_servers`:

```bash
claude -p "Say ok" --output-format json --max-turns 1
```

Design history, including the rejected `--mcp-config` launcher-bridge approach: `docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md`.

## Model Injection (Claude targets)

Model injection is **off by default.** When an effective model resolves, `promptdeploy` injects a `model:` field into a deployed agent's or skill's frontmatter; commands, MCP servers, hooks, and models are never touched. The effective model is resolved in this order:

1. **Per-target override** -- `model:` set on a specific target in `deploy.yaml` wins.
2. **Global default** -- `providers.anthropic.claude.default_model` in `models.yaml`. **No `anthropic` provider is configured today, so there is no global default and nothing is injected unless you set a per-target override.**
3. **No injection** -- if neither is set, no `model:` field is written.

This default-off stance is deliberate (2026-06-13). Claude Code subagents default to `inherit` (they follow your `/model` choice); injecting a model everywhere overrides that. For skills, `model:` is only a *per-turn* override that force-switches the session model whenever the skill runs -- rarely what you want. Leaving injection off lets agents and skills inherit your session model.

When you *do* set a per-target override, **prefer an alias (`fable`, `opus`, `sonnet`, `haiku`, `inherit`) over a full dated ID** -- aliases track the recommended version and survive model retirements. Injection overwrites any `model:` field authored in the source item.

### Per-target override

```yaml
# deploy.yaml
targets:
  claude-personal:
    type: claude
    path: ~/.config/claude/personal
    labels: [claude, personal, local]
    model: sonnet
```

Accepted values: any model alias accepted by Claude Code's `model:` frontmatter field (`opus`, `sonnet`, `haiku`, `fable`, `inherit`) or a full ID like `claude-opus-4-8`. Aliases are recommended over full IDs (they survive model retirements). The value is written verbatim. Setting `model:` on a non-claude target is a validation error.

### Global default

```yaml
# models.yaml
providers:
  anthropic:
    display_name: "Anthropic"
    only: [claude]
    claude:
      default_model: claude-opus-4-8
    models:
      claude-opus-4-8:
        display_name: "Claude Opus 4.8"
```

The `anthropic` provider itself is scoped to claude targets via `only: [claude]` (the auto-generated label group) so it does not leak into Droid or OpenCode configuration. The `models:` dict is informational -- `promptdeploy validate` warns when a claude target's effective model is neither listed there nor one of the always-accepted aliases (`fable`, `opus`, `sonnet`, `haiku`, `inherit`), catching typos. `models:` entries require no credentials; `base_url` and `api_key` are only required when a provider deploys to Droid or OpenCode.

The block above mirrors the repository's current `models.yaml`, so model injection is active: every agent and skill deployed to a claude target gets `model: claude-fable-5` injected (no target in `deploy.yaml` currently sets a per-target `model:` override).

## Settings (Claude targets)

`settings.yaml` at the repository root single-sources Claude Code's `settings.json`. It has two keys: a shared `base:` and an optional `overrides:` map keyed by target id or group name.

```yaml
# settings.yaml
base:
  effortLevel: low
  env:
    EDITOR: vim
overrides:
  claude-positron:        # exact target id -- wins over any group
    effortLevel: high
  positron:               # a group from deploy.yaml
    env:
      FAST: "1"
```

### Rendering

For each claude target, `promptdeploy` renders the effective settings by starting from `base` and applying every matching `overrides` entry as a JSON Merge Patch ([RFC 7396](https://www.rfc-editor.org/rfc/rfc7396)):

- A value of `null` deletes that key; nested objects merge deeply.
- Group/label overrides apply first, in file order; the exact target id override applies last, so an exact match always wins over a group.
- `hooks`, `mcpServers`, `extraKnownMarketplaces`, and `enabledPlugins` are stripped from the rendered result (they are managed by the `hooks/`, `mcp/`, and `marketplaces/` deploy paths, not here), along with any leftover `null` values.

### Gentle merge into settings.json

Deploy merges only the rendered top-level keys into the target's `settings.json`. The keys it manages are recorded per target in the manifest (`managed_keys`), so:

- Keys you add under `settings.yaml` are written.
- Keys you later remove from `settings.yaml` are removed from `settings.json` on the next deploy.
- `hooks`, `mcpServers`, `extraKnownMarketplaces`, `enabledPlugins`, and any keys you never put under `settings.yaml` are left untouched.

Removing `settings.yaml` (or filtering a target out) removes exactly the previously-managed keys. Droid and OpenCode targets skip settings entirely. Remote claude targets are covered: the rendered settings are written into the staging tree and synced over rsync like everything else.

### init and reconcile

```bash
# Build settings.yaml from what is already on your hosts.
promptdeploy settings init [--from REF] [--target TARGET] [--force]

# Show host drift relative to settings.yaml; --apply folds it into overrides.
promptdeploy settings reconcile [--target TARGET] [--apply]
```

`settings init` reads live `settings.json` from each selected target, factors the values shared across all of them into `base`, and records per-target differences as `overrides` (use `--from` to pick which target seeds the base, `--force` to overwrite an existing file). `settings reconcile` compares each host against the rendered settings and reports drift; with `--apply` it writes that drift back into `overrides`. Write-back uses ruamel.yaml, so existing comments and formatting in `settings.yaml` are preserved.

### statusline-command.sh

The `statusLine` entries in `settings.yaml` point each host at its own local copy of `statusline-command.sh` (per-host absolute paths). The script is host-managed: `promptdeploy` does not deploy it, and the copy at the repository root is a reference master kept in sync by hand.

## Marketplaces (Claude targets)

Each `marketplaces/*.yaml` file declares one Claude Code plugin marketplace and the plugins enabled from it. Marketplaces are Claude-only; Droid, OpenCode, and gptel skip them. A marketplace file drives two top-level `settings.json` keys: `extraKnownMarketplaces` (map of marketplace name to `{source, autoUpdate?}`) and `enabledPlugins` (map of `"<plugin>@<marketplace>"` to a boolean).

```yaml
# marketplaces/acme.yaml
name: acme                     # defaults to filename stem; no @ or whitespace
description: Acme's plugins
source:                        # optional; omit for built-in marketplaces
  source: github
  repo: acme/claude-plugins
autoUpdate: true               # optional; copied into the marketplace entry
plugins:                       # optional; each becomes "<plugin>@acme"
  formatter: true
  linter: false
enabled: true                  # false removes this marketplace's entries
```

- **Source-less (built-in) marketplaces** -- Omit `source` for marketplaces that ship with Claude Code (e.g. `claude-plugins-official`); only `enabledPlugins` entries are written, never an `extraKnownMarketplaces` entry.
- **Ownership** -- Each `enabledPlugins` key is self-tagged with `@<marketplace>`, so no extra metadata is needed. On redeploy or removal, `promptdeploy` reclaims exactly the keys whose part after the final `@` equals this marketplace's name, plus `extraKnownMarketplaces[name]`. Unrelated entries from other marketplaces are never touched.
- **Migration** -- Marketplaces deploy after settings in the same run, so keys formerly managed via `settings.yaml` are popped before the marketplace files re-add their own entries.

See `marketplaces/schema.md` for the full field reference.

## Development

### Running from source with Nix (recommended)

The repository includes a `flake.nix` and `.envrc`. With [direnv](https://direnv.net/) installed:

```bash
cd ~/src/promptdeploy
direnv allow
```

This drops you into a shell with Python 3.12, PyYAML, Jinja2, ruamel.yaml, pytest, pytest-cov, mypy, and ruff.

Run deployment through the flake:

```bash
nix run . -- --dry-run
nix run .
```

### Running from source with a virtualenv

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/promptdeploy deploy --dry-run
```

### Running tests

```bash
# All tests
pytest

# With coverage report
pytest --cov=promptdeploy --cov-report=term-missing

# Single module
pytest tests/test_deploy.py -v
```

Coverage is enforced at 100% with branch measurement enabled (`branch = true`, `fail_under = 100` in `pyproject.toml`); the run fails if any line *or branch* goes uncovered. mypy runs in strict mode for the package (relaxed for `tests.*`), and ruff enforces a curated lint baseline (`E`, `F`, `W`, `I`, `UP`, `B`, `SIM`, `C4`, `RUF`).

### Project layout

```
src/promptdeploy/
  cli.py             # Argument parsing and command dispatch
  config.py          # deploy.yaml loading (Config, TargetConfig)
  source.py          # Source item discovery (all item types)
  frontmatter.py     # YAML frontmatter parsing and transformation
  filetags.py        # `basename -- tag1 tag2` filename label parsing
  filters.py         # only/except environment filtering with group expansion
  envsubst.py        # ${VAR} expansion (.env loading, lenient and strict modes)
  poet.py            # Prompt Poet (.poet/Jinja) parsing and rendering
  manifest.py        # SHA256 hash tracking for change detection
  deploy.py          # Core deploy orchestration
  settings.py        # settings.yaml rendering (JSON Merge Patch)
  settings_sync.py   # settings init/reconcile against live hosts
  ssh.py             # rsync/ssh transport for remote targets
  validate.py        # Source item validation
  status.py          # Deployment status comparison
  output.py          # Verbosity levels and formatted output
  targets/
    base.py          # Abstract Target interface
    claude.py        # Claude Code target
    droid.py         # Factory Droid target
    opencode.py      # OpenCode target
    gptel.py         # Emacs gptel-prompts target (prompts only)
    remote.py        # RemoteTarget wrapper (local staging + rsync over SSH)

tests/               # pytest suite (100% coverage enforced)
deploy.yaml          # Target environment definitions
models.yaml          # Custom model providers
settings.yaml        # Claude Code settings.json master (base + overrides)
prompts/             # Prompt Poet / plain prompt sources
hooks/               # Claude Code hook group YAML definitions
mcp/                 # MCP server YAML definitions
  schema.md          # MCP YAML schema documentation
marketplaces/        # Claude plugin marketplace YAML definitions
  schema.md          # Marketplace YAML schema documentation
```

## System Installation

### Building with Nix

```bash
nix build
./result/bin/promptdeploy --help
```

### Adding to a NixOS/nix-darwin configuration

In your system flake (e.g., `~/src/nix/flake.nix`), add the input:

```nix
promptdeploy.url = "git+file:///Users/johnw/src/promptdeploy";
```

Then reference the package wherever you build your package list:

```nix
inputs.promptdeploy.packages.${system}.default
```

Rebuild your system to install `promptdeploy` to the Nix profile.

## How It Works

1. **Discovery** -- Scans all nine item types in the source repo: `agents/*.md`, `commands/*.md`, `skills/*/SKILL.md`, `mcp/*.yaml`, `models.yaml`, `hooks/*.yaml`, `prompts/*` (`.poet`/`.j2`/`.jinja`/`.jinja2`/`.txt`/`.md`/`.org`/`.json`), `settings.yaml`, and `marketplaces/*.yaml`. Marketplaces are discovered after settings so that, during a migration, the settings item releases formerly-managed keys before marketplace items re-add their own entries in the same run.
2. **Filtering** -- Evaluates filename filetags and `only`/`except` frontmatter against each target, expanding group names.
3. **Change detection** -- Computes SHA256 hashes and compares against the manifest from the last deploy. Unchanged items are skipped (unless `--force`).
4. **Deployment** -- Writes each item in the format the target expects:
   - Claude Code: agents/, commands/, skills/ directories (agents and skills get `model:` injection when configured); prompts render to `commands/{name}.md`; MCP merges into `.claude.json`'s `mcpServers` with deploy-time-expanded `env`/`headers`; hooks merge into settings.json with `_source` tagging; marketplaces merge into top-level `extraKnownMarketplaces`/`enabledPlugins`; `settings.yaml` keys gently merge into settings.json; models are skipped.
   - OpenAI Codex: agents render to `.codex/agents/{name}.toml`; commands render to `.agents/skills/command-{name}/` generated skills; prompts render to `.agents/skills/prompt-{name}/`; skills copy to `.agents/skills/{name}/`; MCP servers and Codex model providers merge into managed `.codex/config.toml` blocks; hooks write to `.codex/hooks.json`; marketplaces and settings are skipped.
   - Factory Droid: agents go to droids/; commands are skipped (unless `droid_deploy: skill`); prompts and skills become `skills/{name}/` directories; MCP merges into mcp.json with a `type` field; models go to settings.json `customModels`; hooks, marketplaces, and settings are skipped.
   - OpenCode: agents/, commands/, skills/ layout; prompts render to `commands/{name}.md`; MCP merges into opencode.json with `command` as an array and `environment` instead of `env`; models go under opencode.json's `provider` key; hooks, marketplaces, and settings are skipped.
   - gptel: prompts only -- `.poet` sources deploy as `{name}.poet` for gptel-prompts.el to process directly; Jinja-flavoured Poet sources render to `{name}.json`; plain prompts are copied verbatim; every other item type is skipped.
5. **Cleanup** -- Items present in the old manifest but absent from the current source are removed. Pre-existing unmanaged items are never touched.
6. **Manifest update** -- A `.prompt-deploy-manifest.json` is saved atomically to each target directory.
7. **Remote targets** -- For targets with `host:`, steps 4-6 run against a local staging directory: remote state is pulled before deploying and pushed back afterwards via rsync over SSH.
