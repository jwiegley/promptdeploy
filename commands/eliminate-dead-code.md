---
allowed-tools: Read, Grep, Glob, Bash(git:*), Bash(gh:*), Bash(rg:*), Bash(grep:*), Bash(find:*), Bash(wc:*), Bash(ls:*), Bash(cat:*), Bash(test:*), Bash(make:*), Bash(just:*), Bash(bazel:*), Bash(pre-commit:*), Bash(lefthook:*), Bash(npm:*), Bash(pnpm:*), Bash(yarn:*), Bash(bun:*), Bash(npx:*), Bash(node:*), Bash(tsc:*), Bash(eslint:*), Bash(knip:*), Bash(ts-prune:*), Bash(depcheck:*), Bash(python:*), Bash(python3:*), Bash(uv:*), Bash(pip:*), Bash(pixi:*), Bash(poetry:*), Bash(pdm:*), Bash(pytest:*), Bash(ruff:*), Bash(mypy:*), Bash(pyright:*), Bash(vulture:*), Bash(go:*), Bash(staticcheck:*), Bash(deadcode:*), Bash(cargo:*), Bash(rustc:*), Bash(periphery:*), Bash(swift:*), Bash(xcodebuild:*), Bash(dotnet:*), Bash(composer:*), Bash(php:*), Bash(psalm:*), Bash(phpstan:*), Bash(bundle:*), Bash(rake:*), Bash(rails:*), Bash(rubocop:*), Bash(debride:*), Bash(stack:*), Bash(cabal:*), Bash(ghc:*), Bash(hlint:*), Bash(weeder:*), Bash(clang:*), Bash(clang-tidy:*), Bash(cppcheck:*), Bash(cmake:*), Bash(gcc:*), Bash(g++:*), Bash(mvn:*), Bash(gradle:*), Bash(gradlew:*), Bash(mix:*), Bash(emacs:*), Bash(nix:*), Bash(deadnix:*), Bash(shellcheck:*), Edit, Write, Task
description: Find and remove dead code and stale documentation with evidence-based safety, using a mark / debate / act / verify workflow
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

## The four-phase workflow

This command runs in four phases. Do not interleave them.

1. **MARK** — Discover, analyze, and bracket every dead-code region with
   in-source markers (plus a sidecar manifest for things that can't be
   bracketed). No code is changed or removed in this phase; markers are left
   as **uncommitted working-tree changes** for review.
2. **DEBATE** — For each marked region, decide its fate. Ambiguous or
   high-blast-radius regions get a three-advocate debate (keep-as-is / modify /
   remove); trivially-dead regions take a lighter safety-biased checklist.
   Every region ends with exactly one verdict: `keep`, `modify`, or `remove`.
3. **ACT** — Walk the regions in dependency order and apply each verdict as an
   atomic commit, stripping the region's markers as you go.
4. **VERIFY** — Run the full build/test/lint suite, confirm **zero markers
   remain** in the tree, and print a structured report.

---

## Scope

Interpret `$ARGUMENTS`:

- **Empty or `.`** → full repository.
- **A path** (`src/foo`, `docs/`) → restrict discovery, analysis, marking, and
  removals to that subtree (cross-reference checks still scan the entire repo).
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

1. **Conservative by default.** When uncertain, the verdict is `keep`, never
   `remove`. Uncertainty never resolves to removal — not by majority vote, not
   by "the evidence mostly points that way."
2. **Pure deletions and surgical modifications only.** Do not refactor, rename,
   reformat, or "improve" adjacent code. A `modify` verdict applies only the
   concrete diff the debate produced — nothing more. Don't fix unrelated bugs
   along the way — record them in the final report instead.
3. **Atomic commits.** One logical region per commit, with a clear message.
   Each commit must leave the build and tests passing.
4. **Two-evidence rule for dynamic languages.** In Python, Ruby, JavaScript,
   TypeScript (and any language that supports reflection or string-based
   dispatch), a passing test suite is **not sufficient evidence** of safety.
   Require **at least two independent forms of evidence** before a region is
   even marked as a removal candidate, and again before a `remove` verdict is
   finalized. The two sources must be *independent modalities* (e.g. static
   "no references" *and* an entry-point/registration check), not two variants
   of the same grep. See "Cross-reference verification".
