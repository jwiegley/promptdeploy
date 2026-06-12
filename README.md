# promptdeploy

I've been using several different AI tools -- Claude Code, Factory Droid, OpenCode, and gptel in Emacs -- and quickly got tired of maintaining the same agents, commands, prompts, and configurations in four different places with four different formats. So I wrote `promptdeploy`: you define everything once in this repository, and it deploys to all your targets, handling the format differences for you.

It's a single Python CLI tool with a few small dependencies (PyYAML, Jinja2, ruamel.yaml). You describe what you want in Markdown files and YAML, point it at your targets in `deploy.yaml`, and it figures out the rest.

## What's in here

The repository holds these types of content, all defined in simple Markdown or YAML:

| Type | Location | What it is |
|------|----------|------------|
| Agents | `agents/*.md` | Specialized sub-agents (Markdown + YAML frontmatter) |
| Commands | `commands/*.md` | Slash command prompts, `$ARGUMENTS` for user input |
| Skills | `skills/*/SKILL.md` | Multi-file skill directories with a `SKILL.md` entry point |
| Prompts | `prompts/*` | Prompt Poet (`.poet`/Jinja) or plain prompts, rendered per target: slash-command Markdown for the coding tools, role/content JSON for gptel |
| MCP Servers | `mcp/*.yaml` | Model Context Protocol server definitions |
| Hooks | `hooks/*.yaml` | Claude Code hook groups for tool events |
| Marketplaces | `marketplaces/*.yaml` | Claude Code plugin marketplaces + enabled plugins (Claude-only) |
| Models | `models.yaml` | Custom model providers and their models |
| Settings | `settings.yaml` | Claude Code `settings.json`, single-sourced with `base:` + per-target `overrides:` |

Any item can use `only`/`except` in its frontmatter to control which targets it deploys to. Group names from `deploy.yaml` expand to their members. Labels can also be embedded directly in a filename with the filetags convention -- `heavy -- positron.md` deploys as `heavy`, and only to targets matching every listed label.

## Using promptdeploy

```bash
promptdeploy deploy [--dry-run] [--force] [--target TARGET] [--target-root DIR] [--only-type TYPE] [--verbose|--quiet]
promptdeploy validate    # check for YAML errors, missing fields
promptdeploy status      # show what's changed since last deploy
promptdeploy list        # show managed items per target
promptdeploy settings init [--from REF] [--target T] [--force]  # bootstrap settings.yaml from live hosts
promptdeploy settings reconcile [--target T] [--apply]          # pull host settings drift into overrides
```

The deploy pipeline works like this: discover all source items, filter by target, compute SHA256 hashes against the last manifest, write only what changed, clean up anything that's been removed from source. Unmanaged items in target directories are never touched. `--force` redeploys everything even when unchanged; `--target-root DIR` redirects all output under a scratch directory (one subdirectory per target id) so you can preview a deploy without touching real configuration.

### Targets

Targets are defined in `deploy.yaml`. Each has a type (`claude`, `droid`, `opencode`, or `gptel`) and a path. Remote targets add a `host:` field and deploy via rsync over SSH. The `gptel` type receives only prompts, rendered as JSON for Emacs' gptel-prompts; the other types receive the full content set.

```yaml
source_root: .
targets:
  claude-personal:
    type: claude
    path: ~/.config/claude/personal
  droid:
    type: droid
    path: ~/.factory
  opencode:
    type: opencode
    path: ~/.config/opencode
  gptel-emacs:
    type: gptel
    path: ~/.emacs.d/prompts
groups:
  claude:
    - claude-personal
```

### Environment variables

API keys and other secrets use `${VAR}` syntax in MCP and model definitions, resolved at deploy time from your shell environment plus a `.env` file at the repo root (which never overrides exported variables). How strictly they are expanded depends on the target: Claude targets expand only the `env` block of MCP servers, leaving unset variables as literal `${VAR}`; Droid expands model `api_key` values the same lenient way and copies MCP definitions verbatim; OpenCode expands both model `api_key` and MCP `env` values strictly -- a missing variable aborts the deploy -- since OpenCode runs from a directory where your shell variables won't be set. See `.env.example` for the full list.

### Single-source settings.yaml

`settings.yaml` lets you maintain Claude Code's `settings.json` from one place. You write a shared `base:` and, where targets differ, per-target or per-group `overrides:`. Overrides are applied as a JSON Merge Patch ([RFC 7396](https://www.rfc-editor.org/rfc/rfc7396)): a key set to `null` deletes it, nested objects merge, and an exact target id wins over any group.

```yaml
base:
  effortLevel: low
  env:
    EDITOR: vim
overrides:
  claude-positron:        # exact target id
    effortLevel: high
  positron:               # a group from deploy.yaml
    env:
      FAST: "1"
```

Deploying renders the settings for each claude target and gently merges only the rendered top-level keys into that target's `settings.json`. Keys you manage are tracked per target, so dropping one from `settings.yaml` removes it on the next deploy, while `hooks`, `mcpServers`, `extraKnownMarketplaces`, `enabledPlugins`, and any keys you didn't put under `settings.yaml` are left untouched. Droid and OpenCode targets skip settings entirely.

Two helpers bootstrap and maintain the file:

```bash
# Build settings.yaml from what's already on your hosts: shared values become
# base, per-host differences become overrides.
promptdeploy settings init [--from REF_TARGET] [--target T] [--force]

# Report where live hosts have drifted from settings.yaml; with --apply, fold
# that drift back into overrides (comments are preserved).
promptdeploy settings reconcile [--target T] [--apply]
```

## Getting started

**With Nix** (recommended):

```bash
direnv allow   # Python 3.12 + all dependencies
PYTHONPATH=src python -m promptdeploy deploy --dry-run
```

**With pip:**

```bash
pip install -e ".[dev]"
promptdeploy deploy --dry-run
```

**System-wide:**

```bash
nix build
./result/bin/promptdeploy --help
```

## Development

The test suite enforces 100% code coverage. Pre-commit hooks run formatting, linting, type checking, tests, and the full Nix build -- all in parallel via lefthook.

```bash
# Run tests
PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing

# Run all checks (same as CI)
nix flake check

# Format
ruff format src/ tests/

# Lint
ruff check src/ tests/

# Type check
PYTHONPATH=src mypy src/ tests/
```

CI runs `nix flake check` on every push and PR, which exercises all five checks: ruff format, ruff lint, mypy, pytest with coverage, and the Nix package build.

## Nix integration

There's a home-manager module if you want deployments to happen automatically on every `home-manager switch`:

```nix
{
  imports = [ inputs.promptdeploy.homeManagerModules.default ];

  programs.promptdeploy = {
    enable = true;
    package = inputs.promptdeploy.packages.${system}.default;
    sourceDir = "~/src/promptdeploy";
    # Optional: defaults to the targets labelled `local` in deploy.yaml.
    # List labels or target IDs (including remote ones) to widen the set.
    targets = [ "local" ];
  };
}
```

When `targets` is empty (the default), only the targets carrying the
`local` label in `deploy.yaml` are deployed -- activation never reaches
out to remote hosts over SSH unless you opt in explicitly. A failed
deploy does not abort activation: promptdeploy's output is captured to
`$XDG_STATE_HOME/promptdeploy/deploy.log` (default
`~/.local/state/promptdeploy/deploy.log`) and a warning naming that log
is printed.

## License

BSD 3-Clause. See [LICENSE.md](LICENSE.md).
