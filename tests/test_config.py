"""Tests for promptdeploy configuration loading."""

from pathlib import Path

import pytest
import yaml

from promptdeploy.config import (
    Config,
    TargetConfig,
    expand_target_arg,
    find_config_file,
    load_config,
    remap_targets_to_root,
)

SAMPLE_CONFIG = {
    'source_root': '.',
    'targets': {
        'claude-personal': {
            'type': 'claude',
            'path': '~/.config/claude/personal',
        },
        'claude-positron': {
            'type': 'claude',
            'path': '~/.config/claude/positron',
        },
        'droid': {
            'type': 'droid',
            'path': '~/.factory',
        },
    },
    'groups': {
        'claude': ['claude-personal', 'claude-positron'],
    },
}


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_path = tmp_path / 'deploy.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(SAMPLE_CONFIG, f)
    return tmp_path


@pytest.fixture
def config(config_dir: Path) -> Config:
    return load_config(config_dir / 'deploy.yaml')


class TestLoadConfig:
    def test_loads_valid_config(self, config: Config) -> None:
        assert isinstance(config, Config)
        assert len(config.targets) == 3
        assert 'claude-personal' in config.targets
        assert 'droid' in config.targets

    def test_source_root_resolved_relative(self, config_dir: Path) -> None:
        config = load_config(config_dir / 'deploy.yaml')
        assert config.source_root.is_absolute()
        assert config.source_root == config_dir.resolve()

    def test_source_root_absolute(self, tmp_path: Path) -> None:
        abs_path = str(tmp_path / 'test-src')
        data = {**SAMPLE_CONFIG, 'source_root': abs_path}
        config_path = tmp_path / 'deploy.yaml'
        with open(config_path, 'w') as f:
            yaml.dump(data, f)
        config = load_config(config_path)
        assert config.source_root == Path(abs_path).resolve()

    def test_target_path_expansion(self, config: Config) -> None:
        home = Path.home()
        personal = config.targets['claude-personal']
        assert personal.path == home / '.config' / 'claude' / 'personal'
        assert personal.type == 'claude'
        assert personal.id == 'claude-personal'

    def test_target_types(self, config: Config) -> None:
        assert config.targets['claude-personal'].type == 'claude'
        assert config.targets['droid'].type == 'droid'

    def test_group_definitions(self, config: Config) -> None:
        assert 'claude' in config.groups
        assert config.groups['claude'] == ['claude-personal', 'claude-positron']


class TestFindConfigFile:
    def test_finds_in_current_dir(self, config_dir: Path) -> None:
        found = find_config_file(config_dir)
        assert found == config_dir / 'deploy.yaml'

    def test_walks_up_directories(self, config_dir: Path) -> None:
        sub = config_dir / 'a' / 'b' / 'c'
        sub.mkdir(parents=True)
        found = find_config_file(sub)
        assert found == config_dir / 'deploy.yaml'

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        empty = tmp_path / 'empty'
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="Could not find deploy.yaml"):
            find_config_file(empty)

    def test_defaults_to_cwd(self, config_dir: Path, monkeypatch) -> None:
        """find_config_file(None) defaults to Path.cwd()."""
        monkeypatch.chdir(config_dir)
        found = find_config_file()
        assert found == config_dir / 'deploy.yaml'


class TestLoadConfigDefaults:
    def test_load_config_none_uses_find(self, config_dir: Path, monkeypatch) -> None:
        """load_config(None) calls find_config_file() to locate deploy.yaml."""
        monkeypatch.chdir(config_dir)
        config = load_config()
        assert isinstance(config, Config)
        assert len(config.targets) == 3


class TestRemapTargetsToRoot:
    def test_remaps_all_target_paths(self, config: Config, tmp_path: Path) -> None:
        root = tmp_path / "preview"
        remapped = remap_targets_to_root(config, root)
        assert remapped.targets["claude-personal"].path == root / "claude-personal"
        assert remapped.targets["claude-positron"].path == root / "claude-positron"
        assert remapped.targets["droid"].path == root / "droid"

    def test_preserves_target_id_and_type(self, config: Config, tmp_path: Path) -> None:
        root = tmp_path / "preview"
        remapped = remap_targets_to_root(config, root)
        tc = remapped.targets["droid"]
        assert tc.id == "droid"
        assert tc.type == "droid"

    def test_preserves_source_root(self, config: Config, tmp_path: Path) -> None:
        root = tmp_path / "preview"
        remapped = remap_targets_to_root(config, root)
        assert remapped.source_root == config.source_root

    def test_preserves_groups(self, config: Config, tmp_path: Path) -> None:
        root = tmp_path / "preview"
        remapped = remap_targets_to_root(config, root)
        assert remapped.groups == config.groups

    def test_returns_new_config_instance(self, config: Config, tmp_path: Path) -> None:
        root = tmp_path / "preview"
        remapped = remap_targets_to_root(config, root)
        assert remapped is not config

    def test_original_config_unchanged(self, config: Config, tmp_path: Path) -> None:
        original_paths = {tid: tc.path for tid, tc in config.targets.items()}
        remap_targets_to_root(config, tmp_path / "preview")
        for tid, path in original_paths.items():
            assert config.targets[tid].path == path

    def test_empty_targets(self, tmp_path: Path) -> None:
        empty_config = Config(source_root=tmp_path, targets={}, groups={})
        remapped = remap_targets_to_root(empty_config, tmp_path / "root")
        assert remapped.targets == {}

    def test_target_id_used_as_subdirectory_name(self, tmp_path: Path) -> None:
        tc = TargetConfig(id="my-target", type="claude", path=Path("/some/original/path"))
        cfg = Config(source_root=tmp_path, targets={"my-target": tc}, groups={})
        root = tmp_path / "scratch"
        remapped = remap_targets_to_root(cfg, root)
        assert remapped.targets["my-target"].path == root / "my-target"


class TestExpandTargetArg:
    def test_none_returns_all(self, config: Config) -> None:
        result = expand_target_arg(None, config)
        assert set(result) == {'claude-personal', 'claude-positron', 'droid'}

    def test_group_expansion(self, config: Config) -> None:
        result = expand_target_arg(['claude'], config)
        assert result == ['claude-personal', 'claude-positron']

    def test_single_target(self, config: Config) -> None:
        result = expand_target_arg(['droid'], config)
        assert result == ['droid']

    def test_mixed_groups_and_targets(self, config: Config) -> None:
        result = expand_target_arg(['claude', 'droid'], config)
        assert result == ['claude-personal', 'claude-positron', 'droid']

    def test_unknown_target_raises(self, config: Config) -> None:
        with pytest.raises(ValueError, match="Unknown target: nonexistent"):
            expand_target_arg(['nonexistent'], config)
