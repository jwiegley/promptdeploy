# MCP Launcher Bridge Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make promptdeploy's MCP deployment to claude targets effective by keeping `settings.json` canonical and passing it to Claude Code via `--mcp-config`, with `${VAR}` secrets passthrough.

**Architecture:** Three in-repo changes (ClaudeTarget stops deploy-time env expansion; `validate` warns on `${VAR}` references missing from `.env.example`; `mcp/context7.yaml` adopts the live HTTP form) plus one out-of-repo wrapper patch and an operational migration checklist. Spec: `docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md`.

**Tech Stack:** Python 3.12, pytest (100% line-coverage gate), ruff, mypy. Run tests with `PYTHONPATH=src python -m pytest tests/<file> -v` from the repo root.

**PRECONDITION:** The audit-implementation workflow's working-tree changes must be landed (committed) before executing this plan — it edits some of the same files (`claude.py`, `validate.py`, `CLAUDE.md`, `PROMPTDEPLOY.md`, tests). Start from a clean `git status`.

**File map:**
- Modify: `src/promptdeploy/targets/claude.py` (`deploy_mcp_server`) — remove deploy-time env expansion
- Modify: `src/promptdeploy/envsubst.py` — remove now-dead `expand_env_in_dict`; add `find_env_refs` and `read_env_example_keys`
- Modify: `src/promptdeploy/validate.py` — new warning in the MCP branch of `validate_item` (signature gains the env-example key set, or reads it in `validate_all`)
- Modify: `mcp/context7.yaml`, `.env.example`
- Verify/possibly modify: `src/promptdeploy/targets/opencode.py`, `src/promptdeploy/targets/droid.py` (headers passthrough for url servers)
- Tests: `tests/test_claude_target.py`, `tests/test_envsubst.py`, `tests/test_validate.py`, `tests/test_opencode_target.py`, `tests/test_droid_target.py`
- Docs: `CLAUDE.md`, `PROMPTDEPLOY.md`, `mcp/schema.md`
- Out of repo: `~/src/scripts/ai` (owner applies), migration checklist execution

**Out of scope (tracked elsewhere):** the `mcp/schema.md` "Local Overrides" section (audit finding S7, in the user-decisions list); any writing to `.claude.json`; rewiring other targets' MCP semantics beyond headers passthrough.

---

## Chunk 1: promptdeploy code changes

### Task 1: `${VAR}` passthrough in `ClaudeTarget.deploy_mcp_server`

**Files:**
- Modify: `src/promptdeploy/targets/claude.py` (the `deploy_mcp_server` method, currently lines 176-191)
- Test: `tests/test_claude_target.py` (tests at lines ~442-496: `test_expands_env_vars_in_env_dict`, `test_non_env_keys_not_affected_by_expansion`, `test_unset_env_vars_preserved`, `test_no_env_key_no_expansion`)

- [ ] **Step 1: Rewrite the four expansion tests as passthrough tests**

Replace the four tests named above (keep the same test class) with:

```python
def test_env_vars_passed_through_verbatim(self, tmp_path: Path, monkeypatch):
    # Even when the variable IS set, the reference must be written verbatim:
    # Claude Code expands ${VAR} at runtime via the --mcp-config launcher
    # bridge (see docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md).
    monkeypatch.setenv("MY_API_KEY", "secret123")
    target = ClaudeTarget("claude-test", tmp_path)
    target.deploy_mcp_server(
        "srv", {"name": "srv", "command": "x", "env": {"KEY": "${MY_API_KEY}"}}
    )
    result = json.loads((tmp_path / "settings.json").read_text())
    assert result["mcpServers"]["srv"]["env"]["KEY"] == "${MY_API_KEY}"

def test_headers_passed_through_verbatim(self, tmp_path: Path):
    target = ClaudeTarget("claude-test", tmp_path)
    target.deploy_mcp_server(
        "srv",
        {"name": "srv", "url": "https://x", "headers": {"K": "${SOME_KEY}"}},
    )
    result = json.loads((tmp_path / "settings.json").read_text())
    assert result["mcpServers"]["srv"]["headers"]["K"] == "${SOME_KEY}"
```

Match the existing tests' import style and fixtures (read the surrounding class first — the file's convention is `_make_target(tmp_path)` with settings at `tmp_path/".claude"/settings.json`; adapt the snippets above to that convention rather than copying them literally).

