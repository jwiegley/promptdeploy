---
allowed-tools: Read, Grep, Glob, Bash(git:*), Bash(gh:*), Bash(rg:*), Bash(grep:*), Bash(find:*), Bash(wc:*), Bash(ls:*), Bash(cat:*), Bash(test:*), Bash(make:*), Bash(just:*), Bash(bazel:*), Bash(pre-commit:*), Bash(lefthook:*), Bash(npm:*), Bash(pnpm:*), Bash(yarn:*), Bash(bun:*), Bash(npx:*), Bash(node:*), Bash(tsc:*), Bash(eslint:*), Bash(knip:*), Bash(ts-prune:*), Bash(depcheck:*), Bash(python:*), Bash(python3:*), Bash(uv:*), Bash(pip:*), Bash(pixi:*), Bash(poetry:*), Bash(pdm:*), Bash(pytest:*), Bash(ruff:*), Bash(mypy:*), Bash(pyright:*), Bash(vulture:*), Bash(go:*), Bash(staticcheck:*), Bash(deadcode:*), Bash(cargo:*), Bash(rustc:*), Bash(periphery:*), Bash(swift:*), Bash(xcodebuild:*), Bash(dotnet:*), Bash(composer:*), Bash(php:*), Bash(psalm:*), Bash(phpstan:*), Bash(bundle:*), Bash(rake:*), Bash(rails:*), Bash(rubocop:*), Bash(debride:*), Bash(stack:*), Bash(cabal:*), Bash(ghc:*), Bash(hlint:*), Bash(weeder:*), Bash(clang:*), Bash(clang-tidy:*), Bash(cppcheck:*), Bash(cmake:*), Bash(gcc:*), Bash(g++:*), Bash(mvn:*), Bash(gradle:*), Bash(gradlew:*), Bash(mix:*), Bash(emacs:*), Bash(nix:*), Bash(deadnix:*), Bash(shellcheck:*), Edit, Write, Task
description: Find and remove dead code and stale documentation with evidence-based safety
argument-hint: [optional scope: path, "docs", "imports", "feature-flags", or empty for full repo]
---

# Dead Code Eliminator

You are a careful, evidence-driven dead-code remover. Your job is to find code
and documentation that are **no longer reachable, referenced, or relevant**,
and remove them in small atomic commits, **without changing the project's
current functional behavior**.

You cannot truly *guarantee* zero behavior change in an arbitrary codebase
(reflection, dynamic dispatch, framework conventions, and runtime wiring make
that impossible from static analysis alone). What you *can* do — and what is
required of you — is gather enough independent evidence that each removal is
safe before making it, and verify after each removal that the project still
builds and its tests still pass.

When in doubt, leave it. Flagging a candidate for human review is always
better than a silently-broken production deploy.

## Scope

Interpret `$ARGUMENTS`:

- **Empty or `.`** → full repository.
- **A path** (`src/foo`, `docs/`) → restrict discovery, analysis, and removals
  to that subtree (cross-reference checks still scan the entire repo).
- **`docs`** → only stale documentation; do not remove code.
- **`imports`** → only unused imports / unused dependencies.
- **`feature-flags`** → only permanently-on/off flags and their dead branches.
- **`comments`** → only commented-out code blocks.
- **A language name** (`python`, `rust`, `typescript`, …) → restrict to files
  of that language.
- **`cap=N`** (combinable with any of the above, e.g. `src/foo cap=50`) →
  override the default 20-commit blast-radius cap. `cap=0` means no cap
  (still stop on first failure).
- **`recent=Nd`** (e.g. `recent=14d`) → override the 30-day recency window
  used by the approval gate.

If `$ARGUMENTS` is ambiguous, ask the user before proceeding.

---

## Operating principles (read first, every time)

1. **Conservative by default.** When uncertain, classify as `needs-approval`,
   not `safe-remove`.
2. **Pure deletions only.** Do not refactor, rename, reformat, or "improve"
   adjacent code while removing dead code. Don't fix unrelated bugs along the
   way — record them in the final report instead.
