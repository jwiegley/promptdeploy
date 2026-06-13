"""Integration tests for hook deploy/remove in the Claude target."""

import json
from pathlib import Path

from promptdeploy.targets.claude import ClaudeTarget


def _make_target(tmp_path: Path) -> ClaudeTarget:
    config = tmp_path / ".claude"
    config.mkdir()
    return ClaudeTarget("my-target", config)


_GIT_AI_CONFIG = {
    "name": "git-ai",
    "description": "Git AI checkpoint hooks",
    "only": ["claude"],
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Write|Edit|MultiEdit",
                "hooks": [
                    {
                        "command": "git-ai checkpoint claude --hook-input stdin",
                        "type": "command",
                    }
                ],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Write|Edit|MultiEdit",
                "hooks": [
                    {
                        "command": "git-ai checkpoint claude --hook-input stdin",
                        "type": "command",
                    }
                ],
            }
        ],
    },
}


class TestDeployHook:
    def test_creates_hooks_in_settings(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "hooks" in settings
        post = settings["hooks"]["PostToolUse"]
        assert len(post) == 1
        assert post[0]["matcher"] == "Write|Edit|MultiEdit"
        assert post[0]["_source"] == "git-ai"

    def test_creates_settings_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_merges_with_existing_settings(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"allowedTools": ["Edit"]}))

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        assert settings["allowedTools"] == ["Edit"]
        assert "hooks" in settings

    def test_preserves_other_hook_sources(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        sources = {e["_source"] for e in post}
        assert "git-ai" in sources

    def test_replaces_existing_entries_on_redeploy(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        # Only one entry per event type (no duplicates)
        git_ai_entries = [e for e in post if e.get("_source") == "git-ai"]
        assert len(git_ai_entries) == 1

    def test_deploy_empty_hooks_config(self, tmp_path: Path):
        target = _make_target(tmp_path)
        config = {"name": "empty", "hooks": {}}
        target.deploy_hook("empty", config)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        # hooks key should be absent when empty
        assert "hooks" not in settings

    def test_redeploy_cleans_stale_event_types(self, tmp_path: Path):
        """If a hook group drops an event type in a new version, the old
        entries for that event type are cleaned up during redeploy."""
        target = _make_target(tmp_path)
        # Deploy with both PostToolUse and PreToolUse
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "PreToolUse" in settings["hooks"]

        # Redeploy with only PostToolUse (PreToolUse dropped)
        hooks = _GIT_AI_CONFIG["hooks"]
        assert isinstance(hooks, dict)
        updated = {
            "hooks": {
                "PostToolUse": hooks["PostToolUse"],
            },
        }
        target.deploy_hook("git-ai", updated)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "PostToolUse" in settings["hooks"]
        assert "PreToolUse" not in settings["hooks"]

    def test_deploy_adds_source_tag_to_each_entry(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        for event_entries in settings["hooks"].values():
            for entry in event_entries:
                assert "_source" in entry
                assert entry["_source"] == "git-ai"

    def test_deploy_removes_pre_existing_duplicates(self, tmp_path: Path):
        """Pre-existing entries without _source that match new entries are removed."""
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"

        # Simulate a manually-installed hook (no _source tag)
        manual_hooks = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write|Edit|MultiEdit",
                        "hooks": [
                            {
                                "command": (
                                    "git-ai checkpoint claude --hook-input stdin"
                                ),
                                "type": "command",
                            }
                        ],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(manual_hooks))

        # Deploy the same hook via promptdeploy
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        post = settings["hooks"]["PostToolUse"]
        # Should have exactly one entry (with _source), not two
        assert len(post) == 1
        assert post[0]["_source"] == "git-ai"

    def test_deploy_no_hooks_key_in_config(self, tmp_path: Path):
        """When config has no 'hooks' key, nothing is written to hooks."""
        target = _make_target(tmp_path)
        config = {"name": "minimal"}
        target.deploy_hook("minimal", config)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        # Empty hooks dict is cleaned up
        assert "hooks" not in settings


class TestRemoveHook:
    def test_removes_entries_by_source(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        target.remove_hook("git-ai")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings.get("hooks", {}).get("PostToolUse", [])
        sources = {e.get("_source") for e in post}
        assert "git-ai" not in sources

    def test_removes_empty_event_type_keys(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        target.remove_hook("git-ai")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        # Both PostToolUse and PreToolUse should be removed
        assert "PostToolUse" not in settings.get("hooks", {})
        assert "PreToolUse" not in settings.get("hooks", {})

    def test_removes_hooks_key_when_all_empty(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        target.remove_hook("git-ai")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "hooks" not in settings

    def test_no_error_if_settings_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # No settings.json created - remove_hook should be safe
        target.remove_hook("nonexistent")

    def test_no_error_if_hook_not_present(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)
        # Remove a hook that was never deployed
        target.remove_hook("nonexistent")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "hooks" in settings

    def test_no_error_if_hooks_key_not_a_dict(self, tmp_path: Path):
        """If settings.json has 'hooks' as non-dict, remove_hook is safe."""
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": "not-a-dict"}))

        target.remove_hook("git-ai")  # should not raise

    def test_preserves_other_settings_on_remove(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"allowedTools": ["Edit"]}))

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)
        target.remove_hook("git-ai")

        settings = json.loads(settings_path.read_text())
        assert settings["allowedTools"] == ["Edit"]
        assert "hooks" not in settings

    def test_partial_removal_keeps_other_event_types(self, tmp_path: Path):
        """Removing one hook source keeps other sources' event types intact."""
        target = _make_target(tmp_path)
        target.deploy_hook("git-ai", _GIT_AI_CONFIG)
        target.deploy_hook("session-logger", _SESSION_LOGGER_CONFIG)

        target.remove_hook("session-logger")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        # session-logger's event type is gone...
        assert "Stop" not in settings["hooks"]
        # ...but git-ai's event types and entries are untouched.
        assert "PostToolUse" in settings["hooks"]
        assert "PreToolUse" in settings["hooks"]
        post_sources = {e["_source"] for e in settings["hooks"]["PostToolUse"]}
        assert post_sources == {"git-ai"}


def _hook_group(name: str, event: str, command: str) -> dict:
    return {
        "name": name,
        "hooks": {
            event: [
                {
                    "matcher": "Write|Edit",
                    "hooks": [{"command": command, "type": "command"}],
                }
            ]
        },
    }


_SESSION_LOGGER_CONFIG = _hook_group("session-logger", "Stop", "log-session stop")


class TestMultiSourceHooks:
    """Coexistence matrix for multiple hook groups on the same event type."""

    def test_different_contents_coexist(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", "cmd-a"))
        target.deploy_hook("group-b", _hook_group("group-b", "PostToolUse", "cmd-b"))

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        assert {e["_source"] for e in post} == {"group-a", "group-b"}
        assert len(post) == 2

    def test_redeploy_one_group_leaves_other_untouched(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", "cmd-a"))
        target.deploy_hook("group-b", _hook_group("group-b", "PostToolUse", "cmd-b"))

        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", "cmd-a2"))

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        by_source = {e["_source"]: e for e in post}
        assert set(by_source) == {"group-a", "group-b"}
        assert by_source["group-a"]["hooks"][0]["command"] == "cmd-a2"
        assert by_source["group-b"]["hooks"][0]["command"] == "cmd-b"

    def test_remove_one_group_leaves_other(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", "cmd-a"))
        target.deploy_hook("group-b", _hook_group("group-b", "PostToolUse", "cmd-b"))

        target.remove_hook("group-a")

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        assert {e["_source"] for e in post} == {"group-b"}

    def test_identical_contents_keep_one_entry_per_group(self, tmp_path: Path):
        """Two groups deploying byte-identical hook content each keep their
        own tagged entry. De-duplication must not steal another group's
        entry: if it did, removing the second group would delete a hook the
        first group's manifest still claims."""
        target = _make_target(tmp_path)
        same = "shared-command --hook-input stdin"
        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", same))
        target.deploy_hook("group-b", _hook_group("group-b", "PostToolUse", same))

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        assert {e["_source"] for e in post} == {"group-a", "group-b"}
        assert len(post) == 2

        # Removing group-b leaves group-a's identical hook installed.
        target.remove_hook("group-b")
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post = settings["hooks"]["PostToolUse"]
        assert [e["_source"] for e in post] == ["group-a"]

    def test_redeploy_still_dedupes_own_and_untagged_entries(self, tmp_path: Path):
        """Within a group, redeploying replaces both its own tagged entries
        and matching untagged (hand-installed) duplicates."""
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        manual = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"command": "cmd-a", "type": "command"}],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(manual))

        target.deploy_hook("group-a", _hook_group("group-a", "PostToolUse", "cmd-a"))

        settings = json.loads(settings_path.read_text())
        post = settings["hooks"]["PostToolUse"]
        assert len(post) == 1
        assert post[0]["_source"] == "group-a"


class TestDeployHookMalformedSettings:
    """deploy_hook must tolerate hand-edited settings.json shapes (B25)."""

    def test_hooks_key_not_a_dict_is_replaced(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": "not-a-dict"}))

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        assert isinstance(settings["hooks"], dict)
        assert settings["hooks"]["PostToolUse"][0]["_source"] == "git-ai"

    def test_event_value_not_a_list_is_replaced(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {"PostToolUse": "junk"}}))

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        post = settings["hooks"]["PostToolUse"]
        assert isinstance(post, list)
        assert post[0]["_source"] == "git-ai"

    def test_non_dict_entries_are_preserved(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"hooks": {"PostToolUse": ["stray-string"]}})
        )

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        post = settings["hooks"]["PostToolUse"]
        assert "stray-string" in post
        assert any(isinstance(e, dict) and e.get("_source") == "git-ai" for e in post)

    def test_stale_non_list_event_survives_strip_pass(self, tmp_path: Path):
        # A non-list event value not present in the new config is left alone.
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {"Stop": "junk"}}))

        target.deploy_hook("git-ai", _GIT_AI_CONFIG)

        settings = json.loads(settings_path.read_text())
        assert settings["hooks"]["Stop"] == "junk"
        assert "PostToolUse" in settings["hooks"]
