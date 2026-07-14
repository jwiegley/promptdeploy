"""Tests for the gptel target implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from promptdeploy.manifest import MANIFEST_FILENAME
from promptdeploy.targets.gptel import GptelTarget


def _make_target(tmp_path: Path) -> GptelTarget:
    config = tmp_path / "prompts"
    config.mkdir()
    return GptelTarget("gptel-emacs", config)


class TestProperties:
    def test_id(self, tmp_path: Path):
        target = GptelTarget("my-id", tmp_path)
        assert target.id == "my-id"

    def test_exists_true(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.exists()

    def test_exists_false(self, tmp_path: Path):
        target = GptelTarget("g", tmp_path / "nope")
        assert not target.exists()

    def test_manifest_path(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.manifest_path() == tmp_path / "prompts" / MANIFEST_FILENAME

    def test_rsync_includes(self, tmp_path: Path):
        target = _make_target(tmp_path)
        includes = target.rsync_includes()
        assert includes is not None
        assert "*.poet" in includes
        assert "*.json" in includes
        assert "*.txt" in includes
        assert "*.md" in includes
        assert "*.org" in includes
        assert MANIFEST_FILENAME in includes


class TestShouldSkip:
    def test_skips_non_prompts(self, tmp_path: Path):
        target = _make_target(tmp_path)
        for item_type in (
            "agent",
            "command",
            "skill",
            "mcp",
            "models",
            "hook",
            "marketplace",
        ):
            assert target.should_skip(item_type, "x") is True

    def test_does_not_skip_prompts(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.should_skip("prompt", "x") is False


class TestDeployPrompt:
    def test_poet_copied_verbatim(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        content = b"- role: system\n  content: Hello\n- role: user\n  content: Hi\n"
        src.write_bytes(content)
        target.deploy_prompt("demo", content, src)

        dest = tmp_path / "prompts" / "demo.poet"
        assert dest.exists()
        assert dest.read_bytes() == content

    def test_txt_copied_verbatim(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "plain.txt"
        body = b"Just text.\n"
        src.write_bytes(body)
        target.deploy_prompt("plain", body, src)

        dest = tmp_path / "prompts" / "plain.txt"
        assert dest.exists()
        assert dest.read_bytes() == body

    def test_md_copied_verbatim(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        body = b"# A heading\n"
        src.write_bytes(body)
        target.deploy_prompt("doc", body, src)
        assert (tmp_path / "prompts" / "doc.md").read_bytes() == body

    def test_jinja_extension_treated_as_poet(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "x.j2"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)
        target.deploy_prompt("x", body, src)
        assert (tmp_path / "prompts" / "x.json").exists()

    def test_atomic_write_cleanup_on_replace_failure(self, tmp_path: Path):
        from unittest.mock import patch

        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)

        with (
            patch("os.replace", side_effect=OSError("mock failure")),
            pytest.raises(OSError, match="mock failure"),
        ):
            target.deploy_prompt("demo", body, src)

        leftover = list((tmp_path / "prompts").glob("*.tmp"))
        assert leftover == []

    def test_atomic_write_cleanup_unlink_also_fails(self, tmp_path: Path):
        import os
        from unittest.mock import patch

        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)

        original_unlink = os.unlink

        def failing_unlink(p):
            if str(p).endswith(".tmp"):
                raise OSError("unlink failed")
            return original_unlink(p)

        with (
            patch("os.replace", side_effect=OSError("replace failed")),
            patch("os.unlink", side_effect=failing_unlink),
            pytest.raises(OSError, match="replace failed"),
        ):
            target.deploy_prompt("demo", body, src)


class TestRemovePrompt:
    def test_removes_json(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "demo.json").write_text("[]")
        target.remove_prompt("demo")
        assert not (tmp_path / "prompts" / "demo.json").exists()

    def test_removes_poet(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        target.remove_prompt("demo")
        assert not (tmp_path / "prompts" / "demo.poet").exists()

    def test_removes_txt(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.txt"
        body = b"Hello\n"
        src.write_bytes(body)
        target.deploy_prompt("doc", body, src)
        target.remove_prompt("doc")
        assert not (tmp_path / "prompts" / "doc.txt").exists()

    def test_no_error_when_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_prompt("nonexistent")

    def test_with_target_path_removes_only_that_file(self, tmp_path: Path):
        # The user has an unrelated foo.md they authored; promptdeploy
        # had previously deployed foo.json. Removing foo with the
        # recorded target_path must NOT touch foo.md.
        target = _make_target(tmp_path)
        prompts_dir = tmp_path / "prompts"
        (prompts_dir / "foo.json").write_text("[]")
        unrelated = prompts_dir / "foo.md"
        unrelated.write_text("user-authored")

        target.remove_prompt("foo", target_path=Path("foo.json"))

        assert not (prompts_dir / "foo.json").exists()
        assert unrelated.exists()
        assert unrelated.read_text() == "user-authored"

    def test_with_target_path_missing_file_no_error(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_prompt("foo", target_path=Path("foo.json"))

    @pytest.mark.parametrize(
        "target_path",
        [
            Path("../../victim"),
            Path("/tmp/victim"),
            Path("nested/foo.md"),
            Path("unrelated.md"),
        ],
    )
    def test_rejects_recorded_path_not_owned_by_prompt(
        self, target_path: Path, tmp_path: Path
    ):
        target = _make_target(tmp_path)
        sentinel = tmp_path / "victim"
        sentinel.write_text("preserve")

        with pytest.raises(ValueError, match="does not match its item name"):
            target.remove_prompt("foo", target_path=target_path)

        assert sentinel.read_text() == "preserve"

    def test_rejects_unsafe_prompt_name_before_legacy_removal(self, tmp_path: Path):
        target = _make_target(tmp_path)
        sentinel = tmp_path / "victim"
        sentinel.write_text("preserve")
        with pytest.raises(ValueError, match="Unsafe prompt name"):
            target.remove_prompt("../../victim")
        assert sentinel.read_text() == "preserve"

    def test_legacy_fallback_without_target_path(self, tmp_path: Path):
        # Legacy manifests (no target_path) fall back to extension probing.
        target = _make_target(tmp_path)
        prompts_dir = tmp_path / "prompts"
        (prompts_dir / "foo.json").write_text("[]")
        target.remove_prompt("foo")
        assert not (prompts_dir / "foo.json").exists()


class TestDeployedArtifactPath:
    def test_returns_path_after_deploy(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: x\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        assert target.deployed_artifact_path("prompt", "demo") == Path("demo.poet")

    def test_returns_path_for_plain_extension(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        body = b"# heading\n"
        src.write_bytes(body)
        target.deploy_prompt("doc", body, src)
        assert target.deployed_artifact_path("prompt", "doc") == Path("doc.md")

    def test_non_prompt_returns_none(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.deployed_artifact_path("agent", "x") is None

    def test_unknown_name_returns_none(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.deployed_artifact_path("prompt", "missing") is None


class TestConsumeWarnings:
    def test_records_warnings_for_undefined_var(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.j2"
        body = b"- role: system\n  content: 'hi {{ missing }}'\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        warnings = target.consume_warnings()
        assert warnings == [("demo", ["Undefined Jinja variable: missing"])]
        # Draining empties the buffer.
        assert target.consume_warnings() == []

    def test_no_warnings_when_no_undefined(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.j2"
        body = b"- role: system\n  content: ok\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        assert target.consume_warnings() == []


class TestItemExists:
    def test_unknown_type_false(self, tmp_path: Path):
        target = _make_target(tmp_path)
        for item_type in ("agent", "command", "skill", "mcp", "models", "hook"):
            assert target.item_exists(item_type, "x") is False

    def test_prompt_not_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.item_exists("prompt", "missing") is False

    def test_prompt_json_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "x.json").write_text("[]")
        assert target.item_exists("prompt", "x") is True

    def test_prompt_poet_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "x.poet").write_text("- role: system\n  content: x\n")
        assert target.item_exists("prompt", "x") is True

    def test_prompt_md_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "x.md").write_text("body")
        assert target.item_exists("prompt", "x") is True

    def test_prompt_txt_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "x.txt").write_text("body")
        assert target.item_exists("prompt", "x") is True

    def test_prompt_org_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "x.org").write_text("body")
        assert target.item_exists("prompt", "x") is True


class TestNoOpMethods:
    """Non-prompt deploy/remove methods are no-ops on the gptel target."""

    def test_all_noops(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_agent("a", b"")
        target.deploy_command("c", b"")
        target.deploy_skill("s", tmp_path)
        target.deploy_mcp_server("m", {})
        target.deploy_models({})
        target.deploy_hook("h", {})
        target.remove_agent("a")
        target.remove_command("c")
        target.remove_skill("s")
        target.remove_mcp_server("m")
        target.remove_models()
        target.remove_hook("h")
        # None of those should have produced any files in the prompts dir.
        assert list((tmp_path / "prompts").iterdir()) == []


class TestWouldDeployBytes:
    def test_plain_prompt_returns_raw_content(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "doc.md"
        src.write_bytes(b"Plain.\n")
        assert target.would_deploy_bytes("prompt", "doc", b"Plain.\n", src) == (
            b"Plain.\n"
        )

    def test_poet_prompt_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.poet"
        body = b"- role: system\n  content: hi\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        on_disk = (tmp_path / "prompts" / "demo.poet").read_bytes()
        assert target.would_deploy_bytes("prompt", "demo", body, src) == on_disk

    def test_jinja_prompt_matches_deploy_output(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "demo.j2"
        body = b"- role: system\n  content: hi\n"
        src.write_bytes(body)
        target.deploy_prompt("demo", body, src)
        on_disk = (tmp_path / "prompts" / "demo.json").read_bytes()
        assert target.would_deploy_bytes("prompt", "demo", body, src) == on_disk

    def test_returns_none_without_source_path(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.would_deploy_bytes("prompt", "demo", b"x") is None

    def test_returns_none_for_non_prompt(self, tmp_path: Path):
        target = _make_target(tmp_path)
        src = tmp_path / "x.md"
        src.write_bytes(b"")
        assert target.would_deploy_bytes("agent", "x", b"", src) is None
        assert target.would_deploy_bytes("command", "x", b"", src) is None
        assert target.would_deploy_bytes("skill", "x", b"", src) is None


class TestReadDeployedBytes:
    def test_returns_on_disk_for_each_extension(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # Write a plain prompt at each supported extension and confirm the
        # reader finds it in priority order.
        for ext in (".poet", ".json", ".txt", ".md", ".org"):
            path = tmp_path / "prompts" / f"doc{ext}"
            path.write_bytes(f"content{ext}".encode())
            assert target.read_deployed_bytes("prompt", "doc") == (
                f"content{ext}".encode()
            )
            path.unlink()

    def test_returns_none_when_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.read_deployed_bytes("prompt", "missing") is None

    def test_returns_none_for_non_prompt(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert target.read_deployed_bytes("agent", "x") is None
        assert target.read_deployed_bytes("command", "x") is None
        assert target.read_deployed_bytes("skill", "x") is None

    def test_prefers_artifact_from_would_deploy_bytes(self, tmp_path: Path):
        """A user-authored stem-sibling earlier in the probe order must not
        shadow the artifact deploy owns (B29): after would_deploy_bytes()
        establishes the expected extension, the reader uses exactly that."""
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "doc.json").write_bytes(b"user file")
        (tmp_path / "prompts" / "doc.md").write_bytes(b"deployed")
        src = tmp_path / "doc.md"
        src.write_bytes(b"deployed")
        target.would_deploy_bytes("prompt", "doc", b"deployed", src)
        assert target.read_deployed_bytes("prompt", "doc") == b"deployed"

    def test_expected_artifact_missing_returns_none(self, tmp_path: Path):
        """When the expected artifact is absent, a stem-sibling at another
        extension must not be reported in its place."""
        target = _make_target(tmp_path)
        (tmp_path / "prompts" / "doc.json").write_bytes(b"user file")
        src = tmp_path / "doc.md"
        src.write_bytes(b"# new\n")
        target.would_deploy_bytes("prompt", "doc", b"# new\n", src)
        assert target.read_deployed_bytes("prompt", "doc") is None


def test_base_settings_methods_are_noops(tmp_path):
    from promptdeploy.targets.gptel import GptelTarget

    d = tmp_path / "g"
    d.mkdir()
    t = GptelTarget("g", d)
    # Inherited no-ops must not raise and read returns {}.
    t.deploy_settings({"a": 1}, [])
    t.remove_settings(["a"])
    assert t.read_settings_json() == {}
