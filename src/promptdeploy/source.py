"""Source item discovery for prompts, agents, skills, and MCP servers."""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from promptdeploy.filetags import parse_filetags
from promptdeploy.frontmatter import FrontmatterError, parse_frontmatter
from promptdeploy.imported_tree import ImportedTreeSnapshot
from promptdeploy.manifest import ManifestSource, validate_manifest_source
from promptdeploy.poet import (
    PLAIN_EXTENSIONS,
    POET_EXTENSIONS,
    extract_comment_frontmatter,
)
from promptdeploy.skilltree import scan_skill_source

PROMPT_EXTENSIONS = POET_EXTENSIONS | PLAIN_EXTENSIONS | {".json"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")

ItemIdentity = tuple[str, str]


def _validate_primary_path(value: str) -> str:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise ValueError(
            "Primary source provenance path must be canonical and relative"
        )
    return value


@dataclass(frozen=True)
class SourceProvenance:
    """Primary diagnostic path or checked imported manifest provenance."""

    primary_path: str | None = None
    source: ManifestSource | None = None
    input_sha256: str | None = None
    tree_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.source is None:
            if self.primary_path is not None:
                _validate_primary_path(self.primary_path)
            if self.input_sha256 is not None or self.tree_sha256 is not None:
                raise ValueError("Primary provenance cannot claim imported digests")
            return
        if self.primary_path is not None:
            raise ValueError("Imported provenance cannot claim a primary path")
        validate_manifest_source(self.source)
        for digest in (self.input_sha256, self.tree_sha256):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError("Imported provenance digest must be lowercase SHA-256")

    @classmethod
    def primary(cls, relative_path: str | None = None) -> SourceProvenance:
        return cls(primary_path=relative_path)

    @classmethod
    def imported(
        cls,
        source: ManifestSource,
        *,
        input_sha256: str | None = None,
        tree_sha256: str | None = None,
    ) -> SourceProvenance:
        return cls(
            source=source,
            input_sha256=input_sha256,
            tree_sha256=tree_sha256,
        )

    @property
    def logical_path(self) -> str | None:
        return self.source.path if self.source is not None else self.primary_path


@dataclass(frozen=True, slots=True)
class BundlePayload:
    """One accepted target-specific payload retained without source authority."""

    name: str
    target_types: frozenset[str]
    imported_tree: ImportedTreeSnapshot


@dataclass
class SourceItem:
    """A discovered source item ready for deployment."""

    # 'agent', 'bundle', 'command', 'skill', 'mcp', 'models', 'hook',
    # 'marketplace', 'prompt', or 'settings'
    item_type: str
    name: str
    path: Path
    metadata: dict[str, Any] | None
    content: bytes
    filetags: list[str] = field(default_factory=list)
    provenance: SourceProvenance = field(default_factory=SourceProvenance.primary)
    target_types: frozenset[str] | None = None
    requires: tuple[ItemIdentity, ...] = ()
    imported_tree: ImportedTreeSnapshot | None = None
    bundle_payloads: tuple[BundlePayload, ...] = ()


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

    def _primary_provenance(self, path: Path) -> SourceProvenance:
        root = Path(os.path.abspath(self.source_root))
        candidate = Path(os.path.abspath(path))
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(
                "Primary source path leaves the source repository"
            ) from exc
        return SourceProvenance.primary(relative)

    def _source_directory(self, name: str) -> Path | None:
        """Return a source-confined category directory before enumerating it."""
        boundary = self.source_root.resolve()
        path = self.source_root / name
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError(
                f"Source directory is not safely readable: {path}"
            ) from exc
        try:
            resolved = path.resolve(strict=True)
            resolved_stat = resolved.stat()
        except OSError as exc:
            raise ValueError(
                f"Source directory is not safely readable: {path}"
            ) from exc
        if not resolved.is_relative_to(boundary):
            raise ValueError("Source directory leaves the source repository")
        if not stat.S_ISDIR(resolved_stat.st_mode):
            return None
        return path

    def _read_regular_source_file(self, path: Path) -> bytes:
        """Read one regular source without following links outside the source root."""
        boundary = self.source_root.resolve()
        try:
            readable = path.resolve(strict=True)
            path_stat = readable.stat()
            if not readable.is_relative_to(boundary):
                raise ValueError("Source file leaves the source repository")
            if not stat.S_ISREG(path_stat.st_mode):
                raise ValueError("Source item must be a regular file")
            return readable.read_bytes()
        except OSError as exc:
            raise ValueError(f"Source item is not safely readable: {path}") from exc

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
        prompts_dir = self._source_directory("prompts")
        if prompts_dir is None:
            return
        for path in sorted(prompts_dir.iterdir()):
            if path.name.startswith(".") or path.suffix not in PROMPT_EXTENSIONS:
                continue
            if stat.S_ISDIR(path.lstat().st_mode):
                continue
            base_name, tags = parse_filetags(path.stem)
            content = self._read_regular_source_file(path)
            metadata = extract_comment_frontmatter(content)
            name = _resolve_name(metadata, base_name)
            yield SourceItem(
                item_type="prompt",
                name=name,
                path=path,
                metadata=metadata or None,
                content=content,
                filetags=tags,
                provenance=self._primary_provenance(path),
            )

    def discover_agents(
        self, errors: list[DiscoveryError] | None = None
    ) -> Iterator[SourceItem]:
        """Discover agent definitions from agents/*.md.

        When ``errors`` is provided, per-file frontmatter failures are
        appended to it and discovery continues with the next file; otherwise
        the first failure raises FrontmatterError.
        """
        agents_dir = self._source_directory("agents")
        if agents_dir is None:
            return
        for path in sorted(agents_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".md":
                continue
            try:
                yield self._load_markdown_item("agent", path)
            except (FrontmatterError, ValueError) as exc:
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
        commands_dir = self._source_directory("commands")
        if commands_dir is None:
            return
        for path in sorted(commands_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".md":
                continue
            try:
                yield self._load_markdown_item("command", path)
            except (FrontmatterError, ValueError) as exc:
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
        skills_dir = self._source_directory("skills")
        if skills_dir is None:
            return
        skip_names = {"README.md"}
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith(".") or entry.name in skip_names:
                continue
            if not entry.is_dir():
                continue
            try:
                _resolved_root, files = scan_skill_source(entry)
            except ValueError as exc:
                if errors is None:
                    raise ValueError(f"{entry}: {exc}") from exc
                errors.append(DiscoveryError(path=entry, message=str(exc)))
                continue
            validated_files = dict(files)
            resolved_skill_md = validated_files.get("SKILL.md")
            if resolved_skill_md is None:
                continue
            skill_md = entry / "SKILL.md"
            # Parse filetags from directory name
            base_name, tags = parse_filetags(entry.name)
            # Read only the regular, source-confined path approved above while
            # preserving the lexical source path for diagnostics/deployment.
            content = resolved_skill_md.read_bytes()
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
                provenance=self._primary_provenance(skill_md),
            )

    def broken_skill_symlinks(self) -> list[Path]:
        """Return skills/ entries that are symlinks to nonexistent targets.

        :meth:`discover_skills` necessarily skips these (``entry.is_dir()``
        follows the link and returns False), so validate surfaces them as
        warnings — otherwise a git-tracked skill silently vanishes from
        every target.
        """
        skills_dir = self._source_directory("skills")
        if skills_dir is None:
            return []
        return [
            entry
            for entry in sorted(skills_dir.iterdir())
            if entry.is_symlink() and not entry.exists()
        ]

    def discover_mcp_servers(self) -> Iterator[SourceItem]:
        """Discover MCP server configs from mcp/*.yaml."""
        mcp_dir = self._source_directory("mcp")
        if mcp_dir is None:
            return
        for path in sorted(mcp_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = self._read_regular_source_file(path)
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
                provenance=self._primary_provenance(path),
            )

    def discover_marketplaces(self) -> Iterator[SourceItem]:
        """Discover Claude marketplace configs from marketplaces/*.yaml."""
        marketplaces_dir = self._source_directory("marketplaces")
        if marketplaces_dir is None:
            return
        for path in sorted(marketplaces_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = self._read_regular_source_file(path)
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
                provenance=self._primary_provenance(path),
            )

    def discover_models(self) -> Iterator[SourceItem]:
        """Discover model definitions from models.yaml."""
        models_path = self.source_root / "models.yaml"
        if not models_path.exists():
            return
        content = self._read_regular_source_file(models_path)
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
            provenance=self._primary_provenance(models_path),
        )

    def discover_settings(self) -> Iterator[SourceItem]:
        """Discover the singleton settings master from settings.yaml."""
        settings_path = self.source_root / "settings.yaml"
        if not settings_path.exists():
            return
        content = self._read_regular_source_file(settings_path)
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
            provenance=self._primary_provenance(settings_path),
        )

    def discover_hooks(self) -> Iterator[SourceItem]:
        """Discover hook group configs from hooks/*.yaml."""
        hooks_dir = self._source_directory("hooks")
        if hooks_dir is None:
            return
        for path in sorted(hooks_dir.iterdir()):
            if path.name.startswith(".") or path.suffix != ".yaml":
                continue
            base_name, tags = parse_filetags(path.stem)
            content = self._read_regular_source_file(path)
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
                provenance=self._primary_provenance(path),
            )

    def _load_markdown_item(self, item_type: str, path: Path) -> SourceItem:
        """Load a markdown file as a SourceItem, parsing frontmatter for metadata.

        Raises FrontmatterError naming the offending file on parse failure.
        """
        base_name, tags = parse_filetags(path.stem)
        content = self._read_regular_source_file(path)
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
            provenance=self._primary_provenance(path),
        )
