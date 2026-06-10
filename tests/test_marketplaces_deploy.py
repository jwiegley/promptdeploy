"""Integration tests for marketplace deploy/remove in the Claude target."""

import json
from pathlib import Path

from promptdeploy.targets.claude import ClaudeTarget


def _make_target(tmp_path: Path) -> ClaudeTarget:
    config = tmp_path / ".claude"
    config.mkdir()
    return ClaudeTarget("my-target", config)


def _settings(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".claude" / "settings.json").read_text())


_ACME = {
    "name": "acme",
    "description": "Acme marketplace",
    "source": {"source": "github", "repo": "acme/plugins"},
    "autoUpdate": True,
    "plugins": {"formatter": True, "linter": False},
}


class TestDeployMarketplace:
    def test_creates_marketplace_and_plugins(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)

        s = _settings(tmp_path)
        assert s["extraKnownMarketplaces"]["acme"] == {
            "source": {"source": "github", "repo": "acme/plugins"},
            "autoUpdate": True,
        }
        assert s["enabledPlugins"] == {
            "formatter@acme": True,
            "linter@acme": False,
        }

    def test_creates_settings_if_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        assert (tmp_path / ".claude" / "settings.json").exists()

    def test_autoupdate_omitted_when_absent(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace(
            "acme",
            {"source": {"source": "git", "url": "https://x"}, "plugins": {}},
        )
        entry = _settings(tmp_path)["extraKnownMarketplaces"]["acme"]
        assert entry == {"source": {"source": "git", "url": "https://x"}}
        assert "autoUpdate" not in entry

    def test_autoupdate_false_is_written(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace(
            "acme",
            {"source": {"source": "git", "url": "https://x"}, "autoUpdate": False},
        )
        entry = _settings(tmp_path)["extraKnownMarketplaces"]["acme"]
        assert entry["autoUpdate"] is False

    def test_builtin_source_less_writes_only_plugins(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace(
            "claude-plugins-official",
            {"plugins": {"official-plugin": True}},
        )
        s = _settings(tmp_path)
        assert "extraKnownMarketplaces" not in s
        assert s["enabledPlugins"] == {"official-plugin@claude-plugins-official": True}

    def test_plugin_false_values_written(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", {"plugins": {"p": False}})
        assert _settings(tmp_path)["enabledPlugins"] == {"p@acme": False}

    def test_source_copy_is_independent(self, tmp_path: Path):
        target = _make_target(tmp_path)
        cfg = {"source": {"source": "github", "repo": "acme/plugins"}}
        target.deploy_marketplace("acme", cfg)
        # Mutating the original config must not affect deployed settings.
        cfg["source"]["repo"] = "evil/repo"
        entry = _settings(tmp_path)["extraKnownMarketplaces"]["acme"]
        assert entry["source"]["repo"] == "acme/plugins"

    def test_merges_with_existing_settings(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"effortLevel": "low"}))
        target.deploy_marketplace("acme", _ACME)
        s = _settings(tmp_path)
        assert s["effortLevel"] == "low"
        assert "acme" in s["extraKnownMarketplaces"]

    def test_redeploy_is_idempotent(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        first = _settings(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        assert _settings(tmp_path) == first

    def test_redeploy_drops_removed_plugins(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        target.deploy_marketplace(
            "acme",
            {"source": {"source": "github", "repo": "acme/plugins"}, "plugins": {}},
        )
        s = _settings(tmp_path)
        # All acme plugins were dropped; enabledPlugins became empty and was removed.
        assert "enabledPlugins" not in s
        assert "acme" in s["extraKnownMarketplaces"]

    def test_disabled_removes_entries(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        target.deploy_marketplace("acme", {**_ACME, "enabled": False})
        s = _settings(tmp_path)
        assert "extraKnownMarketplaces" not in s
        assert "enabledPlugins" not in s

    def test_empty_dict_cleanup(self, tmp_path: Path):
        target = _make_target(tmp_path)
        # A marketplace with neither source nor plugins leaves nothing behind.
        target.deploy_marketplace("acme", {"plugins": {}})
        s = _settings(tmp_path)
        assert "extraKnownMarketplaces" not in s
        assert "enabledPlugins" not in s

    def test_other_marketplaces_survive_deploy(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        target.deploy_marketplace(
            "beta",
            {"source": {"source": "git", "url": "https://b"}, "plugins": {"q": True}},
        )
        s = _settings(tmp_path)
        assert set(s["extraKnownMarketplaces"]) == {"acme", "beta"}
        assert s["enabledPlugins"]["q@beta"] is True
        assert s["enabledPlugins"]["formatter@acme"] is True

    def test_unrelated_enabled_plugins_preserved(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"enabledPlugins": {"foreign@other-market": True}})
        )
        target.deploy_marketplace("acme", _ACME)
        s = _settings(tmp_path)
        assert s["enabledPlugins"]["foreign@other-market"] is True
        assert s["enabledPlugins"]["formatter@acme"] is True

    def test_prefix_collision_ownership(self, tmp_path: Path):
        """A marketplace named 'official' must not claim 'y@plugins-official'."""
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "enabledPlugins": {
                        "x@official": True,
                        "y@plugins-official": True,
                    }
                }
            )
        )
        # Redeploy 'official' with no plugins -> only x@official is reclaimed.
        target.deploy_marketplace("official", {"plugins": {}})
        s = _settings(tmp_path)
        assert "x@official" not in s["enabledPlugins"]
        assert s["enabledPlugins"]["y@plugins-official"] is True

    def test_non_dict_existing_keys_are_coerced(self, tmp_path: Path):
        # A hand-edit/TUI may leave a non-dict under either key; the re-add
        # phase must replace it rather than crash on item assignment.
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"extraKnownMarketplaces": "garbage", "enabledPlugins": ["a"]})
        )
        target.deploy_marketplace("acme", _ACME)
        s = _settings(tmp_path)
        assert s["extraKnownMarketplaces"]["acme"]["source"]["repo"] == "acme/plugins"
        assert s["enabledPlugins"] == {"formatter@acme": True, "linter@acme": False}

    def test_non_dict_plugins_ignored(self, tmp_path: Path):
        # A malformed YAML 'plugins' (list/scalar) must not crash the deploy;
        # the source is still written and no enabledPlugins entries are added.
        target = _make_target(tmp_path)
        target.deploy_marketplace(
            "acme",
            {"source": {"source": "github", "repo": "acme/plugins"}, "plugins": ["a"]},
        )
        s = _settings(tmp_path)
        assert s["extraKnownMarketplaces"]["acme"]["source"]["repo"] == "acme/plugins"
        assert "enabledPlugins" not in s


