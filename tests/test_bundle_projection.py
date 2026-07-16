"""Adversarial tests for pure Ponytail bundle projection."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from promptdeploy import bundle_projection as projection
from promptdeploy.imported_tree import (
    ImportedTreeEntry,
    ImportedTreeSnapshot,
    framed_tree_sha256,
)
from promptdeploy.manifest import ManifestSource
from promptdeploy.ponytail import (
    CLAUDE_CODEX_RUNTIME_PAYLOAD,
    OPENCODE_PLUGIN_PAYLOAD,
    PONYTAIL_ALL_TARGET_TYPES,
)
from promptdeploy.source import BundlePayload, SourceItem, SourceProvenance


def _snapshot(
    logical_root: str, entries: tuple[ImportedTreeEntry, ...]
) -> ImportedTreeSnapshot:
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    return ImportedTreeSnapshot(
        logical_root,
        ordered,
        framed_tree_sha256(ordered),
    )


def _claude_codex_snapshot(runtime: bytes = b"runtime\n") -> ImportedTreeSnapshot:
    skill = b"# Ponytail\n"
    return _snapshot(
        projection.CLAUDE_CODEX_LOGICAL_ROOT,
        (
            ImportedTreeEntry("directory", ".", 0o755),
            ImportedTreeEntry("directory", "hooks", 0o755),
            ImportedTreeEntry(
                "file",
                "hooks/claude-codex-hooks.json",
                0o644,
                b'{"hooks":{}}\n',
            ),
            ImportedTreeEntry("file", "hooks/runtime.js", 0o755, runtime),
            ImportedTreeEntry("directory", "skills", 0o755),
            ImportedTreeEntry("directory", "skills/ponytail", 0o755),
            ImportedTreeEntry("file", "skills/ponytail/SKILL.md", 0o644, skill),
            ImportedTreeEntry(
                "link",
                "skills/ponytail/alias.md",
                0o644,
                skill,
                "skills/ponytail/SKILL.md",
            ),
            ImportedTreeEntry("directory", "skills/ponytail/empty", 0o755),
        ),
    )


def _opencode_snapshot(plugin: bytes = b"export default {};\n") -> ImportedTreeSnapshot:
    skill = b"# Ponytail\n"
    return _snapshot(
        projection.OPENCODE_LOGICAL_ROOT,
        (
            ImportedTreeEntry("directory", ".", 0o755),
            ImportedTreeEntry("directory", ".opencode", 0o755),
            ImportedTreeEntry("directory", ".opencode/plugins", 0o755),
            ImportedTreeEntry("file", ".opencode/plugins/ponytail.mjs", 0o644, plugin),
            ImportedTreeEntry("directory", "hooks", 0o755),
            ImportedTreeEntry(
                "file", "hooks/ponytail-instructions.js", 0o644, b"module\n"
            ),
            ImportedTreeEntry("directory", "skills", 0o755),
            ImportedTreeEntry("directory", "skills/ponytail", 0o755),
            ImportedTreeEntry("file", "skills/ponytail/SKILL.md", 0o644, skill),
            ImportedTreeEntry(
                "link",
                "skills/ponytail/alias.md",
                0o644,
                skill,
                "skills/ponytail/SKILL.md",
            ),
        ),
    )


def _bundle(
    *,
    license_content: bytes = b"MIT license\n",
    claude_codex: ImportedTreeSnapshot | None = None,
    opencode: ImportedTreeSnapshot | None = None,
) -> SourceItem:
    source = ManifestSource(
        bundle="ponytail",
        path="LICENSE",
        version="4.8.4",
        revision=None,
        nar_hash=None,
        mutable=True,
        transform=None,
        license="MIT",
    )
    content_sha256 = f"sha256:{hashlib.sha256(license_content).hexdigest()}"
    return SourceItem(
        item_type="bundle",
        name="ponytail",
        path=Path("/source-deleted-before-render/LICENSE"),
        metadata={"spdx": "MIT"},
        content=license_content,
        provenance=SourceProvenance.imported(
            source,
            input_sha256=content_sha256,
        ),
        target_types=PONYTAIL_ALL_TARGET_TYPES,
        bundle_payloads=(
            BundlePayload(
                CLAUDE_CODEX_RUNTIME_PAYLOAD,
                frozenset({"claude", "codex"}),
                claude_codex or _claude_codex_snapshot(),
            ),
            BundlePayload(
                OPENCODE_PLUGIN_PAYLOAD,
                frozenset({"opencode"}),
                opencode or _opencode_snapshot(),
            ),
        ),
    )


def _registration(
    target_type: str, payload: bytes = b"registration"
) -> projection.RegistrationProjection:
    abi = {
        "claude": "claude-settings-hooks-v1",
        "codex": "codex-hooks-json-v1",
    }[target_type]
    return projection.RegistrationProjection(
        abi=abi,
        owner=projection.REGISTRATION_OWNER,
        sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
    )


@pytest.mark.parametrize(
    ("target_type", "payload_name", "logical_root"),
    [
        ("claude", CLAUDE_CODEX_RUNTIME_PAYLOAD, "runtime/claude-codex"),
        ("codex", CLAUDE_CODEX_RUNTIME_PAYLOAD, "runtime/claude-codex"),
        ("opencode", OPENCODE_PLUGIN_PAYLOAD, "runtime/opencode"),
        ("droid", projection.SUPPORT_PAYLOAD, "support/ponytail"),
        ("gptel", projection.SUPPORT_PAYLOAD, "support/ponytail"),
    ],
)
def test_exact_five_target_selection(
    target_type: str, payload_name: str, logical_root: str
) -> None:
    selected = projection.select_bundle_payload(_bundle(), target_type)
    assert selected.name == payload_name
    assert selected.logical_root == logical_root
    assert selected.target_type == target_type
    assert selected.payload_tree_sha256 == selected.snapshot.tree_sha256


@pytest.mark.parametrize(
    "target_type", ["", "Claude", "copilot", "unknown", "claude\n"]
)
def test_unknown_target_is_rejected(target_type: str) -> None:
    with pytest.raises(projection.BundleProjectionError, match="unsupported"):
        projection.select_bundle_payload(_bundle(), target_type)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "runtime"}, "versioned"),
        ({"name": "Runtime-v1"}, "versioned"),
        ({"target_types": frozenset()}, "nonempty"),
        ({"target_types": frozenset({"copilot"})}, "unsupported"),
        ({"target_types": frozenset({cast(Any, 1)})}, "contain strings"),
        ({"target_types": cast(Any, ["claude"])}, "frozenset"),
        ({"imported_tree": cast(Any, "tree")}, "imported tree snapshot"),
    ],
)
def test_bundle_payload_validation_is_fail_closed(
    change: dict[str, object], message: str
) -> None:
    original = _bundle().bundle_payloads[0]
    with pytest.raises(ValueError, match=message):
        BundlePayload(
            cast(str, change.get("name", original.name)),
            cast(
                frozenset[str],
                change.get("target_types", original.target_types),
            ),
            cast(
                ImportedTreeSnapshot,
                change.get("imported_tree", original.imported_tree),
            ),
        )

    payload = object.__new__(BundlePayload)
    object.__setattr__(payload, "name", change.get("name", original.name))
    object.__setattr__(
        payload,
        "target_types",
        change.get("target_types", original.target_types),
    )
    object.__setattr__(
        payload,
        "imported_tree",
        change.get("imported_tree", original.imported_tree),
    )
    with pytest.raises(projection.BundleProjectionError, match=message):
        projection.validate_bundle_payload(payload)


def test_bundle_tuple_order_target_sets_and_roots_are_exact() -> None:
    item = _bundle()
    with pytest.raises(projection.BundleProjectionError, match="tuple"):
        projection.select_bundle_payload(
            replace(item, bundle_payloads=tuple(reversed(item.bundle_payloads))),
            "claude",
        )

    wrong_targets = replace(item.bundle_payloads[0], target_types=frozenset({"claude"}))
    with pytest.raises(projection.BundleProjectionError, match="tuple"):
        projection.select_bundle_payload(
            replace(item, bundle_payloads=(wrong_targets, item.bundle_payloads[1])),
            "claude",
        )

    wrong_root_entries = item.bundle_payloads[0].imported_tree.entries
    wrong_root = ImportedTreeSnapshot(
        "runtime/wrong",
        wrong_root_entries,
        framed_tree_sha256(wrong_root_entries),
    )
    with pytest.raises(projection.BundleProjectionError, match="tuple"):
        projection.select_bundle_payload(
            replace(
                item,
                bundle_payloads=(
                    replace(item.bundle_payloads[0], imported_tree=wrong_root),
                    item.bundle_payloads[1],
                ),
            ),
            "claude",
        )


def test_claude_projection_is_link_free_and_omits_render_input() -> None:
    rendered = projection.render_bundle(
        _bundle(), "claude", registration=_registration("claude")
    )
    assert rendered.runtime_tree is not None
    entries = {entry.relative_path: entry for entry in rendered.runtime_tree}
    assert "hooks/claude-codex-hooks.json" not in entries
    assert entries["skills/ponytail/alias.md"].kind == "file"
    assert entries["skills/ponytail/alias.md"].content == b"# Ponytail\n"
    assert entries["skills/ponytail/empty"].kind == "directory"
    assert "LICENSE" not in entries
    assert rendered.runtime_tree_sha256 == projection.installed_tree_sha256(
        rendered.runtime_tree
    )
    assert rendered.runtime_path is not None
    assert rendered.runtime_path.endswith(
        rendered.runtime_tree_sha256.removeprefix("sha256:")
    )


def test_opencode_projection_adds_license_and_exact_relative_registration() -> None:
    item = _bundle()
    rendered = projection.render_bundle(item, "opencode")
    assert rendered.runtime_tree is not None
    entries = {entry.relative_path: entry for entry in rendered.runtime_tree}
    assert entries["LICENSE"].content == item.content
    assert entries["skills/ponytail/alias.md"].kind == "file"
    assert rendered.registration is not None
    assert rendered.runtime_path is not None
    assert rendered.registration.identity == (
        f"./{rendered.runtime_path}/.opencode/plugins/ponytail.mjs"
    )
    assert "/runtimes/" not in rendered.runtime_path


@pytest.mark.parametrize("target_type", ["droid", "gptel"])
def test_support_projection_is_exact_license_only(target_type: str) -> None:
    item = _bundle()
    rendered = projection.render_bundle(item, target_type)
    assert rendered.runtime_tree is None
    assert rendered.runtime_path is None
    assert rendered.registration is None
    assert rendered.receipt.rendered_tree_sha256 is None
    assert rendered.support_tree == (
        projection.InstalledTreeEntry("directory", ".", 0o755),
        projection.InstalledTreeEntry("file", "LICENSE", 0o644, item.content),
    )


def test_projection_rejects_missing_exclusion_and_added_file_collision() -> None:
    snapshot = _claude_codex_snapshot()
    with pytest.raises(projection.BundleProjectionError, match="missing"):
        projection.project_installed_tree(
            snapshot, exclude=frozenset({"hooks/missing.json"})
        )
    with pytest.raises(projection.BundleProjectionError, match="collision"):
        projection.project_installed_tree(
            snapshot, added_files=(("hooks/runtime.js", 0o644, b"other"),)
        )


def test_target_effective_hash_binds_target_payload_and_registration() -> None:
    item = _bundle()
    claude_registration = _registration("claude", b"claude")
    claude = projection.render_bundle(item, "claude", registration=claude_registration)
    codex = projection.render_bundle(
        item, "codex", registration=_registration("codex", b"codex")
    )
    opencode = projection.render_bundle(item, "opencode")
    droid = projection.render_bundle(item, "droid")
    hashes = {
        claude.source_hash,
        codex.source_hash,
        opencode.source_hash,
        droid.source_hash,
    }
    assert len(hashes) == 4

    changed_registration = projection.render_bundle(
        item,
        "claude",
        registration=_registration("claude", b"changed"),
    )
    assert changed_registration.source_hash != claude.source_hash

    unrelated = _bundle(opencode=_opencode_snapshot(b"changed plugin\n"))
    assert (
        projection.render_bundle(
            unrelated, "claude", registration=claude_registration
        ).source_hash
        == claude.source_hash
    )

    changed_runtime = _bundle(claude_codex=_claude_codex_snapshot(b"changed runtime\n"))
    assert (
        projection.render_bundle(
            changed_runtime, "claude", registration=claude_registration
        ).source_hash
        != claude.source_hash
    )

    changed_license = _bundle(license_content=b"changed MIT license\n")
    assert (
        projection.render_bundle(
            changed_license, "claude", registration=claude_registration
        ).source_hash
        != claude.source_hash
    )


def test_registration_placeholder_is_required_and_target_specific() -> None:
    item = _bundle()
    with pytest.raises(projection.BundleProjectionError, match="requires"):
        projection.render_bundle(item, "claude")
    with pytest.raises(projection.BundleProjectionError, match="requires"):
        projection.render_bundle(item, "claude", registration=_registration("codex"))
    with pytest.raises(projection.BundleProjectionError, match="may not"):
        projection.render_bundle(item, "droid", registration=_registration("claude"))
    with pytest.raises(projection.BundleProjectionError, match="derived"):
        projection.render_bundle(item, "opencode", registration=_registration("claude"))


def test_receipt_contains_no_absolute_or_source_path() -> None:
    rendered = projection.render_bundle(
        _bundle(), "claude", registration=_registration("claude")
    )
    receipt = rendered.receipt
    assert receipt.payload_name == CLAUDE_CODEX_RUNTIME_PAYLOAD
    assert receipt.logical_root == "runtime/claude-codex"
    assert receipt.runtime_path == rendered.runtime_path
    assert rendered.registration is not None
    assert receipt.registration_sha256 == rendered.registration.sha256
    serialized = repr(receipt)
    assert "/source-deleted-before-render" not in serialized
    assert "/var/" not in serialized


def test_receipt_rejects_wrong_digest_path_and_partial_registration() -> None:
    rendered = projection.render_bundle(
        _bundle(), "claude", registration=_registration("claude")
    )
    receipt = rendered.receipt
    assert receipt.runtime_path is not None
    with pytest.raises(projection.BundleProjectionError, match="does not match"):
        wrong_path = receipt.runtime_path.rsplit("/", 1)[0] + "/" + "0" * 64
        replace(receipt, runtime_path=wrong_path)
    with pytest.raises(projection.BundleProjectionError, match="lacks registration"):
        replace(receipt, registration_sha256=None)
    digest = receipt.rendered_tree_sha256
    assert digest is not None
    leaf = digest.removeprefix("sha256:")
    for prefix in (".ssh", "staging", ".promptdeploy/bundles/ponytail"):
        with pytest.raises(projection.BundleProjectionError, match="owned namespace"):
            replace(receipt, runtime_path=f"{prefix}/{leaf}")

    opencode = projection.render_bundle(_bundle(), "opencode")
    assert opencode.receipt.runtime_path is not None
    with pytest.raises(projection.BundleProjectionError, match="owned namespace"):
        replace(
            opencode.receipt,
            runtime_path=(
                ".promptdeploy/bundles/ponytail/runtimes/"
                + opencode.receipt.runtime_path.rsplit("/", 1)[-1]
            ),
        )
    changed_identity = "./staging/.opencode/plugins/ponytail.mjs"
    with pytest.raises(projection.BundleProjectionError, match="outside its runtime"):
        replace(
            opencode.receipt,
            registration_identity=changed_identity,
            registration_sha256=projection._opencode_registration_sha256(
                changed_identity
            ),
        )


def test_opencode_registration_identity_is_self_authenticating() -> None:
    rendered = projection.render_bundle(_bundle(), "opencode")
    assert rendered.registration is not None
    with pytest.raises(projection.BundleProjectionError, match="digest"):
        replace(
            rendered.registration,
            identity="./staging/.opencode/plugins/ponytail.mjs",
        )
    with pytest.raises(projection.BundleProjectionError, match="must be absent"):
        replace(
            _registration("claude"),
            identity="./staging/.opencode/plugins/ponytail.mjs",
        )


def test_rendered_bundle_rejects_coordinated_tree_and_descriptor_drift() -> None:
    rendered = projection.render_bundle(
        _bundle(),
        "claude",
        registration=_registration("claude"),
    )
    changed_support = tuple(
        replace(entry, content=b"changed license\n")
        if entry.relative_path == "LICENSE"
        else entry
        for entry in rendered.support_tree
    )
    changed_digest = projection.installed_tree_sha256(changed_support)
    with pytest.raises(projection.BundleProjectionError, match="hash descriptor"):
        replace(
            rendered,
            support_tree=changed_support,
            support_tree_sha256=changed_digest,
        )

    changed_descriptor = replace(
        rendered.hash_descriptor,
        support_content_sha256=(
            "sha256:" + hashlib.sha256(b"changed license\n").hexdigest()
        ),
        support_tree_sha256=changed_digest,
    )
    with pytest.raises(projection.BundleProjectionError, match="receipt"):
        replace(
            rendered,
            support_tree=changed_support,
            support_tree_sha256=changed_digest,
            hash_descriptor=changed_descriptor,
        )


def test_hash_descriptor_rejects_every_target_state_mismatch() -> None:
    claude = projection.render_bundle(
        _bundle(),
        "claude",
        registration=_registration("claude"),
    ).hash_descriptor
    droid = projection.render_bundle(_bundle(), "droid").hash_descriptor
    opencode = projection.render_bundle(_bundle(), "opencode").hash_descriptor

    invalid = (
        (claude, {"bundle_name": cast(Any, "other")}, "bundle must"),
        (claude, {"source": cast(Any, "source")}, "source is invalid"),
        (claude, {"adapter_abi": "ponytail-codex-runtime-v1"}, "adapter ABI"),
        (droid, {"payload_name": CLAUDE_CODEX_RUNTIME_PAYLOAD}, "wrong payload"),
        (droid, {"runtime_tree_sha256": "sha256:" + "0" * 64}, "runtime state"),
        (claude, {"payload_name": OPENCODE_PLUGIN_PAYLOAD}, "wrong payload"),
        (
            claude,
            {"runtime_tree_sha256": None, "runtime_path": None},
            "lacks its installed tree",
        ),
        (claude, {"runtime_path": ".ssh/" + "0" * 64}, "owned namespace"),
        (
            claude,
            {
                "registration_abi": None,
                "registration_owner": None,
                "registration_sha256": None,
            },
            "lacks registration",
        ),
        (claude, {"registration_abi": "codex-hooks-json-v1"}, "registration ABI"),
    )
    for descriptor, changes, message in invalid:
        with pytest.raises(projection.BundleProjectionError, match=message):
            replace(descriptor, **changes)

    assert opencode.registration_identity is not None
    changed_identity = "./staging/.opencode/plugins/ponytail.mjs"
    with pytest.raises(projection.BundleProjectionError, match="outside its runtime"):
        replace(
            opencode,
            registration_identity=changed_identity,
            registration_sha256=projection._opencode_registration_sha256(
                changed_identity
            ),
        )


def test_rendered_bundle_rejects_nonexact_support_and_runtime_paths() -> None:
    droid = projection.render_bundle(_bundle(), "droid")
    extra_support = (
        *droid.support_tree,
        projection.InstalledTreeEntry("file", "extra", 0o644, b"extra"),
    )
    with pytest.raises(projection.BundleProjectionError, match="support tree"):
        replace(
            droid,
            support_tree=extra_support,
            support_tree_sha256=projection.installed_tree_sha256(extra_support),
        )

    claude = projection.render_bundle(
        _bundle(),
        "claude",
        registration=_registration("claude"),
    )
    assert claude.runtime_tree_sha256 is not None
    with pytest.raises(projection.BundleProjectionError, match="owned namespace"):
        replace(
            claude,
            runtime_path=(
                "staging/" + claude.runtime_tree_sha256.removeprefix("sha256:")
            ),
        )


def test_full_revalidation_catches_source_plan_and_registration_changes() -> None:
    item = _bundle()
    registration = _registration("claude")
    rendered = projection.render_bundle(item, "claude", registration=registration)
    projection.revalidate_rendered_bundle(
        item,
        "claude",
        rendered,
        registration=registration,
    )

    with pytest.raises(projection.BundleProjectionError, match="changed"):
        projection.revalidate_rendered_bundle(
            item,
            "claude",
            rendered,
            registration=_registration("claude", b"new registration"),
        )

    changed_item = replace(item, content=b"different license, stale provenance\n")
    with pytest.raises(projection.BundleProjectionError, match="LICENSE"):
        projection.revalidate_rendered_bundle(
            changed_item,
            "claude",
            rendered,
            registration=registration,
        )


def test_bundle_identity_and_provenance_are_revalidated() -> None:
    item = _bundle()
    with pytest.raises(projection.BundleProjectionError, match="bundle:ponytail"):
        projection.select_bundle_payload(replace(item, name="other"), "claude")
    assert item.provenance.source is not None
    wrong_source = replace(item.provenance.source, path="README.md")
    with pytest.raises(projection.BundleProjectionError, match="provenance"):
        projection.select_bundle_payload(
            replace(item, provenance=SourceProvenance.imported(wrong_source)),
            "claude",
        )


def test_projection_scalar_and_installed_entry_invariants() -> None:
    with pytest.raises(projection.BundleProjectionError, match="lowercase SHA-256"):
        projection._require_sha256("sha256:BAD", field="test")
    for value in ("", ".", "/absolute", "a\\b", "a/../b", "bad\npath", "e\u0301"):
        with pytest.raises(projection.BundleProjectionError, match="relative path"):
            projection._require_relative_path(value, field="test")
    with pytest.raises(projection.BundleProjectionError, match="portable UTF-8"):
        projection._require_relative_path("bad\ud800path", field="test")
    with pytest.raises(projection.BundleProjectionError, match="length limit"):
        projection._require_relative_path("x" * 4097, field="test")

    with pytest.raises(projection.BundleProjectionError, match="outside range"):
        projection.InstalledTreeEntry("file", "file", 0o10000, b"value")
    with pytest.raises(projection.BundleProjectionError, match="may not carry"):
        projection.InstalledTreeEntry("directory", ".", 0o755, b"value")
    with pytest.raises(projection.BundleProjectionError, match="must carry bytes"):
        projection.InstalledTreeEntry("file", "file", 0o644)
    with pytest.raises(projection.BundleProjectionError, match="must carry bytes"):
        projection.InstalledTreeEntry("file", "file", 0o644, cast(Any, "value"))
    with pytest.raises(projection.BundleProjectionError, match="unsupported installed"):
        projection.InstalledTreeEntry(cast(Any, "link"), "file", 0o644, b"value")


def test_selected_payload_cross_checks_snapshot_root_and_digest() -> None:
    payload = _bundle().bundle_payloads[0]
    snapshot = payload.imported_tree
    with pytest.raises(projection.BundleProjectionError, match="logical root"):
        projection.SelectedBundlePayload(
            payload.name,
            "claude",
            "runtime/other",
            snapshot.tree_sha256,
            snapshot,
        )
    with pytest.raises(projection.BundleProjectionError, match="digest"):
        projection.SelectedBundlePayload(
            payload.name,
            "claude",
            snapshot.logical_root,
            "sha256:" + "0" * 64,
            snapshot,
        )


def test_forged_payload_revalidation_rejects_nonstring_targets_and_tree() -> None:
    original = _bundle().bundle_payloads[0]
    for field, value, message in (
        ("target_types", frozenset({cast(Any, 1)}), "contain strings"),
        ("imported_tree", cast(Any, "tree"), "imported tree snapshot"),
    ):
        payload = object.__new__(BundlePayload)
        object.__setattr__(payload, "name", original.name)
        object.__setattr__(payload, "target_types", original.target_types)
        object.__setattr__(payload, "imported_tree", original.imported_tree)
        object.__setattr__(payload, field, value)
        with pytest.raises(projection.BundleProjectionError, match=message):
            projection.validate_bundle_payload(payload)


def test_projection_may_not_exclude_a_directory() -> None:
    with pytest.raises(projection.BundleProjectionError, match="omit a directory"):
        projection.project_installed_tree(
            _claude_codex_snapshot(),
            exclude=frozenset({"hooks"}),
        )


@pytest.mark.parametrize("chained", [False, True])
def test_projection_rejects_links_to_excluded_render_inputs(chained: bool) -> None:
    content = b'{"hooks": {}}\n'
    entries = [
        ImportedTreeEntry("directory", ".", 0o755),
        ImportedTreeEntry("directory", "hooks", 0o755),
        ImportedTreeEntry("file", "hooks/map.json", 0o644, content),
        ImportedTreeEntry(
            "link",
            "hooks/map-alias.json",
            0o644,
            content,
            "hooks/map.json",
        ),
    ]
    if chained:
        entries.append(
            ImportedTreeEntry(
                "link",
                "hooks/map-chain.json",
                0o644,
                content,
                "hooks/map-alias.json",
            )
        )
    snapshot = _snapshot("runtime/aliases", tuple(entries))
    with pytest.raises(projection.BundleProjectionError, match="link aliases"):
        projection.project_installed_tree(
            snapshot,
            exclude=frozenset(
                {"hooks/map-alias.json" if chained else "hooks/map.json"}
            ),
        )


def test_registration_projection_rejects_owner_digest_and_identity() -> None:
    digest = "sha256:" + "0" * 64
    with pytest.raises(projection.BundleProjectionError, match="unsupported"):
        projection.RegistrationProjection(
            "other-registration-v1",
            projection.REGISTRATION_OWNER,
            digest,
        )
    with pytest.raises(projection.BundleProjectionError, match="owner"):
        projection.RegistrationProjection("claude-settings-hooks-v1", "other", digest)
    with pytest.raises(projection.BundleProjectionError, match="lowercase SHA-256"):
        projection.RegistrationProjection(
            "claude-settings-hooks-v1",
            projection.REGISTRATION_OWNER,
            "bad",
        )
    with pytest.raises(projection.BundleProjectionError, match="relative plugin"):
        projection.RegistrationProjection(
            "opencode-plugin-array-v1",
            projection.REGISTRATION_OWNER,
            digest,
            "/absolute",
        )
    identity = "./runtime/other-plugin.mjs"
    with pytest.raises(projection.BundleProjectionError, match="does not name"):
        projection.RegistrationProjection(
            "opencode-plugin-array-v1",
            projection.REGISTRATION_OWNER,
            projection._opencode_registration_sha256(identity),
            identity,
        )


def test_bundle_receipt_rejects_every_support_and_runtime_mismatch() -> None:
    droid = projection.render_bundle(_bundle(), "droid").receipt
    with pytest.raises(projection.BundleProjectionError, match="adapter ABI"):
        replace(droid, adapter_abi="ponytail-claude-runtime-v1")
    with pytest.raises(projection.BundleProjectionError, match="wrong payload"):
        replace(droid, payload_name=CLAUDE_CODEX_RUNTIME_PAYLOAD)
    with pytest.raises(projection.BundleProjectionError, match="wrong logical root"):
        replace(droid, logical_root="support/other")
    with pytest.raises(projection.BundleProjectionError, match="may not claim"):
        replace(droid, rendered_tree_sha256="sha256:" + "0" * 64)

    claude = projection.render_bundle(
        _bundle(), "claude", registration=_registration("claude")
    ).receipt
    with pytest.raises(projection.BundleProjectionError, match="wrong payload"):
        replace(claude, payload_name=OPENCODE_PLUGIN_PAYLOAD)
    with pytest.raises(projection.BundleProjectionError, match="wrong payload"):
        replace(claude, logical_root=projection.OPENCODE_LOGICAL_ROOT)
    with pytest.raises(projection.BundleProjectionError, match="lacks its installed"):
        replace(claude, rendered_tree_sha256=None, runtime_path=None)
    with pytest.raises(projection.BundleProjectionError, match="registration ABI"):
        replace(claude, registration_abi="codex-hooks-json-v1")
    with pytest.raises(projection.BundleProjectionError, match="owner is invalid"):
        replace(claude, registration_owner="other")
    with pytest.raises(projection.BundleProjectionError, match="registration digest"):
        replace(claude, registration_sha256="bad")


def test_rendered_bundle_rejects_each_inconsistent_field() -> None:
    item = _bundle()
    claude = projection.render_bundle(
        item, "claude", registration=_registration("claude")
    )
    droid = projection.render_bundle(item, "droid")
    codex = projection.render_bundle(item, "codex", registration=_registration("codex"))
    with pytest.raises(projection.BundleProjectionError, match="name"):
        replace(claude, name=cast(Any, "other"))
    with pytest.raises(
        projection.BundleProjectionError, match="does not match selection"
    ):
        replace(claude, selected=codex.selected)
    with pytest.raises(projection.BundleProjectionError, match="adapter ABI"):
        replace(claude, adapter_abi="ponytail-codex-runtime-v1")
    with pytest.raises(projection.BundleProjectionError, match="support tree digest"):
        replace(claude, support_tree_sha256="sha256:" + "0" * 64)
    with pytest.raises(projection.BundleProjectionError, match="claims runtime"):
        replace(droid, runtime_tree_sha256="sha256:" + "0" * 64)
    assert claude.runtime_tree is not None
    changed_tree = tuple(
        replace(entry, normalized_mode=0o600) if entry.kind == "file" else entry
        for entry in claude.runtime_tree
    )
    with pytest.raises(projection.BundleProjectionError, match="runtime tree digest"):
        replace(claude, runtime_tree=changed_tree)
    with pytest.raises(projection.BundleProjectionError, match="has a registration"):
        replace(droid, registration=_registration("claude"))
    with pytest.raises(projection.BundleProjectionError, match="registration ABI"):
        replace(claude, registration=None)
    with pytest.raises(projection.BundleProjectionError, match="receipt"):
        replace(
            claude,
            receipt=replace(
                claude.receipt,
                effective_sha256="sha256:" + "0" * 64,
            ),
        )


def _unchecked_receipt(
    receipt: projection.BundleReceipt,
    field: str,
    value: object,
) -> projection.BundleReceipt:
    changed = object.__new__(projection.BundleReceipt)
    for name in (
        "payload_name",
        "target_type",
        "logical_root",
        "payload_tree_sha256",
        "adapter_abi",
        "rendered_tree_sha256",
        "runtime_path",
        "registration_abi",
        "registration_owner",
        "registration_sha256",
        "registration_identity",
        "effective_sha256",
    ):
        object.__setattr__(changed, name, getattr(receipt, name))
    object.__setattr__(changed, field, value)
    return changed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payload_name", OPENCODE_PLUGIN_PAYLOAD),
        ("target_type", "codex"),
        ("logical_root", projection.OPENCODE_LOGICAL_ROOT),
        ("payload_tree_sha256", "sha256:" + "0" * 64),
        ("adapter_abi", "ponytail-codex-runtime-v1"),
        ("rendered_tree_sha256", "sha256:" + "0" * 64),
        ("runtime_path", ".promptdeploy/bundles/ponytail/runtimes/" + "0" * 64),
        ("registration_abi", "codex-hooks-json-v1"),
        ("registration_owner", "other"),
        ("registration_sha256", "sha256:" + "0" * 64),
        ("registration_identity", "./other/plugin.mjs"),
        ("effective_sha256", "sha256:" + "0" * 64),
    ],
)
def test_rendered_bundle_rejects_each_receipt_field_tamper(
    field: str,
    value: object,
) -> None:
    rendered = projection.render_bundle(
        _bundle(), "claude", registration=_registration("claude")
    )
    with pytest.raises(projection.BundleProjectionError, match="receipt"):
        replace(
            rendered,
            receipt=_unchecked_receipt(rendered.receipt, field, value),
        )


def test_bundle_item_revalidation_covers_identity_pin_and_authority() -> None:
    item = _bundle()
    invalid_items = (
        (replace(item, item_type="skill"), "bundle:ponytail"),
        (replace(item, target_types=frozenset({"claude"})), "applicability"),
        (replace(item, provenance=SourceProvenance.primary()), "lacks imported"),
        (replace(item, content=cast(Any, "license")), "content must be bytes"),
        (
            replace(
                item,
                provenance=SourceProvenance.imported(
                    replace(
                        cast(ManifestSource, item.provenance.source), bundle="other"
                    )
                ),
            ),
            "provenance",
        ),
        (
            replace(
                item,
                provenance=SourceProvenance.imported(
                    replace(
                        cast(ManifestSource, item.provenance.source), version="4.8.5"
                    )
                ),
            ),
            "pin",
        ),
        (
            replace(
                item,
                provenance=SourceProvenance.imported(
                    replace(cast(ManifestSource, item.provenance.source), license="BSD")
                ),
            ),
            "verbatim MIT",
        ),
        (
            replace(item, imported_tree=item.bundle_payloads[0].imported_tree),
            "unexpected tree authority",
        ),
        (replace(item, bundle_payloads=item.bundle_payloads[:1]), "exactly two"),
    )
    for changed, message in invalid_items:
        with pytest.raises(projection.BundleProjectionError, match=message):
            projection.select_bundle_payload(changed, "claude")

    source = cast(ManifestSource, item.provenance.source)
    immutable = replace(
        source,
        mutable=False,
        revision="0" * 40,
        nar_hash="sha256-" + "A" * 43 + "=",
    )
    with pytest.raises(projection.BundleProjectionError, match="pin"):
        projection.select_bundle_payload(
            replace(item, provenance=SourceProvenance.imported(immutable)),
            "claude",
        )

    transformed = replace(source, transform="other-v1")
    with pytest.raises(projection.BundleProjectionError, match="verbatim MIT"):
        projection.select_bundle_payload(
            replace(item, provenance=SourceProvenance.imported(transformed)),
            "claude",
        )

    tree_authority = replace(
        item,
        provenance=SourceProvenance.imported(
            source,
            input_sha256=item.provenance.input_sha256,
            tree_sha256=item.bundle_payloads[0].imported_tree.tree_sha256,
        ),
    )
    with pytest.raises(projection.BundleProjectionError, match="tree authority"):
        projection.select_bundle_payload(tree_authority, "claude")