5. **Native tooling first.** Prefer compiler flags and lints already
   configured in the repo (`tsc --noUnusedLocals`, `go vet`, `cargo check`,
   `-Wunused`, `mypy --warn-unreachable`, `ruff` if configured) over
   third-party tools. **Never auto-install** a static-analysis tool — if a
   recommended tool is not present, note it and move on.
6. **Avoid the yak-shaving trap.** If the project's build/test commands fail
   in your environment due to missing toolchains, abort and report — do not
   spend the run trying to install things.
7. **Blast-radius cap.** Make at most **20 removal/modification commits per
   invocation** by default. If more candidates carry a non-`keep` verdict,
   list them in the report and stop. The user can re-invoke for further passes.
8. **Never bypass safety.** No `--no-verify`, no `--force`, no skipping tests,
   no amending commits to hide failures.
9. **Markers never escape.** The `DCE-BEGIN`/`DCE-END` markers introduced in
   Phase 1 are working-tree-only scaffolding. They are never committed, never
   pushed, and must be fully gone before Phase 4 reports success. If the run is
   interrupted, the markers can be discarded with `git restore --worktree -- .`
   because the working tree was clean at the start of the run.

---

# Phase 1 — MARK

The goal of this phase is to produce a complete, evidence-backed inventory of
dead-code regions, each bracketed by in-source markers (or recorded in the
sidecar manifest when bracketing is impossible). **No code is removed or
modified in Phase 1.** At the end you will have a working tree containing only
added marker comments and a new sidecar file, ready for review.

## 1.0 — Discover the project

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

## 1.1 — Inventory dynamic mechanisms (Exclusion Allowlist)

This step is the most important defense against breaking the project. Catalog
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

The allowlist is consulted twice: in step 1.7 (an allowlist hit may only be
*marked* with `class=needs-approval`, never `class=safe`) and again in Phase 2
(an allowlist hit caps the verdict at `needs-approval` and must be escalated
to the user before any `remove`/`modify` verdict is finalized).

## 1.2 — Establish baseline

1. **Working tree must be clean.** Run `git status`. If there are
   uncommitted changes, **abort** and ask the user to commit/stash first. A
   clean starting tree is what lets you discard markers safely later
   (`git restore --worktree -- .`).
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
exact commands in Phase 3 and Phase 4.

## 1.3 — Static analysis

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

## 1.4 — Cross-reference verification

For every candidate, you must collect independent evidence before marking it.
Do not skip checks — silent breakage usually comes from a symbol referenced
from a place you didn't search.

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
   the 1.1 Exclusion Allowlist (decorator-registered, autoloaded, ORM-mapped,
   route-handler, FFI-exported)? If yes → at most `needs-approval`.

### Two-evidence rule (mandatory for dynamic languages)

Before *marking* any symbol in Python, Ruby, JavaScript, TypeScript, Elixir,
PHP, or Lua as a removal candidate, you must have **at least two of**:

- A static analyzer that does whole-program reachability (knip, ts-prune,
  cargo-udeps-style closure analysis) confirming no callers.
- An LSP "find references" that returns zero references.
- A repo-wide grep across all variants and string literals returning zero
  hits outside the definition itself.
- An entry-point/manifest scan confirming the symbol is not declared anywhere.

The two sources must be independent modalities, not two variants of the same
grep. A single passing test suite is **not** enough. If the two-evidence bar
is **not** met, do not bracket the region — record it in the sidecar manifest
with `class=needs-review` instead, so Phase 2 can examine it without it
counting as a removal candidate.

## 1.5 — Initial classification

For each candidate, assign a *provisional* class. This class only controls how
the region is marked and routed in Phase 2 — it is **not** the final decision.

**Mandatory pre-classification check**: cross-check the candidate against the
1.1 Exclusion Allowlist (file path, directory, symbol name, string pattern).
If it matches *any* allowlist entry, the maximum class is `needs-approval` —
never `safe`.

- **`safe`** — All evidence says no callers, no dynamic wiring, not on the
  allowlist, not recently-touched, not on a public surface, two-evidence rule
  met. Routed to the **lighter checklist** in Phase 2.
- **`needs-approval`** — Probably removable, but touches public API,
  reflection sites, recent code, deploy/ops configs, or any allowlist entry.
  Routed to the **full three-advocate debate** in Phase 2 and gated on user
  approval.