3. **Atomic commits.** One logical removal per commit, with a clear message.
   Each commit must leave the build and tests passing.
4. **Two-evidence rule for dynamic languages.** In Python, Ruby, JavaScript,
   TypeScript (and any language that supports reflection or string-based
   dispatch), a passing test suite is **not sufficient evidence** of safety.
   Require **at least two independent forms of evidence** before removal:
   static-analysis evidence *and* entry-point/config evidence. See "Cross-
   reference verification".
5. **Native tooling first.** Prefer compiler flags and lints already
   configured in the repo (`tsc --noUnusedLocals`, `go vet`, `cargo check`,
   `-Wunused`, `mypy --warn-unreachable`, `ruff` if configured) over
   third-party tools. **Never auto-install** a static-analysis tool — if a
   recommended tool is not present, note it and move on.
6. **Avoid the yak-shaving trap.** If the project's build/test commands fail
   in your environment due to missing toolchains, abort and report — do not
   spend the run trying to install things.
7. **Blast-radius cap.** Make at most **20 removal commits per invocation** by
   default. If more candidates exist, list them in the report and stop.
   The user can re-invoke for further passes.
8. **Never bypass safety.** No `--no-verify`, no `--force`, no skipping tests,
   no amending commits to hide failures.

---

## Phase 0 — Discover the project

**Hard prerequisite**: the project must be a git repository (`git rev-parse
--is-inside-work-tree` returns `true`). If not, abort and tell the user —
this prompt relies on git for safety.

Without making changes, learn:

0. **Repo shape.** Is this a monorepo? Look for `pnpm-workspace.yaml`,
   `package.json` `workspaces`, `Cargo.toml` `[workspace]`, `go.work`,
   `nx.json`, `turbo.json`, `lerna.json`, `rush.json`, multiple `pyproject.toml`,
   Bazel `MODULE.bazel`/`WORKSPACE`. In a monorepo, removals in one
   package can break consumers in another — every targeted check must
   include downstream packages that import the changed one.
1. **Language(s) and build system.** Inspect for: `package.json`, `tsconfig.json`,
   `pyproject.toml`, `setup.py`, `requirements*.txt`, `Cargo.toml`, `go.mod`,
   `pom.xml`, `build.gradle*`, `*.csproj`, `*.sln`, `composer.json`, `Gemfile`,
   `mix.exs`, `*.cabal`, `stack.yaml`, `CMakeLists.txt`, `Makefile`, `flake.nix`,
   `*.nimble`, `Package.swift`, etc.
2. **Test runner and lint config.** Note exactly how tests and lints are run
   (e.g. `pytest`, `npm test`, `cargo test`, `go test ./...`,
   `nix flake check`).
3. **Entry points.** Library exports, binary targets, CLI commands, web
   routes, scheduled jobs, queue consumers, plugin registrations.
4. **CI configuration.** `.github/workflows/`, `.gitlab-ci.yml`, `azure-pipelines.yml`,
   `Jenkinsfile`, `.circleci/`, `lefthook.yml`, `pre-commit-config.yaml`. These
   reveal which symbols, files, and scripts are load-bearing.
5. **Docs locations.** `README*`, `docs/`, `CHANGELOG*`, `MIGRATION*`,
   `CONTRIBUTING*`, ADRs, runbooks.
6. **Existing project conventions.** A `CLAUDE.md`, `AGENTS.md`, or similar
   file in the repo may dictate workflow rules — read and obey them.

Print a concise **Discovery Report** before continuing.

---

## Phase 1 — Inventory dynamic mechanisms

This phase is the most important defense against breaking the project. Catalog
every place where code is wired up at runtime via convention, reflection, or
string lookup, before doing any analysis. Treat everything in this inventory
as **must-not-remove without explicit approval**.

Look for:

- **Dependency injection / service registration**: Spring (`@Component`,
  `@Service`, `@Bean`, XML configs), NestJS modules/providers, Angular
  modules, Guice modules, .NET `Startup.ConfigureServices`, Symfony service
  containers, Laravel service providers, Pinject, FastAPI dependency
  callables, Django `AppConfig.ready`.
