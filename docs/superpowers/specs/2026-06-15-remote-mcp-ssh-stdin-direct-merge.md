# Implementation Spec: Remote MCP via SSH-stdin Direct Surgical Merge (FINAL)

**Supersedes** `docs/superpowers/specs/2026-06-15-remote-mcp-deployment-design.md` (the verbatim/`add-json` design). This is the implementation-ready final after the adversarial security/correctness/lifecycle/tests review. Locked decisions: **direct surgical merge** into the remote `.claude.json`, **deploy-time strict expansion** of secrets transported **only over SSH stdin**, **flush-before-`save_manifest`**, **env-folded remote-MCP hash**, **automatic for all remote claude targets**. It keeps 100% line+branch coverage and strict mypy green.

---

## 0. What changed vs. the prior spec, and why

The prior spec chose **verbatim `${VAR}` refs + `claude mcp add-json <name> <json>` over a `set -e` SSH batch**. After the security review, two decisions changed:

1. **Mechanism** is no longer the `claude` CLI. It is a **direct surgical merge into the remote `<remote_path>/.claude.json`**, mirroring the *local* `ClaudeTarget.deploy_mcp_server`/`remove_mcp_server` (claude.py:212-228, 366-374): for an enabled server set `mcpServers[name]=entry`, preserving every other app-owned key; for `enabled:false` or a removed server, pop `mcpServers[name]`. Reuse `ClaudeTarget._claude_mcp_entry` (`@staticmethod`, claude.py:230-243) so url servers get `type="http"`.
2. **Secrets** are now **deploy-time strict-expanded** (mirroring OpenCode, opencode.py:324-382) and transported **only over SSH stdin**: a generated `python3` merge program embeds the ops as `base64(json(ops))` so the secret is never an argv token and never appears in `ps`/`/proc/<pid>/cmdline`. The program is piped to `ssh <host> python3 -` on **stdin**.

Cascading consequences (reversals from the prior spec):

- `claude mcp add-json`/`remove`, `build_mcp_add_argv`, `build_mcp_remove_argv`, `_render_remote_script`, `--scope`, and `CLAUDE_CONFIG_DIR` are **all deleted from the design.** We write the known file `<remote_path>/.claude.json` directly: no scope plumbing, no config-dir export. `scope` remains stripped by `_claude_mcp_entry` and is otherwise a no-op on the remote merge (see §9.2).
- **Strict expansion is re-introduced** on the remote path (`expand_env_vars_strict`, envsubst.py:117-147): a missing var raises `EnvVarError` → exit 1 (never ship an empty secret).
- **The env-folded remote-MCP hash is re-introduced** (mirroring `_expand_env_for_hash`, deploy.py:160-176), because the expanded secret is now baked into the remote file, so a rotated secret value must trigger a redeploy. This must **not** change local-claude mcp hashing (local ships verbatim).
- **Convergence is fixed**: the flush moves OUT of `finalize()` and INTO the deploy loop, executed **before** `save_manifest`, so a failed flush leaves the manifest untouched and the next run auto-retries. `finalize()` reverts to push-only plus accumulator resets.

---

## Resolved decisions (post-review, 2026-06-15) — these supersede §9

All nine §9 open questions are now decided by the user:

1. **scope** — `scope: project` is silently ignored on remote (no rejection, no `validate` warning).
2. **python3** — required on the remote non-interactive PATH; missing → `SSHError` with a clear hint; **no** `python` fallback.
3. **at-rest secret** — accepted. The remote `.claude.json` write keeps the mkstemp-derived `0600` (no extra `os.chmod`).
4. **Host-key trust — FAIL CLOSED.** Change `_SSH_OPTS` in `ssh.py` from `StrictHostKeyChecking=accept-new` to `StrictHostKeyChecking=yes`, **global** (applies to all remote SSH incl. rsync). Partial would be unsound: rsync's `accept-new` could record a spoofed key that the secret-bearing merge then trusts. Existing hosts are already in `known_hosts` from prior deploys, so current deploys are unaffected; a brand-new host must be seeded out-of-band first. This adds a new change (the one-line constant + a failure hint mentioning `known_hosts`) and a doc note; update any test asserting `accept-new`.
5. **`--target-root` preview** — accept the local-verbatim preview gap; document it (no stderr notice).
6. **`status` without secret** — accept "changed" reporting (matches `models`); do **not** auto-load `.env` in `status`.
7. **3 SSH connections per remote deploy** — accepted.
8. **Live remote `claude` session race** — operational rule "deploy with remote sessions closed"; documented; no code mitigation.
9. **Spec file** — the superseded `2026-06-15-remote-mcp-deployment-design.md` is deleted; this file is the single spec.

---

## 1. Verified ground truth (re-read against real code 2026-06-15)

- `RemoteTarget.__init__(self, inner, host, remote_path, staging_path)` (remote.py:21-31); delegates all `Target` methods to `self._inner`, specializing `id`/`exists`/`rsync_includes`/`prepare`/`finalize`/`cleanup`/`_cleanup_staging`/`manifest_path`. Imports today: `from ..ssh import ssh_exists, ssh_pull, ssh_push`, `from .base import Target`, `from typing import Any`, `import shutil`, `from pathlib import Path`.
- `RemoteTarget.finalize` (remote.py:52-60) runs `ssh_push(...)` then `self._cleanup_staging()`. The deploy loop (deploy.py:601-609): on `not dry_run` it calls `save_manifest(...)` **then** `target.finalize(...)`; on dry-run it calls `target.cleanup()`; the `except BaseException:` path calls `target.cleanup()` then `raise`. **Today the flush would run AFTER `save_manifest` — this is what we move.**
- `create_target` (`targets/__init__.py:38-66`): `is_remote = host is not None and host != current_host()`; builds the claude inner with `manage_mcp=not is_remote`; wraps in `RemoteTarget(inner, host, target_config.path, staging_path)`. `ClaudeTarget` already imported.
- `ClaudeTarget._claude_mcp_entry(config)` (claude.py:230-243, `@staticmethod`): `entry = {k: v for k,v in config.items() if k not in _MCP_STRIP_KEYS}`; injects `type="http"` for url servers lacking `type` (preserves explicit `sse`). `_MCP_STRIP_KEYS = frozenset({"name","description","scope","enabled","only","except"})`. Does NOT expand env/headers.
- `ClaudeTarget.deploy_mcp_server` (claude.py:212-228) writes `self._claude_json_path()` = `self._config_path / ".claude.json"`. `enabled:false` → `servers.pop(name, None)`; else `_ensure_dict(data,"mcpServers")[name] = _claude_mcp_entry(config)`. env/headers VERBATIM. `_save_json` is atomic (tempfile `mkstemp` + `os.replace`, claude.py:502-514). `remove_mcp_server` (claude.py:366-374) pops the key, atomic save, no-op if file missing. `_load_json` raises `JsonConfigError` on a non-empty unparseable file (claude.py:496-500).
- `ClaudeTarget.item_exists("mcp", name)` (claude.py:408-411) reads `_claude_json_path()` and checks `mcpServers` membership; for a remote target the inner ClaudeTarget operates on the **staging** dir, which never has a `.claude.json` (we intercept), so a delegated read is always False. `content_fingerprint("mcp")` → constant `"claude-mcp-entry-v2"`. `would_deploy_bytes("mcp",...)` / `read_deployed_bytes("mcp",...)` return `None` (claude.py:422-446, 448-454) → the drift block (deploy.py:431-440) is **inert** for mcp.
- `compute_item_hash` (deploy.py:179-217): `settings`/`models` have config-aware branches; everything else, incl. `mcp`, takes the generic branch — `base = compute_file_hash(item.content)` (`"sha256:..."` per manifest.py:41-43), `fingerprint = "claude-mcp-entry-v2"`, returns `f"sha256:{sha256(f'{base}|{fingerprint}')}"`. Shared with `status.get_status`.
- `_expand_env_for_hash(value)` (deploy.py:160-176) recursively expands `${VAR}` from `os.environ`, leaving unset refs literal, **no warning** (hash-only). The new remote-MCP branch mirrors this.
- `expand_env_vars_strict(value, *, context="")` (envsubst.py:117-147) raises `EnvVarError` listing every unset var (NAMES only, never values). OpenCode applies it per-string over `env`/`headers` dict values with `if isinstance(v,str)` guards (opencode.py:350-369). We mirror this exactly.
- `EnvVarError` **is** in the cli.py caught tuple (cli.py:191-197) → `out.error(str(exc)); sys.exit(1)`. `SSHError` is **NOT** caught today → raw traceback. This spec adds `SSHError` (Change 6).
- `ssh.py` has `_SSH_OPTS` (incl. `StrictHostKeyChecking=accept-new`), `SSHError`, `_check_tools()` (requires rsync+ssh via `shutil.which`), `_quote_remote_path`, `from collections.abc import Sequence`, `import shlex`, `import shutil`, `import subprocess`, `import sys`. Direct-ssh calls (`ssh_exists`) use `subprocess.run([...], capture_output=True)` (BYTES, no `text=True`); decode-guard `result.stderr.decode(errors="replace").strip() if result.stderr else ""`; returncode 255 = connection failure.
- `load_manifest` returns an empty `Manifest()` on a missing file (manifest.py) — **no `.exists()` guard needed**. `Manifest.items: dict[str, dict[str, ManifestItem]]`; `ManifestItem(source_hash, target_path=None, managed_keys=None)`. `MANIFEST_FILENAME = ".prompt-deploy-manifest.json"`.
- The deploy loop's create/update decision is `is_update = category in manifest.items and item.name in manifest.items[category]` (deploy.py:444-447), read from the **same** staging manifest `item_exists` reads. The pre-existing branch (deploy.py:450-481) fires only when `not is_update and exists_on_target` and falls to `_disk_matches_source` (False for mcp since `would_deploy_bytes` is None).
- Source loader sets mcp `metadata=None` when the YAML is not a dict (source.py:265-269), so `item.metadata or {}` is a **live branch** for mcp.
- Stale-removal (`_remove_item` → `remove_mcp_server(name)`, deploy.py:296-297) passes NO config; runs inside `if not dry_run:`.
- `remap_targets_to_root` (config.py:166-175) sets `host=None` → `is_remote` False → bare `ClaudeTarget` with `manage_mcp=True`. So `--target-root` previews a remote-MCP target as a **local `.claude.json` write with verbatim `${VAR}`** (§6, §9.5).
- Selection predicate is `item_selected` (deploy.py:135), shared by `status.get_status` (status.py:8,58). The `list` command is **manifest-driven** and uses neither `item_selected` nor `compute_item_hash` (see §9.10).
- Local atomic-write pattern to mirror remotely: `tempfile.mkstemp(dir=path.parent)`, write, `os.replace(tmp, path)`, unlink-on-error (claude.py:476-514).