- **`ambiguous`** — Evidence is mixed, the region is high-blast-radius (many
  importers, exported boundary, monorepo cross-package), or you simply are not
  sure. Routed to the **full three-advocate debate** in Phase 2.

Default uncertain cases to `ambiguous` or `needs-approval`. Never default to
`safe`.

## 1.6 — Stale-documentation sweep

Stale documentation has no compiler to validate it. Be especially careful;
when uncertain, leave it. This step only **identifies** stale-doc candidates
and records them; edits happen in Phase 3.

Look for:

1. **Symbol-anchored docs**. For each code candidate, search docs/, README*,
   comments, `*.md`, `*.rst`, `*.adoc`, `*.org` for the symbol's name. Record
   the locations on the candidate so a later `remove`/`modify` verdict updates
   both in one commit.
2. **Outdated examples**. README/docs code examples that reference removed
   APIs or no-longer-valid commands. If you can run the example (compile,
   `--help`, doctest), do so; flag failures as candidates.
3. **Completed migration guides**. `MIGRATION*` documents whose target
   version is now older than the codebase's current version (check `package.json`,
   `Cargo.toml`, `pyproject.toml`, etc.).
4. **TODO/FIXME comments referencing completed work**. If a TODO refers to
   an issue tracker ID, check it (`gh issue view`); if closed, the TODO can
   be a candidate. Without an ID, leave it — TODOs are often load-bearing
   reminders.
5. **README sections for removed dependencies**. Record so a dependency
   removal can scrub the README's install/usage/troubleshooting sections.
6. **Stale ADRs / runbooks / changelogs / API specs**. Treat as
   `needs-approval` — these are often historical record and should be kept
   even when superseded.
7. **Commented-out code blocks** older than ~6 months (per `git blame`).
   These are noise; class `safe`. Smaller/younger blocks → leave.
8. **Dead docstrings / API specs**. If an OpenAPI/GraphQL spec describes a
   removed endpoint, regenerate from source if a generator exists; otherwise
   flag for approval.

Doc candidates are recorded the same way as code candidates: bracketable doc
blocks get markers; anything anchored to a code symbol is attached to that
symbol's region; standalone doc files go in the sidecar manifest.

## 1.7 — Insert markers and write the sidecar manifest

Now bracket every candidate region. **This is the only mutation Phase 1 makes,
and it is never committed.**

### Marker format

For any region that can be safely bracketed by comments, wrap it with a
matched `DCE-BEGIN` / `DCE-END` pair using the file's native comment syntax:

```
<comment> DCE-BEGIN id=<NNN> kind=<symbol|block|comment-block|doc> class=<safe|needs-approval|ambiguous> name=<symbol-or-desc> evidence="<one-line summary>"
...the candidate region, unchanged...
<comment> DCE-END id=<NNN>
```

- `id` is a stable zero-padded integer, unique within the run.
- Comment leader matches the language: `//` (C/Java/Go/Rust/TS), `#`
  (Python/Ruby/Shell/YAML), `;;` (Elisp/Lisp), `--` (Haskell/SQL/Lua),
  `<!-- ... -->` (HTML/Markdown — close the comment on each line), `%`
  (LaTeX/Erlang), etc.
- **Syntax safety is mandatory.** Never insert a marker where a comment is not
  legal (inside a string literal, inside a JSON file, mid-expression, between a
  decorator and its function, inside a multi-line literal). If a region cannot
  be bracketed without risking a syntax/parse error, do **not** bracket it —
  record it in the sidecar manifest instead.
- Insert markers only; do not touch the bracketed lines.

### Sidecar manifest

Write `.dce-pass-<n>/candidates.json` — the **authoritative** record of every
candidate, including those that could not be bracketed. Schema:

```json
{
  "pass": <n>,
  "branch": "chore/dead-code-pass-<n>",
  "baseline_commands": ["<build>", "<test>", "<lint>"],
  "starting_commit": "<sha>",
  "candidates": [
    {
      "id": "001",
      "kind": "symbol|block|file|import|dependency|doc|feature-flag|comment-block",
      "location": "path/to/file.py:120-138 | path/to/file | package.json:devDependencies.foo",
      "name": "<symbol or description>",
      "class": "safe|needs-approval|ambiguous|needs-review",
      "marked_in_source": true,
      "evidence": ["no refs (rg, all variants)", "zero refs (pyright LSP)", "not in entry points"],
      "anchored_docs": ["docs/api.md:40-52"],
      "allowlist_hit": false,
      "verdict": null,
      "verdict_rationale": null
    }
  ]
}
```

