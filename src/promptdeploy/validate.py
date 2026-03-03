"""Validation for source items and configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from .config import Config
from .frontmatter import FrontmatterError, parse_frontmatter
from .source import SourceDiscovery, SourceItem


@dataclass
class ValidationIssue:
    """A single validation problem found in a source item."""

    level: str  # 'error' or 'warning'
    message: str
    file_path: Path
    line: Optional[int] = None


def validate_all(config: Config) -> List[ValidationIssue]:
    """Validate all discoverable source items against the config."""
    issues: List[ValidationIssue] = []
    discovery = SourceDiscovery(config.source_root)

    # Discover each type separately, catching parse errors during discovery
    for discover_fn, dir_name in [
        (discovery.discover_agents, "agents"),
        (discovery.discover_commands, "commands"),
        (discovery.discover_skills, "skills"),
        (discovery.discover_mcp_servers, "mcp"),
    ]:
        try:
            for item in discover_fn():
                issues.extend(validate_item(item, config))
        except (FrontmatterError, yaml.YAMLError) as e:
            # Discovery itself failed on a file - report as validation error
            issues.append(
                ValidationIssue(
                    level="error",
                    message=f"Discovery failed in {dir_name}/: {e}",
                    file_path=config.source_root / dir_name,
                )
            )
    return issues


def validate_item(item: SourceItem, config: Config) -> List[ValidationIssue]:
    """Validate a single source item."""
    issues: List[ValidationIssue] = []

    # Parse metadata
    try:
        if item.item_type == "mcp":
            metadata = yaml.safe_load(item.content.decode("utf-8"))
            if not isinstance(metadata, dict):
                metadata = None
        else:
            metadata, _ = parse_frontmatter(item.content)
    except (yaml.YAMLError, FrontmatterError) as e:
        return [
            ValidationIssue(
                level="error",
                message=f"Invalid YAML: {e}",
                file_path=item.path,
            )
        ]

    if metadata is None:
        return issues

    # Check only/except mutual exclusivity
    only = metadata.get("only")
    except_ = metadata.get("except")
    if only is not None and except_ is not None:
        issues.append(
            ValidationIssue(
                level="error",
                message="Cannot specify both 'only' and 'except'",
                file_path=item.path,
            )
        )

    # Validate environment IDs
    valid_ids = set(config.targets.keys()) | set(config.groups.keys())
    for env_list, field_name in [(only, "only"), (except_, "except")]:
        if env_list is not None:
            if not isinstance(env_list, list):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"'{field_name}' must be a list",
                        file_path=item.path,
                    )
                )
                continue
            for env_id in env_list:
                if env_id not in valid_ids:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Invalid environment ID '{env_id}' in '{field_name}'",
                            file_path=item.path,
                        )
                    )

    # MCP-specific validations
    if item.item_type == "mcp":
        if "name" not in metadata:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="MCP server missing 'name' field",
                    file_path=item.path,
                )
            )
        if "command" not in metadata and "url" not in metadata:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="MCP server missing 'command' or 'url'",
                    file_path=item.path,
                )
            )

    return issues
