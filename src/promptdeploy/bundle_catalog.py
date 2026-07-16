"""Strict external-bundle manifests and side-effect-free source composition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast

import yaml

from .bundles import BundleConfig, BundleSchemaError
from .filetags import parse_filetags
from .frontmatter import FrontmatterError, parse_frontmatter
from .imported_tree import (
    BundleSnapshotSession,
    DirectoryChildKind,
    ImportedDirectorySnapshot,
    ImportedFileSnapshot,
    ImportedTreeEntry,
    ImportedTreeSnapshot,
    framed_tree_sha256,
    validate_imported_tree_snapshot,
)
from .manifest import ManifestSource
from .ponytail import (
    CLAUDE_CODEX_RUNTIME_PAYLOAD,
    GPTEL_PRESET_TRANSFORM,
    ONE_SHOT_REVIEW_TRANSFORM,
    OPENCODE_PLUGIN_PAYLOAD,
    PONYTAIL_ALL_TARGET_TYPES,
    PONYTAIL_NAMES,
    PONYTAIL_REVISION,
    PONYTAIL_VERSION,
    STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM,
)
from .ponytail_transforms import PONYTAIL_RUNTIME_TRANSFORMS, PONYTAIL_TRANSFORMS
from .source import BundlePayload, ItemIdentity, SourceItem, SourceProvenance
from .yamlutil import load_unique_yaml

SUPPORT_IDENTITY: ItemIdentity = ("bundle", "ponytail")
SUPPORT_REQUIREMENT = (SUPPORT_IDENTITY,)

_ITEM_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SRI_SHA256 = re.compile(r"sha256-[A-Za-z0-9+/]{43}=\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MANIFEST_MAX_BYTES = 1024 * 1024
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)

_RUNTIME_INVENTORY: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "hooks",
        (
            "claude-codex-hooks.json",
            "copilot-hooks.json",
            "ponytail-activate.js",
            "ponytail-config.js",
            "ponytail-instructions.js",
            "ponytail-mode-tracker.js",
            "ponytail-runtime.js",
            "ponytail-statusline.ps1",
            "ponytail-statusline.sh",
            "ponytail-subagent.js",
            "qoder-hooks.json",
        ),
        "file",
    ),
    (".opencode", ("command", "plugins"), "directory"),
    (
        ".opencode/command",
        (
            "ponytail-audit.md",
            "ponytail-debt.md",
            "ponytail-gain.md",
            "ponytail-help.md",
            "ponytail-review.md",
            "ponytail.md",
        ),
        "file",
    ),
    (
        ".opencode/plugins",
        ("ponytail-frontmatter.cjs", "ponytail.mjs"),
        "file",
    ),
    ("skills", tuple(sorted(PONYTAIL_NAMES)), "directory"),
)

_CLAUDE_CODEX_RUNTIME_INCLUDE = (
    "hooks/claude-codex-hooks.json",
    "hooks/ponytail-activate.js",
    "hooks/ponytail-config.js",
    "hooks/ponytail-instructions.js",
    "hooks/ponytail-mode-tracker.js",
    "hooks/ponytail-runtime.js",
    "hooks/ponytail-statusline.sh",
    "hooks/ponytail-statusline.ps1",
    "hooks/ponytail-subagent.js",
    "skills/ponytail/SKILL.md",
)
_OPENCODE_RUNTIME_INCLUDE = (
    ".opencode/command/ponytail.md",
    ".opencode/command/ponytail-review.md",
    ".opencode/command/ponytail-audit.md",
    ".opencode/command/ponytail-debt.md",
    ".opencode/command/ponytail-gain.md",
    ".opencode/command/ponytail-help.md",
    ".opencode/plugins/ponytail.mjs",
    ".opencode/plugins/ponytail-frontmatter.cjs",
    "hooks/ponytail-config.js",
    "hooks/ponytail-instructions.js",
    *(f"skills/{name}" for name in PONYTAIL_NAMES),
)

_RUNTIME_SPECS = (
    (
        CLAUDE_CODEX_RUNTIME_PAYLOAD,
        ("claude", "codex"),
        _CLAUDE_CODEX_RUNTIME_INCLUDE,
        (
            (
                "hooks/ponytail-instructions.js",
                STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM,
            ),
            ("hooks/ponytail-mode-tracker.js", ONE_SHOT_REVIEW_TRANSFORM),
        ),
        "runtime/claude-codex",
    ),
    (
        OPENCODE_PLUGIN_PAYLOAD,
        ("opencode",),
        _OPENCODE_RUNTIME_INCLUDE,
        (
            (
                "hooks/ponytail-instructions.js",
                STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM,
            ),
        ),
        "runtime/opencode",
    ),
)


class BundleCatalogError(BundleSchemaError):
    """A composed source catalog is ambiguous or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class BundleVersion:
    value: str
    file: str
    key: str


@dataclass(frozen=True, slots=True)
class BundleLicense:
    spdx: Literal["MIT"]
    file: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BundleProjection:
    item_type: Literal["prompt"]
    name: str
    target_types: frozenset[str]
    transform: str


@dataclass(frozen=True, slots=True)
class BundleExport:
    item_type: Literal["skill"]
    name: str
    path: str
    tree_sha256: str
    skill_md_sha256: str
    target_types: frozenset[str]
    projections: tuple[BundleProjection, ...]


