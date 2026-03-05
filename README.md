# claude-prompts

A repository of agents, slash commands, skills, MCP server definitions, and custom model configurations for AI coding tools. Everything is defined once here and deployed to multiple environments -- Claude Code, Factory Droid, and OpenCode -- using the included `promptdeploy` tool.

## Agents

`agents/*.md` -- Specialized sub-agent definitions. Each is a Markdown file with YAML frontmatter (`name`, `description`) followed by the agent's system prompt.

| Agent | Description |
|-------|-------------|
| python-pro | Idiomatic Python with decorators, generators, async/await |
| rust-pro | Ownership patterns, lifetimes, trait implementations |
| haskell-pro | Haskell language and cabal module system |
| cpp-pro | Modern C++ with RAII, smart pointers, STL algorithms |
| emacs-lisp-pro | Emacs Lisp, editor environment, use-package |
| nix-pro | NixOS configurations, flakes, module system |
| typescript-pro | Type safety, monorepo architecture, advanced types |
| sql-pro | Complex queries, execution plans, normalized schemas |
| rocq-pro | Rocq/Coq proofs for theorems encoded as type specs |
| prd-architect | Product requirements documents for Task Master |
| prompt-engineer | Optimize prompts for LLMs and AI systems |
| persian-translator | English to Persian (Farsi) translation |
| python-reviewer | Type safety, security, common pitfalls |
| rust-reviewer | Ownership, unsafe code, error handling |
| haskell-reviewer | Laziness pitfalls, type safety, space leaks |
| cpp-reviewer | Memory safety, undefined behavior, concurrency |
| elisp-reviewer | Lexical binding, package conventions, macro hygiene |
| nix-reviewer | Reproducibility, flake hygiene, NixOS module design |
| bash-reviewer | Quoting correctness, POSIX compliance, security |
| coq-reviewer | Proof soundness, tactic hygiene, termination |
| typescript-reviewer | Type safety, async correctness, security |
| security-reviewer | Vulnerability detection, authentication, data exposure |
| perf-reviewer | Algorithmic complexity, resource leaks, allocations |
| web-searcher | AI-powered web search via Perplexity |
| task-breakdown | Org-Mode task decomposition |

**Format:**

```markdown
---
name: python-pro
description: Write idiomatic Python with advanced features.
---

Python expert specializing in clean, performant, idiomatic Python code.

## Focus Areas
- Advanced Python features (decorators, metaclasses, descriptors)
- Performance optimization and profiling
```

## Commands

`commands/*.md` -- Slash command prompts invoked via `/command-name`. Plain Markdown files. Use `$ARGUMENTS` as a placeholder for user-supplied arguments.

| Command | Purpose |
|---------|---------|
| commit | Atomic, logically sequenced commits |
| push | Commit and push all work |
| fix | Think, research, plan, act, review |
| fix-ci | Diagnose and fix failing CI tests |
| fix-github-issue | Analyze and fix a GitHub issue |
| fix-alert | Diagnose Alertmanager alerts with NixOS tools |
| fix-integration | Install/troubleshoot Home Assistant integrations |
| forge | Multi-model collaborative workflow (Opus + GPT + Gemini) |
| heavy | Deep analysis with all available tools |
| medium | Standard analysis workflow |
| deep-review | Multi-language code review with specialist sub-agents |
| quick-review | Single-pass code review |
| code-review | Thorough repository code review |
| security-review | Security-focused code review |
| review-github-pr | Review a GitHub pull request |
| bugbot | Address BugBot/Cursor/Devin comments on a PR |
| rebase | Git rebase with language-aware assistance |
| nix-rebuild | Diagnose NixOS build failures |
| install-service | Install service with nginx, monitoring, secrets |
| remove-service | Remove service and all related infrastructure |
| initialize | Analyze codebase and create CLAUDE.md |
| prepare-with | Deep project analysis for expert assistance |
| teams | Create an agent team for multi-angle exploration |
| run-orchestrator | Task orchestrator coordination |
| webfix | Fix web issues using Playwright |
| query-builder | SQL query building with MCP |
| port-model | Add support for running an ML model |
| logits | Logit comparison testing |
| flaky-rust | Fix flaky Rust tests |
| tron-debug | Debug C++ from Torch Fx ingest pipeline |
| meeting-notes | Analyze meeting notes |
| transcribe | Transcribe handwriting from images |
| fix-transcript | Rewrite transcript into proper English |
| clean-transcription | Clean up meeting transcript grammar |
| proofread | Correct English in Markdown/Org files |
| smooth | Light-touch rewriting to simplify text |
| process-checklist | Execute a Markdown checklist of tasks |
| johnw | Write in John Wiegley's authentic voice |

