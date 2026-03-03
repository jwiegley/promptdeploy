# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a collection of Claude Code prompts organized into three categories:

- **agents/** - Custom agent definitions (`.md` files) that define specialized sub-agent types (e.g., `rust-pro.md`, `python-reviewer.md`, `haskell-pro.md`)
- **commands/** - Slash command prompts (`.md` files) invoked via `/command-name` (e.g., `/commit`, `/fix`, `/forge`)
- **skills/** - Skill folders, each containing a `SKILL.md` with YAML frontmatter (`name`, `description`) plus instructions and optional supporting files

## Key Conventions

- All prompts are plain Markdown files. There is no build system, test suite, or linter.
- Commands use `$ARGUMENTS` as a placeholder for user-supplied arguments.
- Skills follow the Claude skills spec: a directory with at minimum a `SKILL.md` file containing YAML frontmatter and instruction content.
- `gravity.md` is a standalone prompt template, not part of the three main categories.

## Task Master AI Instructions
**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