@dataclass(frozen=True, slots=True)
class BundleRuntimePayload:
    name: str
    target_types: frozenset[str]
    include: tuple[str, ...]
    transforms: tuple[tuple[str, str], ...]
    tree_sha256: str
    logical_root: str


@dataclass(frozen=True, slots=True)
class BundleRuntime:
    inventory: tuple[tuple[str, tuple[str, ...], str], ...]
    payloads: tuple[BundleRuntimePayload, ...]


@dataclass(frozen=True, slots=True)
class BundleManifest:
    schema: Literal[2]
    name: Literal["ponytail"]
    revision: str
    version: BundleVersion
    license: BundleLicense
    exports: tuple[BundleExport, ...]
    runtime: BundleRuntime


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    path: Path
    message: str


def _mapping(value: object, *, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BundleSchemaError(f"{where} must be a mapping with string keys")
    return cast(dict[str, object], value)


def _keys(
    value: Mapping[str, object],
    *,
    required: Set[str],
    optional: Set[str] = frozenset(),
    where: str,
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise BundleSchemaError(
            f"{where} is missing required key(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise BundleSchemaError(
            f"{where} has unknown key(s): {', '.join(sorted(unknown))}"
        )


def _trimmed_string(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BundleSchemaError(f"{where} must be a non-empty trimmed string")
    return value


def _canonical_name(value: object, *, where: str) -> str:
    if not isinstance(value, str) or _ITEM_NAME.fullmatch(value) is None:
        raise BundleSchemaError(f"{where} must be a lowercase canonical item name")
    return value


def _canonical_relative_path(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise BundleSchemaError(f"{where} must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BundleSchemaError(f"{where} must be canonical and relative")
    return value


def _sha256(value: object, *, where: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BundleSchemaError(f"{where} must be sha256 followed by lowercase hex")
    return value


def _exact_string_list(
    value: object,
    *,
    expected: Sequence[str],
    where: str,
) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BundleSchemaError(f"{where} must be a list of strings")
    if len(value) != len(set(value)):
        raise BundleSchemaError(f"{where} must not contain duplicates")
    if value != list(expected):
        raise BundleSchemaError(f"{where} must be exactly [{', '.join(expected)}]")
    return frozenset(value)


def _exact_path_list(
    value: object,
    *,
    expected: Sequence[str],
    where: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BundleSchemaError(f"{where} must be a list of paths")
    paths = tuple(
        _canonical_relative_path(item, where=f"{where}[{index}]")
        for index, item in enumerate(value)
    )
    if len(paths) != len(set(paths)):
        raise BundleSchemaError(f"{where} must not contain duplicates")
    if paths != tuple(expected):
        raise BundleSchemaError(f"{where} is not the reviewed ordered path list")
    return paths


def _stable_file_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_adapter_manifest(path: Path) -> str:
    descriptor: int | None = None
    try:
        before_path = path.stat(follow_symlinks=False)
        descriptor = os.open(path, _READ_FLAGS)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before_path.st_mode) or not stat.S_ISREG(before.st_mode):
            raise BundleSchemaError(f"{path}: manifest must be a regular file")
        if (before_path.st_dev, before_path.st_ino) != (before.st_dev, before.st_ino):
            raise BundleSchemaError(f"{path}: manifest changed during capture")
        if before.st_size > _MANIFEST_MAX_BYTES:
            raise BundleSchemaError(f"{path}: manifest exceeds the size limit")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MANIFEST_MAX_BYTES:
                raise BundleSchemaError(f"{path}: manifest exceeds the size limit")
        after = os.fstat(descriptor)
        after_path = path.stat(follow_symlinks=False)
        if _stable_file_key(before) != _stable_file_key(after) or _stable_file_key(
            before_path
        ) != _stable_file_key(after_path):
            raise BundleSchemaError(f"{path}: manifest changed during capture")
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleSchemaError(f"{path}: manifest is not valid UTF-8") from exc
    except OSError as exc:
        raise BundleSchemaError(f"{path}: manifest is not safely readable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_binding(bundle: BundleConfig) -> None:
    binding = bundle.binding
    if binding.name != bundle.name:
        raise BundleSchemaError("bundle and binding names do not match")
    if not binding.source_root.is_absolute():
        raise BundleSchemaError("bundle binding source root must be absolute")
    if binding.binding_kind not in {"descriptor", "cli"}:
        raise BundleSchemaError("bundle binding kind is invalid")
    if binding.mutable:
        if binding.revision is not None or binding.nar_hash is not None:
            raise BundleSchemaError("mutable bundle binding may not claim a pin")
    else:
        if (
            binding.revision is None
            or _GIT_REVISION.fullmatch(binding.revision) is None
            or binding.nar_hash is None
            or _SRI_SHA256.fullmatch(binding.nar_hash) is None
            or not binding.version
        ):
            raise BundleSchemaError("immutable bundle binding lacks a complete pin")


def load_bundle_manifest(bundle: BundleConfig) -> BundleManifest:
    """Load the closed Ponytail schema and reassert its source identity."""
    _validate_binding(bundle)
    path = bundle.manifest_path
    raw_text = _read_adapter_manifest(path)
    try:
        raw = load_unique_yaml(raw_text)
    except yaml.YAMLError as exc:
        raise BundleSchemaError(f"{path}: invalid YAML: {exc}") from exc

    root = _mapping(raw, where=f"{path} root")
    _keys(
        root,
        required={
            "schema",
            "name",
            "revision",
            "version",
            "license",
            "exports",
            "runtime",
        },
        where=f"{path} root",
    )
    if type(root["schema"]) is not int or root["schema"] != 2:
        raise BundleSchemaError(f"{path}: schema must be integer 2")
    name = _canonical_name(root["name"], where=f"{path} name")
    if name != bundle.name or name != "ponytail":
        raise BundleSchemaError(f"{path}: schema 2 is only for bundle 'ponytail'")
    revision = _trimmed_string(root["revision"], where=f"{path} revision")
    if revision != PONYTAIL_REVISION:
        raise BundleSchemaError(f"{path}: revision is not the reviewed Ponytail pin")
    if not bundle.binding.mutable and bundle.binding.revision != revision:
        raise BundleSchemaError(f"{path}: binding revision does not match manifest")

    raw_version = _mapping(root["version"], where=f"{path} version")
    _keys(raw_version, required={"value", "file", "key"}, where=f"{path} version")
    version = BundleVersion(
        value=_trimmed_string(raw_version["value"], where=f"{path} version.value"),
        file=_canonical_relative_path(
            raw_version["file"], where=f"{path} version.file"
        ),
        key=_trimmed_string(raw_version["key"], where=f"{path} version.key"),
    )
    if version != BundleVersion(PONYTAIL_VERSION, "package.json", "version"):
        raise BundleSchemaError(f"{path}: version tuple is not the reviewed tuple")
    if bundle.binding.version is not None and bundle.binding.version != version.value:
        raise BundleSchemaError(f"{path}: binding version does not match manifest")

    raw_license = _mapping(root["license"], where=f"{path} license")
    _keys(raw_license, required={"spdx", "file", "sha256"}, where=f"{path} license")
    if raw_license["spdx"] != "MIT":
        raise BundleSchemaError(f"{path}: schema 2 supports exactly SPDX MIT")
    license_info = BundleLicense(
        spdx="MIT",
        file=_canonical_relative_path(
            raw_license["file"], where=f"{path} license.file"
        ),
        sha256=_sha256(raw_license["sha256"], where=f"{path} license.sha256"),
    )
    if license_info.file != "LICENSE":
        raise BundleSchemaError(f"{path}: license.file must be exactly LICENSE")

    raw_exports = root["exports"]
    if not isinstance(raw_exports, list) or len(raw_exports) != len(PONYTAIL_NAMES):
        raise BundleSchemaError(f"{path}: exports must contain exactly six entries")
    exports: list[BundleExport] = []
    for index, (raw_export, expected_name) in enumerate(
        zip(raw_exports, PONYTAIL_NAMES, strict=True)
    ):
        where = f"{path} exports[{index}]"
        value = _mapping(raw_export, where=where)
        _keys(
            value,
            required={
                "type",
                "name",
                "path",
                "tree_sha256",
                "skill_md_sha256",
                "target_types",
                "projections",
            },
            where=where,
        )
        if value["type"] != "skill":
            raise BundleSchemaError(f"{where}.type must be exactly skill")
        export_name = _canonical_name(value["name"], where=f"{where}.name")
        if export_name != expected_name:
            raise BundleSchemaError(f"{where}.name is not in canonical order")
        export_path = _canonical_relative_path(value["path"], where=f"{where}.path")
        if export_path != f"skills/{export_name}":
            raise BundleSchemaError(f"{where}.path must match the exported name")
        target_types = _exact_string_list(
            value["target_types"],
            expected=("claude", "codex", "droid", "opencode"),
            where=f"{where}.target_types",
        )
        projections = value["projections"]
        if not isinstance(projections, list) or len(projections) != 1:
            raise BundleSchemaError(f"{where}.projections must contain one entry")
        projection_where = f"{where}.projections[0]"
        projection = _mapping(projections[0], where=projection_where)
        _keys(
            projection,
            required={"type", "name", "target_types", "transform"},
            where=projection_where,
        )
        if projection["type"] != "prompt":
            raise BundleSchemaError(f"{projection_where}.type must be prompt")
        projection_name = _canonical_name(
            projection["name"], where=f"{projection_where}.name"
        )
        if projection_name != export_name:
            raise BundleSchemaError(f"{projection_where}.name must match its skill")
        projection_targets = _exact_string_list(
            projection["target_types"],
            expected=("gptel",),
            where=f"{projection_where}.target_types",
        )
        if projection["transform"] != GPTEL_PRESET_TRANSFORM:
            raise BundleSchemaError(
                f"{projection_where}.transform must be {GPTEL_PRESET_TRANSFORM}"
            )
        exports.append(
            BundleExport(
                item_type="skill",
                name=export_name,
                path=export_path,
                tree_sha256=_sha256(value["tree_sha256"], where=f"{where}.tree_sha256"),
                skill_md_sha256=_sha256(
                    value["skill_md_sha256"], where=f"{where}.skill_md_sha256"
                ),
                target_types=target_types,
                projections=(
                    BundleProjection(
                        item_type="prompt",
                        name=projection_name,
                        target_types=projection_targets,
                        transform=GPTEL_PRESET_TRANSFORM,
                    ),
                ),
            )
        )

    raw_runtime = _mapping(root["runtime"], where=f"{path} runtime")
    _keys(
        raw_runtime,
        required={"inventory", "payloads"},
        where=f"{path} runtime",
    )
    raw_inventory = _mapping(
        raw_runtime["inventory"], where=f"{path} runtime.inventory"
    )
    _keys(
        raw_inventory,
        required={directory for directory, _children, _kind in _RUNTIME_INVENTORY},
        where=f"{path} runtime.inventory",
    )
    for directory, children, _kind in _RUNTIME_INVENTORY:
        _exact_string_list(
            raw_inventory[directory],
            expected=children,
            where=f"{path} runtime.inventory.{directory}",
        )

    raw_payloads = raw_runtime["payloads"]
    if not isinstance(raw_payloads, list) or len(raw_payloads) != len(_RUNTIME_SPECS):
        raise BundleSchemaError(
            f"{path}: runtime.payloads must contain exactly two entries"
        )
    runtime_payloads: list[BundleRuntimePayload] = []
    for index, (raw_payload, spec) in enumerate(
        zip(raw_payloads, _RUNTIME_SPECS, strict=True)
    ):
        (
            expected_name,
            expected_targets,
            expected_include,
            expected_transforms,
            root_name,
        ) = spec
        where = f"{path} runtime.payloads[{index}]"
        value = _mapping(raw_payload, where=where)
        _keys(
            value,
            required={
                "name",
                "target_types",
                "include",
                "transforms",
                "tree_sha256",
            },
            where=where,
        )
        payload_name = _canonical_name(value["name"], where=f"{where}.name")
        if payload_name != expected_name:
            raise BundleSchemaError(f"{where}.name is not in canonical order")
        targets = _exact_string_list(
            value["target_types"],
            expected=expected_targets,
            where=f"{where}.target_types",
        )
        includes = _exact_path_list(
            value["include"],
            expected=expected_include,
            where=f"{where}.include",
        )
        raw_transforms = _mapping(value["transforms"], where=f"{where}.transforms")
        _keys(
            raw_transforms,
            required={transform_path for transform_path, _name in expected_transforms},
            where=f"{where}.transforms",
        )
        for transform_path, transform_name in expected_transforms:
            if raw_transforms[transform_path] != transform_name:
                raise BundleSchemaError(
                    f"{where}.transforms.{transform_path} must be {transform_name}"
                )
        runtime_payloads.append(
            BundleRuntimePayload(
                name=payload_name,
                target_types=targets,
                include=includes,
                transforms=expected_transforms,
                tree_sha256=_sha256(value["tree_sha256"], where=f"{where}.tree_sha256"),
                logical_root=root_name,
            )
        )
    return BundleManifest(
        schema=2,
        name="ponytail",
        revision=revision,
        version=version,
        license=license_info,
        exports=tuple(exports),
        runtime=BundleRuntime(
            inventory=_RUNTIME_INVENTORY,
            payloads=tuple(runtime_payloads),
        ),
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise BundleSchemaError(f"bundle version file has duplicate key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise BundleSchemaError(f"bundle version file contains invalid constant {value}")


def _validate_metadata_files(
    session: BundleSnapshotSession,
    manifest: BundleManifest,
) -> bytes:
    version_file = session.read_regular(manifest.version.file)
    try:
        version_text = version_file.content.decode("utf-8")
        version_document = json.loads(
            version_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except BundleSchemaError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BundleSchemaError("bundle version file must be valid UTF-8 JSON") from exc
    if not isinstance(version_document, dict):
        raise BundleSchemaError("bundle version file must contain an object")
    if version_document.get(manifest.version.key) != manifest.version.value:
        raise BundleSchemaError("bundle source version does not match manifest")

    license_file = session.read_regular(manifest.license.file)
    content = license_file.content
    if not content.strip():
        raise BundleSchemaError("bundle license file is empty")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleSchemaError("bundle license file must be valid UTF-8") from exc
    actual = _content_sha256(content)
    if actual != manifest.license.sha256:
        raise BundleSchemaError(
            f"bundle license digest mismatch: expected {manifest.license.sha256}, "
            f"got {actual}"
        )
    return content


def _content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _manifest_source(
    bundle: BundleConfig,
    manifest: BundleManifest,
    *,
    path: str,
    transform: str | None = None,
) -> ManifestSource:
    binding = bundle.binding
    return ManifestSource(
        bundle=bundle.name,
        path=path,
        version=manifest.version.value,
        revision=None if binding.mutable else binding.revision,
        nar_hash=None if binding.mutable else binding.nar_hash,
        mutable=binding.mutable,
        transform=transform,
        license=manifest.license.spdx,
    )


def _skill_md(entries: Sequence[ImportedTreeEntry], logical_root: str) -> bytes:
    matches = [entry for entry in entries if entry.relative_path == "SKILL.md"]
    if len(matches) != 1 or matches[0].kind not in {"file", "link"}:
        raise BundleSchemaError(f"{logical_root}: selected tree lacks regular SKILL.md")
    content = matches[0].content
    if content is None:
        raise BundleSchemaError(f"{logical_root}: SKILL.md snapshot has no bytes")
    return content


def _capture_runtime_inventory(
    session: BundleSnapshotSession,
    manifest: BundleManifest,
) -> tuple[ImportedDirectorySnapshot, ...]:
    snapshots: list[ImportedDirectorySnapshot] = []
    for directory, children, child_kind in manifest.runtime.inventory:
        snapshot = session.scan_directory_shallow(directory)
        expected = tuple(
            (child, cast(DirectoryChildKind, child_kind)) for child in children
        )
        if snapshot.children != expected:
            raise BundleSchemaError(
                f"{directory}: runtime inventory mismatch; expected "
                f"{expected!r}, got {snapshot.children!r}"
            )
        snapshots.append(snapshot)
    return tuple(snapshots)


def _snapshot_regular_file(
    snapshot: ImportedTreeSnapshot,
    relative_path: str,
) -> ImportedFileSnapshot:
    matches = [
        entry for entry in snapshot.entries if entry.relative_path == relative_path
    ]
    if len(matches) != 1 or matches[0].kind not in {"file", "link"}:
        raise BundleSchemaError(
            f"{snapshot.logical_root}/{relative_path}: selected runtime file is missing"
        )
    entry = matches[0]
    assert entry.content is not None
    return ImportedFileSnapshot(
        f"{snapshot.logical_root}/{relative_path}",
        entry.normalized_mode,
        entry.content,
    )


def _assemble_runtime_snapshot(
    runtime: BundleRuntimePayload,
    files: Mapping[str, ImportedFileSnapshot],
    skill_snapshots: Mapping[str, ImportedTreeSnapshot],
    directory_modes: Mapping[str, int],
    *,
    bundle_name: str,
    version: str,
    revision: str,
) -> ImportedTreeSnapshot:
    entries: dict[str, ImportedTreeEntry] = {}
    portable_paths: dict[str, str] = {}

    def add(entry: ImportedTreeEntry) -> None:
        previous = entries.get(entry.relative_path)
        if previous is not None:
            if previous != entry:
                raise BundleSchemaError(
                    f"{runtime.name}: conflicting runtime path {entry.relative_path!r}"
                )
            return
        folded = entry.relative_path.casefold()
        previous_path = portable_paths.get(folded)
        if previous_path is not None and previous_path != entry.relative_path:
            raise BundleSchemaError(
                f"{runtime.name}: runtime case-fold collision between "
                f"{previous_path!r} and {entry.relative_path!r}"
            )
        portable_paths[folded] = entry.relative_path
        entries[entry.relative_path] = entry

    def ensure_directory(relative_path: str) -> None:
        if relative_path != "." and "/" in relative_path:
            ensure_directory(relative_path.rsplit("/", 1)[0])
        elif relative_path != ".":
            ensure_directory(".")
        add(
            ImportedTreeEntry(
                "directory",
                relative_path,
                directory_modes.get(relative_path, 0o755),
            )
        )

    ensure_directory(".")
    transforms = dict(runtime.transforms)
    for include in runtime.include:
        skill_snapshot = skill_snapshots.get(include)
        if skill_snapshot is not None:
            parent = include.rsplit("/", 1)[0]
            ensure_directory(parent)
            for source_entry in skill_snapshot.entries:
                destination = (
                    include
                    if source_entry.relative_path == "."
                    else f"{include}/{source_entry.relative_path}"
                )
                link_target = source_entry.link_target
                if link_target is not None:
                    link_target = f"{include}/{link_target}"
                add(
                    ImportedTreeEntry(
                        source_entry.kind,
                        destination,
                        source_entry.normalized_mode,
                        source_entry.content,
                        link_target,
                    )
                )
            continue

        source_file = files.get(include)
        if source_file is None:
            raise BundleSchemaError(f"{runtime.name}: missing runtime input {include}")
        parent = include.rsplit("/", 1)[0] if "/" in include else "."
        ensure_directory(parent)
        content = source_file.content
        transform = transforms.get(include)
        if transform is not None:
            content = PONYTAIL_RUNTIME_TRANSFORMS[transform](
                content,
                bundle_name=bundle_name,
                version=version,
                revision=revision,
                logical_path=include,
            )
        add(ImportedTreeEntry("file", include, source_file.normalized_mode, content))

    ordered = tuple(sorted(entries.values(), key=lambda entry: entry.relative_path))
    snapshot = ImportedTreeSnapshot(
        runtime.logical_root,
        ordered,
        framed_tree_sha256(ordered),
    )
    validate_imported_tree_snapshot(snapshot)
    if snapshot.tree_sha256 != runtime.tree_sha256:
        raise BundleSchemaError(
            f"{runtime.name}: transformed tree digest mismatch; expected "
            f"{runtime.tree_sha256}, got {snapshot.tree_sha256}"
        )
    return snapshot


def _capture_runtime_payloads(
    session: BundleSnapshotSession,
    manifest: BundleManifest,
    skill_snapshots: Mapping[str, ImportedTreeSnapshot],
    inventory: Sequence[ImportedDirectorySnapshot],
) -> tuple[BundlePayload, ...]:
    directory_modes = {
        snapshot.relative_path: snapshot.normalized_mode for snapshot in inventory
    }
    for skill_root, snapshot in skill_snapshots.items():
        root_entry = next(
            entry
            for entry in snapshot.entries
            if entry.relative_path == "." and entry.kind == "directory"
        )
        directory_modes[skill_root] = root_entry.normalized_mode
    files: dict[str, ImportedFileSnapshot] = {}
    skill_roots = frozenset(skill_snapshots)
    selected_files = {
        include
        for runtime in manifest.runtime.payloads
        for include in runtime.include
        if include not in skill_roots
    }
    for relative_path in sorted(selected_files):
        if relative_path == "skills/ponytail/SKILL.md":
            files[relative_path] = _snapshot_regular_file(
                skill_snapshots["skills/ponytail"], "SKILL.md"
            )
        else:
            files[relative_path] = session.read_regular(relative_path)

    payloads: list[BundlePayload] = []
    for runtime in manifest.runtime.payloads:
        snapshot = _assemble_runtime_snapshot(
            runtime,
            files,
            skill_snapshots,
            directory_modes,
            bundle_name=manifest.name,
            version=manifest.version.value,
            revision=manifest.revision,
        )
        payloads.append(BundlePayload(runtime.name, runtime.target_types, snapshot))
    return tuple(payloads)


def discover_bundle_items(bundle: BundleConfig) -> tuple[SourceItem, ...]:
    """Emit the support item, six frozen skill trees, and six GPTel prompts."""
    manifest = load_bundle_manifest(bundle)
    root = bundle.binding.source_root
    with BundleSnapshotSession(root) as session:
        inventory_before = _capture_runtime_inventory(session, manifest)
        license_content = _validate_metadata_files(session, manifest)
        support_digest = _content_sha256(license_content)
        items: list[SourceItem] = []
        skill_snapshots: dict[str, ImportedTreeSnapshot] = {}
        for export in manifest.exports:
            snapshot = session.scan_tree(export.path)
            skill_snapshots[export.path] = snapshot
            if snapshot.tree_sha256 != export.tree_sha256:
                raise BundleSchemaError(
                    f"{export.path}: tree digest mismatch; expected "
                    f"{export.tree_sha256}, got {snapshot.tree_sha256}"
                )
            skill_content = _skill_md(snapshot.entries, export.path)
            skill_digest = _content_sha256(skill_content)
            if skill_digest != export.skill_md_sha256:
                raise BundleSchemaError(
                    f"{export.path}/SKILL.md: digest mismatch; expected "
                    f"{export.skill_md_sha256}, got {skill_digest}"
                )
            try:
                metadata, body = parse_frontmatter(skill_content)
            except FrontmatterError as exc:
                raise BundleSchemaError(f"{export.path}/SKILL.md: {exc}") from exc
            if not isinstance(metadata, dict) or metadata.get("name") != export.name:
                raise BundleSchemaError(
                    f"{export.path}/SKILL.md: frontmatter name must be {export.name!r}"
                )
            if not body.strip():
                raise BundleSchemaError(f"{export.path}/SKILL.md: body is empty")
            skill_path = root / export.path / "SKILL.md"
            _base_name, tags = parse_filetags(export.name)
            items.append(
                SourceItem(
                    item_type="skill",
                    name=export.name,
                    path=skill_path,
                    metadata=metadata,
                    content=skill_content,
                    filetags=tags,
                    provenance=SourceProvenance.imported(
                        _manifest_source(bundle, manifest, path=export.path),
                        input_sha256=skill_digest,
                        tree_sha256=snapshot.tree_sha256,
                    ),
                    target_types=export.target_types,
                    requires=SUPPORT_REQUIREMENT,
                    imported_tree=snapshot,
                )
            )
            for projection in export.projections:
                logical_path = f"{export.path}/SKILL.md"
                rendered = PONYTAIL_TRANSFORMS[projection.transform](
                    export.name,
                    skill_content,
                    bundle_name=manifest.name,
                    version=manifest.version.value,
                    revision=manifest.revision,
                    logical_path=logical_path,
                )
                items.append(
                    SourceItem(
                        item_type="prompt",
                        name=projection.name,
                        path=skill_path,
                        metadata=None,
                        content=rendered,
                        provenance=SourceProvenance.imported(
                            _manifest_source(
                                bundle,
                                manifest,
                                path=logical_path,
                                transform=projection.transform,
                            ),
                            input_sha256=skill_digest,
                            tree_sha256=snapshot.tree_sha256,
                        ),
                        target_types=projection.target_types,
                        requires=SUPPORT_REQUIREMENT,
                    )
                )
        payloads = _capture_runtime_payloads(
            session,
            manifest,
            skill_snapshots,
            inventory_before,
        )
        inventory_after = _capture_runtime_inventory(session, manifest)
        if inventory_after != inventory_before:
            raise BundleSchemaError("runtime inventory changed during capture")
        items.insert(
            0,
            SourceItem(
                item_type="bundle",
                name="ponytail",
                path=root / manifest.license.file,
                metadata={
                    "spdx": manifest.license.spdx,
                    "file": manifest.license.file,
                    "sha256": manifest.license.sha256,
                },
                content=license_content,
                provenance=SourceProvenance.imported(
                    _manifest_source(
                        bundle,
                        manifest,
                        path=manifest.license.file,
                    ),
                    input_sha256=support_digest,
                ),
                target_types=PONYTAIL_ALL_TARGET_TYPES,
                bundle_payloads=payloads,
            ),
        )
    return tuple(items)


def item_applies_to_target_type(item: SourceItem, target_type: str) -> bool:
    return item.target_types is None or target_type in item.target_types


def _logical_origin(item: SourceItem) -> str:
    source = item.provenance.source
    if source is not None:
        return f"{source.bundle}:{source.path}"
    return f"primary:{item.provenance.primary_path or item.path}"


def _logical_issue_path(item: SourceItem) -> Path:
    """Return a catalog diagnostic path without exposing a bound source root."""
    source = item.provenance.source
    if source is None:
        return item.path
    return Path(f"{source.bundle}:{source.path}")


def preflight_catalog(items: Sequence[SourceItem]) -> tuple[CatalogIssue, ...]:
    """Check identity, dependency, cycle, and applicability invariants."""
    issues: list[CatalogIssue] = []
    grouped: dict[ItemIdentity, list[SourceItem]] = defaultdict(list)
    for item in items:
        grouped[(item.item_type, item.name)].append(item)
    for identity in sorted(grouped):
        duplicates = grouped[identity]
        if len(duplicates) > 1:
            origins = ", ".join(_logical_origin(item) for item in duplicates)
            issues.append(
                CatalogIssue(
                    _logical_issue_path(duplicates[-1]),
                    f"duplicate source identity {identity[0]}:{identity[1]} "
                    f"from {origins}",
                )
            )

    unique = {
        identity: values[0] for identity, values in grouped.items() if len(values) == 1
    }
    for identity, item in sorted(unique.items()):
        for required in item.requires:
            required_item = unique.get(required)
            if required_item is None:
                issues.append(
                    CatalogIssue(
                        _logical_issue_path(item),
                        f"{identity[0]}:{identity[1]} requires missing or "
                        f"ambiguous {required[0]}:{required[1]}",
                    )
                )
                continue
            dependent_types = item.target_types
            requirement_types = required_item.target_types
            if dependent_types is None:
                covered = requirement_types is None
            else:
                covered = (
                    requirement_types is None or dependent_types <= requirement_types
                )
            if not covered:
                issues.append(
                    CatalogIssue(
                        _logical_issue_path(item),
                        f"{required[0]}:{required[1]} does not apply everywhere "
                        f"required by {identity[0]}:{identity[1]}",
                    )
                )

    visiting: set[ItemIdentity] = set()
    visited: set[ItemIdentity] = set()

    def visit(identity: ItemIdentity, trail: tuple[ItemIdentity, ...]) -> None:
        if identity in visited or identity not in unique:
            return
        if identity in visiting:
            cycle = (*trail[trail.index(identity) :], identity)
            rendered = " -> ".join(f"{kind}:{name}" for kind, name in cycle)
            issues.append(
                CatalogIssue(
                    _logical_issue_path(unique[identity]),
                    f"dependency cycle: {rendered}",
                )
            )
            return
        visiting.add(identity)
        for required in unique[identity].requires:
            visit(required, (*trail, identity))
        visiting.remove(identity)
        visited.add(identity)

    for identity in sorted(unique):
        visit(identity, ())
    return tuple(issues)


def preflight_name_collisions(
    items: Sequence[SourceItem],
    *,
    configured_target_types: Mapping[str, str],
    applies: Callable[[SourceItem, str, str], bool],
) -> tuple[CatalogIssue, ...]:
    """Check imported cross-type names with the caller's effective predicate."""
    issues: list[CatalogIssue] = []
    by_name: dict[str, list[SourceItem]] = defaultdict(list)
    for item in items:
        if item.item_type in {"command", "skill", "prompt"}:
            by_name[item.name].append(item)
    for name, same_name in sorted(by_name.items()):
        for left, right in combinations(same_name, 2):
            if left.item_type == right.item_type:
                continue
            if left.provenance.source is None and right.provenance.source is None:
                continue
            for target_id, target_type in sorted(configured_target_types.items()):
                if applies(left, target_id, target_type) and applies(
                    right, target_id, target_type
                ):
                    issues.append(
                        CatalogIssue(
                            _logical_issue_path(right),
                            f"imported slash-name collision {name!r}: "
                            f"{left.item_type} from {_logical_origin(left)} and "
                            f"{right.item_type} from {_logical_origin(right)} both "
                            f"apply to target {target_id!r}",
                        )
                    )
                    break
    return tuple(issues)


def raise_catalog_issues(issues: Sequence[CatalogIssue]) -> None:
    if issues:
        raise BundleCatalogError(
            "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )


def compose_catalog(
    primary_items: Iterable[SourceItem],
    bundles: Sequence[BundleConfig],
    *,
    configured_target_types: Mapping[str, str],
    applies: Callable[[SourceItem, str, str], bool],
) -> tuple[SourceItem, ...]:
    """Append bundles and run every catalog preflight for effective targets."""
    items = list(primary_items)
    for bundle in bundles:
        items.extend(discover_bundle_items(bundle))
    issues = (
        *preflight_catalog(items),
        *preflight_name_collisions(
            items,
            configured_target_types=configured_target_types,
            applies=applies,
        ),
    )
    raise_catalog_issues(issues)
    return tuple(items)


def dependency_closure_for_target(
    requested: Iterable[ItemIdentity],
    items: Sequence[SourceItem],
    *,
    target_type: str,
    requested_filter: Callable[[SourceItem], bool] | None = None,
) -> frozenset[ItemIdentity]:
    """Close applicable requested items per target; requirements bypass filters."""
    raise_catalog_issues(preflight_catalog(items))
    index = {(item.item_type, item.name): item for item in items}
    requested_identities = tuple(requested)
    for identity in requested_identities:
        if identity not in index:
            raise BundleCatalogError(
                f"unknown selected item {identity[0]}:{identity[1]}"
            )
    predicate = requested_filter or (lambda _item: True)
    selected = {
        identity
        for identity in requested_identities
        if item_applies_to_target_type(index[identity], target_type)
        and predicate(index[identity])
    }
    queue = list(selected)
    while queue:
        identity = queue.pop()
        for required in index[identity].requires:
            required_item = index[required]
            if not item_applies_to_target_type(required_item, target_type):
                raise BundleCatalogError(
                    f"{required[0]}:{required[1]} is not applicable to {target_type}"
                )
            if required not in selected:
                selected.add(required)
                queue.append(required)
    return frozenset(selected)


class _Digest(Protocol):
    def update(self, value: bytes) -> object: ...


def _frame(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def imported_tree_snapshot(item: SourceItem) -> ImportedTreeSnapshot:
    """Return one tree snapshot consistent with its logical provenance."""
    source = item.provenance.source
    if source is None:
        raise BundleCatalogError("primary item does not have an imported tree")
    snapshot = item.imported_tree
    if snapshot is None:
        raise BundleCatalogError("imported item lacks its accepted tree snapshot")
    if snapshot.logical_root != source.path:
        raise BundleCatalogError(
            "imported item snapshot root does not match its provenance"
        )
    if item.provenance.tree_sha256 != snapshot.tree_sha256:
        raise BundleCatalogError(
            "imported item tree snapshot does not match its provenance"
        )
    validate_imported_tree_snapshot(snapshot)
    return snapshot


def imported_skill_snapshot(item: SourceItem) -> ImportedTreeSnapshot:
    """Return one internally consistent imported skill snapshot."""
    if item.provenance.source is None or item.item_type != "skill":
        raise BundleCatalogError("item is not an imported skill")
    snapshot = imported_tree_snapshot(item)
    skill_content = _skill_md(snapshot.entries, snapshot.logical_root)
    if skill_content != item.content:
        raise BundleCatalogError(
            "imported skill content does not match its accepted tree snapshot"
        )
    if item.provenance.input_sha256 != _content_sha256(item.content):
        raise BundleCatalogError(
            "imported skill content digest does not match its provenance"
        )
    return snapshot


def compute_imported_source_hash(item: SourceItem) -> str:
    """Domain-frame payload identity and logical provenance for an imported item."""
    source = item.provenance.source
    if source is None:
        raise BundleCatalogError("primary items do not have imported source hashes")
    snapshot = item.imported_tree
    if item.item_type == "skill":
        snapshot = imported_skill_snapshot(item)
        payload_hash = snapshot.tree_sha256
    elif snapshot is not None:
        snapshot = imported_tree_snapshot(item)
        payload_hash = snapshot.tree_sha256
    else:
        payload_hash = _content_sha256(item.content)
    digest = hashlib.sha256()
    _frame(digest, b"promptdeploy-imported-item-v1")
    for value in (
        item.item_type,
        item.name,
        payload_hash,
        source.bundle,
        source.path,
        source.version,
        source.revision or "",
        source.nar_hash or "",
        "mutable" if source.mutable else "immutable",
        source.transform or "",
        source.license,
    ):
        _frame(digest, value.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def catalog_summary(items: Sequence[SourceItem]) -> dict[str, object]:
    """Return a logical, absolute-path-free summary for smoke verification."""
    return {
        "count": len(items),
        "identities": [f"{item.item_type}:{item.name}" for item in items],
        "items": [
            {
                "identity": f"{item.item_type}:{item.name}",
                "source_hash": (
                    compute_imported_source_hash(item)
                    if item.provenance.source is not None
                    else None
                ),
                "source": (
                    {
                        "bundle": item.provenance.source.bundle,
                        "path": item.provenance.source.path,
                        "version": item.provenance.source.version,
                        "revision": item.provenance.source.revision,
                        "narHash": item.provenance.source.nar_hash,
                        "mutable": item.provenance.source.mutable,
                        "transform": item.provenance.source.transform,
                        "license": item.provenance.source.license,
                    }
                    if item.provenance.source is not None
                    else None
                ),
                "bundle_payloads": [
                    {
                        "name": payload.name,
                        "target_types": sorted(payload.target_types),
                        "logical_root": payload.imported_tree.logical_root,
                        "tree_sha256": payload.imported_tree.tree_sha256,
                    }
                    for payload in item.bundle_payloads
                ],
            }
            for item in items
        ],
    }
