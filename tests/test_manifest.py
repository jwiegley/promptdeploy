"""Tests for manifest tracking system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptdeploy.manifest import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    Manifest,
    ManifestItem,
    compute_directory_hash,
    compute_file_hash,
    has_changed,
    load_manifest,
    save_manifest,
)


class TestComputeFileHash:
    def test_consistent_for_same_content(self) -> None:
        content = b"hello world"
        assert compute_file_hash(content) == compute_file_hash(content)

    def test_differs_for_different_content(self) -> None:
        assert compute_file_hash(b"hello") != compute_file_hash(b"world")

    def test_sha256_prefix(self) -> None:
        result = compute_file_hash(b"test")
        assert result.startswith("sha256:")

    def test_empty_content(self) -> None:
        result = compute_file_hash(b"")
        assert result.startswith("sha256:")
        assert len(result) > len("sha256:")


class TestComputeDirectoryHash:
    def test_deterministic(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"aaa")
        (tmp_path / "b.txt").write_bytes(b"bbb")
        assert compute_directory_hash(tmp_path) == compute_directory_hash(tmp_path)

    def test_order_independent_of_creation(self, tmp_path: Path) -> None:
        """Hash is based on sorted paths, not creation order."""
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "b.txt").write_bytes(b"bbb")
        (d1 / "a.txt").write_bytes(b"aaa")

        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "a.txt").write_bytes(b"aaa")
        (d2 / "b.txt").write_bytes(b"bbb")

        assert compute_directory_hash(d1) == compute_directory_hash(d2)

    def test_differs_with_different_content(self, tmp_path: Path) -> None:
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "a.txt").write_bytes(b"aaa")

        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "a.txt").write_bytes(b"zzz")

        assert compute_directory_hash(d1) != compute_directory_hash(d2)

    def test_includes_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_bytes(b"nested")
        h1 = compute_directory_hash(tmp_path)

        (sub / "file.txt").write_bytes(b"changed")
        h2 = compute_directory_hash(tmp_path)

        assert h1 != h2

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = compute_directory_hash(tmp_path)
        assert result.startswith("sha256:")

    def test_path_content_boundary_is_framed(self, tmp_path: Path) -> None:
        """(path, content) pairs are length-framed, so shifting bytes across
        the path/content boundary must change the hash."""
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "ab").write_bytes(b"c")

        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "a").write_bytes(b"bc")

        assert compute_directory_hash(d1) != compute_directory_hash(d2)


class TestLoadManifest:
    def test_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        manifest = load_manifest(tmp_path / "nope.json")
        assert manifest.version == MANIFEST_VERSION
        # Categories are created on demand, not pre-seeded.
        assert manifest.items == {}

    def test_loads_valid_json(self, tmp_path: Path) -> None:
        data = {
            "version": 1,
            "deployed_at": "2025-01-01T00:00:00+00:00",
            "items": {
                "agents": {
                    "rust-pro": {
                        "source_hash": "sha256:abc123",
                        "target_path": "/home/.claude/agents/rust-pro.md",
                    }
                },
                "commands": {},
                "skills": {},
                "mcp_servers": {},
            },
        }
        path = tmp_path / MANIFEST_FILENAME
        path.write_text(json.dumps(data))

        manifest = load_manifest(path)
        assert manifest.version == 1
        assert manifest.deployed_at == "2025-01-01T00:00:00+00:00"
        assert "rust-pro" in manifest.items["agents"]
        item = manifest.items["agents"]["rust-pro"]
        assert item.source_hash == "sha256:abc123"
        assert item.target_path == "/home/.claude/agents/rust-pro.md"


class TestLoadManifestRobustness:
    """The manifest is a rebuildable cache: corrupt files fall back to empty (B31)."""

    def test_corrupt_json_returns_empty_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / MANIFEST_FILENAME
        path.write_text("{not valid json")
        manifest = load_manifest(path)
        assert manifest.version == MANIFEST_VERSION
        for entries in manifest.items.values():
            assert entries == {}
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert str(path) in err

    def test_non_mapping_json_returns_empty_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / MANIFEST_FILENAME
        path.write_text("[1, 2, 3]")
        manifest = load_manifest(path)
        assert manifest.items == {}
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert str(path) in err

    def test_future_version_loads_known_fields(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A newer-version manifest loads its known fields; unknown ones are ignored."""
        data = {
            "version": 99,
            "deployed_at": "2026-01-01T00:00:00+00:00",
            "items": {
                "agents": {
                    "a": {
                        "source_hash": "sha256:abc",
                        "target_path": "/x/a.md",
                        "shiny_new_field": {"nested": True},
                    }
                }
            },
        }
        path = tmp_path / MANIFEST_FILENAME
        path.write_text(json.dumps(data))
        manifest = load_manifest(path)
        assert manifest.version == 99
        item = manifest.items["agents"]["a"]
        assert item.source_hash == "sha256:abc"
        assert item.target_path == "/x/a.md"
        err = capsys.readouterr().err
        assert "version" in err

    def test_missing_source_hash_defaults_to_empty(self, tmp_path: Path) -> None:
        data = {
            "version": MANIFEST_VERSION,
            "items": {"agents": {"a": {"target_path": "/x/a.md"}}},
        }
        path = tmp_path / MANIFEST_FILENAME
        path.write_text(json.dumps(data))
        manifest = load_manifest(path)
        assert manifest.items["agents"]["a"].source_hash == ""