---

## 2. Ordered list of changes (exact file, symbol, full signature)

### Change 1 — `src/promptdeploy/ssh.py`: add the merge-program renderer + `ssh_stdin` runner

Two new functions plus the embedded remote-program template. The renderer is a pure function (no I/O), unit-testable in isolation; `ssh_stdin` does the subprocess.

**Imports to add:** `import base64`, `from string import Template`, `from typing import Any`. (The file already has `shlex`, `shutil`, `subprocess`, `sys`, `Sequence`.)

#### 1a. The remote-program template (uses `string.Template`, NOT `str.format`)

> **Why `string.Template` and not `str.format`** (review correctness/minor): the program text is full of literal `{` / `}` (dict literals). `str.format` would require exhaustively doubling every brace, and a single missed brace in a future edit raises at render time. `string.Template` uses `$NAME` placeholders and ignores braces entirely. Only two substitutions exist; `${...}` JSON in entries lives **inside** the base64 payload and never in the template body, so there is no `$` collision in the literal program text (the template body contains no `$`). `Template.substitute` is called exactly once and does not re-scan substituted values, so a `$` inside a repr'd path is inert. We additionally `compile()`-check the rendered text in a test (test 1b) to catch any future syntax/placeholder regression.

```python
# NOTE on this template's design:
#   * SECURITY: the entire program body after _fail() is wrapped in one outer
#     try/except BaseException -> _fail("unexpected error during merge"). This
#     makes "no secret-bearing value ever reaches stderr" a STRUCTURAL property,
#     independent of the remote interpreter's default traceback formatter. The
#     base64-decoded ops live in locals `ops`/`op`/`entry`; without this guard a
#     KeyError/ValueError raised in the merge loop or path-expansion could emit a
#     traceback that some interpreters (e.g. -X dev, custom excepthook) render
#     with locals. _fail prints ONLY fixed strings.
#   * The $PAYLOAD_B64 and $TARGET_PATH_REPR placeholders are filled by
#     build_claude_merge_script via string.Template.substitute. target_path is
#     injected as a repr() literal; ops only as base64.
_REMOTE_MERGE_TEMPLATE = Template(
    '''\
import base64, json, os, sys, tempfile

def _fail(msg):
    # NEVER print the payload, ops, or any entry value here.
    sys.stderr.write("promptdeploy remote MCP merge failed: " + msg + "\\n")
    sys.exit(1)

try:
    try:
        raw = base64.b64decode("$PAYLOAD_B64")
        ops = json.loads(raw.decode("utf-8"))
    except Exception:
        _fail("could not decode operations payload")

    path = os.path.expanduser($TARGET_PATH_REPR)

    data = {}
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            text = blob.decode("utf-8")
            if text.strip():
                data = json.loads(text)
        if not isinstance(data, dict):
            _fail("existing .claude.json is not a JSON object")
    except json.JSONDecodeError:
        _fail("existing .claude.json is not valid JSON; fix or remove it on the remote")
    except UnicodeDecodeError:
        _fail("existing .claude.json is not valid UTF-8; fix or remove it on the remote")
    except OSError:
        _fail("could not read existing .claude.json")

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers

    for op in ops:
        name = op["name"]
        if op["action"] == "pop":
            servers.pop(name, None)
        else:
            servers[name] = op["entry"]

    if not servers:
        data.pop("mcpServers", None)

    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        _fail("could not write .claude.json atomically")
except SystemExit:
    raise
except BaseException:
    _fail("unexpected error during merge")
'''
)
```

> Locked-in template guarantees:
> - **outer try/except** (review security/major): every path after `_fail`'s definition — payload decode, `expanduser`, file read incl. **non-UTF-8 (`UnicodeDecodeError`)**, the merge loop (`op["name"]`, `op["action"]`, `op["entry"]`), and the write — is inside the outer guard. `SystemExit` from `_fail` re-raises; anything else collapses to `_fail("unexpected error during merge")`. No traceback, no locals, ever reach stderr.
> - **load-or-empty**: missing file or present-but-blank both yield `{}`; a present non-empty file that is invalid JSON or non-UTF-8 is a HARD error (we must not clobber a real app-owned file we cannot parse), matching local `_load_json`'s `JsonConfigError` posture.
> - **surgical**: only `mcpServers[name]` keys are touched; all other top-level keys (OAuth, caches, per-project history) are preserved by load-merge-write.
> - **empty-cleanup**: if `mcpServers` becomes empty after pops, drop the key.
> - **atomic + 0600**: tempfile in the same dir + `os.replace`, unlink-on-error — byte-for-byte the local pattern. `tempfile.mkstemp` creates the temp file mode `0600`, and `os.replace` adopts the *source* (temp) file's mode, so the written `.claude.json` is **0600 regardless of the prior file's mode or the remote umask** — never widened. (See §9.5(ii): we deliberately do NOT add an explicit `os.chmod`, matching local `_save_json`; mkstemp already yields the narrow mode and the review confirmed this satisfies the not-widened requirement. Documented in §6.)

#### 1b. `build_claude_merge_script`

```python
def build_claude_merge_script(ops: Sequence[dict[str, Any]], target_path: str) -> str:
    """Render the python3 program that surgically merges MCP ops into a remote .claude.json.

    `ops` is a list of {"action": "set"|"pop", "name": str, "entry": dict|None}.
    It is embedded as base64(json(ops)) INSIDE the returned source so the
    secret-bearing payload is never an argv token and never appears in
    `ps`/`/proc/<pid>/cmdline`; the program decodes it from its own text.
    `target_path` is the remote .claude.json path (e.g. "~/.claude/.claude.json");
    `~` is expanded on the remote via os.path.expanduser inside the program.

    The program loads the file (or {} if missing/blank), sets/pops
    mcpServers[name] per op, and writes atomically (mkstemp 0600 in the same
    dir + os.replace). Its entire body is wrapped in an outer try/except so any
    error prints ONLY a fixed diagnostic to stderr (never the payload, ops, or
    any entry value) and exits non-zero; stdout stays empty on success.
    """
    payload = base64.b64encode(
        json.dumps(list(ops), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return _REMOTE_MERGE_TEMPLATE.substitute(
        PAYLOAD_B64=payload,
        TARGET_PATH_REPR=repr(target_path),
    )
```

> `import json` is already present in ssh.py? **No — add `import json`** to ssh.py imports (the file does not currently import json). Confirm during implementation; the renderer needs it.

#### 1c. `ssh_stdin`

```python
def ssh_stdin(host: str, script: str) -> None:
    """Run `python3 -` on `host`, piping `script` (which embeds the payload) via STDIN.

    SECURITY INVARIANT: never interpolate `script` into any message, log, or
    exception — it embeds the base64 secret payload. The remote process argv is
    exactly ["ssh", *_SSH_OPTS, host, "python3", "-"], so a secret embedded in
    `script` never appears in `ps`/`/proc/<pid>/cmdline`. The program is written
    to the child's stdin (input=script.encode()), NOT passed as an argument.

    Raises SSHError naming `host` on any non-zero exit. returncode 255 is a
    connection failure; 127 is missing python3; any other non-zero is a remote
    failure (the program's _fail diagnostic). Only the remote stderr + host are
    interpolated into the message; this is SAFE because the merge program never
    prints the payload/ops/entries.
    """
    _check_tools()
    result = subprocess.run(
        ["ssh", *_SSH_OPTS, host, "python3", "-"],
        input=script.encode("utf-8"),
        capture_output=True,
    )
    if result.returncode == 0:
        return
    stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
    if result.returncode == 255:
        raise SSHError(f"SSH connection to {host} failed: {stderr}")
    if result.returncode == 127:
        raise SSHError(
            f"python3 not found on {host} (exit 127): {stderr}. "
            "The remote MCP merge requires python3 on the non-interactive PATH."
        )
    raise SSHError(f"Remote MCP merge on {host} failed (exit {result.returncode}): {stderr}")
```

> - `_check_tools()` is called unguarded (same as the other ssh.py functions); its raise is already line-covered by existing tests. An optional `ssh_stdin`-specific tool-missing test (test 9b) pins the early-exit for the new function.
> - `verbose` is intentionally NOT a parameter (no untested branch, and — review security/minor — no path that could echo the secret-bearing `script`).
> - Renderer and runner are separate so tests can assert (a) the rendered program embeds base64 and never the raw secret, and (b) `subprocess.run` got the script via `input=` with argv `["ssh", *_SSH_OPTS, host, "python3", "-"]`.

### Change 2 — `src/promptdeploy/targets/remote.py`: capability flag + op accumulator + 4 MCP overrides + flush method

Top-of-file imports: change the ssh import to
`from ..ssh import build_claude_merge_script, ssh_exists, ssh_pull, ssh_push, ssh_stdin`
and add `from .claude import ClaudeTarget`.

> mypy/cycle note: claude.py imports only `..frontmatter`, `..manifest`, `.base` — it does NOT import remote.py, so `from .claude import ClaudeTarget` creates no cycle. The `EnvVarError`/`load_manifest`/`expand_env_vars_strict` imports stay function-local to match the codebase's lazy-import style.

**Ctor** — new signature (single-use instance; one per `deploy()`/`get_status()` call via `create_target`):

```python
def __init__(
    self,
    inner: Target,
    host: str,
    remote_path: Path,
    staging_path: Path,
    *,
    remote_mcp: bool = False,
) -> None:
    self._inner = inner
    self._host = host
    self._remote_path = remote_path
    self._staging_path = staging_path
    self._remote_mcp = remote_mcp
    # Accumulated surgical MCP merge ops, flushed by flush_remote_mcp()
    # BEFORE save_manifest in the deploy loop. Each op is one of:
    #   {"action": "set", "name": str, "entry": dict}
    #   {"action": "pop", "name": str, "entry": None}
    self._mcp_ops: list[dict[str, Any]] = []
    # Names whose set/pop op was queued this run. NOTE: not consulted during the
    # normal deploy loop ordering (item_exists for a name runs BEFORE its op is
    # queued); it exists only so a direct deploy_mcp_server->item_exists call
    # (e.g. in tests) reports True. Manifest is the authoritative source.
    self._mcp_seen: set[str] = set()
    # Memoized staging-manifest mcp names (one parse per deploy). None = unread.
    self._mcp_manifest_names: set[str] | None = None
```

> `_mcp_seen` comment corrected per review (correctness/minor): it is **not** load-bearing for deploy-loop ordering. We keep it (cheap, harmless, simplifies the `item_exists` contract) rather than dropping it.