- [ ] **Step 2: Run the new tests to verify the first one fails**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py -k "verbatim" -v`
Expected: `test_env_vars_passed_through_verbatim` FAILS (value is `secret123`); the headers test already passes (headers were never expanded).

- [ ] **Step 3: Remove the expansion from `deploy_mcp_server`**

In `src/promptdeploy/targets/claude.py`, delete these lines from `deploy_mcp_server`:

```python
            if "env" in claude_config:
                from ..envsubst import expand_env_in_dict

                claude_config["env"] = expand_env_in_dict(claude_config["env"])
```

The method body becomes: strip keys, assign `settings.setdefault("mcpServers", {})[name] = claude_config`, save.

- [ ] **Step 4: Run the target test file**

Run: `PYTHONPATH=src python -m pytest tests/test_claude_target.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promptdeploy/targets/claude.py tests/test_claude_target.py
git commit -m "claude target: pass \${VAR} MCP references through verbatim

Runtime expansion happens via the --mcp-config launcher bridge; baking
expanded secrets into settings.json is no longer needed and leaked
plaintext keys to rsynced hosts. Spec: docs/superpowers/specs/
2026-06-12-mcp-launcher-bridge-design.md"
```

### Task 2: Remove the now-dead `expand_env_in_dict`

**Files:**
- Modify: `src/promptdeploy/envsubst.py` (delete function, lines ~107-121)
- Test: `tests/test_envsubst.py` (delete its tests, lines ~63-95, and its import at line 9)

- [ ] **Step 1: Confirm it is dead**

Run: `grep -rn expand_env_in_dict src/ tests/`
Expected: only the definition in `envsubst.py` and uses in `tests/test_envsubst.py`. If anything else uses it (the concurrent workflow may have changed code), STOP and keep the function; skip this task with a note.

- [ ] **Step 2: Delete the function and its tests**

Remove `expand_env_in_dict` from `src/promptdeploy/envsubst.py`, its name from the `tests/test_envsubst.py` import, and the test methods that call it.

- [ ] **Step 3: Run the full suite with coverage**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing -q`
Expected: PASS at 100% (deleting dead code must not open coverage holes; if envsubst.py shows misses, the deletion was incomplete).

- [ ] **Step 4: Commit**

```bash
git add src/promptdeploy/envsubst.py tests/test_envsubst.py
git commit -m "envsubst: drop expand_env_in_dict, dead after claude passthrough"
```

### Task 3: `validate` warning for `${VAR}` references missing from `.env.example`

**Files:**
- Modify: `src/promptdeploy/envsubst.py` — add two helpers
- Modify: `src/promptdeploy/validate.py` — extend the `item.item_type == "mcp"` branch of `validate_item` (currently near line 366) and thread the key set from `validate_all`
- Test: `tests/test_envsubst.py`, `tests/test_validate.py`

- [ ] **Step 1: Write failing helper tests in `tests/test_envsubst.py`**

```python
class TestFindEnvRefs:
    def test_finds_refs_in_nested_values(self):
        refs = find_env_refs(
            {"env": {"A": "${KEY_ONE}"}, "headers": {"B": "x ${KEY_TWO} y"}, "n": 3}
        )
        assert refs == {"KEY_ONE", "KEY_TWO"}

    def test_no_refs(self):
        assert find_env_refs({"command": "npx", "args": ["-y", "pkg"]}) == set()


class TestReadEnvExampleKeys:
    def test_reads_keys_skipping_comments(self, tmp_path):
        p = tmp_path / ".env.example"
        p.write_text("# comment\nFOO=bar\nexport BAZ=qux\n\nnot a pair\n")
        assert read_env_example_keys(p) == {"FOO", "BAZ"}

    def test_missing_file_returns_none(self, tmp_path):
        assert read_env_example_keys(tmp_path / "absent") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_envsubst.py -k "FindEnvRefs or ReadEnvExample" -v`
Expected: FAIL with ImportError/NameError.

- [ ] **Step 3: Implement the helpers in `src/promptdeploy/envsubst.py`**

```python
def find_env_refs(data: object) -> set[str]:
    """Collect ${VAR} variable names referenced anywhere in a nested value."""
    refs: set[str] = set()
    if isinstance(data, str):
        refs.update(m.group(1) for m in _ENV_PATTERN.finditer(data))
    elif isinstance(data, dict):
        for v in data.values():
            refs |= find_env_refs(v)
    elif isinstance(data, list):
        for v in data:
            refs |= find_env_refs(v)
    return refs


def read_env_example_keys(path: Path) -> set[str] | None:
    """Return variable names declared in a .env-style file, or None if absent.

    Mirrors load_dotenv's line rules (comments, blanks, optional ``export``).
    """
    if not path.is_file():
        return None
    keys: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key:
            keys.add(key)
    return keys
```

