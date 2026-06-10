# Marketplace Definition Schema

Each `.yaml` file in this directory declares one Claude Code plugin
marketplace plus the plugins enabled from it. Marketplaces are **Claude-only**:
the droid, opencode, and gptel targets skip them.

A marketplace file drives two top-level keys in Claude's `settings.json`:

- `extraKnownMarketplaces` — a map of marketplace name to `{source, autoUpdate?}`.
- `enabledPlugins` — a map of `"<plugin>@<marketplace>"` to a boolean.

## Fields

| Field         | Type   | Default        | Description                                                                 |
|---------------|--------|----------------|-----------------------------------------------------------------------------|
| `name`        | string | filename stem  | Marketplace identifier. Non-empty, no `@`, no whitespace.                   |
| `description` | string | —              | Human-readable purpose of the marketplace.                                  |
| `source`      | map    | —              | Passed through verbatim into `extraKnownMarketplaces[name].source`. Omit for built-in marketplaces. |
| `autoUpdate`  | bool   | —              | When present, copied into the `extraKnownMarketplaces` entry.               |
| `plugins`     | map    | `{}`           | Map of plugin name → bool. Each becomes `enabledPlugins["<plugin>@<name>"]`. Plugin names: non-empty, no `@`. |
| `enabled`     | bool   | `true`         | `false` removes this marketplace's entries (mirrors `mcp/`).                |
| `only`        | string[] | `[]`         | If non-empty, only deploy to the listed targets/groups.                     |
| `except`      | string[] | `[]`         | Exclude the listed targets/groups.                                          |

Unknown top-level keys produce a validation warning.

## Source

`source` is passed through untouched, so any extra keys (`ref`, `skipLfs`, …)
survive. Known `source.source` values used for validation are:

| `source.source` | Shape                                       |
|-----------------|---------------------------------------------|
| `github`        | `{source: github, repo: owner/repo}`        |
| `git`           | `{source: git, url: https://…}`             |
| `directory`     | `{source: directory, path: /abs/or/rel}`    |

An unrecognized `source.source` is a validation **warning**, not an error.

## enabledPlugins key derivation

Each entry under `plugins` becomes one `enabledPlugins` key by joining the
plugin name and the marketplace name with `@`:

```
plugins:
  my-plugin: true     ->  enabledPlugins["my-plugin@<name>"] = true
```

Because the marketplace name is encoded in the key suffix, ownership is
self-tagged: promptdeploy reclaims exactly the keys whose part after the final
`@` equals this marketplace's `name`.

## Example

```yaml
name: acme
description: Acme's plugin marketplace
source:
  source: github
  repo: acme/claude-plugins
autoUpdate: true
plugins:
  formatter: true
  linter: false
only:
  - claude
```

This writes:

```json
{
  "extraKnownMarketplaces": {
    "acme": {
      "source": {"source": "github", "repo": "acme/claude-plugins"},
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "formatter@acme": true,
    "linter@acme": false
  }
}
```

## Built-in (source-less) marketplaces

Some marketplaces ship with Claude Code (for example `claude-plugins-official`)
and must not be re-registered in `extraKnownMarketplaces`. Omit `source` to
enable plugins from a built-in marketplace; only `enabledPlugins` entries are
written:

```yaml
name: claude-plugins-official
description: Plugins from the marketplace bundled with Claude Code
plugins:
  some-official-plugin: true
```