**`should_skip`** override:

```python
def should_skip(self, item_type, name, content=None, metadata=None) -> bool:
    if self._remote_mcp and item_type == "mcp":
        return False
    return self._inner.should_skip(item_type, name, content, metadata)
```

**`deploy_mcp_server`** override — strict deploy-time expansion + `_claude_mcp_entry` reuse:

```python
def deploy_mcp_server(self, name: str, config: dict[str, Any]) -> None:
    if not self._remote_mcp:
        self._inner.deploy_mcp_server(name, config)
        return
    if not config.get("enabled", True):
        # Disabled => removal on the remote (mirrors local pop).
        self._mcp_ops.append({"action": "pop", "name": name, "entry": None})
        self._mcp_seen.add(name)
        return
    entry = ClaudeTarget._claude_mcp_entry(config)
    entry = self._expand_entry_secrets(name, entry)  # may raise EnvVarError
    self._mcp_ops.append({"action": "set", "name": name, "entry": entry})
    self._mcp_seen.add(name)
```

> Ordering note: `_expand_entry_secrets` runs **before** the op is appended (and before `_mcp_seen.add`), so a missing var raises `EnvVarError` with NO op queued — never ships an empty secret (tests 16/47).

**`_expand_entry_secrets`** — strict expansion of `env`/`headers` string values (mirrors OpenCode):

```python
@staticmethod
def _expand_entry_secrets(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Strict-expand ${VAR} in the entry's env/headers values for the remote bake.

    A missing variable raises EnvVarError (envsubst), which cli.py catches ->
    exit 1, so we never ship an empty secret to the remote .claude.json. Only
    env and headers carry secrets per the MCP schema; ${VAR} in any other field
    (command, args, url, type, ...) is out of schema contract and passes through
    VERBATIM -- and, unlike local where runtime never expands non-env/headers
    fields either, it will NOT be expanded on the remote. Returns a NEW dict;
    the source entry is not mutated.
    """
    from ..envsubst import expand_env_vars_strict

    out = dict(entry)
    for key, ctx in (("env", "env"), ("headers", "headers")):
        block = entry.get(key)
        if isinstance(block, dict):
            out[key] = {
                k: (
                    expand_env_vars_strict(v, context=f"mcp.{name}.{ctx}.{k}")
                    if isinstance(v, str)
                    else v
                )
                for k, v in block.items()
            }
    return out
```

**`remove_mcp_server`** override (stale path; no config):

```python
def remove_mcp_server(self, name: str) -> None:
    if not self._remote_mcp:
        self._inner.remove_mcp_server(name)
        return
    self._mcp_ops.append({"action": "pop", "name": name, "entry": None})
    self._mcp_seen.add(name)
```

**`item_exists`** override (manifest-backed; unchanged servers skip → no op):

```python
def item_exists(self, item_type: str, name: str) -> bool:
    if self._remote_mcp and item_type == "mcp":
        if name in self._mcp_seen:
            return True
        return name in self._remote_mcp_manifest_names()
    return self._inner.item_exists(item_type, name)
```

```python
def _remote_mcp_manifest_names(self) -> set[str]:
    """mcp_servers names from the staging manifest pulled by prepare().

    Memoized (one parse per deploy). load_manifest tolerates a missing file
    (returns empty Manifest), so a first-ever deploy reports no names and every
    server classifies as `create`. Reads the SAME staging manifest file the
    deploy loop loads (target.manifest_path()), so item_exists and the loop's
    is_update can never disagree in the single-threaded loop. Reset to None in
    finalize/cleanup."""
    if self._mcp_manifest_names is None:
        from ..manifest import load_manifest

        manifest = load_manifest(self._inner.manifest_path())
        self._mcp_manifest_names = set(manifest.items.get("mcp_servers", {}))
    return self._mcp_manifest_names
```

**`flush_remote_mcp`** — NEW public method, called by the deploy loop BEFORE `save_manifest` (Change 3). NOT in `finalize()`:

```python
def flush_remote_mcp(self) -> None:
    """Apply accumulated MCP ops to the remote .claude.json over SSH stdin.

    No-op when not a remote_mcp target or when no ops were queued (so no SSH
    connection is opened). Builds the merge program with the ops embedded as
    base64 and pipes it to `python3 -` on the host. Raises SSHError on failure;
    the caller (deploy loop) has NOT yet saved the manifest, so the next run
    retries. Does NOT clear _mcp_ops (cleared only in finalize/cleanup), so a
    flush-success followed by a finalize/push failure does not re-queue here.
    The remote `~` in remote_path is expanded inside the program via
    os.path.expanduser; no shell quoting is involved (the path is a repr()
    literal inside the piped program, not a shell word)."""
    if not self._remote_mcp or not self._mcp_ops:
        return
    target_path = f"{self._remote_path}/.claude.json"
    script = build_claude_merge_script(self._mcp_ops, target_path)
    ssh_stdin(self._host, script)
```

> `target_path` construction: `self._remote_path` is a `Path` from deploy.yaml that may be `~/.claude`. `f"{self._remote_path}/.claude.json"` yields `~/.claude/.claude.json`; `os.path.expanduser` in the remote program expands `~`. We do NOT use `_quote_remote_path` — the path is not on a shell command line; it is a Python `repr()` string literal inside the piped program. This is the key simplification of stdin transport: zero shell quoting anywhere.

**`remote_mcp_hash`** property (Change 5b):

```python
@property
def remote_mcp_hash(self) -> bool:
    return self._remote_mcp
```

**`finalize`** — push-only (current behavior) + accumulator resets. Flush is NOT here:

```python
def finalize(self, *, verbose: bool = False) -> None:
    ssh_push(
        self._host, self._remote_path, self._staging_path,
        verbose=verbose, includes=self._inner.rsync_includes(),
    )
    self._mcp_ops.clear()
    self._mcp_seen.clear()
    self._mcp_manifest_names = None
    self._cleanup_staging()
```

> If `ssh_push` raises, the resets and `_cleanup_staging` never run — matches the existing `test_finalize_push_failure_propagates` posture. The `.claude.json` is NEVER in `rsync_includes()` (Change 7), so the push never touches it and never races the flush.

**`cleanup`** — discard ops without SSH (dry-run + exception):

```python
def cleanup(self) -> None:
    self._mcp_ops.clear()
    self._mcp_seen.clear()
    self._mcp_manifest_names = None
    self._cleanup_staging()
```

> `_cleanup_staging` (remote.py:65-67) unchanged; idempotent (`if self._staging_path.exists()`).

### Change 3 — `src/promptdeploy/deploy.py`: flush BEFORE save_manifest (convergence fix)

In `deploy()`, the per-target success branch (deploy.py:601-604) becomes:

```python
            if not dry_run:
                _flush_remote_mcp(target)
                save_manifest(new_manifest, target.manifest_path())
                target.finalize(verbose=verbose)
            else:
                target.cleanup()
```

Add a module-private duck-typed helper near the other `_`-helpers:

```python
def _flush_remote_mcp(target: Target) -> None:
    """Flush a RemoteTarget's accumulated remote-MCP ops before the manifest is saved.

    Duck-typed: only RemoteTarget (claude inner, remote_mcp=True) defines
    flush_remote_mcp; for every other target this is a no-op. Running this BEFORE
    save_manifest means a failed SSH merge (SSHError) leaves the manifest
    unchanged, so the next deploy re-detects the change and retries automatically
    (self-healing). The surrounding `except BaseException: target.cleanup();
    raise` then discards the queued ops and removes staging."""
    flush = getattr(target, "flush_remote_mcp", None)
    if flush is not None:
        flush()
```

> Why `getattr` not `isinstance(target, RemoteTarget)`: avoids importing `RemoteTarget` into deploy.py (it imports `create_target`/`Target` only) and keeps mypy-strict happy without widening the `Target` ABC for the *flush* method. (`remote_mcp_hash` IS added to the ABC because the hash layer needs to read it polymorphically with no import; flush is only ever called from the deploy loop, where duck-typing is the minimal change and is what tests target.)
>
> **Control-flow guarantee.** Success path order: `_flush_remote_mcp(target)` (SSH merge of `.claude.json`) → `save_manifest(...)` → `target.finalize(...)` (rsync push of staging + accumulator reset + staging cleanup). If `flush_remote_mcp` raises `SSHError`, `save_manifest` and `finalize` never run; the `except BaseException` clause calls `target.cleanup()` (clears `_mcp_ops`/`_mcp_seen`/`_mcp_manifest_names`, removes staging) and re-raises. The on-disk manifest is the *previous* run's, so the next `deploy()` recomputes the same `changed=True` and re-queues the merge. **Dry-run** takes `else: target.cleanup()` — `_flush_remote_mcp` is never reached, so dry-run does NO SSH merge and writes nothing (it still `ssh_pull`s in `prepare`, §8).
>
> **Independent-writes window** (review lifecycle/nit + correctness/nit): the MCP merge (SSH stdin) and the artifact/manifest sync (rsync) are two independent remote writes. On **flush-success then push-failure**, the remote `.claude.json` is already correctly merged but the remote manifest is stale (push never ran). The next run re-pulls the OLD remote manifest, recomputes `changed=True`, and re-flushes the **idempotent** op (a `set` re-bakes identical bytes; a `pop` is a no-op), then re-pushes — so it self-heals. No data loss, no double-effect.

### Change 4 — `src/promptdeploy/targets/__init__.py`: wire `remote_mcp`

At the wrap site:

```python
    if is_remote:
        assert target_config.host is not None  # narrowed by is_remote check
        return RemoteTarget(
            inner,
            target_config.host,
            target_config.path,
            staging_path,
            remote_mcp=isinstance(inner, ClaudeTarget),
        )
```

`manage_mcp=not is_remote` stays UNCHANGED so the inner ClaudeTarget never writes a staging `.claude.json`; the `remote_mcp` flag rides on the wrapper only. Under `--target-root` (`host=None`) there is no wrapper, so the preview is a local ClaudeTarget write (§6, §9.5).

### Change 5 — Hashing: re-introduce an env-folded remote-MCP hash branch

The expanded secret value is baked into the remote `.claude.json`, so a rotated secret must change the hash. **Local-claude mcp hashing must NOT change.** Gate the env-fold on a target capability property.

**5a — `src/promptdeploy/targets/base.py`**: add a default capability property to the `Target` ABC:

```python
    @property
    def remote_mcp_hash(self) -> bool:
        """True when this target bakes deploy-time-expanded MCP secrets into a
        remote file, so its mcp manifest hash must fold current env values
        (mirroring _expand_env_for_hash for models). Default False: local
        targets ship ${VAR} verbatim, so their mcp hash stays source-bytes-only."""
        return False
```

