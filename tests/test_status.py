"""Tests for promptdeploy deployment status."""

import hashlib
import json
from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import _TYPE_TO_CATEGORY
from promptdeploy.manifest import MANIFEST_FILENAME, compute_file_hash
from promptdeploy.status import (
    _CATEGORY_TO_TYPE,
    StatusEntry,
    get_status,
)
from promptdeploy.targets.claude import ClaudeTarget


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    """Create a minimal source tree."""
    root = tmp_path / "src"
    agents = root / "agents"
    agents.mkdir(parents=True)
    (agents / "alpha.md").write_bytes(b"---\nname: alpha\n---\nAlpha agent")

    commands = root / "commands"
    commands.mkdir()
    (commands / "deploy.md").write_bytes(b"---\nname: deploy\n---\nDeploy cmd")

    mcp = root / "mcp"
    mcp.mkdir()
    (mcp / "server.yaml").write_bytes(b"name: server\ncommand: npx\nargs:\n  - srv\n")

    return root


@pytest.fixture
def target_path(tmp_path: Path) -> Path:
    return tmp_path / "target"


@pytest.fixture
def config(source_root: Path, target_path: Path) -> Config:
    target_path.mkdir(parents=True, exist_ok=True)
    targets = {
        "local": TargetConfig(id="local", type="claude", path=target_path),
    }
    return Config(source_root=source_root, targets=targets, groups={})


class TestMappings:
    def test_type_to_category(self) -> None:
        assert _TYPE_TO_CATEGORY["agent"] == "agents"
        assert _TYPE_TO_CATEGORY["command"] == "commands"
        assert _TYPE_TO_CATEGORY["skill"] == "skills"
        assert _TYPE_TO_CATEGORY["mcp"] == "mcp_servers"
        assert _TYPE_TO_CATEGORY["hook"] == "hooks"
        assert _TYPE_TO_CATEGORY["marketplace"] == "marketplaces"

    def test_category_to_type(self) -> None:
        assert _CATEGORY_TO_TYPE["agents"] == "agent"
        assert _CATEGORY_TO_TYPE["commands"] == "command"
        assert _CATEGORY_TO_TYPE["skills"] == "skill"
        assert _CATEGORY_TO_TYPE["mcp_servers"] == "mcp"
        assert _CATEGORY_TO_TYPE["hooks"] == "hook"
        assert _CATEGORY_TO_TYPE["marketplaces"] == "marketplace"


class TestGetStatusNoManifest:
    """All items should be 'new' when no manifest exists."""

    def test_all_new(self, config: Config) -> None:
        entries = get_status(config)
        assert len(entries) == 3
        assert all(e.state == "new" for e in entries)

    def test_entry_fields(self, config: Config) -> None:
        entries = get_status(config)
        names = {e.name for e in entries}
        assert "alpha" in names
        assert "deploy" in names
        assert "server" in names
        for e in entries:
            assert e.target_id == "local"

    def test_types(self, config: Config) -> None:
        entries = get_status(config)
        types = {e.name: e.item_type for e in entries}
        assert types["alpha"] == "agent"
        assert types["deploy"] == "command"
        assert types["server"] == "mcp"