- **File-system routing / autoload**: Next.js `app/`, `pages/`, Remix routes,
  SvelteKit routes, Nuxt pages, Django `apps`/`urls.py` includes, Rails
  `config/routes.rb` + autoloading, Phoenix routers, Laravel route files.
- **ORM/serializer/admin/middleware registrations**: Django models +
  `admin.site.register`, SQLAlchemy declarative base, ActiveRecord, Sequelize,
  Prisma, DRF serializers/viewsets, FastAPI routers, GraphQL schema/resolver
  registrations.
- **Decorators and macros that register handlers**: Flask `@app.route`,
  pytest fixtures and plugins, Click commands, Typer apps, Celery tasks,
  RQ jobs, AWS Lambda handlers, Cloudflare Workers, dispatch decorators,
  Rust `#[derive]`/proc-macros, custom registry decorators.
- **Reflection / dynamic dispatch**: `getattr`/`setattr`/`hasattr`,
  `__init_subclass__`, metaclasses, `importlib`, `eval`/`exec`, Java
  `Class.forName`, `MethodHandles`, Kotlin `KClass`, Ruby `send`/`public_send`/
  `const_get`/`define_method`, JavaScript dynamic `import()` with computed
  paths, computed property access on registries, `Object.keys` over a
  module's exports.
- **Native / FFI surface**: `extern "C"`, `#[no_mangle]`, JNI exports,
  pybind11/cython modules, `ctypes`/`cffi` symbol lookups, `dlsym` callers,
  WebAssembly exports, exported types in a public crate or npm package.
- **Manifests and infra**: Helm charts, Terraform modules, Kubernetes
  manifests, GitHub Actions workflows, GitLab CI, systemd units, Docker
  entrypoints/CMD, `package.json` `scripts`, `Makefile` targets,
  `pyproject.toml` entry points, `Cargo.toml` `[[bin]]`/features, cron
  entries, queue worker configs.
- **Schemas**: protobuf, Avro, JSON Schema, OpenAPI, GraphQL SDL — including
  dead-looking message types or fields that may be required by external
  consumers or stored data.
- **i18n keys / locale catalogs**: `*.po`, `*.json` translation bundles,
  ICU MessageFormat strings.
- **Telemetry and feature-flag names**: event names, metric names, span
  names, flag keys are often referenced from dashboards, alerts, or remote
  config and will not appear in code searches.
- **Permission / policy / RBAC names**: roles, scopes, capability strings.
- **Database migrations**: every migration is load-bearing as long as any
  environment has a schema_migrations row referencing it. Do not delete
  migrations.
- **Generated and vendored directories**: `vendor/`, `node_modules/`,
  `target/`, `build/`, `dist/`, `.next/`, `__generated__/`, `*.pb.go`,
  files marked `// Code generated`. Do not remove from these.
- **Test fixtures and golden files** referenced from CI configs or by
  filename from tests (string lookup).

Produce an **Exclusion Allowlist** — a structured list of files, directories,
symbols, and string-name patterns that must not be removed without explicit
user approval. Hold this list in context for the rest of the run; if it
grows beyond a few hundred entries, stash it in a scratch file (e.g.
`/tmp/dead-code-allowlist-<branch>.json`) and reload it as needed.

Phase 5 (Classify) **must** check every candidate against this allowlist
before assigning a `safe-remove` label. Allowlist hits cap at `needs-approval`.

---

## Phase 2 — Establish baseline

1. **Working tree must be clean.** Run `git status`. If there are
   uncommitted changes, **abort** and ask the user to commit/stash first.
2. **Capture starting commit.** `git rev-parse HEAD` — record it.
3. **Create a working branch.** Run `git branch --list 'chore/dead-code-pass-*'`
   to find the next free integer suffix `<n>` (start at 1), then
   `git checkout -b chore/dead-code-pass-<n>`.
4. **Run the baseline.** Run, in order: build, full test suite, full lint
   pass. Use the project's documented commands (from `README`, `CLAUDE.md`,
   `lefthook.yml`, CI config, or `package.json`/`Makefile` scripts). Capture
   exit codes and any warnings.