**5b — `src/promptdeploy/targets/remote.py`**: override it (shown in Change 2).

**5c — `src/promptdeploy/deploy.py` `compute_item_hash`**: add an mcp branch BEFORE the generic else, gated on the new capability. Insert after the `models` branch, before the `skill`/generic branch:

```python
    if item.item_type == "mcp" and target.remote_mcp_hash:
        # Remote claude bakes deploy-time-expanded env/headers into the remote
        # .claude.json, so fold current env values into the hash (like models)
        # -- a rotated secret VALUE changes the hash and triggers a redeploy.
        # We fold over the RAW source metadata so enabled:/scope: flips are
        # still detected (they live in item.metadata), then mix in the SAME
        # fingerprint and the SAME sha256(f"{base}|{fingerprint}") shape as the
        # generic branch so the two compose and the local path is byte-identical.
        effective = _expand_env_for_hash(item.metadata or {})
        base = compute_file_hash(
            json.dumps(effective, sort_keys=True, default=str).encode()
        )
        fingerprint = target.content_fingerprint(item.item_type)  # "claude-mcp-entry-v2"
        digest = hashlib.sha256(f"{base}|{fingerprint}".encode()).hexdigest()
        return f"sha256:{digest}"
```

> **Composition & non-regression rationale.**
> - **Local claude (and droid/opencode):** `target.remote_mcp_hash` is `False` (ABC default), so this branch is skipped; mcp falls through to the unchanged generic branch — `sha256(compute_file_hash(item.content) | "claude-mcp-entry-v2")`. **Local mcp hashing is byte-identical to today** (test 48).
> - **Remote claude:** we hash `_expand_env_for_hash(item.metadata or {})` (the parsed YAML dict, incl. `enabled`, `scope`, `env`, `headers`, `command`, ...) instead of raw bytes. Folding env values means a rotated `${TOK}` changes the hash → redeploy. `enabled`/`scope` flips change `item.metadata` → hash changes too. We mix in the SAME fingerprint constant and the SAME `sha256(f"{base}|{fingerprint}")` shape, so it composes with `content_fingerprint(mcp)` rather than replacing it.
> - `item.metadata or {}` is a **live branch** for mcp (source.py sets `metadata=None` for non-dict YAML) and MUST be covered (test 49b — review tests/major coverage gate).
> - `status.get_status` shares `compute_item_hash` with `config`, so status sees the same env-folded hash for remote mcp and the same source-only hash for local mcp automatically.

No drift-block interaction: `would_deploy_bytes`/`read_deployed_bytes` still return None for mcp, so deploy.py:431-440 stays inert.

> **Env-stability caveat for `status` (review lifecycle/major) — LOCKED behavior + doc.** Because the remote-mcp hash folds `os.environ`, `compute_item_hash` is env-sensitive at *every* call site, including `status`. `_expand_env_for_hash` leaves an unset `${VAR}` literal (no warning). Therefore: if a *deploy* ran with `TOK` set (baking the resolved value, hash folds the resolved value) and a later `status` runs WITHOUT `TOK` exported, the folded value is the literal `${TOK}`, the hashes differ, and `status` reports `changed` even though nothing changed. **Decision: accept this and document it loudly.** Rationale: (1) `deploy` auto-loads `.env` (so the secret is present for the operation that actually writes), and the env-folded hash is the *correct* signal for deploy — a rotated/absent secret genuinely should redeploy; (2) making `status` env-insensitive would desync it from `deploy`, breaking the parity the rest of the design relies on; (3) the failure mode of a spurious `changed` in `status` is read-only and self-corrects the moment the env is present. This is the SAME behavior `models` already has (its hash folds env too), so it introduces no new class of surprise. **This is locked by test 52b** (run `status` with the secret UNSET after a deploy with it SET; assert `changed`) so the behavior is intentional, not accidental. Documented in §6 (mcp/schema.md + CLAUDE.md): "running `status`/`deploy` without the referenced secret exported reports remote MCP as `changed`; export the secret (or rely on `.env` auto-load, which `status` does NOT perform) for an accurate read."

### Change 6 — `src/promptdeploy/cli.py`: catch `SSHError`

Add `from .ssh import SSHError` to the lazy imports (cli.py:177-180 block) and add `SSHError` to the caught tuple (cli.py:191-197):

```python
    from .envsubst import EnvVarError
    from .frontmatter import FrontmatterError
    from .poet import PoetError
    from .ssh import SSHError
    from .targets.claude import JsonConfigError

    try:
        actions = deploy(...)
    except (
        FilterError,
        EnvVarError,
        FrontmatterError,
        JsonConfigError,
        PoetError,
        SSHError,
    ) as exc:
        out.error(str(exc))
        sys.exit(1)
```

This also cleans up the pre-existing remote rsync path (`ssh_pull`/`ssh_push` raised `SSHError` uncaught). The common new failure — missing `python3` (exit 127) or the merge `_fail` diagnostic — now routes through `ssh_stdin` → `SSHError` → clean CLI error.

### Change 7 — `rsync_includes` audit (verification, no code change expected)

`.claude.json` must NEVER be rsynced. `RemoteTarget.rsync_includes()` delegates to `ClaudeTarget.rsync_includes()`, whose allowlist is `agents/`, `agents/**`, `commands/`, `commands/**`, `skills/`, `skills/**`, `settings.json`, `MANIFEST_FILENAME` — **verified `.claude.json` absent**. Expected outcome: no change. Pin with a regression test (test 54) so a future allowlist edit cannot silently start rsyncing it.

### Change 8 — Docs (see §6).

---

## 3. `item_exists` truth table (mcp, remote_mcp=True), manifest-backed

| Situation | manifest has entry? | `_mcp_seen` at item_exists call | `item_exists` | `is_update` | `changed` | branch | merge op queued? |
|---|---|---|---|---|---|---|---|
| New server, fresh remote (no manifest) | no | no | False | False | True | `not exists` → **create** | yes (set) |
| New server, manifest exists w/o it | no | no | False | False | True | **create** | yes (set) |
| Managed, unchanged (source + env stable) | yes | no | True | True | False | **skip** | **no** |
| Managed, source changed (enabled/cmd/url) | yes | no | True | True | True | **update** | yes (set or pop) |
| Managed, **secret value rotated** (env-folded hash differs) | yes | no | True | True | **True** | **update** | yes (set, re-bakes new secret) |
| Managed, **scope flipped** (in `item.metadata`) | yes | no | True | True | **True** | **update** | yes (set) |
| Source deleted (stale) | yes (old) | n/a | n/a | n/a | n/a | stale-remove loop | yes (pop) |

> **Pre-existing detection is structurally unreachable for remote mcp** (review correctness/minor). `item_exists` and the loop's `is_update` both read `manifest.items["mcp_servers"]` (the same staging file `_remote_mcp_manifest_names` parses), and during the deploy loop `_mcp_seen` is always empty for a name when its `item_exists` runs (the op is queued only later, inside `_deploy_item`). Therefore `item_exists(True) ⇔ is_update(True)` for remote mcp, so the `not is_update and exists_on_target` pre-existing branch (deploy.py:450-481) can never fire for it. (Even if it did, `would_deploy_bytes("mcp")` is None → `_disk_matches_source` False → it would emit `pre-existing`, which is why we lock the unreachability with test 49c.) The manifest read happens once (memoized), after `prepare()`'s `ssh_pull` populated staging; `load_manifest` is missing-file-safe.

---

## 4. Secret handling, JSON building, atomicity

- **Deploy-time strict expansion**: `_expand_entry_secrets` runs `expand_env_vars_strict` over `entry["env"]` and `entry["headers"]` string values (mirroring opencode.py:350-369). A missing var → `EnvVarError` → cli.py exit 1, **before any op is queued**. Never ship an empty secret.
- **`${VAR}` only in env/headers** (review correctness/nit): the MCP schema supports `${VAR}` only in `env`/`headers`. A `${VAR}` in `command`/`args`/`url` is out of contract; it passes through verbatim and — unlike runtime-only local fields — is NOT expanded on the remote either. Documented (§6).
- **Entry build**: `ClaudeTarget._claude_mcp_entry(config)` first (strips metadata incl. `scope`, injects `type="http"` for url servers), then expand. The expanded entry is a plain JSON-serializable dict.
- **Stdin-only transport**: ops list → `build_claude_merge_script` embeds `base64(json(ops, sort_keys=True))` inside the program text → `ssh_stdin` pipes the program to `ssh <host> python3 -` via `input=`. **Remote argv is exactly `["ssh", *_SSH_OPTS, host, "python3", "-"]`** — the secret is invisible in `ps`/`/proc/<pid>/cmdline`. No secret is ever an argv token, a shell word, or a remote env var.
- **No injection / no break-out** (review security/nit, positive): ops are Python dicts serialized via `json.dumps` then base64; a malicious/typo'd env value (quotes, braces, backslashes, JSON metacharacters) is a string *value* that `json.dumps` escapes and base64 opaquifies. On the remote it is decoded back to structured data — no `eval`, no shell. Server names reach `servers[name]` but originate from trusted config (deploy.yaml/filenames) and are JSON-encoded. `string.Template.substitute` substitutes once and does not re-scan, so a `$`/brace in the repr'd path cannot inject a placeholder. Test 2c round-trips an adversarial env value to lock no-break-out.
- **No-secret-on-error (structural)**: the merge program's body is wrapped in an outer `try/except BaseException -> _fail(...)`; `_fail` emits only fixed strings. `ssh_stdin` interpolates only remote `stderr` (which carries no payload) + host, and NEVER the `script` text. No caller-captured log therefore receives the secret. Home Manager activation is `--local-only` and cannot exercise this remote-SSH path. Locked by tests 2/53b.
- **Atomicity + perms on the remote**: load existing `.claude.json` (or `{}` if missing/blank), surgical set/pop on `mcpServers[name]`, write via `tempfile.mkstemp(dir=same)` + `os.replace` + unlink-on-error. `mkstemp` yields mode `0600`; `os.replace` adopts the temp file's mode, so the written file is **0600, never widened** by the remote umask or a prior 0644 file. A large app-owned file is never corrupted; a parse/decode failure on a real non-empty file aborts without writing. (We do not add explicit `os.chmod`, matching local `_save_json`; see §9.5(ii).)
- **Remote `~` expansion**: `target_path = f"{remote_path}/.claude.json"`; `os.path.expanduser` inside the program expands `~`. No shell quoting (it is a Python string literal inside the piped program).
- **Single SSH connection per flush**: all queued ops go in one merge program → one `ssh_stdin` call → one connection. Total per deploy: pull in `prepare`, one merge flush, one rsync push = up to 3 connections (§9.7).