- [ ] **Step 4: Run helper tests**

Run: `PYTHONPATH=src python -m pytest tests/test_envsubst.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing validate tests in `tests/test_validate.py`**

Follow the file's existing fixture conventions (it builds `Config` objects and `SourceItem`s; read a nearby MCP test first). The behavior to pin down:

```python
def test_mcp_env_ref_missing_from_env_example_warns(...):
    # mcp item with env: {"K": "${NOT_DECLARED}"}; .env.example declares only OTHER.
    # Expect exactly one warning-level issue naming NOT_DECLARED and .env.example.

def test_mcp_headers_ref_checked_too(...):
    # url server with headers: {"K": "${ALSO_MISSING}"} -> warning. The flagship
    # migrated server (context7) keeps its key in headers; env-only would miss it.

def test_mcp_refs_all_declared_no_warning(...):
    # env + headers refs all present in .env.example -> no issues.

def test_no_env_example_file_skips_check(...):
    # .env.example absent -> no warnings (repos without the convention).
```

- [ ] **Step 6: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_validate.py -k env_example -v`
Expected: FAIL (no such warnings produced).

- [ ] **Step 7: Implement in `src/promptdeploy/validate.py`**

In `validate_all`, read the key set once: `env_keys = read_env_example_keys(config.source_root / ".env.example")` and pass it to `validate_item` (add a keyword parameter `env_example_keys: set[str] | None = None` so existing callers stay valid — check `cli.py` for other `validate_item` call sites and thread it there too if validation is invoked per-item).

In the MCP branch of `validate_item`:

```python
        if env_example_keys is not None:
            refs = find_env_refs(
                {k: metadata[k] for k in ("env", "headers") if k in metadata}
            )
            for var in sorted(refs - env_example_keys):
                issues.append(
                    ValidationIssue(
                        level="warning",
                        message=(
                            f"${{{var}}} is not declared in .env.example; "
                            f"an unset variable expands to empty at runtime"
                        ),
                        file_path=item.path,
                    )
                )
```

- [ ] **Step 8: Run the full suite with coverage**

Run: `PYTHONPATH=src python -m pytest tests/ --cov --cov-report=term-missing -q`
Expected: PASS at 100%.

- [ ] **Step 9: Run validate against the real repo**

Run: `PYTHONPATH=src python -m promptdeploy validate`
Expected: exit 0. Warnings are acceptable ONLY for variables genuinely missing from `.env.example` — if any appear, fix `.env.example` (next task adds `CONTEXT7_API_KEY`).

- [ ] **Step 10: Commit**

```bash
git add src/promptdeploy/envsubst.py src/promptdeploy/validate.py tests/test_envsubst.py tests/test_validate.py
git commit -m "validate: warn when mcp \${VAR} refs are missing from .env.example

Unset variables expand to empty (not an error) on the --mcp-config
surface, so a typo'd reference ships a server that fails auth; make
validate surface it. Covers env and headers values."
```

### Task 4: context7 HTTP form + headers passthrough on other targets

**Files:**
- Modify: `mcp/context7.yaml`, `.env.example`
- Verify/modify: `src/promptdeploy/targets/opencode.py` (url branch near line 290), `src/promptdeploy/targets/droid.py` (url branch near line 193)
- Test: `tests/test_opencode_target.py`, `tests/test_droid_target.py`

- [ ] **Step 1: Check headers passthrough for url servers on droid and opencode**

Read both url-server conversion branches. Determine whether a `headers` key survives into `mcp.json` (droid) / `opencode.json` (opencode). Check the consuming tools' current formats before adding anything (droid `mcp.json` and opencode `opencode.json` both support headers for remote servers as of their 2026 formats — verify against existing tests or docs in the repo's git history; if genuinely unsupported downstream, leave that target's conversion unchanged and instead add `only:`-style guidance to Step 4's yaml).

- [ ] **Step 2: Write headers tests for both targets (mostly characterization)**