class TestRemoveMarketplace:
    def test_removes_marketplace_and_plugins(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        target.remove_marketplace("acme")
        s = _settings(tmp_path)
        assert "extraKnownMarketplaces" not in s
        assert "enabledPlugins" not in s

    def test_no_error_if_settings_missing(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.remove_marketplace("nonexistent")  # must not raise
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_preserves_other_marketplaces(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        target.deploy_marketplace(
            "beta",
            {"source": {"source": "git", "url": "https://b"}, "plugins": {"q": True}},
        )
        target.remove_marketplace("acme")
        s = _settings(tmp_path)
        assert set(s["extraKnownMarketplaces"]) == {"beta"}
        assert s["enabledPlugins"] == {"q@beta": True}

    def test_preserves_unrelated_enabled_plugins(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"enabledPlugins": {"foreign@other-market": True}})
        )
        target.deploy_marketplace("acme", _ACME)
        target.remove_marketplace("acme")
        s = _settings(tmp_path)
        assert s["enabledPlugins"] == {"foreign@other-market": True}

    def test_preserves_other_top_level_keys(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"effortLevel": "low"}))
        target.deploy_marketplace("acme", _ACME)
        target.remove_marketplace("acme")
        assert _settings(tmp_path) == {"effortLevel": "low"}


class TestItemExistsMarketplace:
    def test_exists_via_marketplace_entry(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace(
            "acme", {"source": {"source": "git", "url": "https://x"}}
        )
        assert target.item_exists("marketplace", "acme")

    def test_exists_via_enabled_plugin(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", {"plugins": {"p": True}})
        assert target.item_exists("marketplace", "acme")

    def test_absent_when_unknown(self, tmp_path: Path):
        target = _make_target(tmp_path)
        target.deploy_marketplace("acme", _ACME)
        assert not target.item_exists("marketplace", "beta")

    def test_prefix_collision_not_matched(self, tmp_path: Path):
        target = _make_target(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps({"enabledPlugins": {"y@plugins-official": True}})
        )
        assert not target.item_exists("marketplace", "official")
        assert target.item_exists("marketplace", "plugins-official")

    def test_absent_when_no_settings(self, tmp_path: Path):
        target = _make_target(tmp_path)
        assert not target.item_exists("marketplace", "acme")