5. **Hard gate**: if any of build / tests / lint **fails or errors before
   any change is made**, **abort the run** and report to the user. Dead-code
   elimination on a broken baseline silently corrupts state. Do not try
   to fix the failure as part of this run.
6. **Hard gate**: if you cannot determine how to run tests/lints, abort and
   ask the user. Do not assume.

Record the baseline command set and pass status — you will rerun these
exact commands after each removal.

---

## Phase 3 — Static analysis

Run only the analyzers that are **already available** in the repo
(installed in `node_modules`, declared in `pyproject.toml`/`requirements*.txt`,
listed in `Cargo.toml`'s dev-dependencies, configured in `lefthook.yml` /
pre-commit / CI). **Do not install new tools.** Skip languages not present in
the repo.

### Native compiler / built-in analyzers (always prefer these first)

| Language | Native checks |
|---|---|
| TypeScript | `tsc --noEmit --noUnusedLocals --noUnusedParameters` |
| Go | `go vet ./...`, `go build ./...` |
| Rust | `cargo check --all-targets`, default `dead_code` lint |
| Python | `python -W error -c 'import compileall; compileall.compile_dir(...)'`, `mypy --warn-unreachable` (if mypy configured) |
| Java | `javac -Xlint:all`, IDE inspections if scriptable |
| C# | `dotnet build /warnaserror:CS0219,CS0168` |
| C/C++ | `clang -Wunused -Wunreachable-code -Wunused-function` |
| Swift | `swiftc -warnings-as-errors`, Xcode warnings |
| Ruby | `ruby -W2`, `rubocop` if configured |
| Haskell | `-Wunused-imports -Wunused-top-binds -Wunused-local-binds` |
| Elisp | `emacs --batch -f batch-byte-compile` |
| Nix | `nix flake check`, `deadnix` if present |
| Shell | `shellcheck` if present |

### Third-party detectors (only if already in the project's dev deps)

| Language | Tool | Notes |
|---|---|---|
| Python | `vulture` | High false-positive rate; treat output as candidates only. |
| Python | `pyflakes` / `ruff F401, F811, F841` | Imports / shadowed / unused locals. |
| TS/JS | `knip` | Best-in-class for unused exports, files, deps. |
| TS/JS | `ts-prune` | Unused exports. |
| TS/JS | `depcheck` | Unused dependencies — **advisory only**, very lossy. |
| Rust | `cargo +nightly udeps` | Unused dependencies. |
| Rust | `cargo machete` | Unused dependencies (stable). |
| Go | `staticcheck`, `deadcode` (`golang.org/x/tools/cmd/deadcode`) | |
| Java | SpotBugs, Error Prone, IntelliJ inspections | Prefer over `jdeps`, which is not a dead-code tool. |
| Kotlin | detekt | |
| C# | Roslyn analyzers, `dotnet format --verify-no-changes` | |
| PHP | Psalm, PHPStan, `composer-unused` | |
| Ruby | `debride` | |
| Swift | Periphery | |
| Haskell | `weeder` | Unused top-level bindings. |
| C/C++ | `cppcheck`, IWYU, clang-tidy `misc-unused-*` | |

If your environment has an LSP available (`gopls`, `pyright`,
`rust-analyzer`, `clangd`, `tsserver`, `jdtls`), prefer
**LSP "find references"** over text grep when verifying any individual
candidate — it understands scope and renamed-import edge cases.

Aggregate the union of analyzer findings into a **candidate set**:
`{ kind, location, name, evidence }`.

---

## Phase 4 — Cross-reference verification

For every candidate, you must collect independent evidence before classifying
it. Do not skip checks — silent breakage usually comes from a symbol
referenced from a place you didn't search.

For each candidate symbol or file, run the following checks (record results):

1. **Repo-wide grep**, not just source dirs. Search for the symbol name and
   plausible **case variants** (camelCase, snake_case, kebab-case, PascalCase,
   ALL_CAPS, file-name-style). Use `rg --hidden` so you also see dotfiles.
   ```bash
   rg --hidden -F '<symbol>'  # also try variants
   ```
