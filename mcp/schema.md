# MCP Server Definition Schema

Each `.yaml` file in this directory defines a single MCP server.

## Required Fields

| Field         | Type   | Description                              |
|---------------|--------|------------------------------------------|
| `name`        | string | Unique server identifier                 |
| `description` | string | Human-readable purpose of the server     |

## Transport

Exactly one transport must be specified:

### stdio (command + args)

| Field     | Type     | Description                        |
|-----------|----------|------------------------------------|
| `command` | string   | Path or name of the executable     |
| `args`    | string[] | Command-line arguments             |

### HTTP (url + headers)

| Field     | Type              | Description                    |
|-----------|-------------------|--------------------------------|
| `url`     | string            | HTTP endpoint URL              |
| `headers` | map[string,string] | HTTP headers for requests. Supports `${VAR}` syntax (see Environment Variable Expansion). |

## Optional Fields

| Field     | Type     | Default | Description                                                  |
|-----------|----------|---------|--------------------------------------------------------------|
| `env`     | map[string,string] | `{}` | Environment variables. Supports `${VAR}` syntax (see Environment Variable Expansion). |
| `scope`   | string   | `user`  | `user` (all projects) or `project` (current project only)    |
| `enabled` | bool     | `true`  | Set to `false` to disable without deleting the definition    |
| `only`    | string[] | `[]`    | If non-empty, only include this server for listed profiles   |
| `except`  | string[] | `[]`    | Exclude this server for listed profiles                      |

## Environment Variable Expansion

`${VAR}` references in `env` and `headers` values are handled differently per
target type:

- **Claude Code** -- deployed VERBATIM, in both `env` and `headers`. Expansion
  happens at runtime when Claude Code reads `.claude.json` (see the next
  section), so secrets are never baked into deployed config. Caveat: an
  unset variable expands to *empty* at runtime rather than failing.
- **Factory Droid** -- `env` and `headers` are copied verbatim into
  `mcp.json`; promptdeploy expands nothing in MCP definitions for this target
  (only the models provider `api_key` is expanded, leniently).
- **OpenCode** -- strict deploy-time expansion of both `env` and `headers`
  via `expand_env_vars_strict`: a missing variable raises `EnvVarError` and
  the deploy exits 1, since OpenCode runs from a directory where shell
  variables won't be set.

`promptdeploy validate` warns when an `env` or `headers` value references a
`${VAR}` that is not declared in `.env.example` (the check is skipped when no
`.env.example` exists).

## How Deployed Servers Reach Claude Code

promptdeploy merges each MCP server into the Claude target's
`$CLAUDE_CONFIG_DIR/.claude.json` under the top-level `mcpServers` key -- the
user-scope surface Claude Code reads natively (its other MCP read surfaces are
project `.mcp.json`, plugins, claude.ai connectors, enterprise
`managed-mcp.json`, and the `--mcp-config` flag; it does **not** read
`settings.json`). The merge is surgical: only the named server keys are
written, and every other key in the app-owned file is preserved. Plain
`claude` picks the servers up with no wrapper or flags.

Two consequences:

- **Local profiles only.** `.claude.json` is machine-specific (OAuth session,
  caches, per-project state) and must never be rsynced, so remote claude
  targets skip MCP entirely (`manage_mcp=False`). Manage MCP for remote hosts
  on those hosts directly if needed.
- **Deploy with sessions closed.** A running `claude` session rewrites
  `.claude.json` wholesale from memory with no locking, so an MCP deploy
  concurrent with a live session can be lost (or lose the session's changes).
  This is the same constraint `claude mcp add` operates under.

To verify a profile's deployed MCP servers end to end, run a headless probe
and inspect the init event's `mcp_servers`:

```bash
claude -p "Say ok" --output-format json --max-turns 1
```

Design history, including the rejected `--mcp-config` launcher-bridge approach:
`docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md`.

## Example

```yaml
name: my-server
description: Example MCP server
command: npx
args:
  - -y
  - "@example/mcp-server"
env:
  API_KEY: "${MY_API_KEY}"
scope: user
enabled: true
only:
  - claude
```

## Filename Tags

A filename may embed deployment labels after a ` -- ` (space-dash-dash-space)
separator, e.g. `my-server -- positron.yaml`. Each tag is a target ID, label,
or group name and acts as an implicit `only` entry with AND semantics: the
server deploys only to targets matching every tag. Tags compose with the
`only`/`except` fields and are stripped from the filename stem before it is
used as the default `name`.