- Whole-file deletions, unused imports, unused dependencies, and standalone
  doc files are **sidecar-only** (`marked_in_source: false`) — they have no
  natural bracketable region.
- `class=needs-review` entries are below the marking bar (e.g. dynamic-language
  candidates that failed the two-evidence rule). They are carried into Phase 2
  for examination but default to `keep`.
- `verdict` and `verdict_rationale` are filled in during Phase 2.

> Add `.dce-pass-*/` to your mental ignore-list. If the repo has a
> `.gitignore`, you may note (do not commit) that the sidecar directory is
> transient. Never commit the sidecar directory or the markers.

### End-of-phase review

Print a **Mark Report**: count of candidates by `kind` and `class`, the
sidecar path, and a reminder that the working tree now contains uncommitted
markers. Then run `git diff --stat` so the user can see exactly what was
annotated. **Do not proceed to Phase 2 until you have shown this.** If the
user wants to stop here, they can discard everything with
`git restore --worktree -- .` and delete the sidecar directory.

---

# Phase 2 — DEBATE

Decide the fate of every candidate in the sidecar manifest. The output of this
phase is a `verdict` (`keep` | `modify` | `remove`) plus a `verdict_rationale`
written back into `.dce-pass-<n>/candidates.json` for each candidate. **No code
changes happen in this phase.**

Route each candidate by its `class`:

## 2.A — Lighter path (class `safe`)

For trivially-dead regions where the two-evidence rule is already met and there
is no allowlist hit, public surface, or recency concern, you do not need a full
debate. Apply a **safety-biased checklist** directly:

- Re-confirm the recorded evidence still holds (no new references introduced).
- Confirm no allowlist hit, no public-surface membership, not recently touched.
- If all clear → `verdict: remove` (or `modify` for a doc block that should be
  trimmed rather than deleted).
- If *any* check is now uncertain → upgrade the candidate to `ambiguous` and
  send it through the full debate (2.B). Never downgrade silently to `remove`.

## 2.B — Full debate (class `ambiguous` or `needs-approval`)

For ambiguous or high-blast-radius regions, run a three-advocate debate. Spawn
three sub-agents via the `Task` tool, one per stance. Give each the same
context packet: the region's source (with surrounding file context), its
manifest entry and recorded evidence, the relevant slice of the Exclusion
Allowlist, and the project's framework/language profile from Phase 1.0–1.1.

You may **batch** several similar regions into one debate round to control
cost, and you may run the three stance-agents **in parallel** (a single message
with three `Task` calls). Reserve the full debate for `ambiguous`/`needs-approval`
regions only — do not spend it on `safe` regions.

Spawn these three advocates:

- **Keep-as-is advocate.** Argues the region must be retained unchanged. Job:
  find any reason it is or might be live — hidden callers, dynamic dispatch,
  framework convention, public-surface membership, recency, allowlist hit,
  external consumers. **May win on genuine uncertainty or a protected
  pattern.** Must cite concrete evidence (file:line, config key, route table
  entry); a bare "it might be used somewhere" is not admissible.
- **Modify advocate.** Argues the region should be transformed rather than kept
  or deleted (slimmed, inlined, a stub left behind, deprecated, narrowed in
  visibility, moved). **Must produce a concrete diff**, not a theory. If it
  cannot produce a specific safe diff, this stance loses by default — "modify"
  is not a place to park risk.
- **Remove advocate.** Argues full deletion. **Must enumerate the independent
  evidence** (the two-evidence sources for dynamic languages) and explicitly
  confirm there is no allowlist hit. Speculative or hypothetical justifications
  are inadmissible; only the recorded and re-verified evidence counts.

### Rules of evidence (binding on all advocates)

- Cite concrete artifacts: file:line, grep/LSP results, config keys, route
  tables, git history. No invented future use-cases. No imagined reflection
  risks that cannot be pointed to in the code or config.
- The allowlist is authoritative: if a region hits the allowlist, the
  Keep/Modify stances are the only ones that can prevail without explicit user
  approval.