## Skills

`skills/*/SKILL.md` -- Each skill is a directory containing a `SKILL.md` with YAML frontmatter (`name`, `description`) and instruction content, plus optional supporting files.

| Skill | Description |
|-------|-------------|
| forge | Multi-phase, multi-model deep analysis (Opus orchestrator + GPT-5.2-Pro + Gemini 3 Pro consensus) |
| claude-code | Prime sessions with proper use of installed plugins and tools |
| caveman | Compress prompts to preserve meaning while reducing context |
| nixos | Resolve NixOS issues using research and sequential thinking |
| node-red | Edit, analyze, and create Node-RED flows |
| persian | Persian translation with specialist reviewers |
| skill-creator | Guide for creating effective new skills |
| swiftui | SwiftUI best practices and iOS 26+ Liquid Glass adoption |

**Format:**

```markdown
---
name: caveman
description: Compress and simplify prompts to preserve meaning
---

You are a caveman compression expert. Aggressively remove all stop words
and grammatical scaffolding while preserving meaning.
```

## MCP Servers

`mcp/*.yaml` -- Each file defines a single MCP (Model Context Protocol) server. These are deployed into the appropriate configuration format for each target tool.

| Server | Description |
|--------|-------------|
| pal | Provider Abstraction Layer for multi-model AI collaboration |
| perplexity | Perplexity AI web search |
| context7 | Up-to-date documentation and code examples for any library |
| sequential-thinking | Structured multi-step reasoning |
| claude-mem | Persistent cross-session memory |
| mem0 | Memory tools with Qdrant vector store |

**Format:**

```yaml
name: perplexity
description: Perplexity AI web search via MCP
command: uvx
args:
  - perplexity-mcp
env:
  PERPLEXITY_API_KEY: "${PERPLEXITY_API_KEY}"
scope: user
enabled: true
only:
  - claude
```

Fields: `name`, `description` (required); `command`+`args` or `url`+`headers` (transport); `env`, `scope`, `enabled`, `only`, `except` (optional). See `mcp/schema.md` for full documentation.

`${VAR}` references in `env` values are passed through to the target tool for runtime expansion.

## Models

`models.yaml` -- Custom model providers and their models, deployed to Factory Droid and OpenCode. Models are organized by provider, each specifying connection details and target-specific configuration.

```yaml
providers:
  my-provider:
    display_name: "My Provider"
    base_url: "https://api.example.com/v1"
    api_key: "${MY_API_KEY}"
    droid:
      provider_type: openai
    opencode:
      npm: "@ai-sdk/openai-compatible"
      name: "my-provider"
    models:
      my-model:
        display_name: "My Model"
        max_output_tokens: 32768
```

Each provider requires `display_name`, `base_url`, `api_key`, and a `models` map. Target-specific blocks (`droid:`, `opencode:`) configure how models appear in each tool. `${VAR}` references are expanded at deploy time from the shell environment.

Filtering with `only`/`except` works at both the provider and model level, allowing fine-grained control over which models appear in which targets.

Claude Code does not support custom models and is skipped during model deployment.

## Hooks

`hooks/*.yaml` -- Claude Code hook groups that fire shell commands on tool events. Each YAML file defines a named hook group with handlers for one or more event types.