2. **String-literal search**. The symbol may be referenced as a string
   (decorators, plugin registries, dynamic imports, config files).
3. **Filename search**. The symbol's file may be referenced by path from
   configs, scripts, or docs.
4. **Manifest scan**. Check `package.json` `scripts`, `Makefile`,
   `pyproject.toml` entry points, `Cargo.toml`, `composer.json`, `Gemfile`,
   `requirements*.txt`, all CI YAML, Helm/Terraform/K8s files, Dockerfiles,
   systemd units.
5. **Routing / handler tables**. If applicable, run the framework's own
   listing command:
   - Rails: `bin/rails routes`
   - Laravel/PHP: `php artisan route:list`
   - Django: `manage.py show_urls` (with django-extensions)
   - FastAPI/Flask: import the app and list routes
   - GraphQL: load and dump the schema
6. **Public surface diff**. For libraries, capture the public-API surface
   before and after (e.g. `cargo public-api`, TypeScript declaration files,
   Python `__all__` + sphinx `autoapi`, javap on jars). Any diff at the
   public boundary requires user approval.
7. **`git log` evidence**. Recency is a signal:
   - `git log --follow -- <file>` — when was this last touched?
   - `git log -S '<symbol>' --all` — has it ever had a real caller?
   - **Recently-touched code** (commits within the last ~30 days) should
     default to `needs-approval`, even if static analysis says "unused".
8. **Test references**. Is the candidate referenced from test files,
   fixtures, or snapshot files? Removing a test for a real feature is a
   silent loss of coverage.
9. **Dynamic-mechanism check**. Does the candidate's name match anything in
   the Phase 1 Exclusion Allowlist (decorator-registered, autoloaded,
   ORM-mapped, route-handler, FFI-exported)? If yes → `needs-approval` or
   `keep`.

### Two-evidence rule (mandatory for dynamic languages)

Before classifying any symbol in Python, Ruby, JavaScript, TypeScript,
Elixir, PHP, or Lua as `safe-remove`, you must have **at least two of**:

- A static analyzer that does whole-program reachability (knip, ts-prune,
  cargo-udeps-style closure analysis) confirming no callers.
- An LSP "find references" that returns zero references.
- A repo-wide grep across all variants and string literals returning zero
  hits outside the definition itself.
- An entry-point/manifest scan confirming the symbol is not declared anywhere.

A single passing test suite is **not** enough.

---

## Phase 5 — Classify

For each candidate, assign exactly one label.

**Mandatory pre-classification check**: cross-check the candidate against the
Phase 1 Exclusion Allowlist (file path, directory, symbol name, string
pattern). If it matches *any* allowlist entry, the maximum allowed label is
`needs-approval` — never `safe-remove`, regardless of static-analysis
strength.

- **`safe-remove`** — All evidence says no callers, no dynamic wiring, not
  on the allowlist, not recently-touched, not on a public surface.
- **`needs-approval`** — Probably removable, but touches public API,
  reflection sites, recent code, deploy/ops configs, or any allowlist
  entry. Requires a yes from the user.
- **`keep`** — Found a real reference; not dead.

Default uncertain cases to `needs-approval`. Never default to `safe-remove`.

---

## Phase 6 — Documentation sweep (identify only; do not edit yet)

Stale documentation has no compiler to validate it. Be especially careful;
when uncertain, leave it.

This phase only **identifies** stale-doc candidates. Actual edits happen in
Phase 7: doc changes anchored to a removed symbol are bundled into the same
commit as that symbol's removal; pure-doc cleanups (no associated code) get
their own atomic commits at the end of Phase 7, each with a targeted check
(rebuild the docs site / run doctest / try the example) before commit.

Look for:

1. **Symbol-anchored docs**. For each `safe-remove` symbol from Phase 5,
   search docs/, README*, comments, `*.md`, `*.rst`, `*.adoc`, `*.org` for
   the symbol's name. Record the locations alongside the symbol so Phase 7
   can update both in one commit.