Both targets' url branches already copy `headers` through their remaining-keys loops, so the basic passthrough tests below are CHARACTERIZATION tests — expected to PASS immediately (do not stop-and-investigate when they do). The one genuinely failing (TDD) test is the opencode strict-expansion test.

In each target's test file, mirroring existing url-server tests:

```python
def test_url_server_headers_pass_through(self, tmp_path: Path):
    # deploy_mcp_server with {"url": ..., "headers": {"K": "${X}"}}
    # -> deployed config contains the headers mapping.  PASSES today.
```

For opencode, the policy change (this is the failing test): opencode strict-expands `env` at deploy time because nothing expands at runtime there; apply `expand_env_vars_strict` to headers values the same way, with context `mcp.<name>.headers.<key>`. Add a test with the variable set via monkeypatch asserting the EXPANDED value lands in opencode.json (and an unset variable raises `EnvVarError`). Implementation note: when adding strict expansion, exclude `headers` from opencode's remaining-keys copy loop so the key is not written twice.

- [ ] **Step 3: Implement, run target tests**

Run: `PYTHONPATH=src python -m pytest tests/test_opencode_target.py tests/test_droid_target.py -v`
Expected: PASS.

- [ ] **Step 4: Update `mcp/context7.yaml` to the live HTTP form**