---

## 5. Change-detection & `content_fingerprint` interaction

- **Remote mcp**: env-folded branch (Change 5c) — `sha256(json(_expand_env_for_hash(item.metadata or {})) | "claude-mcp-entry-v2")`. Folds env values (rotated secret → redeploy), includes `enabled`/`scope` (flips → redeploy), composes with the existing fingerprint constant.
- **Local claude mcp** (and droid/opencode mcp): generic branch UNCHANGED — `sha256(compute_file_hash(item.content) | "claude-mcp-entry-v2")`. **Byte-identical to today.** A rotated secret does NOT trigger a local redeploy (correct — local ships `${VAR}` verbatim; runtime picks up the new value).
- The asymmetry is intentional and gated by `target.remote_mcp_hash` (False for everything except `remote_mcp=True` `RemoteTarget`). No concrete-type checks in the hash layer.
- `status.get_status` shares `compute_item_hash` with `config`, so status reports `new`/`changed`/`current`/`pending_removal` for remote mcp with env-folded semantics. `status` never flushes (no flush call) but DOES `ssh_pull` in `prepare`. See the §3 Change-5 env-stability caveat for the documented `status`-without-secret behavior.

---

## 6. Docs to update (only these two files; do NOT write any new .md)

1. **`mcp/schema.md`** — rewrite the "Local profiles only / remote claude targets skip MCP (`manage_mcp=False`)" paragraph:
   - Remote claude targets now deploy MCP by a **direct surgical merge into `<remote_path>/.claude.json` over SSH** (not the `claude` CLI, not rsync). For each enabled server, `mcpServers[name]=entry` is set; for `enabled:false` or a removed server the key is popped; **all other app-owned keys are preserved**; the write is atomic (mkstemp `0600` + `os.replace`) on the remote.
   - **Transport**: a small `python3` merge program is generated with the operations embedded as base64 and piped to `ssh <host> python3 -` on **stdin**. The remote argv is just `python3 -`, so secrets never appear in the remote process table or logs. The program's entire body is wrapped so any error prints only a fixed diagnostic (never the payload/ops/values).
   - **Env-var policy — local vs remote DIFFER:**
     - *Local claude*: `env`/`headers` `${VAR}` ship **VERBATIM**; expand at runtime when Claude Code reads `.claude.json`. Hash is source-bytes only; rotating a secret does NOT trigger redeploy.
     - *Remote claude*: `env`/`headers` `${VAR}` are **strict-expanded at deploy time** (like OpenCode) and the resolved value is baked into the remote `.claude.json`. A missing variable raises `EnvVarError` → deploy exits 1 (never ships an empty secret). The hash folds current env values, so **rotating a referenced secret triggers a redeploy**, and **running `status`/`deploy` without the referenced secret exported reports the server as `changed`** (export it, or note that `deploy` auto-loads `.env` but `status` does not). Net exposure: in transit over the encrypted SSH channel and at rest in the remote `.claude.json` (mode `0600`).
   - **`${VAR}` is only supported in `env`/`headers`**; in `command`/`args`/`url` it is out of schema and is baked verbatim (and, on remote, never expanded).
   - **Requirement**: `python3` must be on the remote non-interactive SSH PATH (NixOS + Amazon Linux both ship it). If absent (exit 127) the deploy fails loudly with a clear hint.
   - **Caveats**: (a) deploy with remote `claude` sessions closed — a live session rewrites `.claude.json` wholesale and can drop the merge; (b) the mechanism relies on TOFU host-key trust (`StrictHostKeyChecking=accept-new`) — **pre-populate `known_hosts` out-of-band before first deploy** (now load-bearing, since real secrets transit the channel); (c) `.claude.json` is never rsynced (machine-specific, app-owned); (d) `--dry-run` performs no SSH merge and no write, but still does a read-only `ssh_pull` in `prepare`; (e) `--target-root` previews a remote-MCP target as a LOCAL `.claude.json` write with **verbatim `${VAR}`** (it does NOT reflect the remote deploy-time-expanded/baked form).
2. **`CLAUDE.md`** — three bullets:
   - claude.py one-liner (~line 38): remote claude now manages MCP via an **SSH stdin direct-merge into the remote `.claude.json`** (the `RemoteTarget` intercept), not via `.claude.json` rsync and not via the `claude` CLI.
   - MCP-deployment bullet (~line 52): replace "remote claude targets cannot manage MCP … MCP deploys to local profiles only" with the SSH-stdin direct-merge mechanism (base64-embedded ops piped to `python3 -`, atomic remote write at mode 0600, **flushed in the deploy loop BEFORE `save_manifest` so a failed flush self-heals on the next run**; dry-run/error never flush; `--target-root` previews as a LOCAL `.claude.json` write with verbatim `${VAR}`).
   - env-var policy bullet (~line 53): state that **local claude keeps verbatim `${VAR}`** while **remote claude strict-expands at deploy time** (baked into the remote file, transported only over SSH stdin so the secret is never an argv token), that the remote-MCP manifest hash folds env values (so a rotated secret redeploys and a `status`/`deploy` without the secret reports `changed`; local does not), and that `${VAR}` is only honored in `env`/`headers`.

---

## 7. FULL TEST LIST (100% line+branch; each name + assertion + branch covered)

Mocking strategy:
- **ssh.py unit tests** (`tests/test_ssh.py`): shared fixture monkeypatches `shutil.which` → `lambda tool: f"/usr/bin/{tool}"` so `_check_tools` passes; patch `promptdeploy.ssh.subprocess.run` returning `CompletedProcess(args=[], returncode=N, stderr=b"...", stdout=b"")`. For `ssh_stdin`, assert argv is exactly `["ssh", *_SSH_OPTS, host, "python3", "-"]`, that the script reached the child via `input=` (NOT in `args`), `capture_output=True`, NO `text=True`. For `build_claude_merge_script`, assert the returned text contains the base64 of the ops and does NOT contain the raw secret substring, and that it `compile()`s.
- **Remote-program behavioral tests** (`tests/test_ssh.py`): EXECUTE the rendered program against a real temp file via `subprocess.run([sys.executable, "-"], input=script.encode())`, asserting actual file effects (this converts the remote-only branches into hermetic local assertions — review tests/blocker). Localhost; no network.
- **RemoteTarget tests** (`tests/test_remote_target.py`): two fixtures — (a) existing `remote_target` (MagicMock inner, `remote_mcp=False`) for delegation; (b) NEW `remote_mcp_target`: `RemoteTarget(inner=ClaudeTarget(tid, staging, manage_mcp=False), host=..., remote_path=Path("~/.claude"), staging_path=staging, remote_mcp=True)` (real ClaudeTarget so `_claude_mcp_entry` works). Patch `promptdeploy.targets.remote.{ssh_stdin,ssh_pull,ssh_push,ssh_exists}` (imported INTO remote.py).
- **deploy/status integration** (`tests/test_deploy.py`, `tests/test_status.py`): remote `TargetConfig` (`host="user@remotehost"`, `PROMPTDEPLOY_HOST` set so `is_remote` True); patch `promptdeploy.targets.remote.{ssh_pull,ssh_push,ssh_stdin}`. Shared `_seed_remote_manifest(monkeypatch, *, names_to_hashes)` helper: monkeypatch `ssh_pull` to (a) `local_path.mkdir(parents=True, exist_ok=True)`, (b) `save_manifest(Manifest(items={"mcp_servers": {n: ManifestItem(source_hash=h)}}), local_path / MANIFEST_FILENAME)`. For "unchanged"/"rotated"/"scope" tests, compute the expected hash by building the SAME remote target and calling `compute_item_hash(item, target, config)` under the desired env BEFORE the deploy, then seed exactly that value.

