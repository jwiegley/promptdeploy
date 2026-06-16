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

On a Claude target, a URL server is written to `.claude.json` with `"type":
"http"` -- Claude Code reads a `type`-less entry as stdio and rejects it for the
missing `command` ("command: expected string, received undefined"). For an SSE
endpoint set `type: sse` explicitly; an explicit `type` is always preserved.

## Optional Fields

| Field     | Type     | Default | Description                                                  |
|-----------|----------|---------|--------------------------------------------------------------|
| `env`     | map[string,string] | `{}` | Environment variables. Supports `${VAR}` syntax (see Environment Variable Expansion). |
| `scope`   | string   | `user`  | `user` (all projects) or `project` (current project only)    |
| `enabled` | bool     | `true`  | Set to `false` to disable without deleting the definition    |
| `only`    | string[] | `[]`    | If non-empty, only include this server for listed profiles   |
| `except`  | string[] | `[]`    | Exclude this server for listed profiles                      |
| `codex`   | mapping  | `{}`    | Codex-only field overrides merged before writing `~/.codex/config.toml` |

## Environment Variable Expansion

`${VAR}` references in `env` and `headers` values are handled differently per
target type:

- **Claude Code (local)** -- `env`/`headers` `${VAR}` are strict-expanded at
  deploy time and the resolved values are baked into `.claude.json` (mode
  `0600`). A missing variable raises `EnvVarError` and the deploy exits 1.
  The manifest hash folds current env values, so **rotating a referenced
  secret triggers a redeploy**.
- **Claude Code (remote)** -- `env`/`headers` `${VAR}` are **strict-expanded
  at deploy time** (like OpenCode) and the resolved value is baked into the
  remote `.claude.json` (transported only over the encrypted SSH channel, at
  rest at mode `0600`). A missing variable raises `EnvVarError` and the deploy
  exits 1 (never ships an empty secret). The manifest hash folds current env
  values, so **rotating a referenced secret triggers a redeploy**, and
  **running `status`/`deploy` without the referenced secret exported reports
  the server as `changed`** (export it; note that `deploy` auto-loads `.env`
  but `status` does not). `${VAR}` is only honored in `env`/`headers`; in
  `command`/`args`/`url` it is out of schema and is baked verbatim (and, on
  remote, never expanded).
- **Factory Droid** -- `env` and `headers` are copied verbatim into
  `mcp.json`; promptdeploy expands nothing in MCP definitions for this target
  (only the models provider `api_key` is expanded, leniently).
- **OpenCode** -- strict deploy-time expansion of both `env` and `headers`
  via `expand_env_vars_strict`: a missing variable raises `EnvVarError` and
  the deploy exits 1, since OpenCode runs from a directory where shell
  variables won't be set.
- **OpenAI Codex** -- deployed into `~/.codex/config.toml` as
  `[mcp_servers.<name>]`. `env` values are strict-expanded at deploy time and
  written to Codex's `env` table, matching OpenCode's behavior and avoiding a
  dependency on the environment used later to launch `codex`. A missing
  variable raises `EnvVarError` and the deploy exits 1. Header references are
  mapped to Codex's `env_http_headers` or, for
  `Authorization: "Bearer ${TOKEN}"`, `bearer_token_env_var`. Other
  Codex-native keys can be supplied directly, including explicit `env_vars`
  entries when runtime forwarding is desired, or under `codex:` when they
  should override the shared definition only for Codex.

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

- **Remote claude targets deploy MCP via an SSH-stdin direct merge.**
  `.claude.json` is machine-specific (OAuth session, caches, per-project
  state) and is never rsynced, so for a remote claude host promptdeploy
  performs a **direct surgical merge into `<remote_path>/.claude.json` over
  SSH** -- not the `claude` CLI, not rsync. For each enabled server it sets
  `mcpServers[name]=entry`; for `enabled:false` or a removed server it pops
  the key; **all other app-owned keys are preserved**; the write is atomic
  (`mkstemp` mode `0600` + `os.replace`, so the file is never widened by the
  remote umask). Transport: a small `python3` merge program is generated with
  the operations embedded as base64 and piped to `ssh <host> python3 -` on
  **stdin** -- the remote argv is just `python3 -`, so secrets never appear in
  the remote process table or logs, and the program's entire body is wrapped
  so any error prints only a fixed diagnostic (never the payload/ops/values).
  The merge is flushed in the deploy loop **before** the manifest is saved, so
  a failed merge leaves the manifest untouched and the next run retries
  automatically. `--dry-run` performs no SSH merge and no write (it still does
  a read-only `ssh_pull` in `prepare`), and `--target-root` previews a
  remote-MCP target as a **local `.claude.json` write with deploy-time-expanded
  `${VAR}` values** (it does NOT exercise the SSH-stdin merge transport).
  **Requirement:** `python3` must be on the remote non-interactive SSH PATH
  (NixOS and Amazon Linux both ship it); if absent (exit 127) the deploy fails
  loudly with a clear hint. Because real secrets now transit the channel,
  **pre-populate `known_hosts` out-of-band before the first remote deploy**
  (`StrictHostKeyChecking=yes` does not auto-accept unknown keys).
- **Deploy with sessions closed.** A running `claude` session (local or
  remote) rewrites `.claude.json` wholesale from memory with no locking, so an
  MCP deploy concurrent with a live session can be lost (or lose the session's
  changes). This is the same constraint `claude mcp add` operates under.

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
codex:
  command: my-server
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
