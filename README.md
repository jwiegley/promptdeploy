# promptdeploy

I've been using several different AI tools -- Claude Code, OpenAI Codex, Factory Droid, OpenCode, and gptel in Emacs -- and quickly got tired of maintaining the same agents, commands, prompts, and configurations in five different places with five different formats. So I wrote `promptdeploy`: you define everything once in this repository, and it deploys to all your targets, handling the format differences for you.

It's a single Python CLI tool with a few small dependencies (PyYAML, Jinja2, ruamel.yaml). You describe what you want in Markdown files and YAML, point it at your targets in `deploy.yaml`, and it figures out the rest.

## What's in here

The repository holds these types of content, all defined in simple Markdown or YAML:

| Type | Location | What it is |
|------|----------|------------|
| Agents | `agents/*.md` | Specialized sub-agents (Markdown + YAML frontmatter) |
| Commands | `commands/*.md` | Slash command prompts, `$ARGUMENTS` for user input; Codex receives generated skills |
| Skills | `skills/*/SKILL.md` | Multi-file skill directories with a `SKILL.md` entry point |
| Prompts | `prompts/*` | Prompt Poet (`.poet`/Jinja) or plain prompts, rendered per target: slash-command Markdown for the coding tools; gptel receives `.poet` files verbatim and Jinja variants as role/content JSON |
| MCP Servers | `mcp/*.yaml` | Model Context Protocol server definitions |
| Hooks | `hooks/*.yaml` | Claude Code and Codex hook groups for tool events |
| Marketplaces | `marketplaces/*.yaml` | Claude Code plugin marketplaces + enabled plugins (Claude-only) |
| Models | `models.yaml` | Custom model providers and their models |
| Settings | `settings.yaml` | Claude Code `settings.json`, single-sourced with `base:` + per-target `overrides:` |

Any item can use `only`/`except` in its frontmatter to control which targets it deploys to. Group names from `deploy.yaml` expand to their members. Labels can also be embedded directly in a filename with the filetags convention -- `heavy -- positron.md` deploys as `heavy`, and only to targets matching every listed label.

## Using promptdeploy

```bash
promptdeploy deploy [--dry-run] [--force] [--target TARGET] [--target-root DIR] [--only-type TYPE] [--verbose|--quiet]
promptdeploy validate    # check for YAML errors, missing fields, undeclared ${VAR} refs
promptdeploy status      # show what's changed since last deploy
promptdeploy list        # show managed items per target
promptdeploy settings init [--from REF] [--target T] [--force]  # bootstrap settings.yaml from live hosts
promptdeploy settings reconcile [--target T] [--apply]          # pull host settings drift into overrides
```

The deploy pipeline works like this: discover all source items, filter by target, compute SHA256 hashes against the last manifest, write only what changed, clean up anything that's been removed from source. Unmanaged items in target directories are never touched. `--force` redeploys everything even when unchanged; `--target-root DIR` redirects all output under a scratch directory (one subdirectory per target id) so you can preview a deploy without touching real configuration.

### Ponytail skills

The packaged Nix CLI pins Ponytail and supplies its source binding
automatically. Claude, Codex, Droid, and OpenCode receive the six complete
skill trees; GPTel receives six one-shot prompt projections. This selection
does not install hooks, persistent modes, or an OpenCode plugin.

The native skill trees retain the pinned upstream text. In this static tier,
invoke them through each host's ordinary skill mechanism. The upstream
`Persistence`, `Configure Default Mode`, `Update`, and OpenCode slash-command
claims in `ponytail` and `ponytail-help` require the optional plugin/runtime
and do not apply here. GPTel's one-shot projections replace those claims with
its actual prompt-only capability boundary.

Use an isolated target root to prove every configured target without SSH or
live configuration changes:

```zsh
ponytail_items=(--only-item bundle:ponytail)
for name in ponytail ponytail-review ponytail-audit ponytail-debt ponytail-gain ponytail-help; do
  ponytail_items+=(--only-item "skill:$name" --only-item "prompt:$name")
done

proof_root=$(mktemp -d /private/var/tmp/promptdeploy-ponytail-proof.XXXXXX)
nix run '.#promptdeploy' -- validate
nix run '.#promptdeploy' -- deploy --target-root "$proof_root" "${ponytail_items[@]}"
nix run '.#promptdeploy' -- verify --target-root "$proof_root" "${ponytail_items[@]}"
```

After that succeeds, deploy only to the current `hera` surfaces—never the
targetless remote fleet:

```zsh
local_targets=(
  --target claude-personal --target claude-positron --target codex-local
  --target droid --target opencode-hera --target gptel-emacs
)
nix run '.#promptdeploy' -- deploy --local-only "${local_targets[@]}" "${ponytail_items[@]}"
nix run '.#promptdeploy' -- verify --local-only "${local_targets[@]}" "${ponytail_items[@]}"
```

For development against the mutable Desktop checkout, add the global option
`--bundle-source ponytail=/Users/johnw/Desktop/ponytail` before the subcommand.

### Targets

Targets are defined in `deploy.yaml`. Each has a type (`claude`, `codex`, `droid`, `opencode`, or `gptel`) and a path. Remote targets add a `host:` field and deploy via rsync over SSH. The `gptel` type receives only prompts: `.poet` files are copied for gptel-prompts to read directly, Jinja variants render to JSON, and plain prompts are copied verbatim. The other coding-agent types receive the full content set they support.

