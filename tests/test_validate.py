"""Tests for promptdeploy validation."""

from pathlib import Path

import pytest

from promptdeploy.config import Config, TargetConfig
from promptdeploy.source import SourceItem
from promptdeploy.validate import ValidationIssue, validate_all, validate_item


@pytest.fixture
def config() -> Config:
    targets = {
        "claude-personal": TargetConfig(
            id="claude-personal", type="claude", path=Path("/tmp/claude-personal")
        ),
        "droid": TargetConfig(id="droid", type="droid", path=Path("/tmp/droid")),
    }
    groups = {"claude": ["claude-personal"]}
    return Config(source_root=Path("/tmp/src"), targets=targets, groups=groups)


class TestValidateItem:
    def test_valid_agent_no_frontmatter(self, config: Config) -> None:
        item = SourceItem(
            "agent", "test", Path("/tmp/test.md"), None, b"No frontmatter"
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_valid_agent_with_frontmatter(self, config: Config) -> None:
        content = b"---\nname: test\ndescription: A test agent\n---\nBody text"
        item = SourceItem(
            "agent", "test", Path("/tmp/test.md"), {"name": "test"}, content
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_invalid_yaml_frontmatter(self, config: Config) -> None:
        content = b"---\ninvalid: yaml: [broken\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert issues[0].level == "error"
        assert "Invalid YAML" in issues[0].message

    def test_both_only_and_except(self, config: Config) -> None:
        content = b"---\nonly:\n  - droid\nexcept:\n  - claude-personal\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert any("Cannot specify both" in i.message for i in issues)

    def test_invalid_env_in_only(self, config: Config) -> None:
        content = b"---\nonly:\n  - nonexistent\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert "Invalid environment ID 'nonexistent'" in issues[0].message
        assert "'only'" in issues[0].message

    def test_invalid_env_in_except(self, config: Config) -> None:
        content = b"---\nexcept:\n  - bogus\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert "Invalid environment ID 'bogus'" in issues[0].message
        assert "'except'" in issues[0].message

    def test_only_not_a_list(self, config: Config) -> None:
        content = b"---\nonly: not-a-list\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert any("'only' must be a list" in i.message for i in issues)

    def test_except_not_a_list(self, config: Config) -> None:
        content = b"---\nexcept: not-a-list\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert any("'except' must be a list" in i.message for i in issues)

    def test_valid_only_with_group(self, config: Config) -> None:
        content = b"---\nonly:\n  - claude\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert issues == []

    def test_valid_except_with_target(self, config: Config) -> None:
        content = b"---\nexcept:\n  - droid\n---\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert issues == []


class TestValidateItemMcp:
    def test_valid_mcp_with_command(self, config: Config) -> None:
        content = b"name: test-mcp\ncommand: npx\nargs:\n  - test-server\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert issues == []

    def test_valid_mcp_with_url(self, config: Config) -> None:
        content = b"name: test-mcp\nurl: https://example.com/mcp\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert issues == []

    def test_mcp_missing_name(self, config: Config) -> None:
        content = b"command: npx\nargs:\n  - test-server\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("missing 'name'" in i.message for i in issues)

    def test_mcp_missing_command_and_url(self, config: Config) -> None:
        content = b"name: test-mcp\nargs:\n  - test-server\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("missing 'command' or 'url'" in i.message for i in issues)

    def test_mcp_invalid_yaml(self, config: Config) -> None:
        content = b"name: [broken yaml\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert issues[0].level == "error"
        assert "Invalid YAML" in issues[0].message

    def test_mcp_non_dict_yaml(self, config: Config) -> None:
        content = b"just a string\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        # Non-mapping YAML is an error: deploy would otherwise write junk
        # entries like ``"mcpServers": {"test-mcp": {}}`` into settings.json.
        assert len(issues) == 1
        assert issues[0].level == "error"
        assert "must be a YAML mapping" in issues[0].message

    def test_mcp_list_yaml_is_error(self, config: Config) -> None:
        content = b"- name: test-mcp\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("must be a YAML mapping" in i.message for i in issues)

    def test_mcp_empty_file_is_error(self, config: Config) -> None:
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, b"")
        issues = validate_item(item, config)
        assert any("must be a YAML mapping" in i.message for i in issues)

    def test_mcp_enabled_bool_accepted(self, config: Config) -> None:
        content = b"name: test-mcp\ncommand: npx\nenabled: false\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        assert validate_item(item, config) == []

    def test_mcp_enabled_non_bool_is_error(self, config: Config) -> None:
        # All targets gate on truthiness, so the string "false" would
        # silently deploy a server the author meant to disable.
        content = b'name: test-mcp\ncommand: npx\nenabled: "false"\n'
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "'enabled' must be a boolean" in i.message
            for i in issues
        )

    def test_mcp_with_only_filter(self, config: Config) -> None:
        content = b"name: test-mcp\ncommand: npx\nonly:\n  - droid\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert issues == []

    def test_mcp_with_invalid_only_filter(self, config: Config) -> None:
        content = b"name: test-mcp\ncommand: npx\nonly:\n  - nonexistent\n"
        item = SourceItem("mcp", "test-mcp", Path("/tmp/mcp/test.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("Invalid environment ID" in i.message for i in issues)


class TestValidateItemModels:
    def _make_models_item(self, content_dict: dict) -> SourceItem:
        import yaml

        content = yaml.dump(content_dict).encode("utf-8")
        return SourceItem(
            "models", "models", Path("/tmp/models.yaml"), content_dict, content
        )

    def test_valid_models_yaml(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "sk-test",
                        "models": {"gpt-4": {"display_name": "GPT-4"}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_missing_providers_key(self, config: Config) -> None:
        item = self._make_models_item({"something_else": "value"})
        issues = validate_item(item, config)
        assert any("missing or empty 'providers'" in i.message for i in issues)

    def test_empty_providers(self, config: Config) -> None:
        item = self._make_models_item({"providers": {}})
        issues = validate_item(item, config)
        assert any("missing or empty 'providers'" in i.message for i in issues)

    def test_provider_not_a_dict(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {"bad": "not-a-dict"},
            }
        )
        issues = validate_item(item, config)
        assert any("must be a mapping" in i.message for i in issues)

    def test_missing_required_fields(self, config: Config) -> None:
        # Provider has a droid: subsection, so all three fields are required.
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "droid": {"type": "openai"},
                        "models": {"m": {}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'display_name'" in m for m in messages)
        assert any("'base_url'" in m for m in messages)
        assert any("'api_key'" in m for m in messages)

    def test_claude_only_provider_does_not_require_credentials(
        self, config: Config
    ) -> None:
        # A provider with only a claude: subsection (no droid:, no opencode:)
        # does not need base_url or api_key — Claude Code reads no credentials
        # from models.yaml.
        item = self._make_models_item(
            {
                "providers": {
                    "anthropic": {
                        "display_name": "Anthropic",
                        "claude": {"default_model": "claude-opus-4-7"},
                        "models": {
                            "claude-opus-4-7": {"display_name": "Claude Opus 4.7"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert not any("'base_url'" in m for m in messages)
        assert not any("'api_key'" in m for m in messages)

    def test_claude_only_provider_still_requires_display_name(
        self, config: Config
    ) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "anthropic": {
                        "claude": {"default_model": "claude-opus-4-7"},
                        "models": {
                            "claude-opus-4-7": {"display_name": "Claude Opus 4.7"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'display_name'" in m for m in messages)

    def test_provider_with_opencode_subsection_requires_credentials(
        self, config: Config
    ) -> None:
        # A provider with opencode: (or droid:) still requires base_url and api_key.
        item = self._make_models_item(
            {
                "providers": {
                    "vendor": {
                        "display_name": "Vendor",
                        "opencode": {"type": "openai"},
                        "models": {"m": {"display_name": "M"}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        messages = [i.message for i in issues]
        assert any("'base_url'" in m for m in messages)
        assert any("'api_key'" in m for m in messages)

    def test_empty_models_in_provider(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("no models defined" in i.message for i in issues)

    def test_provider_level_only_and_except_mutually_exclusive(
        self, config: Config
    ) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "only": ["droid"],
                        "except": ["claude-personal"],
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any(
            "cannot specify both 'only' and 'except'" in i.message for i in issues
        )

    def test_provider_level_only_not_a_list(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "only": "droid",
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'only' must be a list" in i.message for i in issues)

    def test_provider_level_invalid_environment_id(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "only": ["nonexistent-env"],
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any(
            "invalid environment ID 'nonexistent-env'" in i.message for i in issues
        )

    def test_provider_level_except_not_a_list(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "except": "droid",
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'except' must be a list" in i.message for i in issues)

    def test_model_level_only_and_except_mutually_exclusive(
        self, config: Config
    ) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": {
                                "only": ["droid"],
                                "except": ["claude-personal"],
                            },
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any(
            "cannot specify both 'only' and 'except'" in i.message for i in issues
        )

    def test_model_level_only_not_a_list(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": {"only": "droid"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'only' must be a list" in i.message for i in issues)

    def test_model_level_invalid_environment_id(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": {"only": ["bogus-env"]},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("invalid environment ID 'bogus-env'" in i.message for i in issues)

    def test_model_level_except_not_a_list(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": {"except": "droid"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'except' must be a list" in i.message for i in issues)

    def test_model_level_except_invalid_environment_id(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": {"except": ["nonexistent"]},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("invalid environment ID 'nonexistent'" in i.message for i in issues)

    def test_model_not_a_dict_skipped_gracefully(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {
                            "m": "not-a-dict",
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        # Should not crash; no model-level only/except errors for non-dict models
        model_issues = [i for i in issues if "Model " in i.message]
        assert model_issues == []

    def test_providers_is_none(self, config: Config) -> None:
        item = self._make_models_item({"providers": None})
        issues = validate_item(item, config)
        assert any("missing or empty 'providers'" in i.message for i in issues)

    def test_valid_provider_level_only_with_group(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "only": ["claude"],
                    },
                },
            }
        )
        issues = validate_item(item, config)
        # "claude" is a valid group, so no environment ID errors
        env_issues = [i for i in issues if "invalid environment ID" in i.message]
        assert env_issues == []

    def test_provider_overrides_must_be_a_mapping(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "overrides": ["not-a-dict"],
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'overrides' must be a mapping" in i.message for i in issues)

    def test_provider_overrides_invalid_environment_id(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "overrides": {"nonexistent-env": {"base_url": "x"}},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any(
            "invalid environment ID 'nonexistent-env' in 'overrides'" in i.message
            for i in issues
        )

    def test_provider_overrides_entry_must_be_a_mapping(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "overrides": {"droid": "not-a-dict"},
                    },
                },
            }
        )
        issues = validate_item(item, config)
        assert any("'overrides.droid' must be a mapping" in i.message for i in issues)

    def test_provider_overrides_valid(self, config: Config) -> None:
        item = self._make_models_item(
            {
                "providers": {
                    "acme": {
                        "display_name": "Acme",
                        "base_url": "https://acme.com",
                        "api_key": "key",
                        "models": {"m": {}},
                        "overrides": {
                            "droid": {"base_url": "http://localhost:4000/v1/"},
                            "claude": {"base_url": "http://other/v1/"},
                        },
                    },
                },
            }
        )
        issues = validate_item(item, config)
        # Both target ID and group key resolve; no errors
        assert not any("overrides" in i.message for i in issues)


class TestValidateAll:
    def test_empty_source(self, tmp_path: Path) -> None:
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert issues == []

    def test_finds_issues_across_types(self, tmp_path: Path) -> None:
        # Create an agent with invalid YAML frontmatter
        # Discovery raises FrontmatterError for this, so validate_all catches it
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "bad.md").write_bytes(b"---\ninvalid: yaml: [broken\n---\n")
        (agents_dir / "good.md").write_bytes(b"---\nname: good\n---\nBody")

        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert len(issues) == 1
        assert issues[0].level == "error"
        assert "Discovery failed" in issues[0].message

    def test_finds_validation_issues_in_valid_items(self, tmp_path: Path) -> None:
        # Create agents with parseable but invalid env references
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "bad-env.md").write_bytes(
            b"---\nname: bad-env\nonly:\n  - nonexistent\n---\n"
        )
        (agents_dir / "good.md").write_bytes(b"---\nname: good\n---\nBody")

        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert len(issues) == 1
        assert "Invalid environment ID" in issues[0].message
        assert issues[0].file_path == agents_dir / "bad-env.md"

    def test_duplicate_names_detected(self, tmp_path: Path) -> None:
        # Two commands with different filetags that resolve to the same name
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "deploy -- prod.md").write_bytes(b"Deploy to prod")
        (commands_dir / "deploy -- dev.md").write_bytes(b"Deploy to dev")

        config = Config(
            source_root=tmp_path,
            targets={
                "t": TargetConfig(
                    id="t", type="claude", path=tmp_path, labels=["prod", "dev"]
                )
            },
            groups={"prod": ["t"], "dev": ["t"]},
        )
        issues = validate_all(config)
        dup_issues = [i for i in issues if "Duplicate" in i.message]
        assert len(dup_issues) == 1
        assert "deploy" in dup_issues[0].message

    def test_per_target_model_on_non_claude_target_is_error(
        self, tmp_path: Path
    ) -> None:
        config = Config(
            source_root=tmp_path,
            targets={
                "d": TargetConfig(
                    id="d",
                    type="droid",
                    path=tmp_path / "d",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        errors = [
            i
            for i in issues
            if i.level == "error" and "'model'" in i.message and "'d'" in i.message
        ]
        assert len(errors) == 1
        assert "only applies to claude targets" in errors[0].message

    def test_per_target_model_on_claude_target_is_ok(self, tmp_path: Path) -> None:
        # Include a models.yaml so the model name is recognized and Task 4.4's
        # unknown-model warning does not fire for this valid configuration.
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        assert issues == []

    def test_unknown_effective_model_produces_warning(self, tmp_path: Path) -> None:
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="made-up-model",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [
            i for i in issues if i.level == "warning" and "made-up-model" in i.message
        ]
        assert len(warnings) == 1

    def test_known_effective_model_no_warning(self, tmp_path: Path) -> None:
        (tmp_path / "models.yaml").write_text(
            "providers:\n"
            "  anthropic:\n"
            "    display_name: Anthropic\n"
            "    claude:\n"
            "      default_model: claude-opus-4-7\n"
            "    models:\n"
            "      claude-opus-4-7:\n"
            "        display_name: Claude Opus 4.7\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(id="c", type="claude", path=tmp_path / "c"),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_alias_opus_is_always_accepted(self, tmp_path: Path) -> None:
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="opus",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_no_effective_model_no_warning(self, tmp_path: Path) -> None:
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(id="c", type="claude", path=tmp_path / "c"),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if i.level == "warning"]
        assert warnings == []

    def test_unknown_model_without_models_yaml_warns(self, tmp_path: Path) -> None:
        config = Config(
            source_root=tmp_path,
            targets={
                "c": TargetConfig(
                    id="c",
                    type="claude",
                    path=tmp_path / "c",
                    model="claude-opus-4-7",
                ),
            },
            groups={},
        )
        issues = validate_all(config)
        warnings = [
            i for i in issues if i.level == "warning" and "claude-opus-4-7" in i.message
        ]
        assert len(warnings) == 1

    def test_non_claude_target_without_model_is_silent(self, tmp_path: Path) -> None:
        config = Config(
            source_root=tmp_path,
            targets={
                "d": TargetConfig(id="d", type="droid", path=tmp_path / "d"),
            },
            groups={},
        )
        issues = validate_all(config)
        assert [i for i in issues if i.level in ("error", "warning")] == []


class TestValidateItemHook:
    def _make_hook_item(self, content_dict: dict) -> SourceItem:
        import yaml

        content = yaml.dump(content_dict).encode("utf-8")
        return SourceItem(
            "hook",
            content_dict.get("name", "test-hook"),
            Path("/tmp/hooks/test.yaml"),
            content_dict,
            content,
        )

    def test_valid_hook(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "name": "my-hook",
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"command": "echo", "type": "command"}],
                        }
                    ],
                },
            }
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_hook_missing_name(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "hooks": {
                    "PostToolUse": [{"matcher": "Write", "hooks": []}],
                },
            }
        )
        issues = validate_item(item, config)
        assert any("missing 'name'" in i.message for i in issues)

    def test_hook_missing_hooks_field(self, config: Config) -> None:
        item = self._make_hook_item({"name": "my-hook"})
        issues = validate_item(item, config)
        assert any("missing or invalid 'hooks'" in i.message for i in issues)

    def test_hook_hooks_not_a_dict(self, config: Config) -> None:
        item = self._make_hook_item({"name": "my-hook", "hooks": "not-a-dict"})
        issues = validate_item(item, config)
        assert any("missing or invalid 'hooks'" in i.message for i in issues)

    def test_hook_invalid_event_type(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "name": "my-hook",
                "hooks": {
                    "InvalidEvent": [{"matcher": "Write", "hooks": []}],
                },
            }
        )
        issues = validate_item(item, config)
        assert any(
            "Invalid hook event type 'InvalidEvent'" in i.message for i in issues
        )

    def test_hook_empty_event_list(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "name": "my-hook",
                "hooks": {
                    "PostToolUse": [],
                },
            }
        )
        issues = validate_item(item, config)
        assert any("must be a non-empty list" in i.message for i in issues)

    def test_hook_event_not_a_list(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "name": "my-hook",
                "hooks": {
                    "PostToolUse": "not-a-list",
                },
            }
        )
        issues = validate_item(item, config)
        assert any("must be a non-empty list" in i.message for i in issues)

    def test_hook_all_valid_event_types(self, config: Config) -> None:
        for event in [
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "Notification",
            "SubagentStart",
            "SubagentStop",
            "Stop",
            "TeammateIdle",
            "TaskCompleted",
            "SessionStart",
            "SessionEnd",
            "PreCompact",
            "PostCompact",
            "UserPromptSubmit",
            "InstructionsLoaded",
            "ConfigChange",
            "WorktreeCreate",
            "WorktreeRemove",
        ]:
            item = self._make_hook_item(
                {
                    "name": "my-hook",
                    "hooks": {
                        event: [{"matcher": "", "hooks": []}],
                    },
                }
            )
            issues = validate_item(item, config)
            event_issues = [i for i in issues if "Invalid hook event type" in i.message]
            assert event_issues == [], (
                f"Unexpected error for event {event}: {event_issues}"
            )

    def test_hook_with_only_filter(self, config: Config) -> None:
        item = self._make_hook_item(
            {
                "name": "my-hook",
                "only": ["claude-personal"],
                "hooks": {
                    "Stop": [{"matcher": "", "hooks": []}],
                },
            }
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_hook_invalid_yaml(self, config: Config) -> None:
        content = b"name: [broken yaml\n"
        item = SourceItem(
            "hook", "test-hook", Path("/tmp/hooks/test.yaml"), None, content
        )
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert "Invalid YAML" in issues[0].message

    def test_hook_non_dict_yaml(self, config: Config) -> None:
        content = b"just a string\n"
        item = SourceItem(
            "hook", "test-hook", Path("/tmp/hooks/test.yaml"), None, content
        )
        issues = validate_item(item, config)
        # Non-mapping YAML is an error, not a silent pass.
        assert len(issues) == 1
        assert issues[0].level == "error"
        assert "must be a YAML mapping" in issues[0].message

    def test_validate_all_includes_hooks(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "bad.yaml").write_bytes(b"name: bad\nhooks: not-a-dict\n")

        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert any("missing or invalid 'hooks'" in i.message for i in issues)


class TestValidateItemMarketplace:
    def _item(self, content_dict: dict) -> SourceItem:
        import yaml

        content = yaml.dump(content_dict).encode("utf-8")
        return SourceItem(
            "marketplace",
            content_dict.get("name", "acme"),
            Path("/tmp/marketplaces/acme.yaml"),
            content_dict,
            content,
        )

    def test_valid_github_source(self, config: Config) -> None:
        item = self._item(
            {
                "name": "acme",
                "description": "Acme",
                "source": {"source": "github", "repo": "acme/plugins"},
                "autoUpdate": True,
                "plugins": {"formatter": True, "linter": False},
            }
        )
        assert validate_item(item, config) == []

    def test_valid_source_less_builtin(self, config: Config) -> None:
        item = self._item({"name": "official", "plugins": {"p": True}})
        assert validate_item(item, config) == []

    def test_name_with_at_is_error(self, config: Config) -> None:
        item = self._item({"name": "bad@name", "plugins": {}})
        assert any(
            "must not contain '@' or whitespace" in i.message
            for i in validate_item(item, config)
        )

    def test_name_with_whitespace_is_error(self, config: Config) -> None:
        item = self._item({"name": "bad name", "plugins": {}})
        assert any(
            i.level == "error" and "whitespace" in i.message
            for i in validate_item(item, config)
        )

    def test_empty_name_is_error(self, config: Config) -> None:
        item = self._item({"name": "", "plugins": {}})
        assert any(
            i.level == "error" and "non-empty string" in i.message
            for i in validate_item(item, config)
        )

    def test_non_string_name_is_error(self, config: Config) -> None:
        item = self._item({"name": 123, "plugins": {}})
        assert any(
            i.level == "error" and "non-empty string" in i.message
            for i in validate_item(item, config)
        )

    def test_source_not_a_mapping_is_error(self, config: Config) -> None:
        item = self._item({"name": "acme", "source": "github"})
        assert any(
            i.level == "error" and "'source' must be a mapping" in i.message
            for i in validate_item(item, config)
        )

    def test_unknown_source_type_warns(self, config: Config) -> None:
        item = self._item({"name": "acme", "source": {"source": "svn"}})
        assert any(
            i.level == "warning" and "not a known type" in i.message
            for i in validate_item(item, config)
        )

    def test_known_git_and_directory_sources_ok(self, config: Config) -> None:
        git = self._item({"name": "a", "source": {"source": "git", "url": "x"}})
        directory = self._item(
            {"name": "b", "source": {"source": "directory", "path": "/p"}}
        )
        assert validate_item(git, config) == []
        assert validate_item(directory, config) == []

    def test_extra_source_keys_pass_through(self, config: Config) -> None:
        item = self._item(
            {
                "name": "acme",
                "source": {"source": "github", "repo": "a/b", "ref": "main"},
            }
        )
        assert validate_item(item, config) == []

    def test_plugins_not_a_mapping_is_error(self, config: Config) -> None:
        item = self._item({"name": "acme", "plugins": ["p"]})
        assert any(
            i.level == "error" and "'plugins' must be a mapping" in i.message
            for i in validate_item(item, config)
        )

    def test_plugin_name_with_at_is_error(self, config: Config) -> None:
        item = self._item({"name": "acme", "plugins": {"bad@x": True}})
        assert any(
            i.level == "error" and "must not contain '@'" in i.message
            for i in validate_item(item, config)
        )

    def test_empty_plugin_name_is_error(self, config: Config) -> None:
        item = self._item({"name": "acme", "plugins": {"": True}})
        assert any(
            i.level == "error" and "non-empty string" in i.message
            for i in validate_item(item, config)
        )

    def test_unknown_top_level_key_warns(self, config: Config) -> None:
        item = self._item({"name": "acme", "bogus": 1})
        assert any(
            i.level == "warning" and "unknown key 'bogus'" in i.message
            for i in validate_item(item, config)
        )

    def test_only_filter_accepted(self, config: Config) -> None:
        item = self._item({"name": "acme", "only": ["claude"], "plugins": {}})
        assert validate_item(item, config) == []

    def test_enabled_bool_accepted(self, config: Config) -> None:
        item = self._item({"name": "acme", "enabled": False, "plugins": {}})
        assert validate_item(item, config) == []

    def test_enabled_non_bool_is_error(self, config: Config) -> None:
        # A quoted YAML "false" parses to a truthy string; flag it so an
        # intended disable is not silently deployed as enabled.
        item = self._item({"name": "acme", "enabled": "false", "plugins": {}})
        assert any(
            i.level == "error" and "'enabled' must be a boolean" in i.message
            for i in validate_item(item, config)
        )

    def test_validate_all_includes_marketplaces(self, tmp_path: Path) -> None:
        mk = tmp_path / "marketplaces"
        mk.mkdir()
        (mk / "bad.yaml").write_bytes(b"name: bad@name\nplugins: {}\n")
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert any("'@' or whitespace" in i.message for i in issues)


def test_validate_settings_marketplace_keys_warn(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(
        tmp_path,
        "base:\n  extraKnownMarketplaces:\n    acme: {}\n"
        "  enabledPlugins:\n    p@acme: true\n",
    )
    warns = [i.message for i in validate_all(cfg) if i.level == "warning"]
    assert any("extraKnownMarketplaces" in m and "marketplaces/" in m for m in warns)
    assert any("enabledPlugins" in m and "marketplaces/" in m for m in warns)


class TestValidateFiletags:
    def test_valid_filetags(self, config: Config) -> None:
        item = SourceItem(
            "agent",
            "test",
            Path("/tmp/test.md"),
            None,
            b"No frontmatter",
            filetags=["claude"],
        )
        issues = validate_item(item, config)
        assert issues == []

    def test_invalid_filetag_label(self, config: Config) -> None:
        item = SourceItem(
            "agent",
            "test",
            Path("/tmp/test.md"),
            None,
            b"No frontmatter",
            filetags=["nonexistent"],
        )
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert "Invalid filetag label 'nonexistent'" in issues[0].message

    def test_multiple_filetags_one_invalid(self, config: Config) -> None:
        item = SourceItem(
            "agent",
            "test",
            Path("/tmp/test.md"),
            None,
            b"No frontmatter",
            filetags=["claude", "bogus"],
        )
        issues = validate_item(item, config)
        assert len(issues) == 1
        assert "Invalid filetag label 'bogus'" in issues[0].message

    def test_empty_filetags_no_issues(self, config: Config) -> None:
        item = SourceItem(
            "agent", "test", Path("/tmp/test.md"), None, b"No frontmatter", filetags=[]
        )
        issues = validate_item(item, config)
        assert issues == []


class TestValidateItemPrompt:
    def test_valid_poet(self, tmp_path: Path, config: Config) -> None:
        path = tmp_path / "demo.poet"
        body = b"- role: system\n  content: hi\n"
        path.write_bytes(body)
        item = SourceItem("prompt", "demo", path, None, body)
        assert validate_item(item, config) == []

    def test_poet_with_undefined_var_emits_warning(
        self, tmp_path: Path, config: Config
    ) -> None:
        path = tmp_path / "demo.poet"
        body = b"- role: system\n  content: 'hi {{ missing }}'\n"
        path.write_bytes(body)
        item = SourceItem("prompt", "demo", path, None, body)
        issues = validate_item(item, config)
        assert any(i.level == "warning" and "missing" in i.message for i in issues)

    def test_poet_with_invalid_yaml_is_error(
        self, tmp_path: Path, config: Config
    ) -> None:
        path = tmp_path / "demo.poet"
        body = b"- role: bogus\n  content: x\n"
        path.write_bytes(body)
        item = SourceItem("prompt", "demo", path, None, body)
        issues = validate_item(item, config)
        assert any(i.level == "error" and "Poet parse" in i.message for i in issues)

    def test_plain_prompt_skipped_by_poet_validation(
        self, tmp_path: Path, config: Config
    ) -> None:
        path = tmp_path / "demo.txt"
        body = b"plain content"
        path.write_bytes(body)
        item = SourceItem("prompt", "demo", path, None, body)
        # No poet parsing for non-poet extensions; no errors expected.
        assert validate_item(item, config) == []

    def test_prompt_with_metadata_uses_existing_dict(
        self, tmp_path: Path, config: Config
    ) -> None:
        # When item.metadata is set (from source discovery's comment-FM
        # parser), validate_item uses it directly and applies only/except
        # checks against it.
        path = tmp_path / "demo.poet"
        body = b"# only: [bogus-target]\n- role: system\n  content: x\n"
        path.write_bytes(body)
        item = SourceItem(
            "prompt",
            "demo",
            path,
            {"only": ["bogus-target"]},
            body,
        )
        issues = validate_item(item, config)
        assert any("bogus-target" in i.message for i in issues)


class TestValidationIssue:
    def test_fields(self) -> None:
        issue = ValidationIssue(level="error", message="test", file_path=Path("/tmp/x"))
        assert issue.level == "error"
        assert issue.message == "test"
        assert issue.file_path == Path("/tmp/x")


def _cfg_with(tmp_path, settings_yaml: str):
    from promptdeploy.config import Config, TargetConfig

    (tmp_path / "settings.yaml").write_text(settings_yaml)
    tc = TargetConfig(
        id="claude-positron",
        type="claude",
        path=tmp_path / "p",
        labels=["claude", "positron"],
    )
    return Config(
        source_root=tmp_path,
        targets={tc.id: tc},
        groups={"positron": ["claude-positron"], "claude": ["claude-positron"]},
    )


def test_validate_settings_ok(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(
        tmp_path,
        "base:\n  effortLevel: low\noverrides:\n  claude-positron:\n    model: sonnet\n",
    )
    issues = [i for i in validate_all(cfg) if "settings.yaml" in str(i.file_path)]
    assert issues == []


def test_validate_settings_unknown_override_key_errors(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base: {}\noverrides:\n  nope-target:\n    model: x\n")
    msgs = [i.message for i in validate_all(cfg) if i.level == "error"]
    assert any("nope-target" in m for m in msgs)


def test_validate_settings_hooks_in_base_warns(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  hooks:\n    Stop: []\n")
    warns = [
        i for i in validate_all(cfg) if i.level == "warning" and "hooks" in i.message
    ]
    assert warns


def test_validate_settings_null_in_base_warns(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  effortLevel: null\n")
    assert any(
        i.level == "warning" and "null" in i.message.lower() for i in validate_all(cfg)
    )


def test_validate_settings_non_dict_base_errors(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  - 1\n  - 2\n")
    assert any(i.level == "error" for i in validate_all(cfg))


def test_validate_settings_group_key_accepted(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base: {}\noverrides:\n  positron:\n    model: x\n")
    assert [
        i for i in validate_all(cfg) if i.level == "error" and "positron" in i.message
    ] == []


# --- branch-coverage tests for the structural guards (each hits a distinct
# --- statement in validate_settings; required for the 100% line gate) ---


def test_validate_settings_malformed_yaml_errors(tmp_path):
    # Unparseable YAML -> the `except yaml.YAMLError` early return.
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base: [unclosed\n")
    issues = [
        i
        for i in validate_all(cfg)
        if i.level == "error" and "settings.yaml" in str(i.file_path)
    ]
    assert issues


def test_validate_settings_empty_file_no_issues(tmp_path):
    # Comment-only / empty doc -> `if doc is None: return []` (no settings issue).
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "# just a comment\n")
    assert [i for i in validate_all(cfg) if "settings.yaml" in str(i.file_path)] == []


def test_validate_settings_top_level_list_errors(tmp_path):
    # Whole document is a list (not a mapping) -> the top-level not-a-dict error.
    # NOTE: test_validate_settings_non_dict_base_errors makes only `base` a list,
    # leaving `doc` a dict, so it does NOT cover this branch.
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "- a\n- b\n")
    assert any(
        i.level == "error" and "top level must be a mapping" in i.message
        for i in validate_all(cfg)
    )


def test_validate_settings_non_dict_overrides_errors(tmp_path):
    # `overrides` present but a scalar -> the `'overrides' must be a mapping` error.
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "overrides: 5\n")
    assert any(
        i.level == "error" and "'overrides' must be a mapping" in i.message
        for i in validate_all(cfg)
    )


def test_validate_settings_non_dict_override_value_errors(tmp_path):
    # An override value that is a scalar -> the per-override `must be a mapping`
    # error. Key is a known id so the unknown-key error does not mask the shape one.
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "overrides:\n  claude-positron: 5\n")
    assert any(
        i.level == "error"
        and "must be a mapping" in i.message
        and "claude-positron" in i.message
        for i in validate_all(cfg)
    )


# --- §6.13 JSON-representability: reject YAML-only types (dates/times/etc.)
# --- that yaml.safe_load produces but json.dump cannot serialize, which would
# --- otherwise crash deploy with an uncaught TypeError.


def test_validate_settings_rejects_yaml_date_in_base(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  releaseDate: 2026-06-01\n")
    assert any(
        i.level == "error" and "non-JSON" in i.message for i in validate_all(cfg)
    )


def test_validate_settings_rejects_date_nested_in_dict(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  env:\n    when: 2026-06-01\n")
    assert any(
        i.level == "error" and "non-JSON" in i.message for i in validate_all(cfg)
    )


def test_validate_settings_rejects_date_in_list(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(tmp_path, "base:\n  items:\n    - ok\n    - 2026-06-01\n")
    assert any(
        i.level == "error" and "non-JSON" in i.message for i in validate_all(cfg)
    )


def test_validate_settings_rejects_date_in_override(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(
        tmp_path,
        "base: {}\noverrides:\n  claude-positron:\n    when: 2026-06-01\n",
    )
    assert any(
        i.level == "error" and "non-JSON" in i.message for i in validate_all(cfg)
    )


def test_validate_settings_accepts_nested_json_types(tmp_path):
    from promptdeploy.validate import validate_all

    cfg = _cfg_with(
        tmp_path,
        "base:\n"
        "  sandbox:\n"
        "    enabled: false\n"
        "    paths:\n"
        "      - /tmp\n"
        "      - /var\n"
        "  count: 3\n"
        "  ratio: 0.5\n",
    )
    assert [i for i in validate_all(cfg) if i.level == "error"] == []


class TestUnclosedFrontmatterWarning:
    def test_unclosed_frontmatter_warns(self, config: Config) -> None:
        content = b"---\nname: test\nonly:\n  - droid\nNo closing delimiter.\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert any(
            i.level == "warning" and "no frontmatter was parsed" in i.message
            for i in issues
        )

    def test_unclosed_frontmatter_with_bom_warns(self, config: Config) -> None:
        content = b"\xef\xbb\xbf---\nname: test\nNo closing delimiter.\n"
        item = SourceItem("agent", "test", Path("/tmp/test.md"), None, content)
        issues = validate_item(item, config)
        assert any("no frontmatter was parsed" in i.message for i in issues)

    def test_plain_body_does_not_warn(self, config: Config) -> None:
        item = SourceItem(
            "agent", "test", Path("/tmp/test.md"), None, b"No frontmatter at all.\n"
        )
        assert validate_item(item, config) == []


class TestNonStringNameValidation:
    def test_agent_int_name_is_error(self, config: Config) -> None:
        content = b"---\nname: 123\n---\nBody\n"
        item = SourceItem("agent", "helper", Path("/tmp/helper.md"), None, content)
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "'name' must be a string" in i.message
            for i in issues
        )

    def test_mcp_list_name_is_error(self, config: Config) -> None:
        content = b"name: [a, b]\ncommand: npx\n"
        item = SourceItem("mcp", "server", Path("/tmp/mcp/server.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("'name' must be a string" in i.message for i in issues)

    def test_hook_int_name_is_error(self, config: Config) -> None:
        content = b"name: 7\nhooks:\n  Stop:\n    - matcher: ''\n      hooks: []\n"
        item = SourceItem("hook", "guard", Path("/tmp/hooks/guard.yaml"), None, content)
        issues = validate_item(item, config)
        assert any("'name' must be a string" in i.message for i in issues)


class TestPerFileDiscoveryErrors:
    def test_all_bad_files_reported_not_just_first(self, tmp_path: Path) -> None:
        # Two malformed agents plus a good one: both errors must surface in a
        # single run, and the good file must still be validated.
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        bad = b"---\ninvalid: yaml: [broken\n---\n"
        (agents_dir / "a-bad.md").write_bytes(bad)
        (agents_dir / "b-bad.md").write_bytes(bad)
        (agents_dir / "c-good.md").write_bytes(
            b"---\nname: good\nonly:\n  - nonexistent\n---\nBody\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        discovery_errors = [i for i in issues if "Discovery failed" in i.message]
        assert len(discovery_errors) == 2
        assert {i.file_path for i in discovery_errors} == {
            agents_dir / "a-bad.md",
            agents_dir / "b-bad.md",
        }
        # The later good file was still validated.
        assert any("Invalid environment ID" in i.message for i in issues)

    def test_duplicate_detection_survives_parse_error(self, tmp_path: Path) -> None:
        # A parse error in an alphabetically earlier file must not abandon
        # duplicate-name detection for the rest of the directory.
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "a-bad.md").write_bytes(b"---\ninvalid: yaml: [broken\n---\n")
        (commands_dir / "deploy -- prod.md").write_bytes(b"Deploy prod")
        (commands_dir / "deploy -- dev.md").write_bytes(b"Deploy dev")
        config = Config(
            source_root=tmp_path,
            targets={
                "t": TargetConfig(
                    id="t", type="claude", path=tmp_path, labels=["prod", "dev"]
                )
            },
            groups={"prod": ["t"], "dev": ["t"]},
        )
        issues = validate_all(config)
        assert any("Discovery failed" in i.message for i in issues)
        assert any("Duplicate" in i.message for i in issues)


class TestMarketplaceBooleanValues:
    def _item(self, content_dict: dict) -> SourceItem:
        import yaml

        content = yaml.dump(content_dict).encode("utf-8")
        return SourceItem(
            "marketplace",
            str(content_dict.get("name", "acme")),
            Path("/tmp/marketplaces/acme.yaml"),
            content_dict,
            content,
        )

    def test_plugin_value_string_false_is_error(self, config: Config) -> None:
        # bool("false") is True, so a string value silently inverts the
        # author's intent at deploy time.
        item = self._item({"name": "acme", "plugins": {"formatter": "false"}})
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "must be a boolean" in i.message for i in issues
        )

    def test_plugin_value_bool_accepted(self, config: Config) -> None:
        item = self._item({"name": "acme", "plugins": {"formatter": False}})
        assert validate_item(item, config) == []

    def test_auto_update_string_is_error(self, config: Config) -> None:
        item = self._item({"name": "acme", "autoUpdate": "no", "plugins": {}})
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "'autoUpdate' must be a boolean" in i.message
            for i in issues
        )

    def test_auto_update_bool_accepted(self, config: Config) -> None:
        item = self._item({"name": "acme", "autoUpdate": False, "plugins": {}})
        assert validate_item(item, config) == []

    def test_non_dict_marketplace_yaml_is_error(self, config: Config) -> None:
        item = SourceItem(
            "marketplace",
            "acme",
            Path("/tmp/marketplaces/acme.yaml"),
            None,
            b"- just\n- a\n- list\n",
        )
        issues = validate_item(item, config)
        assert any("must be a YAML mapping" in i.message for i in issues)


class TestSkillLimits:
    def _skill(self, frontmatter: bytes, body: bytes = b"Body\n") -> SourceItem:
        from promptdeploy.frontmatter import parse_frontmatter

        content = b"---\n" + frontmatter + b"---\n" + body
        metadata, _ = parse_frontmatter(content)
        assert metadata is not None
        name = metadata.get("name")
        if not isinstance(name, str):
            name = "my-skill"
        return SourceItem(
            "skill", name, Path("/tmp/skills/my-skill/SKILL.md"), metadata, content
        )

    def test_valid_skill_passes(self, config: Config) -> None:
        item = self._skill(b"name: my-skill\ndescription: Does things\n")
        assert validate_item(item, config) == []

    def test_name_over_64_chars_is_error(self, config: Config) -> None:
        long_name = "x" * 65
        item = SourceItem(
            "skill",
            long_name,
            Path(f"/tmp/skills/{long_name}/SKILL.md"),
            {"name": long_name, "description": "d"},
            f"---\nname: {long_name}\ndescription: d\n---\nBody\n".encode(),
        )
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "exceeds 64 characters" in i.message for i in issues
        )

    def test_missing_description_is_error(self, config: Config) -> None:
        item = self._skill(b"name: my-skill\n")
        issues = validate_item(item, config)
        assert any(i.level == "error" and "description" in i.message for i in issues)

    def test_description_over_1024_chars_is_error(self, config: Config) -> None:
        desc = "d" * 1025
        item = self._skill(f"name: my-skill\ndescription: {desc}\n".encode())
        issues = validate_item(item, config)
        assert any(
            i.level == "error" and "exceeds 1024 characters" in i.message
            for i in issues
        )

    def test_skill_md_over_500_lines_warns(self, config: Config) -> None:
        body = b"line\n" * 510
        item = self._skill(b"name: my-skill\ndescription: d\n", body=body)
        issues = validate_item(item, config)
        assert any(i.level == "warning" and "lines" in i.message for i in issues)

    def test_name_directory_mismatch_warns(self, config: Config) -> None:
        item = SourceItem(
            "skill",
            "other-name",
            Path("/tmp/skills/my-skill/SKILL.md"),
            {"name": "other-name", "description": "d"},
            b"---\nname: other-name\ndescription: d\n---\nBody\n",
        )
        issues = validate_item(item, config)
        assert any(
            i.level == "warning" and "does not match its directory" in i.message
            for i in issues
        )

    def test_filetagged_directory_matches_base_name(self, config: Config) -> None:
        # ``skills/my-skill -- prod/`` resolves to base name ``my-skill``.
        item = SourceItem(
            "skill",
            "my-skill",
            Path("/tmp/skills/my-skill -- prod/SKILL.md"),
            {"name": "my-skill", "description": "d"},
            b"---\nname: my-skill\ndescription: d\n---\nBody\n",
            filetags=["prod"],
        )
        config.groups["prod"] = ["claude-personal"]
        assert validate_item(item, config) == []


class TestSlashNamespaceCollisions:
    def test_command_and_skill_with_same_name_warn(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "review.md").write_bytes(b"Review things.\n")
        skill_dir = tmp_path / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(
            b"---\nname: review\ndescription: Reviews\n---\nBody\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        collisions = [i for i in issues if "slash-command namespace" in i.message]
        assert len(collisions) == 1
        assert collisions[0].level == "warning"
        assert "review" in collisions[0].message

    def test_distinct_names_no_warning(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "review.md").write_bytes(b"Review things.\n")
        skill_dir = tmp_path / "skills" / "deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(
            b"---\nname: deploy\ndescription: Deploys\n---\nBody\n"
        )
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        assert not any("slash-command namespace" in i.message for i in issues)


class TestBrokenSkillSymlinkWarning:
    def test_broken_symlink_warns(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "vanished").symlink_to(tmp_path / "no-such-target")
        config = Config(
            source_root=tmp_path,
            targets={"t": TargetConfig(id="t", type="claude", path=tmp_path)},
            groups={},
        )
        issues = validate_all(config)
        warnings = [i for i in issues if "Broken symlink" in i.message]
        assert len(warnings) == 1
        assert warnings[0].level == "warning"
        assert warnings[0].file_path == skills_dir / "vanished"