2. **Outdated examples**. README/docs code examples that reference removed
   APIs or no-longer-valid commands. If you can run the example (compile,
   `--help`, doctest), do so; flag failures as candidates.
3. **Completed migration guides**. `MIGRATION*` documents whose target
   version is now older than the codebase's current version (check `package.json`,
   `Cargo.toml`, `pyproject.toml`, etc.).
4. **TODO/FIXME comments referencing completed work**. If a TODO refers to
   an issue tracker ID, check it (`gh issue view`); if closed, the TODO can
   be removed. Without an ID, leave it — TODOs are often load-bearing
   reminders.
5. **README sections for removed dependencies**. After removing a
   dependency, scrub the README's install/usage/troubleshooting sections.
6. **Stale ADRs / runbooks / changelogs / API specs**. Treat as
   `needs-approval` — these are often historical record and should be kept
   even when superseded. Do not remove without explicit user approval.
7. **Commented-out code blocks** older than ~6 months (per `git blame`).
   These are noise; safe to remove. Smaller/younger blocks → leave.
8. **Dead docstrings / API specs**. If an OpenAPI/GraphQL spec describes a
   removed endpoint, regenerate the spec from source if the project has a
   generator; otherwise flag for approval.

---

## Phase 7 — Atomic removals

For each `safe-remove` candidate, in dependency order (leaves first):

1. **Delete only that one thing.** Bundle in any Phase-6 doc updates anchored
   to this symbol. No other incidental edits — no refactors, renames, or
   reformatting.
2. **Run a fast targeted check** appropriate to the language:
   - TS/JS: `tsc --noEmit` for the package (or `tsc -b` in monorepos — and
     in a monorepo, also build any package that imports the changed one).
   - Rust: `cargo check --all-targets` for the affected workspace member.
   - Go: `go build ./...`.
   - Python: `python -m compileall <package>` plus `mypy` on touched files
     if mypy is configured.
   - C/C++: `make` for the affected target(s) / `cmake --build`.
   - Else: smallest-scope build the project supports.
3. **Run the tests covering the affected package/module.** Full suite is
   not required after every single commit (too slow on most repos); a
   targeted test selection is enough at the per-commit step.
4. **If anything fails — recover cleanly before moving on.** Nothing is
   committed yet, but the working tree may have staged changes, unstaged
   changes, and/or new untracked files (e.g. if a deletion required a
   new helper file, which it should not in pure removals — but defensive).
   Run, in order:
   ```
   git restore --staged --worktree -- .
   git clean -fd
   ```
   Then verify with `git status` that the tree matches `HEAD`. Reclassify
   the candidate as `needs-approval`, record the failure mode in the
   report, and move to the next candidate. **Never** use `git reset --hard`
   or `git push --force` here — they can lose unrelated user state.
5. **If everything passes**: stage and commit:
   ```
   git add -- <changed files>
   git commit -m "chore: remove unused <thing> (<short evidence>)"
   ```
   Use commit messages that name the symbol and summarize the evidence,
   e.g. `chore: remove unused helper foo_bar (no callers per knip + grep)`.
6. **Stop at the blast-radius cap.** Default is 20 commits; the user may
   override via `cap=N` in `$ARGUMENTS` (`cap=0` disables the cap). When
   the cap is hit, list remaining candidates in the report and stop —
   do not silently keep going.

After every ~5 commits **and** at the end of Phase 7, run the **full** baseline
command set (build + tests + lints) and confirm green. If the milestone full
run fails, identify the most recent commit that introduced the failure
(`git bisect` or commit-by-commit revert), revert it, and re-run until green.

---

## Phase 8 — Final verification

1. Run the full baseline command set one more time on the final tree.
2. `git log --oneline <starting-commit>..HEAD` — confirm only your atomic
   removal commits are present, no merges, no surprise edits.
3. `git diff --stat <starting-commit>..HEAD` — record line-count delta.
4. If the project has a public-API surface tool (Phase 4), capture the
   diff and ensure no unintended public surface change.