One Claude Code detail worth knowing: MCP servers are deployed into each profile's `.claude.json` (the user-scope `mcpServers` surface Claude Code actually reads), not `settings.json`. Local targets merge the named server keys directly; remote targets perform the same surgical merge over SSH stdin, so `.claude.json` is never rsynced and unrelated host state remains intact. A running `claude` session can still rewrite `.claude.json` from memory, so deploy MCP with sessions closed. To see what a profile actually serves, run `claude -p "Say ok" --output-format json --max-turns 1` and inspect the init event's `mcp_servers`.

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
  codex:
    type: codex
    path: "~"
  gptel-emacs:
    type: gptel
    path: ~/.emacs.d/prompts
groups:
  claude:
    - claude-personal
```

For Codex, set `path` to your home directory (recommended) or directly to `~/.codex`. The target writes custom agents to `.codex/agents/*.toml`, MCP servers and Codex model providers to managed blocks in `.codex/config.toml`, hooks to `.codex/hooks.json`, and skills to `.agents/skills`. Commands and rendered prompts are installed as generated skills named `command-<name>` and `prompt-<name>`, which makes them available through Codex's skill surfaces. A hook file can also include `codex.notify: ["cmd", "arg"]` to manage Codex's top-level `notify` command in `.codex/config.toml`; promptdeploy inserts that block at the top of the TOML file so it remains a root setting. `settings.yaml` and Claude marketplaces are intentionally skipped for Codex.

### Environment variables

API keys and other secrets use `${VAR}` syntax in MCP and model definitions, resolved from your shell environment plus a `.env` file at the repo root (which never overrides exported variables). When and how strictly they expand depends on the target. Claude Code targets strictly expand MCP `env` and `headers` references at deploy time and write the resolved values to mode-`0600` `.claude.json`; remote targets use the same rule in their SSH-stdin merge. Codex maps simple MCP `${VAR}` references to `env_vars`, `env_http_headers`, or `bearer_token_env_var` in `config.toml`; complex embedded references are written literally with a warning. Droid also copies MCP definitions verbatim and leniently expands only model `api_key` values -- an unset variable stays as literal `${VAR}` with a warning. OpenCode expands model `api_key` and MCP `env`/`headers` values strictly at deploy time -- a missing variable aborts the deploy -- since OpenCode runs from a directory where your shell variables won't be set. `promptdeploy validate` warns when an MCP definition references a variable not declared in `.env.example`. See `.env.example` for the full list.

Codex model providers are opt-in from `models.yaml`: add a `codex:` mapping under a provider to write `[model_providers.<id>]` into `.codex/config.toml`. If the provider's `api_key` is a plain `${VAR}` reference, promptdeploy emits `env_key = "VAR"` rather than writing a secret.

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

Deploying renders the settings for each claude target and gently merges only the rendered top-level keys into that target's `settings.json`. Keys you manage are tracked per target, so dropping one from `settings.yaml` removes it on the next deploy, while `hooks`, `mcpServers`, `extraKnownMarketplaces`, `enabledPlugins`, and any keys you didn't put under `settings.yaml` are left untouched. Codex, Droid, and OpenCode targets skip settings entirely.

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
nix run . -- --dry-run
nix run .
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

The test suite enforces 100% line and branch coverage, mypy runs in strict mode, and ruff enforces a curated lint baseline. Pre-commit hooks run formatting, linting, type checking, tests, and the full Nix build -- all in parallel via lefthook.

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

CI runs `nix flake check` on every push and PR. It exercises seven checks: ruff format, ruff lint, mypy, pytest with coverage, Home Manager module evaluation, activation-driver behavior, and the Nix package build.

## Nix integration

The flake exports a fail-closed Home Manager module that deploys and verifies
the pinned promptdeploy configuration on every `home-manager switch`:

```nix
{
  imports = [ inputs.promptdeploy.homeManagerModules.default ];

  programs.promptdeploy = {
    enable = true;

    # Optional: narrow deployment to local target IDs or labels.
    # Empty means all targets owned by the current host.
    targets = [ ];

    # Both deployment and verification are restricted to these items.
    exactItems = [
      "mcp:anvil"
      "mcp:anvil-tools"
      "skill:anvil"
    ];
  };
}
```

The package, immutable source, and expected Git revision default to the same
pinned `inputs.promptdeploy` revision. The module rejects mutable source
paths, missing revision metadata, or a package and source from different
revisions.

Activation unsets `PROMPTDEPLOY_HOST`, then runs a forced
`deploy --local-only` and strict verification with both operations restricted
to `exactItems`. Unrelated items and their deployment-time secrets are never
part of the activation transaction. Remote targets are excluded even if named
in `targets`. A deploy failure, verification failure, lock timeout, or
transaction timeout aborts Home Manager activation.

The combined deploy-and-verify transaction is serialized through
`$XDG_STATE_HOME/promptdeploy/activation.lock`. This also serializes
different hosts when that state directory is on their shared NFS home. The
default lock wait is 60 seconds and the transaction timeout is 300 seconds.

Command output is retained privately in
`$XDG_STATE_HOME/promptdeploy/deploy.log`: the directory is mode `0700`,
the log is mode `0600`, and only its final 1 MiB is kept. Failure output is
not copied to the console; the console reports only the log path. Treat the
log as potentially sensitive.

The driver never steals an existing lock. After a crashed activation, inspect
`activation.lock/owner` for its host, PID, and start time, and confirm that no
activation or promptdeploy process is still running on any machine sharing the
state directory. Only then remove `owner` and use `rmdir` on
`activation.lock`; do not remove the lock recursively.

## Acknowledgements

Many thanks to Isaac Shapira, who designed and built the `commands/fess.md`
command. He leverages a key insight that LLM’s don’t actually reason, they
only predict: so just as they can predict text that might not be what you
asked for, they can also predict which bad choices you might agree that they
just made. This offers a solid “finishing” command to find things that are
bogus or invalid in the results the AI just generated for you.

## License

BSD 3-Clause. See [LICENSE.md](LICENSE.md).