## 2.C — Synthesis (you, the orchestrator, decide)

Collect the three arguments and decide **one** verdict per region using a
**safety-biased rubric — this is not a majority vote**:

1. **Two-evidence re-check.** For a dynamic-language region, if the `remove`
   stance cannot point to two independent evidence modalities that still hold,
   the verdict is `keep` (or a harmless docs-only `modify`). Missing evidence
   forces `keep`.
2. **Allowlist / approval gate.** If the region hits the allowlist or any
   approval-gate category (see "Approval gates"), do not finalize `remove` or a
   risky `modify` until you have presented the case to the user and received a
   yes. Pause and ask.
3. **Uncertainty resolves to `keep`.** If after the debate you are not
   confident, the verdict is `keep`. A balanced-looking debate is not a license
   to remove.
4. **`modify` requires a concrete, safe diff.** Only choose `modify` if the
   modify advocate produced a specific diff that is clearly behavior-preserving
   (or an approved behavior change). Otherwise prefer `keep`.
5. **`remove` requires clear, unrebutted evidence** of deadness with no
   admissible keep-argument standing.

Write `verdict` and a one-line `verdict_rationale` (citing the deciding
evidence) back into the sidecar manifest for every candidate, including
`keep`s. `needs-review` entries default to `keep` unless the debate surfaced
genuine two-evidence support for removal.

Print a **Debate Report**: per-region verdict with the one-line rationale, and
a list of any regions escalated to the user for approval. Resolve all
escalations before Phase 3.

---

# Phase 3 — ACT

Apply the verdicts. Walk the candidates in **dependency order (leaves first)**
so that removing a callee never orphans a still-present caller mid-pass.

For each candidate with a non-`keep` verdict:

1. **Apply exactly the verdict — nothing more.**
   - `keep`: strip this region's `DCE-BEGIN`/`DCE-END` markers (and clear its
     sidecar entry). No commit needed for a pure keep; just remove its
     scaffolding.
   - `modify`: apply the concrete diff from the debate, strip the region's
     markers, and bundle any anchored doc updates. No incidental edits.
   - `remove`: delete the bracketed region (markers and all) plus any
     sidecar-listed associated artifacts (anchored docs, now-unused imports
     the removal creates). No refactors, renames, or reformatting.
2. **Run a fast targeted check** appropriate to the language:
   - TS/JS: `tsc --noEmit` for the package (or `tsc -b` in monorepos — and
     in a monorepo, also build any package that imports the changed one).
   - Rust: `cargo check --all-targets` for the affected workspace member.
   - Go: `go build ./...`.
   - Python: `python -m compileall <package>` plus `mypy` on touched files
     if mypy is configured.
   - C/C++: `make` for the affected target(s) / `cmake --build`.
   - Else: smallest-scope build the project supports.
3. **Run the tests covering the affected package/module.** A targeted test
   selection is enough at the per-commit step; the full suite runs at the
   milestones below and in Phase 4.
4. **If anything fails — recover cleanly before moving on.** Nothing is
   committed yet, but the working tree may have staged changes, unstaged
   changes, and/or new untracked files. Run, in order:
   ```
   git restore --staged --worktree -- .
   git clean -fd
   ```
   This also restores the markers for the region you were acting on (they live
   in the working tree). Re-mark the region if needed, downgrade its verdict to
   `keep`, record the failure mode in the report, and move to the next
   candidate. **Never** use `git reset --hard` or `git push --force` here —
   they can lose unrelated user state.
5. **If everything passes**: stage and commit only the files this region
   touched:
   ```
   git add -- <changed files>
   git commit -m "chore: remove unused <thing> (<short evidence>)"
   ```
   Name the symbol and summarize the deciding evidence, e.g.
   `chore: remove unused helper foo_bar (no callers per knip + grep, debate: remove)`.
   For a `modify`, use `chore: simplify <thing> (debate: modify — <reason>)`.
6. **Stop at the blast-radius cap.** Default is 20 commits; the user may
   override via `cap=N` in `$ARGUMENTS` (`cap=0` disables the cap). When the
   cap is hit, strip remaining markers for un-acted regions (so none leak),
   list the remaining verdicts in the report, and stop.