(Ordering note: this yaml lands before the Task 7 Step 1 headers-expansion probe by design — the *live* cutover only happens at Task 7 Step 3, after the probe, and the probe's fallback path is a `ClaudeTarget` code change, not a yaml revert.)

```yaml
name: context7
description: Context7 documentation lookup for libraries and frameworks
url: https://mcp.context7.com/mcp
headers:
  CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}"
scope: user
enabled: true
```

- [ ] **Step 5: Add the variable to `.env.example`**

Append (matching the file's comment style):

```
# Context7 documentation lookup (used by: context7)
CONTEXT7_API_KEY=ctx7sk-...
```

- [ ] **Step 6: Validate and dry-run**

Run: `PYTHONPATH=src python -m promptdeploy validate && PYTHONPATH=src python -m promptdeploy deploy --dry-run --only-type mcp`
Expected: validate exits 0 with no warnings; dry-run shows context7 updating on claude/droid/opencode targets and nothing unexpected elsewhere (the other seven MCP servers showing "skip" is expected — their source bytes are unchanged).

- [ ] **Step 7: Commit**

```bash
git add mcp/context7.yaml .env.example src/promptdeploy/targets/opencode.py src/promptdeploy/targets/droid.py tests/test_opencode_target.py tests/test_droid_target.py
git commit -m "mcp: adopt context7 HTTP form; pass url-server headers through targets

The repo's stdio-npx context7 was the stale copy; the live form is HTTP
with an API-key header. Headers carry \${VAR} references: verbatim for
claude (runtime expansion), strict deploy-time expansion for opencode."
```

---

## Chunk 2: wrapper, docs, migration

### Task 5: Wrapper patch (`~/src/scripts/ai` — owner's repo)

**Files:**
- Modify: `~/src/scripts/ai` (insert before the final `exec "${cmd[@]}" "$@"`)

- [ ] **Step 1: Present this diff to the owner and apply on confirmation**

Insert immediately before the final `exec` line (after the `EXEC_TRACE` block, so parity traces are unaffected until `ai.py` gains the same change — flag that to the owner):

```bash
# MCP launcher bridge: promptdeploy deploys MCP servers into
# $CLAUDE_CONFIG_DIR/settings.json (mcpServers key), which Claude Code does
# not read on its own. Hand the file over explicitly; ${VAR} references in
# server env/headers expand at runtime from this environment. See
# ~/src/promptdeploy/docs/superpowers/specs/2026-06-12-mcp-launcher-bridge-design.md
if [[ ${cmd[*]} == *"$claude_cmd"* || ${cmd[*]} == *@anthropic-ai/claude-code* ]]; then
    if [[ -f "$CLAUDE_CONFIG_DIR/settings.json" ]]; then
        cmd+=(--mcp-config "$CLAUDE_CONFIG_DIR/settings.json")
    fi
fi
```

The `${cmd[*]}` substring tests cover the plain, `--sandbox`- and `--develop`-wrapped, and `--npx` invocations while excluding `--droid`/`--opencode`/`--local` (whose cmd arrays contain neither pattern).

- [ ] **Step 2: Verify with the wrapper's own trace mode, then a behavioral probe**

Run: `EXEC_TRACE=1 ai --positron -- --version` (and once per context flag)
Expected: trace exits 0. NOTE: the trace will NOT show `--mcp-config` — the patch sits after the trace-exit block by design; the trace only proves the script still parses. The real check is behavioral: `ai --positron -p "Say ok" --output-format json --max-turns 1` piped to `jq '.[0].mcp_servers'` (the init event) shows the settings.json server set including `context-hub`. If the output shape differs on some host/version, fall back to `--output-format stream-json --verbose` and read the first `system`/`init` event.

- [ ] **Step 3: Owner syncs the scripts repo to all hosts** (vulcan, hera, clio, vps, andoria-08, git-ai host) and notes that `ai.py` (the Python port used for parity testing) needs the same change before the next parity run.

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (claude target bullet + Environment-variable-handling bullet), `PROMPTDEPLOY.md` (MCP/env sections), `mcp/schema.md` (new "How servers reach Claude Code" section; `${VAR}` policy per target)

- [ ] **Step 1: Update the three docs against the POST-Task-1-4 code**

Content requirements (verify each sentence against the code as written, not against this plan):
- Claude Code does not read `settings.json` `mcpServers`; the `ai` wrapper passes `--mcp-config "$CLAUDE_CONFIG_DIR/settings.json"` at launch (load-bearing, lives outside this repo).
- Claude targets: `${VAR}` in `env`/`headers` passes through verbatim, expanded at runtime. OpenCode: strict deploy-time expansion (env + headers). Droid: unchanged policy.
- The standard per-profile verification probe:
  `claude --strict-mcp-config --mcp-config=<profile>/settings.json -p "Say ok" --output-format json --max-turns 1` (expect the profile's server set in the init event).

- [ ] **Step 2: Fact-check the edited sections against the code**

Read each changed paragraph and confirm every named function/file/flag exists. Run: `PYTHONPATH=src python -m pytest tests/ -q` (docs only — expect no change; this is a guard against accidental file touches).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROMPTDEPLOY.md mcp/schema.md
git commit -m "docs: document the MCP launcher bridge and \${VAR} passthrough policy"
```

### Task 7: Migration (operational checklist — run with the owner, sessions closed)

- [ ] **Step 1: Headers-expansion probe** (spec Evidence 3 caveat). Start a local listener that prints raw request headers: `nc -l 127.0.0.1 8765` in one terminal (BSD nc/macOS syntax; on GNU netcat use `nc -l -p 8765`). In another, write a throwaway config `{"mcpServers": {"hdr-probe": {"url": "http://127.0.0.1:8765/", "headers": {"X-Probe": "${HOME}"}}}}` and run the standard headless probe against it with `--strict-mcp-config`. The nc output shows `X-Probe:` either expanded (a real path) or verbatim (`${HOME}`). If headers do NOT expand: per the spec's fallback, restore deploy-time expansion in `ClaudeTarget.deploy_mcp_server` for `headers` values ONLY (`env` still passes through verbatim), leave `mcp/context7.yaml` with its `${VAR}` reference, record the asymmetry in `mcp/schema.md`, and continue.
- [ ] **Step 2: Wrapper coverage check per host**: `grep -n 'mcp-config' ~/src/scripts/ai` on every host named in a `deploy.yaml` `host:` field.
- [ ] **Step 3: Real deploy**: `promptdeploy deploy --only-type mcp --force` (with `.env` populated). `--force` matters: MCP item hashes are source-bytes only, so without it every server except context7 is "unchanged" and gets skipped, leaving the previously baked plaintext secrets in the deployed files. Spot-check one local profile's `settings.json` contains `${VAR}` references, not secrets.
- [ ] **Step 4: Remove duplicates from each profile's `.claude.json`** (every host, no live sessions): delete from top-level `mcpServers` exactly the names promptdeploy manages: `context-hub, context7, drafts, drafts-hera, pal, perplexity, sequential-thinking, stock-trader` (intersect with what each profile actually has; leave anything else). Use `claude mcp remove -s user <name>` with the real binary (`/etc/profiles/per-user/johnw/bin/claude`) and `CLAUDE_CONFIG_DIR` set, or a one-shot jq edit with the session closed.
- [ ] **Step 5: Verify each profile** with the standard probe (Task 6's command) and one interactive launch.
- [ ] **Step 6: Rotate the exposed keys** (Anthropic, Gemini, OpenAI, Perplexity, Context7) and update `.env` on each deploying host.
