"""Tests for models-related deploy orchestration paths."""

import json
from pathlib import Path

import yaml

from promptdeploy.config import Config, TargetConfig
from promptdeploy.deploy import _filter_models_config, deploy


def _make_config(
    source_root: Path,
    targets: dict[str, TargetConfig],
    groups: dict | None = None,
) -> Config:
    return Config(source_root=source_root, targets=targets, groups=groups or {})


def _make_claude_target(tmp_path: Path, target_id: str = "test-claude") -> TargetConfig:
    target_dir = tmp_path / target_id
    target_dir.mkdir()
    return TargetConfig(id=target_id, type="claude", path=target_dir)


def _make_droid_target(tmp_path: Path, target_id: str = "test-droid") -> TargetConfig:
    target_dir = tmp_path / target_id
    target_dir.mkdir()
    return TargetConfig(id=target_id, type="droid", path=target_dir)


# ------------------------------------------------------------------
# _filter_models_config
# ------------------------------------------------------------------


class TestFilterModelsConfig:
    def test_providers_filtered_by_only(self):
        config_dict = {
            "providers": {
                "prov_a": {
                    "display_name": "A",
                    "only": ["target-a"],
                    "models": {"m1": {}},
                },
                "prov_b": {
                    "display_name": "B",
                    "models": {"m2": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        assert "prov_a" in result["providers"]
        assert "prov_b" in result["providers"]

        result_b = _filter_models_config(config_dict, "target-b", config)
        assert "prov_a" not in result_b["providers"]
        assert "prov_b" in result_b["providers"]

    def test_providers_filtered_by_except(self):
        config_dict = {
            "providers": {
                "prov_a": {
                    "display_name": "A",
                    "except": ["target-b"],
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={},
        )
        result_a = _filter_models_config(config_dict, "target-a", config)
        assert "prov_a" in result_a["providers"]

        result_b = _filter_models_config(config_dict, "target-b", config)
        assert "prov_a" not in result_b["providers"]

    def test_models_within_provider_filtered_by_only(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "models": {
                        "m1": {"only": ["target-a"]},
                        "m2": {},
                    },
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={},
        )
        result_a = _filter_models_config(config_dict, "target-a", config)
        assert "m1" in result_a["providers"]["prov"]["models"]
        assert "m2" in result_a["providers"]["prov"]["models"]

        result_b = _filter_models_config(config_dict, "target-b", config)
        # m1 excluded, m2 included -> provider still present
        assert "m1" not in result_b["providers"]["prov"]["models"]
        assert "m2" in result_b["providers"]["prov"]["models"]

    def test_models_within_provider_filtered_by_except(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "models": {
                        "m1": {"except": ["target-a"]},
                        "m2": {},
                    },
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        assert "m1" not in result["providers"]["prov"]["models"]
        assert "m2" in result["providers"]["prov"]["models"]

    def test_provider_excluded_when_no_models_match(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "models": {
                        "m1": {"only": ["other"]},
                    },
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "other": TargetConfig(
                    id="other", type="claude", path=Path("/tmp/other")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        assert result["providers"] == {}

    def test_empty_providers_dict(self):
        config = Config(
            source_root=Path("/tmp"),
            targets={"t": TargetConfig(id="t", type="claude", path=Path("/tmp/t"))},
            groups={},
        )
        result = _filter_models_config({"providers": {}}, "t", config)
        assert result == {"providers": {}}

    def test_none_model_values_handled(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "models": {
                        "m1": None,
                    },
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={"t": TargetConfig(id="t", type="claude", path=Path("/tmp/t"))},
            groups={},
        )
        result = _filter_models_config(config_dict, "t", config)
        assert "m1" in result["providers"]["prov"]["models"]

    def test_group_expansion_in_only(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "only": ["my-group"],
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={"my-group": ["target-a"]},
        )
        result_a = _filter_models_config(config_dict, "target-a", config)
        assert "prov" in result_a["providers"]

        result_b = _filter_models_config(config_dict, "target-b", config)
        assert "prov" not in result_b["providers"]

    def test_missing_providers_key(self):
        config = Config(
            source_root=Path("/tmp"),
            targets={"t": TargetConfig(id="t", type="claude", path=Path("/tmp/t"))},
            groups={},
        )
        result = _filter_models_config({}, "t", config)
        assert result == {"providers": {}}

    def test_overrides_applied_to_matching_target(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "base_url": "https://default.example/v1/",
                    "overrides": {
                        "target-a": {"base_url": "http://localhost:4000/v1/"},
                    },
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={},
        )
        # Matching target gets the override
        result_a = _filter_models_config(config_dict, "target-a", config)
        assert result_a["providers"]["prov"]["base_url"] == "http://localhost:4000/v1/"
        # Non-matching target keeps the default
        result_b = _filter_models_config(config_dict, "target-b", config)
        assert (
            result_b["providers"]["prov"]["base_url"] == "https://default.example/v1/"
        )
        # The 'overrides' key itself is stripped from the output
        assert "overrides" not in result_a["providers"]["prov"]
        assert "overrides" not in result_b["providers"]["prov"]

    def test_overrides_match_via_group_expansion(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "base_url": "https://default.example/v1/",
                    "overrides": {
                        "vulcan-group": {"base_url": "http://localhost:4000/v1/"},
                    },
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
                "target-b": TargetConfig(
                    id="target-b", type="claude", path=Path("/tmp/b")
                ),
            },
            groups={"vulcan-group": ["target-a"]},
        )
        result_a = _filter_models_config(config_dict, "target-a", config)
        assert result_a["providers"]["prov"]["base_url"] == "http://localhost:4000/v1/"
        result_b = _filter_models_config(config_dict, "target-b", config)
        assert (
            result_b["providers"]["prov"]["base_url"] == "https://default.example/v1/"
        )

    def test_overrides_models_and_overrides_keys_ignored(self):
        # An override entry may not redefine models or nested overrides.
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "base_url": "https://default.example/v1/",
                    "overrides": {
                        "target-a": {
                            "base_url": "http://localhost:4000/v1/",
                            "models": {"injected": {}},
                            "overrides": {"target-b": {}},
                        },
                    },
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        prov = result["providers"]["prov"]
        assert prov["base_url"] == "http://localhost:4000/v1/"
        # Original models survive; override's "models" was ignored
        assert set(prov["models"].keys()) == {"m1"}
        # No nested overrides leak through
        assert "overrides" not in prov

    def test_overrides_non_dict_entry_skipped(self):
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "base_url": "https://default.example/v1/",
                    "overrides": {
                        "target-a": "not-a-dict",
                    },
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        # Non-dict override entry is silently skipped; defaults remain
        assert result["providers"]["prov"]["base_url"] == "https://default.example/v1/"

    def test_overrides_non_dict_value_returns_provider_unchanged(self):
        # When the entire ``overrides`` field is malformed, no merging
        # happens but the field is still stripped.
        config_dict = {
            "providers": {
                "prov": {
                    "display_name": "P",
                    "base_url": "https://default.example/v1/",
                    "overrides": "not-a-dict",
                    "models": {"m1": {}},
                },
            }
        }
        config = Config(
            source_root=Path("/tmp"),
            targets={
                "target-a": TargetConfig(
                    id="target-a", type="claude", path=Path("/tmp/a")
                ),
            },
            groups={},
        )
        result = _filter_models_config(config_dict, "target-a", config)
        prov = result["providers"]["prov"]
        assert prov["base_url"] == "https://default.example/v1/"
        assert "overrides" not in prov


# ------------------------------------------------------------------
# Deploy integration for models
# ------------------------------------------------------------------


class TestModelsDeployIntegration:
    def _write_models_yaml(self, src: Path, content: dict) -> None:
        (src / "models.yaml").write_text(yaml.dump(content))

    def test_full_deploy_with_models_creates_action(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        self._write_models_yaml(
            src,
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://api.acme.com/v1",
                        "api_key": "sk-test",
                        "models": {
                            "gpt-4": {"display_name": "GPT-4"},
                        },
                    },
                },
            },
        )
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)

        creates = [
            a for a in actions if a.action == "create" and a.item_type == "models"
        ]
        assert len(creates) == 1
        assert creates[0].name == "models"

    def test_models_deploy_calls_target_deploy_models(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        models_config = {
            "providers": {
                "acme": {
                    "display_name": "Acme",
                    "base_url": "https://api.acme.com/v1",
                    "api_key": "sk-test",
                    "models": {
                        "gpt-4": {"display_name": "GPT-4"},
                    },
                },
            },
        }
        self._write_models_yaml(src, models_config)
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)

        # Verify settings.json was written with customModels
        settings_path = tc.path / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "customModels" in settings
        assert len(settings["customModels"]) == 1

    def test_models_removal_when_models_deleted(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        self._write_models_yaml(
            src,
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://api.acme.com/v1",
                        "api_key": "sk-test",
                        "models": {"gpt-4": {}},
                    },
                },
            },
        )
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        deploy(config)
        # Now remove models.yaml
        (src / "models.yaml").unlink()
        actions = deploy(config)

        removes = [
            a for a in actions if a.action == "remove" and a.item_type == "models"
        ]
        assert len(removes) == 1

    def test_dry_run_does_not_call_deploy_models(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        self._write_models_yaml(
            src,
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://api.acme.com/v1",
                        "api_key": "sk-test",
                        "models": {"gpt-4": {}},
                    },
                },
            },
        )
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, dry_run=True)

        creates = [
            a for a in actions if a.action == "create" and a.item_type == "models"
        ]
        assert len(creates) == 1
        # No settings.json should have been created
        assert not (tc.path / "settings.json").exists()

    def test_only_type_models_filters_correctly(self, tmp_path: Path):
        src = tmp_path / "source"
        src.mkdir()
        # Create an agent too
        agents = src / "agents"
        agents.mkdir()
        (agents / "helper.md").write_bytes(b"---\nname: helper\n---\nBody.\n")
        self._write_models_yaml(
            src,
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://api.acme.com/v1",
                        "api_key": "sk-test",
                        "models": {"gpt-4": {}},
                    },
                },
            },
        )
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        actions = deploy(config, item_types=["models"])

        types = {a.item_type for a in actions}
        assert types == {"models"}

    def test_deploy_mcp_item_calls_deploy_mcp_server(self, tmp_path: Path):
        """Covers deploy.py line 103-104: elif item.item_type == 'mcp'."""
        src = tmp_path / "source"
        src.mkdir()
        mcp_dir = src / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "srv.yaml").write_bytes(b"name: srv\ncommand: echo\n")

        tc = _make_claude_target(tmp_path)
        config = _make_config(src, {tc.id: tc})
        actions = deploy(config)

        creates = [a for a in actions if a.action == "create" and a.item_type == "mcp"]
        assert len(creates) == 1

    def test_remove_models_category(self, tmp_path: Path):
        """Covers deploy.py lines 117-118: elif category == 'models': target.remove_models()."""
        src = tmp_path / "source"
        src.mkdir()
        self._write_models_yaml(
            src,
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://api.acme.com/v1",
                        "api_key": "sk-test",
                        "droid": {},
                        "models": {"gpt-4": {}},
                    },
                },
            },
        )
        tc = _make_droid_target(tmp_path)
        config = _make_config(src, {tc.id: tc})

        # Deploy first, then remove source
        deploy(config)
        settings = json.loads((tc.path / "settings.json").read_text())
        assert "customModels" in settings

        (src / "models.yaml").unlink()
        deploy(config)

        # After removal, customModels should be gone
        settings = json.loads((tc.path / "settings.json").read_text())
        assert "customModels" not in settings