class TestGetStatusWithManifest:
    def _write_manifest(self, target_path: Path, items: dict) -> None:
        manifest_data = {
            "version": 1,
            "deployed_at": "2025-01-01T00:00:00+00:00",
            "items": items,
        }
        (target_path / MANIFEST_FILENAME).write_text(json.dumps(manifest_data))

    def test_current_items(
        self, config: Config, source_root: Path, target_path: Path
    ) -> None:
        # Write manifest with matching hashes
        alpha_hash = compute_file_hash(
            (source_root / "agents" / "alpha.md").read_bytes()
        )
        deploy_hash = compute_file_hash(
            (source_root / "commands" / "deploy.md").read_bytes()
        )
        # MCP entries carry a content fingerprint (ClaudeTarget), so the
        # manifest hash mixes it in exactly as compute_item_hash does.
        server_base = compute_file_hash(
            (source_root / "mcp" / "server.yaml").read_bytes()
        )
        fp = ClaudeTarget("local", target_path).content_fingerprint("mcp")
        server_hash = (
            f"sha256:{hashlib.sha256(f'{server_base}|{fp}'.encode()).hexdigest()}"
        )
        self._write_manifest(
            target_path,
            {
                "agents": {"alpha": {"source_hash": alpha_hash}},
                "commands": {"deploy": {"source_hash": deploy_hash}},
                "mcp_servers": {"server": {"source_hash": server_hash}},
            },
        )

        entries = get_status(config)
        assert all(e.state == "current" for e in entries), [e.state for e in entries]

    def test_changed_items(self, config: Config, target_path: Path) -> None:
        self._write_manifest(
            target_path,
            {
                "agents": {"alpha": {"source_hash": "sha256:old_hash"}},
            },
        )
        entries = get_status(config)
        alpha = next(e for e in entries if e.name == "alpha")
        assert alpha.state == "changed"

    def test_pending_removal(self, config: Config, target_path: Path) -> None:
        self._write_manifest(
            target_path,
            {
                "agents": {
                    "alpha": {"source_hash": "sha256:whatever"},
                    "deleted-agent": {"source_hash": "sha256:old"},
                },
            },
        )
        entries = get_status(config)
        removed = [e for e in entries if e.name == "deleted-agent"]
        assert len(removed) == 1
        assert removed[0].state == "pending_removal"
        assert removed[0].item_type == "agent"
        assert removed[0].target_id == "local"


class TestGetStatusTargetFiltering:
    def test_specific_target(self, source_root: Path, tmp_path: Path) -> None:
        t1 = tmp_path / "t1"
        t2 = tmp_path / "t2"
        t1.mkdir()
        t2.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "t1": TargetConfig(id="t1", type="claude", path=t1),
                "t2": TargetConfig(id="t2", type="claude", path=t2),
            },
            groups={},
        )
        entries = get_status(config, target_ids=["t1"])
        assert all(e.target_id == "t1" for e in entries)
        assert len(entries) == 3

    def test_default_all_targets(self, source_root: Path, tmp_path: Path) -> None:
        t1 = tmp_path / "t1"
        t2 = tmp_path / "t2"
        t1.mkdir()
        t2.mkdir()
        config = Config(
            source_root=source_root,
            targets={
                "t1": TargetConfig(id="t1", type="claude", path=t1),
                "t2": TargetConfig(id="t2", type="claude", path=t2),
            },
            groups={},
        )
        entries = get_status(config)
        target_ids = {e.target_id for e in entries}
        assert target_ids == {"t1", "t2"}
        # 3 items per target
        assert len(entries) == 6


class TestGetStatusWithFilters:
    def test_only_filter_respected(self, tmp_path: Path) -> None:
        root = tmp_path / "src"
        agents = root / "agents"
        agents.mkdir(parents=True)
        (agents / "restricted.md").write_bytes(
            b"---\nname: restricted\nonly:\n  - t1\n---\n"
        )

        t1 = tmp_path / "t1"
        t2 = tmp_path / "t2"
        t1.mkdir()
        t2.mkdir()
        config = Config(
            source_root=root,
            targets={
                "t1": TargetConfig(id="t1", type="claude", path=t1),
                "t2": TargetConfig(id="t2", type="claude", path=t2),
            },
            groups={},
        )
        entries = get_status(config)
        # Should only appear for t1
        assert len(entries) == 1
        assert entries[0].target_id == "t1"
        assert entries[0].name == "restricted"


class TestStatusEntry:
    def test_fields(self) -> None:
        entry = StatusEntry(
            item_type="agent", name="test", target_id="local", state="new"
        )
        assert entry.item_type == "agent"
        assert entry.name == "test"
        assert entry.target_id == "local"
        assert entry.state == "new"


class TestGetStatusSkills:
    def test_skill_status(self, tmp_path: Path) -> None:
        root = tmp_path / "src"
        skill_dir = root / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(
            b"---\nname: my-skill\ndescription: test\n---\nBody"
        )

        target = tmp_path / "target"
        target.mkdir()
        config = Config(
            source_root=root,
            targets={"local": TargetConfig(id="local", type="claude", path=target)},
            groups={},
        )
        entries = get_status(config)
        assert len(entries) == 1
        assert entries[0].item_type == "skill"
        assert entries[0].name == "my-skill"
        assert entries[0].state == "new"