class TestSaveManifest:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "a" / "b" / "c" / MANIFEST_FILENAME
        manifest = Manifest()
        save_manifest(manifest, path)
        assert path.exists()

    def test_atomic_write_no_temp_file_left(self, tmp_path: Path) -> None:
        path = tmp_path / MANIFEST_FILENAME
        save_manifest(Manifest(), path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / MANIFEST_FILENAME
        manifest = Manifest()
        manifest.items.setdefault("agents", {})["test-agent"] = ManifestItem(
            source_hash="sha256:deadbeef",
            target_path="/dest/agent.md",
        )
        save_manifest(manifest, path)

        data = json.loads(path.read_text())
        assert data["version"] == MANIFEST_VERSION
        assert "test-agent" in data["items"]["agents"]
        agent = data["items"]["agents"]["test-agent"]
        assert agent["source_hash"] == "sha256:deadbeef"
        assert agent["target_path"] == "/dest/agent.md"


class TestSaveManifestError:
    def test_cleanup_on_replace_failure(self, tmp_path: Path) -> None:
        """When os.replace fails, temp file is cleaned up and error is raised."""
        from unittest.mock import patch

        path = tmp_path / MANIFEST_FILENAME
        manifest = Manifest()
        manifest.items.setdefault("agents", {})["test"] = ManifestItem(
            source_hash="sha256:abc"
        )

        with patch("os.replace", side_effect=OSError("mock failure")):
            with pytest.raises(OSError, match="mock failure"):
                save_manifest(manifest, path)

        # Temp file should be cleaned up
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []
        # Original manifest should not exist
        assert not path.exists()

    def test_cleanup_on_replace_failure_unlink_fails(self, tmp_path: Path) -> None:
        """When both os.replace and os.unlink fail, the original error propagates."""
        import os
        from unittest.mock import patch

        path = tmp_path / MANIFEST_FILENAME
        manifest = Manifest()

        original_unlink = os.unlink

        def failing_unlink(p):
            if str(p).endswith(".tmp"):
                raise OSError("unlink failed too")
            return original_unlink(p)

        with patch("os.replace", side_effect=OSError("replace failed")):
            with patch("os.unlink", side_effect=failing_unlink):
                with pytest.raises(OSError, match="replace failed"):
                    save_manifest(manifest, path)


class TestRoundTrip:
    def test_save_load_preserves_data(self, tmp_path: Path) -> None:
        path = tmp_path / MANIFEST_FILENAME
        original = Manifest(
            version=MANIFEST_VERSION,
            deployed_at="2025-06-15T12:00:00+00:00",
        )
        original.items.setdefault("agents", {})["my-agent"] = ManifestItem(
            source_hash="sha256:aaa",
            target_path="/agents/my-agent.md",
        )
        original.items.setdefault("skills", {})["my-skill"] = ManifestItem(
            source_hash="sha256:bbb",
        )
        original.items.setdefault("mcp_servers", {})["server1"] = ManifestItem(
            source_hash="sha256:ccc",
            target_path="/mcp/server1.json",
        )
        save_manifest(original, path)

        loaded = load_manifest(path)
        assert loaded.version == original.version
        assert loaded.deployed_at == original.deployed_at

        agent = loaded.items["agents"]["my-agent"]
        assert agent.source_hash == "sha256:aaa"
        assert agent.target_path == "/agents/my-agent.md"

        skill = loaded.items["skills"]["my-skill"]
        assert skill.source_hash == "sha256:bbb"
        assert skill.target_path is None

        server = loaded.items["mcp_servers"]["server1"]
        assert server.source_hash == "sha256:ccc"
        assert server.target_path == "/mcp/server1.json"


class TestHasChanged:
    def test_new_item(self) -> None:
        # Category exists but the item is unknown.
        manifest = Manifest()
        manifest.items["agents"] = {}
        assert has_changed(manifest, "agents", "new-agent", "sha256:xyz")

    def test_unknown_category(self) -> None:
        manifest = Manifest()
        assert has_changed(manifest, "unknown_cat", "item", "sha256:xyz")

    def test_changed_hash(self) -> None:
        manifest = Manifest()
        manifest.items.setdefault("agents", {})["my-agent"] = ManifestItem(
            source_hash="sha256:old"
        )
        assert has_changed(manifest, "agents", "my-agent", "sha256:new")

    def test_unchanged(self) -> None:
        manifest = Manifest()
        manifest.items.setdefault("agents", {})["my-agent"] = ManifestItem(
            source_hash="sha256:same"
        )
        assert not has_changed(manifest, "agents", "my-agent", "sha256:same")


def test_managed_keys_roundtrips(tmp_path):
    from promptdeploy.manifest import (
        Manifest,
        ManifestItem,
        load_manifest,
        save_manifest,
    )

    m = Manifest()
    m.items.setdefault("settings", {})["settings"] = ManifestItem(
        source_hash="sha256:abc", managed_keys=["env", "model"]
    )
    path = tmp_path / ".prompt-deploy-manifest.json"
    save_manifest(m, path)
    loaded = load_manifest(path)
    item = loaded.items["settings"]["settings"]
    assert item.managed_keys == ["env", "model"]


def test_managed_keys_absent_serializes_without_field(tmp_path):
    import json

    from promptdeploy.manifest import Manifest, ManifestItem, save_manifest

    m = Manifest()
    m.items.setdefault("agents", {})["a"] = ManifestItem(source_hash="sha256:x")
    path = tmp_path / ".prompt-deploy-manifest.json"
    save_manifest(m, path)
    data = json.loads(path.read_text())
    assert "managed_keys" not in data["items"]["agents"]["a"]