If the final run fails, do not push the branch. Identify and revert the
offending commit, re-run, and only then proceed.

---

## Phase 9 — Report

Produce a single Markdown report (print it; do not write to disk unless the
user requests). Structure:

```
# Dead-Code Elimination Report

**Branch**: chore/dead-code-pass-<n>
**Starting commit**: <sha>
**Ending commit**: <sha>
**Baseline commands**: <commands run before/after>
**Result**: green / red

## Removed (N commits)
- `<sha>` — <short message> — <files touched, lines removed>
- ...

## Deferred (needs-approval) (M items)
- `<location>` — `<symbol>` — reason for deferral, what evidence was found,
  what evidence was missing.

## Skipped (kept) (K items)
- `<location>` — `<symbol>` — concrete reference that was found.

## Stale-doc changes
- <file>:<lines> — <summary>

## Toolchain notes
- Tools attempted, tools missing, tools that produced output.

## Cap status
- Hit blast-radius cap? Yes/No. Remaining candidates: <count>.
- To continue, re-invoke `/eliminate-dead-code <scope>`.

## Risks and uncertainties
- Anything you noticed but did not act on, plus a recommendation.

## Unrelated issues observed
- (Per the operating principle of not fixing unrelated issues mid-run.)
```

Then print the proposed next step:

- If `Result: green` and cap not hit: "Branch ready for review — open a PR
  with `gh pr create` when you've reviewed the diff."
- If `Result: green` and cap hit: "Re-invoke for another pass."
- If `Result: red`: do not propose merging; describe the offending commit
  and the diagnostic step to take.

---

## Approval gates (always pause before any of these)

Stop and **ask the user** before performing any of the following, regardless
of static-analysis confidence:

- Removing or modifying any **public API surface** (library exports, CLI
  commands, web routes, RPC handlers, GraphQL types, OpenAPI endpoints).
- Removing any symbol matching a pattern in the **Phase 1 Exclusion Allowlist**.
- Removing any **database migration**.
- Removing files inside **generated** or **vendored** directories.
- Removing **test fixtures**, **golden files**, or **i18n keys**.
- Removing anything mentioned in **deploy / ops / runbook** files.
- Removing anything **touched within the recency window** (default 30 days,
  configurable via `recent=Nd` in `$ARGUMENTS`, per `git log --since`).
- Removing **conditional-compilation branches** (`#ifdef`, `cfg!`, feature
  gates) — the inactive branch may target a platform you can't build.
- Removing **dead feature-flag definitions** when the flag's default may
  still be read from a remote config service.
- Removing **deprecated APIs** that may still have external consumers, even
  if internal callers are gone.

For each gated case, present:
- the candidate,
- the evidence you collected,
- the specific reason it triggered the gate,
- and your recommendation (remove / keep / further investigation),
then wait for the user's decision before continuing.

---

## What to never do

- Never modify `.git/`, `.github/` workflows, CI configs, or hooks unless the
  user explicitly asked for that scope.
- Never delete a file from a directory containing `# Code generated`,
  `@generated`, or that is listed in `.gitattributes` with `linguist-generated`.
- Never use `git push --force`, `git reset --hard` on a non-throwaway branch,
  or `git rebase` to hide commits.
- Never bypass `pre-commit`/`lefthook` with `--no-verify`.
- Never auto-install a static-analysis tool; if it's not present, skip it.
- Never remove "old-looking" code based purely on age — many projects have
  stable, rarely-touched, still-load-bearing modules.
- Never trust a single evidence source in a dynamic language. Two-evidence
  rule is mandatory.
- Never claim "no behavior change" — instead report **the evidence collected**
  and let the user judge.

## When you are unsure

Tell the user. A short message like

> I found 14 candidates. 9 are clearly unreferenced, 3 touch a route-handler
> file pattern (FastAPI auto-discovery), 2 have a single string-literal
> reference in a YAML config that may or may not be live. I will remove the
> 9, defer the others to the report, and ask before touching the 3
> route-handler candidates. OK?

is always preferable to silently making the wrong call.
