"""Source item discovery for prompts, agents, skills, and MCP servers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from promptdeploy.filetags import parse_filetags
from promptdeploy.frontmatter import FrontmatterError, parse_frontmatter
from promptdeploy.poet import (
    PLAIN_EXTENSIONS,
    POET_EXTENSIONS,
    extract_comment_frontmatter,
)

PROMPT_EXTENSIONS = POET_EXTENSIONS | PLAIN_EXTENSIONS | {".json"}


@dataclass
class SourceItem:
    """A discovered source item ready for deployment."""

    # 'agent', 'command', 'skill', 'mcp', 'models', 'hook', 'marketplace',
    # 'prompt', or 'settings'
    item_type: str
    name: str
    path: Path
    metadata: dict[str, Any] | None
    content: bytes
    filetags: list[str] = field(default_factory=list)


@dataclass
class DiscoveryError:
    """A per-file discovery failure captured during lenient discovery."""

    path: Path
    message: str


def _resolve_name(metadata: dict[str, Any] | None, base_name: str) -> str:
    """Resolve an item's name from metadata with a string-type guard.

    A non-string ``name:`` (e.g. ``name: 123``) would corrupt manifest and
    settings.json keys (int keys coerce to str on json.dump while the
    manifest keeps the int, so reclamation never matches) or crash on an
    unhashable list/dict; fall back to the filename-derived base name and
    let validate.py report the malformed name.
    """
    name_value = (metadata or {}).get("name")
    return name_value if isinstance(name_value, str) else base_name


class SourceDiscovery:
    """Discovers deployable items from a source root directory."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

    def discover_all(self) -> Iterator[SourceItem]:
        """Yield all discoverable source items."""
        yield from self.discover_agents()
        yield from self.discover_commands()
        yield from self.discover_skills()
        yield from self.discover_mcp_servers()
        yield from self.discover_models()
        yield from self.discover_hooks()
        yield from self.discover_prompts()
        yield from self.discover_settings()
        # Marketplaces deploy AFTER settings: during migration the settings
        # item runs first so it pops the formerly settings.yaml-managed
        # extraKnownMarketplaces/enabledPlugins keys (recorded in the settings
        # manifest's managed_keys) before marketplace items re-add their own
        # entries in the same run.
        yield from self.discover_marketplaces()

    def discover_prompts(self) -> Iterator[SourceItem]:
        """Discover prompts from prompts/*.{poet,j2,jinja,jinja2,txt,md,org,json}."""
        prompts_dir = self.source_root / "prompts"
        if not prompts_dir.is_dir():
            return
        for path in sorted(prompts_dir.iterdir()):
            if path.name.startswith(".") or path.suffix not in PROMPT_EXTENSIONS:
                continue
            if not path.is_file():
                continue
            base_name, tags = parse_filetags(path.stem)
            content = path.read_bytes()
            metadata = extract_comment_frontmatter(content)
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="prompt",
                name=name,
                path=path,
                metadata=metadata or None,
                content=content,
                filetags=tags,
            )

    def discover_agents(
        self, errors: list[DiscoveryError] | None = None
    ) -> Iterator[SourceItem]:
        """Discover agent definitions from agents/*.md.

        When ``errors`` is provided, per-file frontmatter failures are
        appended to it and discovery continues with the next file; otherwise
        the first failure raises FrontmatterError.
        """
        agents_dir = self.source_root / "agents"
        if not agents_dir.is_dir():
            return
        for path in sorted(agents_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".md":
                continue
            try:
                yield self._load_markdown_item("agent", path)
            except FrontmatterError as exc:
                if errors is None:
                    raise
                errors.append(DiscoveryError(path=path, message=str(exc)))

    def discover_commands(
        self, errors: list[DiscoveryError] | None = None
    ) -> Iterator[SourceItem]:
        """Discover command prompts from commands/*.md.

        ``errors`` enables lenient per-file error collection as in
        :meth:`discover_agents`.
        """
        commands_dir = self.source_root / "commands"
        if not commands_dir.is_dir():
            return
        for path in sorted(commands_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".md":
                continue
            try:
                yield self._load_markdown_item("command", path)
            except FrontmatterError as exc:
                if errors is None:
                    raise
                errors.append(DiscoveryError(path=path, message=str(exc)))

    def discover_skills(
        self, errors: list[DiscoveryError] | None = None
    ) -> Iterator[SourceItem]:
        """Discover skills from skills/*/SKILL.md.

        ``errors`` enables lenient per-file error collection as in
        :meth:`discover_agents`.
        """
        skills_dir = self.source_root / "skills"
        if not skills_dir.is_dir():
            return
        skip_names = {"README.md"}
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith(".") or entry.name in skip_names:
                continue
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            # Parse filetags from directory name
            base_name, tags = parse_filetags(entry.name)
            # Resolve symlinks for content but preserve original path
            resolved = skill_md.resolve()
            content = resolved.read_bytes()
            try:
                metadata, _ = parse_frontmatter(content)
            except FrontmatterError as exc:
                if errors is None:
                    raise FrontmatterError(f"{skill_md}: {exc}") from exc
                errors.append(DiscoveryError(path=skill_md, message=str(exc)))
                continue
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="skill",
                name=name,
                path=skill_md,
                metadata=metadata,
                content=content,
                filetags=tags,
            )

    def broken_skill_symlinks(self) -> list[Path]:
        """Return skills/ entries that are symlinks to nonexistent targets.

        :meth:`discover_skills` necessarily skips these (``entry.is_dir()``
        follows the link and returns False), so validate surfaces them as
        warnings — otherwise a git-tracked skill silently vanishes from
        every target.
        """
        skills_dir = self.source_root / "skills"
        if not skills_dir.is_dir():
            return []
        return [
            entry
            for entry in sorted(skills_dir.iterdir())
            if entry.is_symlink() and not entry.exists()
        ]

    def discover_mcp_servers(self) -> Iterator[SourceItem]:
        """Discover MCP server configs from mcp/*.yaml."""
        mcp_dir = self.source_root / "mcp"
        if not mcp_dir.is_dir():
            return
        for path in sorted(mcp_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = path.read_bytes()
            try:
                metadata = yaml.safe_load(content)
            except yaml.YAMLError:
                metadata = None
            if not isinstance(metadata, dict):
                metadata = None
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="mcp",
                name=name,
                path=path,
                metadata=metadata,
                content=content,
                filetags=tags,
            )

    def discover_marketplaces(self) -> Iterator[SourceItem]:
        """Discover Claude marketplace configs from marketplaces/*.yaml."""
        marketplaces_dir = self.source_root / "marketplaces"
        if not marketplaces_dir.is_dir():
            return
        for path in sorted(marketplaces_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = path.read_bytes()
            try:
                metadata = yaml.safe_load(content)
            except yaml.YAMLError:
                metadata = None
            if not isinstance(metadata, dict):
                metadata = None
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="marketplace",
                name=name,
                path=path,
                metadata=metadata,
                content=content,
                filetags=tags,
            )

    def discover_models(self) -> Iterator[SourceItem]:
        """Discover model definitions from models.yaml."""
        models_path = self.source_root / "models.yaml"
        if not models_path.exists():
            return
        content = models_path.read_bytes()
        try:
            metadata = yaml.safe_load(content)
        except yaml.YAMLError:
            metadata = None
        if not isinstance(metadata, dict):
            metadata = None
        yield SourceItem(
            item_type="models",
            name="models",
            path=models_path,
            metadata=metadata,
            content=content,
        )

    def discover_settings(self) -> Iterator[SourceItem]:
        """Discover the singleton settings master from settings.yaml."""
        settings_path = self.source_root / "settings.yaml"
        if not settings_path.exists():
            return
        content = settings_path.read_bytes()
        try:
            metadata = yaml.safe_load(content)
        except yaml.YAMLError:
            metadata = None
        if not isinstance(metadata, dict):
            metadata = None
        yield SourceItem(
            item_type="settings",
            name="settings",
            path=settings_path,
            metadata=metadata,
            content=content,
        )

    def discover_hooks(self) -> Iterator[SourceItem]:
        """Discover hook group configs from hooks/*.yaml."""
        hooks_dir = self.source_root / "hooks"
        if not hooks_dir.is_dir():
            return
        for path in sorted(hooks_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = path.read_bytes()
            try:
                metadata = yaml.safe_load(content)
            except yaml.YAMLError:
                metadata = None
            if not isinstance(metadata, dict):
                metadata = None
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="hook",
                name=name,
                path=path,
                metadata=metadata,
                content=content,
                filetags=tags,
            )

    def _load_markdown_item(self, item_type: str, path: Path) -> SourceItem:
        """Load a markdown file as a SourceItem, parsing frontmatter for metadata.

        Raises FrontmatterError naming the offending file on parse failure.
        """
        base_name, tags = parse_filetags(path.stem)
        resolved = path.resolve()
        content = resolved.read_bytes()
        try:
            metadata, _ = parse_frontmatter(content)
        except FrontmatterError as exc:
            raise FrontmatterError(f"{path}: {exc}") from exc
        name = _resolve_name(metadata, base_name)
        return SourceItem(
            item_type=item_type,
            name=name,
            path=path,
            metadata=metadata,
            content=content,
            filetags=tags,
        )