After every ~5 commits **and** at the end of Phase 3, run the **full** baseline
command set (build + tests + lints) and confirm green. If a milestone full run
fails, identify the most recent commit that introduced the failure
(`git bisect` or commit-by-commit revert), revert it, and re-run until green.

Before leaving Phase 3, ensure **every** region's markers have been stripped —
acted-on regions lose them via the commit; `keep` regions and any skipped
regions must have markers removed manually. Markers must not survive into
Phase 4.

---

# Phase 4 — VERIFY

1. **No markers remain.** Run a repo-wide search and assert zero hits:
   ```bash
   rg --hidden -n 'DCE-BEGIN|DCE-END' && echo "FAIL: markers remain" || echo "OK: clean"
   ```
   If any marker survives, strip it before doing anything else — markers must
   never be committed or pushed.
2. **Full baseline.** Run the full build + test + lint command set one more
   time on the final tree.
3. **Commit history is clean.** `git log --oneline <starting-commit>..HEAD` —
   confirm only your atomic removal/modification commits are present, no
   merges, no surprise edits, no marker commit.
4. **Line-count delta.** `git diff --stat <starting-commit>..HEAD`.
5. **Public-API surface.** If the project has a public-API surface tool
   (Phase 1.4), capture the diff and ensure no unintended public surface change.
6. **Remove the sidecar.** Delete the `.dce-pass-<n>/` directory (it is
   transient analysis state, never committed).

If the final run fails, do not push the branch. Identify and revert the
offending commit, re-run, and only then proceed.

---

## Report

Produce a single Markdown report (print it; do not write to disk unless the
user requests). Structure:

```
# Dead-Code Elimination Report

**Branch**: chore/dead-code-pass-<n>
**Starting commit**: <sha>
**Ending commit**: <sha>
**Baseline commands**: <commands run before/after>
**Result**: green / red
**Markers remaining**: 0 (verified)

## Verdicts
- Removed: <N>   Modified: <M>   Kept: <K>   Escalated to user: <E>

## Removed / Modified (committed)
- `<sha>` — <verdict> — <symbol> — <files touched, lines delta> — <deciding evidence>
- ...

## Kept (and why)
- `<location>` — `<symbol>` — concrete reference or uncertainty that won the debate.

## Deferred (needs-approval, awaiting or denied by user)
- `<location>` — `<symbol>` — what evidence was found, what was missing, gate triggered.

## Stale-doc changes
- <file>:<lines> — <summary>

## Toolchain notes
- Tools attempted, tools missing, tools that produced output.

## Cap status
- Hit blast-radius cap? Yes/No. Remaining non-keep verdicts: <count>.
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

## Approval gates (always pause before finalizing a `remove`/`modify` verdict)

Stop and **ask the user** during Phase 2 synthesis before finalizing a
non-`keep` verdict for any of the following, regardless of static-analysis or
debate confidence:

- Removing or modifying any **public API surface** (library exports, CLI
  commands, web routes, RPC handlers, GraphQL types, OpenAPI endpoints).
- Removing any symbol matching a pattern in the **Exclusion Allowlist** (1.1).
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
- the evidence collected and the three-advocate arguments,
- the specific reason it triggered the gate,
- and your recommendation (remove / modify / keep),
then wait for the user's decision before writing the verdict.

---

## What to never do

- Never **commit or push** `DCE-BEGIN`/`DCE-END` markers or the
  `.dce-pass-*/` sidecar directory. They are working-tree-only scaffolding.
- Never insert a marker where a comment is not syntactically legal — sidecar
  it instead.
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
- Never trust a single evidence source in a dynamic language. The two-evidence
  rule is mandatory at both marking (1.4) and verdict synthesis (2.C).
- Never let a balanced-looking debate justify removal. Uncertainty → `keep`.
- Never claim "no behavior change" — instead report **the evidence collected**
  and let the user judge.

## When you are unsure

Tell the user. A short message like

> Phase 1 marked 14 candidates: 9 class `safe`, 3 `ambiguous` (route-handler
> files — FastAPI auto-discovery), 2 `needs-approval` (single string-literal
> reference in a YAML config). I'll run the lighter path on the 9, the full
> three-advocate debate on the 3 ambiguous ones, and ask before finalizing the
> 2 gated ones. OK?

is always preferable to silently making the wrong call.
