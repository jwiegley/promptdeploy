"""Fail-closed traversal of skill source trees."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath


def _walk_error(error: OSError) -> None:
    raise error


_SKILL_LINK_ALLOWLIST = ".promptdeploy-skill-links.json"


def _external_link_approvals(
    lexical_source: Path, repository_root: Path
) -> dict[str, Path]:
    """Load exact auxiliary-file approvals for one top-level linked skill."""
    manifest = lexical_source.parent / _SKILL_LINK_ALLOWLIST
    try:
        manifest_stat = manifest.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError("Skill link allowlist is not safely readable") from exc
    if not stat.S_ISREG(manifest_stat.st_mode):
        raise ValueError("Skill link allowlist must be a regular file")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Skill link allowlist is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Skill link allowlist must contain an object")

    selected = data.get(lexical_source.name, {})
    if not isinstance(selected, dict):
        raise ValueError("Skill link allowlist entry must contain an object")

    approved: dict[str, Path] = {}
    for relative, target in selected.items():
        if not isinstance(relative, str) or not isinstance(target, str):
            raise ValueError("Skill link allowlist paths must be strings")
        relative_path = PurePosixPath(relative)
        target_path = PurePosixPath(target)
        if (
            relative_path.is_absolute()
            or target_path.is_absolute()
            or relative_path.as_posix() != relative
            or target_path.as_posix() != target
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or any(part in {"", ".", ".."} for part in target_path.parts)
        ):
            raise ValueError(
                "Skill link allowlist paths must be canonical and relative"
            )
        try:
            resolved = (repository_root / Path(target_path)).resolve(strict=True)
            target_stat = resolved.stat()
        except OSError as exc:
            raise ValueError(
                "Approved skill auxiliary file is not safely readable"
            ) from exc
        if not resolved.is_relative_to(repository_root) or not stat.S_ISREG(
            target_stat.st_mode
        ):
            raise ValueError(
                "Approved skill auxiliary file must be regular and repository-confined"
            )
        approved[relative_path.as_posix()] = resolved
    return approved


def _resolve_regular_symlink(
    child: Path, skill_root: Path, approved_target: Path | None = None
) -> Path:
    try:
        resolved = child.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Skill source contains a broken symlink") from exc
    if not resolved.is_relative_to(skill_root) and resolved != approved_target:
        raise ValueError("Skill source contains a symlink outside its allowed tree")
    try:
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise ValueError("Skill source contains an unreadable symlink target") from exc
    if not stat.S_ISREG(resolved_stat.st_mode):
        raise ValueError("Skill source symlinks may target regular files only")
    return resolved


def scan_skill_source(source_dir: Path) -> tuple[Path, tuple[tuple[str, Path], ...]]:
    """Resolve and validate a skill tree before any source file is opened.

    A top-level ``skills/NAME`` symlink may point elsewhere in the same
    promptdeploy repository. Nested symlinks may point only to regular files
    within that repository. Directories, regular files, and those confined
    file symlinks are the only accepted node types.
    """
    lexical_source = Path(os.path.abspath(source_dir))
    try:
        top_level_link = lexical_source.is_symlink()
        root = lexical_source.resolve(strict=True)
        repository_root = (
            lexical_source.parent.parent.resolve(strict=True)
            if lexical_source.parent.name == "skills"
            else root
        )
    except OSError as exc:
        raise ValueError("Skill source does not resolve to a readable tree") from exc
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError("Skill source does not resolve to a readable tree") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or not root.is_relative_to(repository_root):
        raise ValueError("Skill source must resolve inside its promptdeploy repository")

    approvals = (
        _external_link_approvals(lexical_source, repository_root)
        if top_level_link and lexical_source.parent.name == "skills"
        else {}
    )
    used_approvals: set[str] = set()
    discovered: list[tuple[str, Path]] = []
    try:
        for current, directories, files in os.walk(
            root, followlinks=False, onerror=_walk_error
        ):
            directories.sort()
            files.sort()
            current_path = Path(current)
            if not stat.S_ISDIR(current_path.lstat().st_mode):
                raise ValueError("Skill source directory changed during validation")

            for name in directories:
                child = current_path / name
                child_stat = child.lstat()
                if child.is_symlink():
                    resolved = child.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        raise ValueError(
                            "Skill source contains a symlink outside its tree"
                        )
                    raise ValueError("Skill source contains a directory symlink")
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise ValueError("Skill source contains a non-directory node")

            for name in files:
                child = current_path / name
                relative = child.relative_to(root).as_posix()
                child_stat = child.lstat()
                readable = child
                if stat.S_ISLNK(child_stat.st_mode):
                    approved_target = approvals.get(relative)
                    readable = _resolve_regular_symlink(child, root, approved_target)
                    if not readable.is_relative_to(root):
                        used_approvals.add(relative)
                elif not stat.S_ISREG(child_stat.st_mode):
                    raise ValueError("Skill source contains a special filesystem node")
                discovered.append((relative, readable))
    except OSError as exc:
        raise ValueError("Skill source could not be validated safely") from exc

    if used_approvals != approvals.keys():
        raise ValueError("Skill link allowlist contains an unused or mismatched entry")
    return root, tuple(discovered)
