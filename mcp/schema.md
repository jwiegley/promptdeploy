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
  happens at runtime on the `--mcp-config` surface (see the next section), so
  secrets are never baked into deployed `settings.json` files. Caveat: an
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

promptdeploy merges MCP servers into each Claude target's `settings.json`
under a top-level `mcpServers` key -- but Claude Code does not read that key
on its own (its MCP read surfaces are `~/.claude.json`, project `.mcp.json`,
plugins, claude.ai connectors, enterprise `managed-mcp.json`, and the
`--mcp-config` flag). The deployed key takes effect through a launcher bridge
that lives outside this repository: the `ai` wrapper (`~/src/scripts/ai`)
appends `--mcp-config "$CLAUDE_CONFIG_DIR/settings.json"` to every `claude`
invocation when that file exists. A launch that bypasses the wrapper gets no
promptdeploy-managed servers.

To verify a profile's deployed MCP servers end to end, run the standard
headless probe:

```bash
claude --strict-mcp-config --mcp-config=<profile>/settings.json -p "Say ok" \
  --output-format json --max-turns 1
```

`--strict-mcp-config` excludes every other MCP source, so the probe reports
exactly the servers loaded from that `settings.json`. Design history:
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