```yaml
name: git-ai
description: Git AI checkpoint hooks for file modifications
only:
  - claude
hooks:
  PostToolUse:
    - matcher: "Write|Edit|MultiEdit"
      hooks:
        - command: "git-ai checkpoint claude --hook-input stdin"
          type: command
  PreToolUse:
    - matcher: "Write|Edit|MultiEdit"
      hooks:
        - command: "git-ai checkpoint claude --hook-input stdin"
          type: command
```

Valid event types are: `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SessionStart`, `PreCompact`, `UserPromptSubmit`.

Each entry under an event type must be a non-empty list of matcher/hooks objects. When deployed, each entry is tagged with `_source: <hook-group-name>` so that entries can be cleanly updated or removed without affecting hooks from other sources.

Droid and OpenCode targets ignore hooks (no-op).

## Environment Filtering

Any item can be restricted to specific targets using `only` or `except` in its YAML frontmatter or metadata:

```yaml
only:
  - claude        # deploy only to the claude group
except:
  - droid         # deploy everywhere except Factory Droid
```

Group names (defined in `deploy.yaml`) expand to their members. `only` and `except` cannot both be used on the same item.

## Environment Variables

API keys and secrets use `${VAR}` syntax rather than hardcoded values. See `.env.example` for the variables used by the default configuration. Your shell must export them before running `promptdeploy deploy`.

## promptdeploy

The `promptdeploy` CLI tool deploys everything in this repository to target environments. It handles format translation (each tool expects different config layouts), change detection (only deploys what changed), and cleanup (removes items you delete from source).

### Targets

Targets are defined in `deploy.yaml`:

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
groups:
  claude:
    - claude-personal
```

| Type | Tool | Deploys |
|------|------|---------|
| `claude` | Claude Code | agents, commands, skills, MCP servers |
| `droid` | Factory Droid | agents (as droids), commands (as skills, opt-in), MCP servers, models |
| `opencode` | OpenCode | agents, commands, skills, MCP servers, models |

### Usage

```bash
promptdeploy deploy [--dry-run] [--target TARGET] [--only-type TYPE] [--verbose|--quiet]
promptdeploy validate
promptdeploy status [--target TARGET]
promptdeploy list [--target TARGET]
```

- **deploy** -- Deploy items. `--dry-run` previews changes. `--target` limits to specific targets (accepts group names). `--only-type` limits to `agents`, `commands`, `skills`, `mcp`, or `models`.
- **validate** -- Check all source items for YAML errors, invalid environment IDs, and missing fields.
- **status** -- Show what has changed since the last deploy (A = new, M = modified, D = deleted).
- **list** -- Show all items currently managed in each target.

### Installation

**From source with Nix** (recommended):

```bash
direnv allow   # sets up Python 3.12 + dependencies
PYTHONPATH=src python -m promptdeploy deploy --dry-run
```

**From source with pip:**

```bash
pip install -e ".[dev]"
promptdeploy deploy --dry-run
```

**System-wide with Nix:**

```bash
nix build
./result/bin/promptdeploy --help
```

### How it works

1. **Discovery** -- Scans `agents/*.md`, `commands/*.md`, `skills/*/SKILL.md`, `mcp/*.yaml`, and `models.yaml`.
2. **Filtering** -- Evaluates `only`/`except` metadata against each target, expanding groups.
3. **Change detection** -- Computes SHA256 hashes and compares against the manifest from the last deploy.
4. **Deployment** -- Writes files and merges configuration in each target's native format.
5. **Cleanup** -- Removes items present in the old manifest but absent from source. Unmanaged items are never touched.
6. **Manifest update** -- Saves `.prompt-deploy-manifest.json` to each target directory.

### Development

```bash
pytest                                    # run all tests
pytest --cov --cov-report=term-missing    # with coverage
```

Coverage is enforced at 100%. The pre-commit hook runs the test suite when promptdeploy source or test files change. CI runs across Python 3.11, 3.12, and 3.13.