class TestStatusMatchesDeploy:
    """Status must use the same selection + hashing rules as deploy (B30)."""

    def test_droid_target_skipped_items_not_reported(self, tmp_path: Path) -> None:
        """Items a droid target would no-op must not appear as phantom 'new'."""
        from promptdeploy.deploy import deploy

        src = tmp_path / "src"
        (src / "agents").mkdir(parents=True)
        (src / "agents" / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")
        # Plain command (no droid_deploy: skill) -- droid skips it.
        (src / "commands").mkdir()
        (src / "commands" / "fix.md").write_bytes(b"---\nname: fix\n---\nFix.\n")
        # Hooks and settings are claude-only -- droid skips both.
        (src / "hooks").mkdir()
        (src / "hooks" / "my-hook.yaml").write_bytes(
            b"name: my-hook\nhooks:\n  Stop:\n    - matcher: ''\n      hooks:\n"
            b"        - command: 'echo'\n          type: command\n"
        )
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")

        target_dir = tmp_path / "droid"
        target_dir.mkdir()
        tc = TargetConfig(id="droid-t", type="droid", path=target_dir)
        config = Config(source_root=src, targets={tc.id: tc}, groups={})

        deploy(config)
        # A follow-up dry-run deploy reports nothing pending...
        followup = deploy(config, dry_run=True)
        assert all(a.action == "skip" for a in followup)
        # ...and status must agree: only the agent is tracked, all current.
        entries = get_status(config)
        assert {(e.name, e.state) for e in entries} == {("helper", "current")}

    def test_gptel_target_only_reports_prompts(self, tmp_path: Path) -> None:
        """gptel skips everything except prompts; status must mirror that."""
        from promptdeploy.deploy import deploy

        src = tmp_path / "src"
        (src / "agents").mkdir(parents=True)
        (src / "agents" / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")
        (src / "prompts").mkdir()
        (src / "prompts" / "p.txt").write_text("hello")

        target_dir = tmp_path / "gptel"
        target_dir.mkdir()
        tc = TargetConfig(id="gptel-emacs", type="gptel", path=target_dir)
        config = Config(source_root=src, targets={tc.id: tc}, groups={})

        deploy(config)
        followup = deploy(config, dry_run=True)
        assert all(a.action == "skip" for a in followup)
        entries = get_status(config)
        assert {(e.name, e.state) for e in entries} == {("p", "current")}

    def test_filetagged_item_not_reported_on_excluded_target(
        self, tmp_path: Path
    ) -> None:
        """'heavy -- positron.md' must not appear as 'new' on non-positron targets."""
        src = tmp_path / "src"
        (src / "commands").mkdir(parents=True)
        (src / "commands" / "heavy -- positron.md").write_bytes(b"Heavy body.\n")

        t_personal = tmp_path / "claude-personal"
        t_positron = tmp_path / "claude-positron"
        t_personal.mkdir()
        t_positron.mkdir()
        targets = {
            "claude-personal": TargetConfig(
                id="claude-personal",
                type="claude",
                path=t_personal,
                labels=["claude", "personal"],
            ),
            "claude-positron": TargetConfig(
                id="claude-positron",
                type="claude",
                path=t_positron,
                labels=["claude", "positron"],
            ),
        }
        groups = {
            "claude": ["claude-personal", "claude-positron"],
            "personal": ["claude-personal"],
            "positron": ["claude-positron"],
        }
        config = Config(source_root=src, targets=targets, groups=groups)

        entries = get_status(config)
        assert {(e.target_id, e.name, e.state) for e in entries} == {
            ("claude-positron", "heavy", "new")
        }

    def test_current_after_deploy_with_default_model(self, tmp_path: Path) -> None:
        """With a configured default_model, status agrees with a clean deploy.

        The injected model is part of the deploy-side content fingerprint;
        status must fold it into its hashes the same way or every agent and
        skill shows as phantom 'changed'.
        """
        from promptdeploy.deploy import deploy

        src = tmp_path / "src"
        (src / "agents").mkdir(parents=True)
        (src / "agents" / "helper.md").write_bytes(b"---\nname: helper\n---\nAgent.\n")
        skill_dir = src / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(
            b"---\nname: my-skill\ndescription: test\n---\nBody"
        )
        (src / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )

        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="claude-x", type="claude", path=target_dir)
        config = Config(source_root=src, targets={tc.id: tc}, groups={})

        deploy(config)
        entries = get_status(config)
        assert entries, "expected at least the agent and skill"
        assert {(e.name, e.state) for e in entries} == {
            ("helper", "current"),
            ("my-skill", "current"),
        }

    def test_settings_current_after_deploy(self, tmp_path: Path) -> None:
        """settings must show 'current' after a clean deploy, not phantom 'changed'.

        deploy() stores a hash of the rendered settings JSON in the manifest;
        status must hash the same rendered output (i.e. pass config through to
        compute_item_hash) or the hashes never match and settings shows
        'changed' forever even though a follow-up deploy skips it.
        """
        from promptdeploy.deploy import deploy

        src = tmp_path / "src"
        src.mkdir()
        (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")

        target_dir = tmp_path / "claude"
        target_dir.mkdir()
        tc = TargetConfig(id="claude-x", type="claude", path=target_dir)
        config = Config(source_root=src, targets={tc.id: tc}, groups={})

        deploy(config)
        followup = deploy(config, dry_run=True)
        assert all(a.action == "skip" for a in followup)
        entries = get_status(config)
        assert {(e.item_type, e.name, e.state) for e in entries} == {
            ("settings", "settings", "current")
        }

    def test_models_current_after_deploy_on_droid(self, tmp_path: Path) -> None:
        """models must show 'current' after a clean deploy on a droid target.

        deploy() hashes the filtered, env-expanded models config; status must
        do the same or models shows phantom 'changed' on droid/opencode
        targets after every deploy.
        """
        from promptdeploy.deploy import deploy

        src = tmp_path / "src"
        src.mkdir()
        (src / "models.yaml").write_text(
            "providers:\n"
            "  acme:\n"
            "    display_name: Acme\n"
            "    base_url: https://acme.com\n"
            "    api_key: key\n"
            "    models:\n"
            "      m1:\n"
            "        display_name: Model 1\n"
        )

        target_dir = tmp_path / "droid"
        target_dir.mkdir()
        tc = TargetConfig(id="droid-t", type="droid", path=target_dir)
        config = Config(source_root=src, targets={tc.id: tc}, groups={})

        deploy(config)
        followup = deploy(config, dry_run=True)
        assert all(a.action == "skip" for a in followup)
        entries = get_status(config)
        assert {(e.item_type, e.name, e.state) for e in entries} == {
            ("models", "models", "current")
        }


def test_status_handles_settings_and_prompts(tmp_path):
    from promptdeploy.config import Config, TargetConfig
    from promptdeploy.status import get_status

    src = tmp_path / "source"
    src.mkdir()
    (src / "settings.yaml").write_text("base:\n  effortLevel: low\n")
    (src / "prompts").mkdir()
    (src / "prompts" / "p.txt").write_text("hello")
    tgt = tmp_path / "claude"
    tgt.mkdir()
    tc = TargetConfig(id="claude-x", type="claude", path=tgt)
    config = Config(source_root=src, targets={tc.id: tc}, groups={})

    entries = get_status(config, ["claude-x"])  # must not KeyError
    kinds = {e.item_type for e in entries}
    assert "settings" in kinds
    assert "prompt" in kinds


class TestStatusRemoteMcp:
    """Status parity for remote MCP targets (env-folded hash, never flushes)."""

    def _remote_config(self, tmp_path: Path, *, mcp_yaml: bytes) -> Config:
        src = tmp_path / "src"
        (src / "mcp").mkdir(parents=True)
        (src / "mcp" / "srv.yaml").write_bytes(mcp_yaml)
        tc = TargetConfig(
            id="rc", type="claude", path=tmp_path / "rc", host="user@fakehost"
        )
        return Config(source_root=src, targets={tc.id: tc}, groups={})

    def _patch(self, monkeypatch, *, seed: dict | None = None):
        from promptdeploy.manifest import (
            MANIFEST_FILENAME,
            Manifest,
            ManifestItem,
            save_manifest,
        )

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            local_path.mkdir(parents=True, exist_ok=True)
            if seed:
                save_manifest(
                    Manifest(
                        items={
                            "mcp_servers": {
                                n: ManifestItem(source_hash=h) for n, h in seed.items()
                            }
                        }
                    ),
                    local_path / MANIFEST_FILENAME,
                )

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None
        )

    def _hash_for(self, config: Config) -> str:
        from promptdeploy.deploy import compute_item_hash
        from promptdeploy.source import SourceDiscovery
        from promptdeploy.targets import create_target

        target = create_target(config.targets["rc"])
        item = next(
            i
            for i in SourceDiscovery(config.source_root).discover_all()
            if i.item_type == "mcp"
        )
        return compute_item_hash(item, target, config)

    def test_status_reports_remote_mcp_new(self, tmp_path: Path, monkeypatch):
        config = self._remote_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        self._patch(monkeypatch)
        entries = get_status(config)
        mcp = [e for e in entries if e.item_type == "mcp"]
        assert len(mcp) == 1
        assert mcp[0].state == "new"

    def test_status_remote_mcp_never_ssh_stdins(self, tmp_path: Path, monkeypatch):
        config = self._remote_config(tmp_path, mcp_yaml=b"name: srv\ncommand: c\n")
        pulls: list = []
        stdins: list = []

        def fake_pull(host, remote_path, local_path, *, verbose=False, includes=None):
            pulls.append(host)
            local_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("promptdeploy.targets.remote.ssh_pull", fake_pull)
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_push", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin",
            lambda *a, **kw: stdins.append(a),
        )

        get_status(config)

        assert stdins == []
        assert pulls == ["user@fakehost"]

    def test_status_matches_deploy_remote_mcp(self, tmp_path: Path, monkeypatch):
        from promptdeploy.deploy import deploy

        config = self._remote_config(
            tmp_path, mcp_yaml=b'name: srv\nurl: https://x\nenv:\n  K: "${TOK}"\n'
        )
        monkeypatch.setenv("TOK", "v1")
        current = self._hash_for(config)

        # Capture that the status path built a remote_mcp_hash target.
        from promptdeploy.targets import create_target as real_create

        seen: dict = {}

        def spy_create(tc, **kw):
            t = real_create(tc, **kw)
            if tc.id == "rc":
                seen["remote_mcp_hash"] = t.remote_mcp_hash
            return t

        monkeypatch.setattr("promptdeploy.status.create_target", spy_create)

        # current/skip
        self._patch(monkeypatch, seed={"srv": current})
        monkeypatch.setattr(
            "promptdeploy.targets.remote.ssh_stdin", lambda *a, **kw: None
        )
        status_entries = get_status(config)
        assert [e.state for e in status_entries if e.item_type == "mcp"] == ["current"]
        assert seen["remote_mcp_hash"] is True
        deploy_actions = deploy(config)
        assert all(a.action == "skip" for a in deploy_actions if a.item_type == "mcp")

        # rotate -> changed/update
        monkeypatch.setenv("TOK", "v2")
        self._patch(monkeypatch, seed={"srv": current})
        assert [e.state for e in get_status(config) if e.item_type == "mcp"] == [
            "changed"
        ]
        self._patch(monkeypatch, seed={"srv": current})
        deploy_actions = deploy(config)
        assert [a.action for a in deploy_actions if a.item_type == "mcp"] == ["update"]

        # delete source -> pending_removal / remove
        (config.source_root / "mcp" / "srv.yaml").unlink()
        self._patch(monkeypatch, seed={"srv": current})
        assert [e.state for e in get_status(config) if e.name == "srv"] == [
            "pending_removal"
        ]
        self._patch(monkeypatch, seed={"srv": current})
        deploy_actions = deploy(config)
        assert [a.action for a in deploy_actions if a.name == "srv"] == ["remove"]

    def test_status_remote_mcp_secret_unset_reports_changed(
        self, tmp_path: Path, monkeypatch
    ):
        config = self._remote_config(
            tmp_path, mcp_yaml=b'name: srv\nurl: https://x\nenv:\n  K: "${TOK}"\n'
        )
        monkeypatch.setenv("TOK", "secret")
        baked = self._hash_for(config)
        # Now status runs WITHOUT TOK exported.
        monkeypatch.delenv("TOK", raising=False)
        self._patch(monkeypatch, seed={"srv": baked})
        entries = get_status(config)
        assert [e.state for e in entries if e.item_type == "mcp"] == ["changed"]
