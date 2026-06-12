# promptdeploy

Deploy prompts, agents, skills, and MCP servers from a single source repository to multiple AI coding tool environments.

## Targets

| Target | Type | Path |
|--------|------|------|
| claude-personal | Claude Code | ~/.config/claude/personal |
| claude-positron | Claude Code | ~/.config/claude/positron |
| claude-git-ai-local | Claude Code | ~/.config/claude/git-ai |
| claude-git-ai-remote | Claude Code | git-ai:~/.claude |
| droid | Factory Droid | ~/.factory |
| opencode | OpenCode | ~/.config/opencode |

Targets and groups are defined in `deploy.yaml` at the repository root.

## Commands

```
promptdeploy deploy [--dry-run] [--target TARGET] [--only-type TYPE] [--verbose|--quiet]
promptdeploy validate
promptdeploy status [--target TARGET]
promptdeploy list [--target TARGET]
promptdeploy settings init [--from REF] [--target TARGET] [--force]
promptdeploy settings reconcile [--target TARGET] [--apply]
```

- **deploy** -- Copy agents, commands, skills, and MCP servers to target environments. Items unchanged since the last deploy are skipped. Items removed from the source are cleaned up from targets.
- **validate** -- Check all source items for YAML errors, invalid environment IDs, and missing required fields.
- **status** -- Compare source items against deployed manifests. Shows new (A), modified (M), deleted (D), and current items.
- **list** -- Show all items currently managed by promptdeploy in each target.
- **settings init** -- Bootstrap `settings.yaml` from live Claude hosts: shared values become `base`, per-host differences become `overrides`.
- **settings reconcile** -- Report where live hosts have drifted from `settings.yaml`; with `--apply`, fold that drift back into `overrides`.

### Flags

- `--dry-run` -- Show what would happen without making changes.
- `--target TARGET` -- Limit to specific targets. Repeatable. Accepts group names (e.g., `claude`).
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

## Model Injection (Claude targets)

For every agent and skill deployed to a Claude Code target, `promptdeploy` injects a `model:` field into the YAML frontmatter so the deployed copy explicitly pins the model. Injection is applied only to agents and skills -- commands, MCP servers, hooks, and models are not touched.

The effective model is resolved in this order:

1. **Per-target override** -- `model:` set on a specific target in `deploy.yaml` wins.
2. **Global default** -- `providers.anthropic.claude.default_model` in `models.yaml`.
3. **No injection** -- if neither is set, no `model:` field is written.

Injection overwrites any `model:` field authored in the source item. Remove the source `model:` if you want deployed behavior to match source behavior exactly, or set a per-target override when a specific target should use a different model.

### Per-target override

```yaml
# deploy.yaml
targets:
  claude-personal:
    type: claude
    path: ~/.config/claude/personal
    labels: [claude, personal, local]
    model: claude-sonnet-4-6
```

Accepted values: any model alias accepted by Claude Code's `model:` frontmatter field (e.g., `opus`, `sonnet`, `haiku`, `claude-opus-4-7`, `inherit`). The value is written verbatim. Setting `model:` on a non-claude target is a validation error.

### Global default

```yaml
# models.yaml
providers:
  anthropic:
    display_name: "Anthropic"
    except: [droid, opencode, opencode-vulcan]
    claude:
      default_model: claude-fable-5
    models:
      claude-haiku-4-5-20251001:
        display_name: "Claude Haiku 4.5"
      claude-sonnet-4-6:
        display_name: "Claude Sonnet 4.6"
      claude-opus-4-8:
        display_name: "Claude Opus 4.8"
      claude-fable-5:
        display_name: "Claude Fable 5"
```

The `anthropic` provider itself is scoped to claude targets via `except:` so it does not leak into Droid or OpenCode configuration. The `models:` dict is informational -- it lets `promptdeploy validate` warn when a per-target `model:` references a model not listed here (typo detection). `models:` entries require no credentials; `base_url` and `api_key` are only required when a provider deploys to Droid or OpenCode.

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

For each claude target, `promptdeploy` renders the effective settings by starting from `base` and applying every matching `overrides` entry as a JSON Merge Patch ([RFC 7386](https://www.rfc-editor.org/rfc/rfc7386)):

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
cd ~/src/claude-prompts
direnv allow
```

This drops you into a shell with Python 3.12, PyYAML, pytest, pytest-cov, mypy, and ruff.

Run the tool from source:

```bash
PYTHONPATH=src python -m promptdeploy deploy --dry-run
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

Coverage is enforced at 98% via `pyproject.toml`. The current suite has 312 tests at 99.78% coverage.

### Project layout

```
src/promptdeploy/
  cli.py             # Argument parsing and command dispatch
  config.py          # deploy.yaml loading (Config, TargetConfig)
  source.py          # Source item discovery (agents/, commands/, skills/, mcp/)
  frontmatter.py     # YAML frontmatter parsing and transformation
  filters.py         # only/except environment filtering with group expansion
  manifest.py        # SHA256 hash tracking for change detection
  deploy.py          # Core deploy orchestration
  validate.py        # Source item validation
  status.py          # Deployment status comparison
  output.py          # Verbosity levels and formatted output
  targets/
    base.py          # Abstract Target interface
    claude.py        # Claude Code target
    droid.py         # Factory Droid target
    opencode.py      # OpenCode target

tests/               # 312 tests, 99.78% coverage
deploy.yaml          # Target environment definitions
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
claude-prompts.url = "git+file:///Users/johnw/src/claude-prompts";
```

Then reference the package wherever you build your package list:

```nix
inputs.claude-prompts.packages.${system}.default
```

Rebuild your system to install `promptdeploy` to the Nix profile.

## How It Works

1. **Discovery** -- Scans `agents/*.md`, `commands/*.md`, `skills/*/SKILL.md`, and `mcp/*.yaml` in the source repo.
2. **Filtering** -- Evaluates `only`/`except` frontmatter against each target, expanding group names.
3. **Change detection** -- Computes SHA256 hashes and compares against the manifest from the last deploy. Unchanged items are skipped.
4. **Deployment** -- Copies files to each target in the format it expects:
   - Claude Code: agents/, commands/, skills/ directories; MCP merges into settings.json.
   - Factory Droid: agents go to droids/; commands are skipped (unless `droid_deploy: skill`); MCP merges into mcp.json with a `type` field.
   - OpenCode: standard layout; MCP merges into opencode.json with `command` as an array and `environment` instead of `env`.
5. **Cleanup** -- Items present in the old manifest but absent from the current source are removed. Pre-existing unmanaged items are never touched.
6. **Manifest update** -- A `.prompt-deploy-manifest.json` is saved atomically to each target directory.