### `tests/test_ssh.py` — renderer + behavioral exec + ssh_stdin
1. `test_build_merge_script_embeds_base64_ops` — ops with a `set` entry; assert the text contains `base64.b64decode("` with the base64 of `json.dumps(ops, sort_keys=True)` and contains `import json` / `os.replace`.
1b. `test_build_merge_script_compiles` — `compile(build_claude_merge_script(ops, "~/.claude/.claude.json"), "<remote>", "exec")` does not raise (catches brace/placeholder/syntax regressions in the template).
2. `test_build_merge_script_no_secret_in_text` — entry env value `"super-secret-token"`; assert that literal string is NOT in the script text (only its base64 form).
2b. `test_build_merge_script_target_path_repr` — `target_path="~/.claude/.claude.json"`; assert the text contains `os.path.expanduser('~/.claude/.claude.json')` (repr literal, no shell quoting).
2c. `test_build_merge_script_adversarial_value_roundtrips` — entry env value contains `"` `{` `}` `\` `${X}`; extract the base64 from the rendered text, `json.loads(base64.b64decode(...))`, assert the value round-trips byte-identical (locks no-break-out through json+base64).
3. `test_remote_program_set_preserves_siblings` — write a temp `.claude.json` with `{"oauth":{...},"mcpServers":{"old":{...}}}`; build a script with one `set` op for `new`; exec via `subprocess.run([sys.executable,"-"],input=script.encode())` with `TARGET_PATH_REPR` pointing at the temp file; assert rc 0, the file still has `oauth` and `mcpServers["old"]`, and now `mcpServers["new"]`.
3b. `test_remote_program_pop_to_empty_drops_key` — temp file with a single `mcpServers["only"]`; `pop` op for `only`; exec; assert rc 0 and `"mcpServers" not in data`.
3c. `test_remote_program_missing_file_creates` — target path does not exist; `set` op; exec; assert rc 0 and the file is created with `{"mcpServers":{name:entry}}`.
3d. `test_remote_program_blank_file_treated_empty` — temp file containing only whitespace; `set` op; exec; assert rc 0 and the file becomes `{"mcpServers":{...}}` (blank → `{}`).
3e. `test_remote_program_invalid_json_aborts_no_clobber` — temp file with `not json {`; `set` op; exec; assert rc != 0, stderr contains the fixed "not valid JSON" diagnostic and NO payload, and the file content is UNCHANGED.
3f. `test_remote_program_non_object_aborts` — temp file with `[1,2,3]`; `set` op; exec; assert rc != 0, fixed "not a JSON object" diagnostic, file unchanged.
3g. `test_remote_program_malformed_op_fails_no_secret` — hand-build a script whose base64 payload is `[{"action":"set"}]` (missing `name`) carrying a sentinel secret elsewhere; exec; assert rc != 0 and stderr is ONLY `promptdeploy remote MCP merge failed: unexpected error during merge` with NO payload/secret substring (locks the outer try/except — review security/major).
4. `test_ssh_stdin_success_pipes_script_via_input` — rc 0; assert `subprocess.run` called once with argv `["ssh", *_SSH_OPTS, host, "python3", "-"]`, kwarg `input == script.encode("utf-8")`, `capture_output=True`, NO `text=True`; returns None.
5. `test_ssh_stdin_connection_failure_255` — rc 255, stderr bytes; `SSHError match="SSH connection to .* failed"`.
6. `test_ssh_stdin_python3_missing_127` — rc 127, stderr `b"python3: command not found"`; `SSHError match="python3 not found on"`.
7. `test_ssh_stdin_remote_failure_other_nonzero` — rc 1, stderr `b"...could not write .claude.json atomically"`; `SSHError match="Remote MCP merge on .* failed \\(exit 1\\)"`.
8. `test_ssh_stdin_failure_no_stderr` — rc 1, `stderr=b""`; `SSHError` raised (covers the `if result.stderr else ""` False branch).
9. `test_ssh_stdin_secret_absent_from_argv_present_in_stdin` — build a script whose payload carries sentinel `"SECRET-SENTINEL-XYZ"`; call `ssh_stdin`; assert the sentinel plaintext is NOT in `repr(mock_run.call_args.args)` nor in any argv element, AND its base64 IS in `mock_run.call_args.kwargs["input"]` (the one end-to-end proof of the headline security property — review tests/major).
9b. `test_ssh_stdin_tools_missing_raises_before_subprocess` — `shutil.which` → None for ssh; `pytest.raises(SSHError)`; assert `subprocess.run` NOT called (covers the unguarded `_check_tools` early exit for the new function).
9c. `test_ssh_stdin_error_message_never_contains_script` — drive each non-zero branch (255/127/other) with a remote stderr that does NOT contain the payload; assert the script's base64 substring is absent from `str(SSHError)` (pins the no-script-in-message invariant against future regressions — review security/minor).

### `tests/test_remote_target.py` — overrides + accumulate/flush
10. `test_should_skip_mcp_false_when_remote_mcp` — `remote_mcp_target.should_skip("mcp","x") is False`; inner not consulted.
11. `test_should_skip_mcp_delegates_when_not_remote_mcp` — default `remote_target` delegates.
12. `test_should_skip_nonmcp_delegates` — `should_skip("agent","x")` delegates even when remote_mcp.
13. `test_deploy_mcp_server_accumulates_set_op` — stdio config; assert `_mcp_ops == [{"action":"set","name":"srv","entry":{...}}]`, `"srv" in _mcp_seen`, `(staging/".claude.json").exists() is False`.
14. `test_deploy_mcp_server_url_gets_type_http_in_entry` — url config; queued op's `entry["type"] == "http"`.
15. `test_deploy_mcp_server_env_headers_strict_expanded` — env `{"K":"${TOK}"}`, header `{"Authorization":"Bearer ${TOK}"}`, `monkeypatch.setenv("TOK","secret123")`; queued op's `entry["env"]["K"] == "secret123"` and `entry["headers"]["Authorization"] == "Bearer secret123"`.
16. `test_deploy_mcp_server_missing_env_raises` — env `{"K":"${ABSENT}"}` unset; `pytest.raises(EnvVarError)`; assert `_mcp_ops` empty and `_mcp_seen` empty after (no op queued on failure).
17. `test_deploy_mcp_server_disabled_queues_pop` — `enabled:false`; `_mcp_ops == [{"action":"pop","name":"srv","entry":None}]`, no set.
18. `test_deploy_mcp_server_non_string_env_value_passes_through` — env `{"PORT": 8080}`; `entry["env"]["PORT"] == 8080` (covers `if isinstance(v,str)` False branch).
19. `test_deploy_mcp_server_no_env_headers` — stdio config with neither; op entry has no `env`/`headers`, no error (covers `isinstance(block, dict)` False for both keys).
20. `test_deploy_mcp_server_delegates_when_not_remote_mcp` — default `remote_target`; `inner.deploy_mcp_server` called, `_mcp_ops` empty.
21. `test_remove_mcp_server_accumulates_pop` — remote_mcp; `_mcp_ops == [{"action":"pop",...}]`, `"srv" in _mcp_seen`.
22. `test_remove_mcp_server_delegates_when_not_remote_mcp` — `inner.remove_mcp_server` called.
23. `test_item_exists_mcp_seen_returns_true` — after `deploy_mcp_server`, `item_exists("mcp","srv") is True`.
24. `test_item_exists_mcp_reads_manifest` — write staging manifest with `mcp_servers:{srv:...}`; `item_exists("mcp","srv") is True` without prior accumulation.
25. `test_item_exists_mcp_absent_manifest_false` — no manifest / name absent → False (missing-file path, no `.exists()` guard).
26. `test_item_exists_mcp_manifest_memoized` — patch `load_manifest` with a counter; two `item_exists` calls for different absent names → `load_manifest` called ONCE.
27. `test_item_exists_nonmcp_delegates` — `item_exists("agent","x")` delegates.
28. `test_item_exists_mcp_delegates_when_not_remote_mcp` — default target delegates.
29. `test_remote_mcp_hash_property_true` — `remote_mcp_target.remote_mcp_hash is True`.
30. `test_remote_mcp_hash_property_false_when_not_remote_mcp` — default `remote_target.remote_mcp_hash is False`.
30b. `test_base_target_remote_mcp_hash_default_false` — a bare `ClaudeTarget`'s `remote_mcp_hash is False` (covers the ABC default property body).
31. `test_flush_remote_mcp_calls_ssh_stdin_with_built_script` — queue one set op; `flush_remote_mcp()`; assert `ssh_stdin` called once with `host` and `script == build_claude_merge_script(self._mcp_ops, "~/.claude/.claude.json")`.
32. `test_flush_remote_mcp_noop_when_no_ops` — `remote_mcp=True`, empty `_mcp_ops`; `ssh_stdin` NOT called (covers `not self._mcp_ops`).
33. `test_flush_remote_mcp_noop_when_not_remote_mcp` — default target with a fabricated op list; `ssh_stdin` NOT called (covers `not self._remote_mcp`).
34. `test_flush_remote_mcp_propagates_ssherror` — `ssh_stdin` raises `SSHError`; propagates; `_mcp_ops` NOT cleared (flush does not clear).
35. `test_finalize_pushes_and_clears_ops` — queue ops, `finalize()`; `ssh_push` called once, then `_mcp_ops`/`_mcp_seen` empty, `_mcp_manifest_names` None, staging removed; `ssh_stdin` NOT called.
36. `test_finalize_push_failure_propagates_no_clear` — `ssh_push` raises `SSHError`; propagates; `_mcp_ops` NOT cleared, staging still exists.
37. `test_cleanup_discards_ops_no_ssh` — queue ops, `cleanup()`; `ssh_stdin`/`ssh_push` NOT called, `_mcp_ops`/`_mcp_seen` empty, `_mcp_manifest_names` None, staging removed.

### `tests/test_deploy.py` — orchestration + hash + convergence
38. `test_remote_mcp_deploy_flushes_before_save_manifest` — remote claude target, one mcp source, empty staging; patch `ssh_stdin` and `save_manifest` to append markers to a shared list; `deploy(config)`; assert one `create` action, `ssh_stdin` called BEFORE `save_manifest` with a script encoding a single `set` op; staging `.claude.json` never written; `item_exists` returned False on the missing manifest without raising.
39. `test_remote_mcp_dry_run_no_ssh_stdin_no_write` — `dry_run=True`; `create` action present, `ssh_stdin` NOT called, `save_manifest` NOT called, AND `ssh_pull` IS called.
40. `test_remote_mcp_unchanged_skips_no_ssh_stdin` — seed staging manifest with the server's CURRENT env-folded computed hash (helper + compute-before-deploy under the same env); `deploy`; assert `skip` action, `ssh_stdin` NOT called, AND `item_exists("mcp", name)` is True against the same staging file (locks loop-vs-target manifest agreement — review tests/minor).
41. `test_remote_mcp_rotated_secret_redeploys` — seed from a `${TOK}` source hash computed with `TOK=v1`; `monkeypatch.setenv("TOK","v2")`; `deploy`; assert `update` action AND `ssh_stdin` flushes a `set` op whose entry env carries `v2`. **Central new behavior.**
42. `test_remote_mcp_enabled_flip_triggers_pop` — seed from `enabled:true` hash, deploy with source flipped to `enabled:false`; assert `update` action AND a single `pop` op flushed, no `set`.
43. `test_remote_mcp_scope_flip_redeploys` — seed from `scope:user` hash, deploy with `scope:local`; assert env-folded hash differs (`update`) and a `set` op flushed.
44. `test_remote_mcp_stale_removal_flushes_pop` — seed manifest with a server whose source is gone; `deploy`; assert `remove` action and a `pop` op flushed for that name.
45. `test_remote_mcp_stale_removal_dry_run_no_ssh_stdin` — same setup, `dry_run=True`; `remove` action present, `ssh_stdin` NOT called.
46. `test_remote_mcp_flush_failure_leaves_manifest_unchanged` — `ssh_stdin` raises `SSHError`; `deploy` raises `SSHError`; assert `save_manifest` NOT called and `target.cleanup()` ran (staging removed). Then run `deploy` AGAIN with `ssh_stdin` succeeding and assert it re-flushes the same op (self-healing retry).
46b. `test_remote_mcp_push_failure_after_flush_self_heals` — flush succeeds (records `.claude.json` merged), then `ssh_push` raises `SSHError`; `deploy` raises; on re-run (push succeeds) assert the idempotent op is re-flushed harmlessly (locks the flush-success/push-failure window — review correctness/nit).
47. `test_remote_mcp_missing_secret_aborts_deploy` — source env `${ABSENT}` unset; `pytest.raises(EnvVarError)`; `ssh_stdin` NOT called, staging cleaned.
48. `test_local_claude_mcp_hash_unchanged` — a LOCAL claude target (`remote_mcp_hash` False); assert `compute_item_hash(mcp_item, local_target, config)` equals `f"sha256:{sha256(f'{compute_file_hash(item.content)}|claude-mcp-entry-v2')}"` and that a rotated secret does NOT change the local hash.
49. `test_flush_remote_mcp_helper_noop_for_non_remote_target` — call `_flush_remote_mcp(local_claude_target)` directly; no error (covers `flush is None`).
49b. `test_remote_mcp_hash_metadata_none` — remote_mcp target + mcp `SourceItem` with `metadata=None` (content that `yaml.safe_load` returns a scalar/None for); assert `compute_item_hash` returns a stable `sha256:...` without raising (covers `item.metadata or {}` falsy branch — review tests/major, coverage gate).
49c. `test_remote_mcp_queued_but_unmanaged_not_pre_existing` — directly drive the classification: a server present in `_mcp_seen` (via `deploy_mcp_server`) but absent from the prior manifest; assert the resulting deploy action for it is `create`/`skip`, NEVER `pre-existing` (locks the structurally-unreachable pre-existing path — review correctness/minor, tests/minor).

### `tests/test_status.py` — parity
50. `test_status_reports_remote_mcp_new` — remote claude target, mcp source, empty staging manifest; `get_status`; assert `StatusEntry(item_type="mcp", state="new")`.
51. `test_status_remote_mcp_never_ssh_stdins` — patch `ssh_stdin`; `get_status`; `ssh_stdin` NOT called; `ssh_pull` IS called.
52. `test_status_matches_deploy_remote_mcp` — lock-step: seed from current env-folded hash → status `current` AND deploy `skip`; rotate the secret → status `changed` AND deploy `update`; delete source → status `pending_removal` AND deploy `remove`. Additionally assert the target the status path built has `remote_mcp_hash is True` (capture via a spy/monkeypatched `create_target`) so parity is via the same env-folded branch, not coincidental hash equality (review tests/minor).
52b. `test_status_remote_mcp_secret_unset_reports_changed` — deploy with `TOK` SET (seed manifest from that hash), then `get_status` with `TOK` UNSET; assert state `changed`. Locks the documented env-stability behavior (review lifecycle/major) as intentional.

### `tests/test_cli.py` — SSHError handling
53. `test_cli_ssherror_clean_exit` — patch `deploy` to raise `SSHError`; run the CLI path; assert `out.error` called and `SystemExit(1)` (not a raw traceback).

### `tests/test_remote_target.py` / `tests/test_targets.py` — guardrails
54. `test_rsync_includes_excludes_claude_json` — build a remote claude target; assert `".claude.json" not in (target.rsync_includes() or [])` (regression guard for Change 7 — review lifecycle/nit).
55. `test_target_root_previews_remote_mcp_as_local_verbatim` — `remap_targets_to_root` over a remote-claude `TargetConfig`, build via `create_target`; assert the result is a bare `ClaudeTarget` (not `RemoteTarget`), `remote_mcp_hash is False`, and `deploy_mcp_server` writes `${VAR}` VERBATIM into the preview `.claude.json` (no expansion, no `ssh_stdin`). Locks the documented `--target-root` preview semantics (review lifecycle/minor, tests/minor).

> **Existing tests to keep GREEN (rewrite, not delete):** the current `tests/test_remote_target.py` delegation tests keep using the default `remote_target` (`remote_mcp=False`) so they still cover the delegate branches (tests 11/12/20/22/28/36 subsume/extend them). Existing `tests/test_claude_target.py` MCP tests (verbatim env, url→http, `manage_mcp` skip, fingerprint non-None) stay GREEN unchanged — they exercise the LOCAL ClaudeTarget, untouched. Test 48 additionally pins the local-mcp hash formula.

---

## 8. mypy / coverage / lifecycle notes

- **Lifecycle order (success path, non-dry-run):** `prepare` (ssh_pull → staging) → per-item loop (intercept queues ops, never writes staging `.claude.json`) → stale loop (queues pops) → **`_flush_remote_mcp(target)`** (one `ssh_stdin` merge of remote `.claude.json`) → `save_manifest` → `finalize` (rsync push of staging `.md`/`settings.json`/manifest + accumulator reset + staging cleanup). The push never touches `.claude.json` (Change 7). MCP merge (stdin) and artifact/manifest sync (rsync) are **independent** remote writes; on flush-success/push-failure the remote `.claude.json` is correct but the remote manifest is stale, and the next run re-applies the idempotent merge and re-pushes.
- **Dry-run path:** loop + stale loop populate `_mcp_ops`, then `else: target.cleanup()` discards them with NO `ssh_stdin` and NO `save_manifest`. `ssh_pull` still ran in `prepare` (asserted, tests 39/45/51) — dry-run is read-only over SSH, not fully offline; docs use this precise wording.
- **Error path:** any exception in the loop (incl. `EnvVarError` from `_expand_entry_secrets`, `SSHError` from flush) hits `except BaseException: target.cleanup(); raise`. `cleanup` discards ops and removes staging; the on-disk manifest is the prior run's; `cli.py` catches `EnvVarError`/`SSHError` → `out.error` + exit 1.
- **Typing:** `build_claude_merge_script(ops: Sequence[dict[str, Any]], target_path: str) -> str`; `ssh_stdin(host: str, script: str) -> None`. RemoteTarget new attrs: `_remote_mcp: bool`, `_mcp_ops: list[dict[str, Any]]`, `_mcp_seen: set[str]`, `_mcp_manifest_names: set[str] | None`. `remote_mcp_hash` property `-> bool` on `Target` (default False) and `RemoteTarget`. `_flush_remote_mcp(target: Target) -> None` uses `getattr(..., None)`; mypy sees `Any | None`, the `if flush is not None: flush()` is fine, annotate `-> None`. `from .claude import ClaudeTarget` in remote.py: no cycle. Function-local imports for `EnvVarError`/`load_manifest`/`expand_env_vars_strict`. `import json` and `import base64` and `from string import Template` and `from typing import Any` added to ssh.py.
- **Coverage map:**
  - ssh.py runner: tests 4-8 cover all `ssh_stdin` returncode branches (0/255/127/other) + the stderr-empty False branch; 9b covers the `_check_tools` early raise for `ssh_stdin`; 9/9c lock the security invariants.
  - ssh.py renderer (Python-measured): tests 1/1b/2/2b/2c cover `build_claude_merge_script` (base64 embed, compile, no-secret, repr path, adversarial roundtrip). The embedded *program* runs on the remote and is normally not Python-coverage-measured — but tests 3/3b/3c/3d/3e/3f/3g EXECUTE it via `subprocess.run([sys.executable,"-"],...)`, giving real behavioral assertions for surgical-set, pop-to-empty, missing/blank/invalid/non-object files, and the outer try/except no-secret guarantee (these run in a child interpreter, so they validate behavior without counting toward the parent's coverage measurement; they are the review-mandated behavioral tests, not a coverage device).
  - RemoteTarget: should_skip (2 branches), deploy_mcp_server (remote/not, enabled/disabled, env-present/absent, header-present/absent, string/non-string value, missing-var-raise), remove (2 branches), item_exists (seen/manifest-hit/manifest-miss/memoized/nonmcp/not-remote), `remote_mcp_hash` (True/False + ABC default 30b), flush (ops/no-ops/not-remote/error), finalize (push-ok/push-fail), cleanup.
  - deploy: hash branch remote_mcp_hash True (40/41/42/43) and `item.metadata or {}` falsy (49b); gate False falling to generic (48); duck-typed helper present (38) and `flush is None` (49); flush-before-manifest convergence (46) + push-failure window (46b); dry-run (39/45); EnvVarError abort (47); pre-existing unreachable (49c).
  - `_expand_entry_secrets`: env-dict/headers-dict present (15), absent (19), non-string value (18), missing var (16).
  - status: new (50), no-flush (51), parity + same-branch (52), env-unset (52b).
  - cli: SSHError (53). Guardrails: rsync allowlist (54), `--target-root` preview (55).
  - No defensive `or {}` beyond the existing `item.metadata or {}` (covered by 49b).

---

## File-by-file summary

- `src/promptdeploy/ssh.py`: add `import base64`, `import json`, `from string import Template`, `from typing import Any`; add `_REMOTE_MERGE_TEMPLATE` (string.Template, outer try/except, UnicodeDecodeError handling, mkstemp 0600 + os.replace), `build_claude_merge_script(ops, target_path) -> str`, `ssh_stdin(host, script) -> None`. No `claude mcp` builders, no `_render_remote_script`, no `config_dir`.
- `src/promptdeploy/targets/remote.py`: imports (`build_claude_merge_script`, `ssh_stdin`, `ClaudeTarget`); ctor `remote_mcp` kwarg + `_mcp_ops`/`_mcp_seen`/`_mcp_manifest_names`; override `should_skip`, `deploy_mcp_server`, `remove_mcp_server`, `item_exists`; add `_expand_entry_secrets`, `_remote_mcp_manifest_names`, `flush_remote_mcp`, `remote_mcp_hash` property; `finalize` push-only + accumulator reset; `cleanup` resets accumulators.
- `src/promptdeploy/targets/base.py`: add default `remote_mcp_hash` property (`-> bool`, returns False).
- `src/promptdeploy/targets/__init__.py`: pass `remote_mcp=isinstance(inner, ClaudeTarget)` to `RemoteTarget`.
- `src/promptdeploy/deploy.py`: add `_flush_remote_mcp(target) -> None`; call it before `save_manifest` in the non-dry-run branch; add the env-folded mcp branch to `compute_item_hash` gated on `target.remote_mcp_hash`.
- `src/promptdeploy/cli.py`: import `SSHError`, add to the caught tuple.
- `mcp/schema.md`, `CLAUDE.md`: doc updates (§6).
- Tests: `tests/test_ssh.py` (renderer + behavioral exec + `ssh_stdin`), `tests/test_remote_target.py` (rewrite delegation + add `remote_mcp_target` fixture/intercept tests + rsync/target-root guards), `tests/test_deploy.py` (orchestration + hash + convergence + windows), `tests/test_status.py` (parity + env-unset), `tests/test_cli.py` (SSHError).

---

## Resolved review findings

What changed from the revised draft in response to the adversarial review, and why:

- **[security/major] Remote merge program had unguarded paths (merge loop, `expanduser`, `UnicodeDecodeError`, non-OSError in the write block) that could emit a raw traceback (with the base64-decoded secret in locals) into `SSHError` or any caller-captured log.** FIXED structurally: the entire program body after `_fail`'s definition is wrapped in `try: ... except SystemExit: raise; except BaseException: _fail("unexpected error during merge")`, and the file read now catches `UnicodeDecodeError` explicitly. The no-secret-in-stderr guarantee no longer depends on the remote interpreter's default traceback formatter omitting locals. New behavioral test 3g asserts a malformed op (missing `name`) exits with only the fixed diagnostic and no payload substring.
- **[security/minor] No structural bar against a future `verbose`/echo path leaking the script; no test pinning the no-script-in-message property.** FIXED: an explicit "never interpolate `script`" security-invariant comment in `ssh_stdin`, and test 9c drives every non-zero branch and asserts the script's base64 substring is absent from `str(SSHError)`.
- **[security/minor] Remote `.claude.json` perms left to "whatever the umask yields" while now holding real plaintext secrets.** RESOLVED with a precise statement (not a vague deferral): `tempfile.mkstemp` creates the temp file at mode `0600` and `os.replace` adopts the temp file's mode, so the written file is `0600` and **never widened** by the umask or a prior `0644` file. We keep this (no explicit `os.chmod`), matching local `_save_json`; §6 documents the mode and §9.5(ii) records the rationale and the one-line `os.chmod` upgrade path if the human wants belt-and-suspenders.
- **[security/minor] TOFU (`accept-new`) is now load-bearing for confidentiality but enforced only by docs.** ELEVATED: §9.9 is now a required human decision before merge (keep `accept-new` + pre-seed `known_hosts`, vs. switch remote-MCP hosts to `StrictHostKeyChecking=yes`, vs. emit a one-time deploy-time warning when the host is absent from `known_hosts` and `remote_mcp` is active). Documented as a load-bearing caveat in §6.
- **[security/nit] No injection/break-out vector.** Confirmed; locked further with test 2c (adversarial value round-trips through `json.dumps`→base64→`json.loads`).
- **[correctness/nit] Surgical/atomic/load-or-empty/pop/idempotency.** Confirmed positive; now also behaviorally tested (tests 3–3f), not only asserted on queued ops.
- **[correctness/nit] Env-folded hash composes and doesn't alter local hashing.** Confirmed; locked by tests 48 (local formula identity) and 41/43 (remote env/scope fold).
- **[correctness/nit] Flush-before-save converges.** Confirmed; the flush-success/push-failure second-order window is now called out in §2-Change-3 and §8 and tested (46b).
- **[correctness/minor] Pre-existing detection is unreachable for remote mcp.** ADDRESSED: §3 now states it explicitly (item_exists and is_update read the same staging manifest; `_mcp_seen` is empty when item_exists runs in the loop), `_remote_mcp_manifest_names` documents it reads `target.manifest_path()`, and test 49c locks that a queued-but-unmanaged server never yields `pre-existing`.
- **[correctness/minor] `_mcp_seen` is effectively dead on the deploy path.** ADDRESSED: kept (cheap, harmless, simplifies the `item_exists` test contract) but its comment is corrected to state it is NOT consulted during normal loop ordering and is not load-bearing for correctness.
- **[correctness/nit] url/args not expanded.** Documented in `_expand_entry_secrets`'s docstring and §4/§6: `${VAR}` is honored only in `env`/`headers` per schema; elsewhere it is baked verbatim and not expanded on the remote.
- **[correctness/minor] `str.format` brace-doubling is fragile.** FIXED by switching the template to `string.Template` (`$PAYLOAD_B64`/`$TARGET_PATH_REPR`), eliminating brace-doubling entirely, plus test 1b (`compile()` the rendered text) to catch any future placeholder/syntax regression.
- **[correctness/nit] finalize "current behavior" framing.** §2-Change-3 and §8 now state the two independent remote writes and the self-healing window.
- **[correctness/nit] Embedded-program branches unmeasured.** ADDRESSED: tests 3–3f execute the rendered program against real temp files (child interpreter), giving real assertions for surgical-set, pop-to-empty, missing/blank/invalid/non-object, without affecting the parent coverage gate.
- **[lifecycle/major] Status env-folded hash flaps when run without secrets.** RESOLVED with a LOCKED, documented decision: accept the env-sensitivity (it matches `models`, keeps status↔deploy parity, and is read-only/self-correcting), document it in §6 and the Change-5 caveat, and pin it with test 52b (status with secret unset → `changed`). `deploy` auto-loads `.env`; `status` does not, and the docs say so.
- **[lifecycle/minor] Spec referenced a non-existent `_is_item_selected`.** CORRECTED to `item_selected` throughout (§1, §9.10). Parity claim corrected: `status` and `deploy` share `item_selected` + `compute_item_hash`; `list` is manifest-driven and independent (§9.10).
- **[lifecycle/nit] Dry-run still ssh_pulls in prepare.** Stated precisely ("no SSH merge, no write; the read-only ssh_pull in prepare still runs") and propagated into the §6 doc wording; tests 39/45/51 assert it.
- **[lifecycle/nit] Flush-before-save in-memory new_manifest.** Confirmed discarded on the failure path; §2-Change-3 spells out that the on-disk manifest is the prior run's and never written on flush failure.
- **[lifecycle/nit] finalize revert + `.claude.json` excluded from rsync.** Confirmed; test 54 guards the allowlist.
- **[lifecycle/minor] `--target-root` previews local-verbatim semantics.** ELEVATED: §9.5 keeps option (a) accept + prominent doc note, but adds test 55 to lock the divergence and §6 caveat (e) to warn the user the preview does NOT reflect the remote-expanded/baked form. (Optional one-line stderr notice on remap of a host-bearing claude target is offered as a sub-decision in §9.5.)
- **[lifecycle/nit] cleanup avoids SSH/writes in dry-run + exception.** Confirmed; tests 37/39/45/47.
- **[tests/blocker] Merge program's core logic covered by nothing (pure mock-testing).** RESOLVED: tests 3–3f execute the rendered program against real temp files asserting actual file effects (sibling preservation, pop-to-empty, create-on-missing, blank→empty, invalid-JSON no-clobber, non-object abort); test 3g asserts the no-secret error path.
- **[tests/major] `item.metadata or {}` falsy branch under the new remote-mcp hash branch breaks `fail_under=100`.** RESOLVED: test 49b feeds a remote-mcp item with `metadata=None`.
- **[tests/major] No direct assertion that a secret is absent from subprocess argv.** RESOLVED: test 9 builds an op carrying a sentinel secret, calls `ssh_stdin`, and asserts the plaintext is absent from argv while its base64 is present in `input=`.
- **[tests/minor] Status path under-asserts that it hits the remote_mcp_hash branch.** RESOLVED: test 52 additionally asserts the status-path target has `remote_mcp_hash is True`.
- **[tests/minor] No test pins loop-manifest vs target-manifest agreement.** RESOLVED: test 40 asserts `skip` AND `item_exists` True against the same staging file; `_remote_mcp_manifest_names` documents it reads `target.manifest_path()`.
- **[tests/minor] is_update/create vs pre-existing for remote mcp not directly asserted.** RESOLVED: tests 38 (create), 41/42/43 (update), 49c (never pre-existing).
- **[tests/nit] `ssh_stdin` `_check_tools` early-exit untested for the new function.** RESOLVED: test 9b.
- **[tests/minor] `--target-root` preview asserted nowhere.** RESOLVED: test 55.

---

## 9. Open questions for the human

These are genuine decisions, not implementation gaps. Items the review confirmed as positively verified or that this spec already decided (mechanism, deploy-time expansion, flush-before-manifest, env-folded hash, automatic-for-all-remote-claude) are NOT relisted.

1. **`scope` is a silent no-op on the remote merge.** We write `mcpServers` in `<remote_path>/.claude.json` directly (always "user/local" scope) and `_claude_mcp_entry` strips `scope`. A source `scope: project` is silently ignored, not rejected. There is no CLI to reject it against. Confirm silent-ignore is acceptable, or do you want a `promptdeploy validate` warning/rejection for `scope: project` on remote claude targets?

2. **`python3` hard dependency on the remote non-interactive PATH.** NixOS + Amazon Linux ship it, but a login-shell-only PATH could hide it; missing → exit 127 → `SSHError` with a clear hint (no silent skip). Confirm relying on `python3` is fine, or should the remote command fall back to `python3 || python` (I can wire that into the `ssh_stdin` argv / a small shell wrapper) for hosts where only `python` exists?

3. **Net at-rest secret exposure (re-confirm; reverses the prior decision).** Secrets now live (a) in transit over the encrypted SSH channel and (b) at rest in the remote `<remote_path>/.claude.json` at mode `0600` (mkstemp-derived, not widened — see §4). (i) Confirm at-rest plaintext in the remote `.claude.json` is acceptable. (ii) The local analogy is weaker now (local keeps `${VAR}`, remote bakes the secret): do you want the remote write to add an explicit `os.chmod(path, 0o600)` as belt-and-suspenders, or is the mkstemp-derived `0600` sufficient (my default — matches local, umask-independent)?

4. **Host-key TOFU is now load-bearing for confidentiality.** With verbatim refs a first-contact MITM saw only `${VAR}`; now a spoofed first connection (no `known_hosts` entry yet) would receive the real secret. `_SSH_OPTS` uses `StrictHostKeyChecking=accept-new`. Choose: (a) keep `accept-new` and pre-seed `known_hosts` out-of-band (documented; smallest); (b) switch remote-MCP claude hosts to `StrictHostKeyChecking=yes` (fails closed on first contact — but also affects the existing rsync pull/push for those hosts); (c) keep `accept-new` but emit a one-time deploy-time warning when the host is not yet in `known_hosts` and `remote_mcp` is active. I recommend (a)+(c). **This is a required decision before merge.**

5. **`--target-root` preview fidelity gap (accepted, confirm + optional stderr notice).** Under `--target-root`, `host=None` → no `RemoteTarget` wrapper → a bare local `ClaudeTarget` (`manage_mcp=True`, `remote_mcp_hash=False`) writes a real `.claude.json` with `${VAR}` VERBATIM and a source-bytes hash. The preview thus does NOT show deploy-time expansion, the SSH merge, or the env-folded hash. I propose: (a) accept + prominent doc note (§6 caveat e) + lock with test 55. Optionally, print a one-line stderr notice when `--target-root` remaps a host-bearing claude target ("remote MCP previewed with local-verbatim semantics"). Confirm (a), and say whether you want the stderr notice.

6. **`status` reports remote MCP as `changed` when run without the referenced secret exported** (env-folded hash; `status` does not auto-load `.env`). This spec locks "accept + document" (matches `models`, preserves parity, read-only/self-correcting; test 52b). Confirm you are fine with `promptdeploy status` showing remote MCP as `changed` in a secret-less shell, or do you want `status` to also auto-load `.env` (a small change to the status entry point) so the read is accurate?

7. **Up to 3 SSH connections per remote deploy** (pull in `prepare`, one merge flush before `save_manifest`, one rsync push in `finalize`). The merge is unavoidably separate from rsync. Confirm acceptable.

8. **Live remote `claude` session race.** A running remote `claude` rewrites `.claude.json` wholesale and can drop the merge (same as local). Documented (§6 caveat a); no code mitigation. Confirm "deploy with remote sessions closed" is the operational rule.

9. **Spec file disposition.** This file is `docs/superpowers/specs/2026-06-15-remote-mcp-ssh-stdin-direct-merge.md`. Confirm whether to delete the superseded `docs/superpowers/specs/2026-06-15-remote-mcp-deployment-design.md` or leave it in place with a one-line "superseded by" pointer at its top.
