"""Manifest-v3 bundle receipt and migration regressions."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from promptdeploy import manifest as m
from promptdeploy.manifest import BundleManifestReceipt

RUNTIME_DIGEST = "sha256:" + "a" * 64
RUNTIME_PATH = ".promptdeploy/bundles/ponytail/runtimes/" + "a" * 64
EFFECTIVE_DIGEST = "sha256:" + "b" * 64


def _receipt(**changes: object) -> BundleManifestReceipt:
    values: dict[str, object] = {
        "payload_name": "claude-codex-runtime-v1",
        "target_type": "claude",
        "logical_root": "runtime/claude-codex",
        "payload_tree_sha256": "sha256:" + "c" * 64,
        "rendered_tree_sha256": RUNTIME_DIGEST,
        "adapter_abi": "ponytail-claude-runtime-v1",
        "runtime_path": RUNTIME_PATH,
        "registration_kind": "claude-hooks",
        "registration_owner": "bundle:ponytail",
        "registration_abi": "claude-settings-hooks-v1",
        "registration_sha256": "sha256:" + "d" * 64,
    }
    values.update(changes)
    return BundleManifestReceipt(**cast(Any, values))


def _receipt_json(**changes: object) -> dict[str, object]:
    receipt = _receipt()
    values: dict[str, object] = {
        "payload_name": receipt.payload_name,
        "target_type": receipt.target_type,
        "logical_root": receipt.logical_root,
        "payload_tree_sha256": receipt.payload_tree_sha256,
        "rendered_tree_sha256": receipt.rendered_tree_sha256,
        "adapter_abi": receipt.adapter_abi,
        "runtime_path": receipt.runtime_path,
        "registration_kind": receipt.registration_kind,
        "registration_owner": receipt.registration_owner,
        "registration_abi": receipt.registration_abi,
        "registration_sha256": receipt.registration_sha256,
    }
    values.update(changes)
    return values


def _payload(
    *,
    version: object = m.MANIFEST_VERSION,
    category: str = "bundles",
    name: str = "ponytail",
    entry_changes: dict[str, object] | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "source_hash": EFFECTIVE_DIGEST,
        "target_path": RUNTIME_PATH,
        "bundle_receipt": _receipt_json(),
    }
    if entry_changes is not None:
        entry.update(entry_changes)
    return {
        "version": version,
        "deployed_at": "2026-07-16T00:00:00+00:00",
        "items": {category: {name: entry}},
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v3_bundle_receipt_round_trip(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    receipt = _receipt()
    manifest = m.Manifest(
        deployed_at="2026-07-16T00:00:00+00:00",
        items={
            "bundles": {
                "ponytail": m.ManifestItem(
                    source_hash=EFFECTIVE_DIGEST,
                    target_path=receipt.runtime_path,
                    bundle_receipt=receipt,
                )
            }
        },
    )

    m.save_manifest(manifest, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = m.load_manifest_strict(path)

    assert raw == _payload()
    assert loaded.version == m.MANIFEST_VERSION
    assert loaded.items["bundles"]["ponytail"].bundle_receipt == receipt


@pytest.mark.parametrize("version", [1, 2])
def test_strict_legacy_versions_reject_bundle_receipt(
    version: int, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=version))
    with pytest.raises(ValueError, match="unknown manifest item"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    "mutation",
    ["top", "item", "receipt-extra", "receipt-missing"],
)
def test_strict_v3_rejects_unknown_or_incomplete_fields(
    mutation: str, tmp_path: Path
) -> None:
    payload = _payload()
    items = cast(dict[str, object], payload["items"])
    bundles = cast(dict[str, object], items["bundles"])
    entry = cast(dict[str, object], bundles["ponytail"])
    receipt = cast(dict[str, object], entry["bundle_receipt"])
    if mutation == "top":
        payload["future"] = True
    elif mutation == "item":
        entry["future"] = True
    elif mutation == "receipt-extra":
        receipt["future"] = True
    else:
        del receipt["adapter_abi"]
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, payload)
    with pytest.raises(ValueError, match=r"unknown|known|missing"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize("field", sorted(_receipt_json()))
def test_strict_v3_rejects_each_missing_receipt_field(
    field: str, tmp_path: Path
) -> None:
    receipt = _receipt_json()
    del receipt[field]
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(entry_changes={"bundle_receipt": receipt}))
    with pytest.raises(ValueError, match="all and only known"):
        m.load_manifest_strict(path)


def test_strict_rejects_duplicate_bundle_receipt_key(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    raw = json.dumps(_payload(), separators=(",", ":"))
    raw = raw.replace(
        '"payload_name":"claude-codex-runtime-v1",',
        '"payload_name":"first","payload_name":"claude-codex-runtime-v1",',
        1,
    )
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate manifest keys"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_payload(category="agents", name="helper"), "bundles:ponytail"),
        (_payload(name="other"), "bundles:ponytail"),
        (
            _payload(entry_changes={"target_path": "some/other/runtime"}),
            "target_path",
        ),
        (
            _payload(
                entry_changes={
                    "bundle_receipt": _receipt_json(
                        runtime_path=(
                            ".promptdeploy/bundles/ponytail/runtimes/" + "f" * 64
                        )
                    )
                }
            ),
            "rendered digest",
        ),
    ],
)
def test_strict_rejects_receipt_ownership_disagreement(
    payload: dict[str, object], message: str, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, payload)
    with pytest.raises(ValueError, match=message):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        [],
        {1: "not a string key"},
        {"payload_name": "claude-codex-runtime-v1"},
        {**_receipt_json(), "future": True},
        _receipt_json(target_type="unknown"),
        _receipt_json(logical_root="../../escape"),
        _receipt_json(registration_owner="someone-else"),
    ],
)
def test_known_v3_malformed_receipt_falls_back_for_lenient_load(
    receipt: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(entry_changes={"bundle_receipt": receipt}))
    assert m.load_manifest(path).items == {}
    error = capsys.readouterr().err
    assert "bundle" in error and "receipt" in error


def test_future_receipt_is_not_interpreted_as_ownership(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        _payload(
            version=4,
            entry_changes={
                "target_path": None,
                "bundle_receipt": {
                    "runtime_path": "../../must-not-be-used",
                    "secret": "/absolute/private/value",
                },
            },
        ),
    )

    loaded = m.load_manifest(path)

    item = loaded.items["bundles"]["ponytail"]
    assert loaded.version == 4
    assert item.bundle_receipt is None
    assert item.target_path is None
    assert "version 4" in capsys.readouterr().err


def test_complete_future_receipt_is_not_interpreted_or_reserialized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=4))

    loaded = m.load_manifest(path)

    item = loaded.items["bundles"]["ponytail"]
    assert item.bundle_receipt is None
    assert item.target_path == RUNTIME_PATH
    assert "version 4" in capsys.readouterr().err
    m.save_manifest(loaded, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "bundle_receipt" not in saved["items"]["bundles"]["ponytail"]


@pytest.mark.parametrize("target_type", ["unknown", 3])
def test_lenient_v3_checks_receipt_path_before_recoverable_semantics(
    target_type: object, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        _payload(
            entry_changes={
                "bundle_receipt": _receipt_json(
                    target_type=target_type,
                    runtime_path="../../escape",
                )
            }
        ),
    )
    with pytest.raises(m.UnsafeManifestError, match="owned namespace"):
        m.load_manifest(path)


@pytest.mark.parametrize("shape", ["missing", "extra"])
def test_lenient_v3_checks_receipt_path_before_shape_errors(
    shape: str, tmp_path: Path
) -> None:
    receipt = _receipt_json(runtime_path="../../escape")
    if shape == "missing":
        del receipt["adapter_abi"]
    else:
        receipt["future"] = True
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(entry_changes={"bundle_receipt": receipt}))
    with pytest.raises(m.UnsafeManifestError, match="owned namespace"):
        m.load_manifest(path)


@pytest.mark.parametrize(
    "manifest",
    [
        m.Manifest(
            items={
                "agents": {
                    "helper": m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path=RUNTIME_PATH,
                        bundle_receipt=_receipt(),
                    )
                }
            }
        ),
        m.Manifest(
            items={
                "bundles": {
                    "other": m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path=RUNTIME_PATH,
                        bundle_receipt=_receipt(),
                    )
                }
            }
        ),
        m.Manifest(
            items={
                "bundles": {
                    "ponytail": m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path="some/other/runtime",
                        bundle_receipt=_receipt(),
                    )
                }
            }
        ),
        m.Manifest(
            items={
                "bundles": {
                    "ponytail": m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path=RUNTIME_PATH,
                        bundle_receipt=cast(Any, object()),
                    )
                }
            }
        ),
    ],
)
def test_save_rejects_invalid_receipt_ownership(
    manifest: m.Manifest, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    with pytest.raises(ValueError, match=r"bundle_receipt|receipt"):
        m.save_manifest(manifest, path)
    assert not path.exists()


def test_save_receipt_ownership_rejects_string_subclass_bypasses(
    tmp_path: Path,
) -> None:
    class EvilStr(str):
        def __ne__(self, other: object) -> bool:
            return False

    receipt = _receipt()
    manifests = (
        m.Manifest(
            items={
                "bundles": {
                    "ponytail": m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path=EvilStr("some/other/runtime"),
                        bundle_receipt=receipt,
                    )
                }
            }
        ),
        m.Manifest(
            items={
                EvilStr("agents"): {
                    EvilStr("helper"): m.ManifestItem(
                        EFFECTIVE_DIGEST,
                        target_path=RUNTIME_PATH,
                        bundle_receipt=receipt,
                    )
                }
            }
        ),
    )
    for index, manifest in enumerate(manifests):
        path = tmp_path / f"manifest-{index}.json"
        with pytest.raises(ValueError, match=r"exact|ownership"):
            m.save_manifest(manifest, path)
        assert not path.exists()


def test_save_rejects_manifest_item_subclass_receipt_swap(tmp_path: Path) -> None:
    class EvilItem(m.ManifestItem):
        pass

    path = tmp_path / m.MANIFEST_FILENAME
    manifest = m.Manifest(
        items={
            "bundles": {
                "ponytail": EvilItem(
                    EFFECTIVE_DIGEST,
                    target_path=RUNTIME_PATH,
                    bundle_receipt=_receipt(),
                )
            }
        }
    )
    with pytest.raises(ValueError, match="ManifestItem"):
        m.save_manifest(manifest, path)
    assert not path.exists()


def test_receipt_participates_in_currentness() -> None:
    receipt = _receipt()
    manifest = m.Manifest(
        items={
            "bundles": {
                "ponytail": m.ManifestItem(
                    EFFECTIVE_DIGEST,
                    target_path=RUNTIME_PATH,
                    bundle_receipt=receipt,
                )
            }
        }
    )

    assert not m.has_item_changed(
        manifest,
        "bundles",
        "ponytail",
        EFFECTIVE_DIGEST,
        None,
        receipt,
    )
    assert m.has_item_changed(
        manifest,
        "bundles",
        "ponytail",
        EFFECTIVE_DIGEST,
        None,
        replace(receipt, registration_sha256="sha256:" + "e" * 64),
    )


def test_currentness_revalidates_receipts_before_equality() -> None:
    class EvilReceipt(BundleManifestReceipt):
        def __ne__(self, other: object) -> bool:
            return False

    receipt = _receipt()
    evil = EvilReceipt(**cast(Any, _receipt_json()))
    manifest = m.Manifest(
        items={
            "bundles": {
                "ponytail": m.ManifestItem(
                    EFFECTIVE_DIGEST,
                    target_path=RUNTIME_PATH,
                    bundle_receipt=receipt,
                )
            }
        }
    )

    with pytest.raises(ValueError, match="exact BundleManifestReceipt"):
        m.has_item_changed(
            manifest,
            "bundles",
            "ponytail",
            EFFECTIVE_DIGEST,
            None,
            evil,
        )
    manifest.items["bundles"]["ponytail"].bundle_receipt = evil
    with pytest.raises(ValueError, match="exact BundleManifestReceipt"):
        m.has_item_changed(
            manifest,
            "bundles",
            "ponytail",
            EFFECTIVE_DIGEST,
            None,
            receipt,
        )


@pytest.mark.parametrize(
    ("category", "name", "target_path"),
    [
        ("agents", "helper", RUNTIME_PATH),
        ("bundles", "other", RUNTIME_PATH),
        ("bundles", "ponytail", "wrong/runtime"),
    ],
)
@pytest.mark.parametrize(
    "current_hash",
    [EFFECTIVE_DIGEST, "sha256:" + "e" * 64],
)
def test_currentness_revalidates_stored_receipt_ownership(
    category: str, name: str, target_path: str, current_hash: str
) -> None:
    receipt = _receipt()
    manifest = m.Manifest(
        items={
            category: {
                name: m.ManifestItem(
                    EFFECTIVE_DIGEST,
                    target_path=target_path,
                    bundle_receipt=receipt,
                )
            }
        }
    )
    with pytest.raises(ValueError, match=r"bundles:ponytail|target_path"):
        m.has_item_changed(
            manifest,
            category,
            name,
            current_hash,
            None,
            receipt,
        )


def test_currentness_rejects_manifest_item_subclass() -> None:
    class EvilItem(m.ManifestItem):
        pass

    manifest = m.Manifest(items={"agents": {"helper": EvilItem(EFFECTIVE_DIGEST)}})
    with pytest.raises(ValueError, match="exact ManifestItem"):
        m.has_item_changed(
            manifest,
            "agents",
            "helper",
            EFFECTIVE_DIGEST,
            None,
        )


@pytest.mark.parametrize("field", sorted(_receipt_json()))
def test_receipt_validator_rejects_each_nonstring_field(field: str) -> None:
    forged = replace(_receipt(), **{field: cast(Any, 3)})
    with pytest.raises(ValueError, match="exact strings"):
        m.validate_bundle_manifest_receipt(forged)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("payload_name", "other-v1", "payload name"),
        ("target_type", "codex", "target type"),
        ("logical_root", "runtime/other", "logical root"),
        ("adapter_abi", "other-v1", "adapter ABI"),
        ("registration_kind", "other-hooks", "registration kind"),
        ("registration_owner", "bundle:other", "registration owner"),
        ("registration_abi", "other-hooks-v1", "registration ABI"),
    ],
)
def test_receipt_validator_rejects_each_wrong_constant(
    field: str, value: str, message: str
) -> None:
    forged = replace(_receipt(), **{field: value})
    with pytest.raises(ValueError, match=message):
        m.validate_bundle_manifest_receipt(forged)


@pytest.mark.parametrize(
    "field",
    ["payload_tree_sha256", "rendered_tree_sha256", "registration_sha256"],
)
def test_receipt_validator_rejects_each_malformed_digest(field: str) -> None:
    forged = replace(_receipt(), **{field: "sha256:BAD"})
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        m.validate_bundle_manifest_receipt(forged)


@pytest.mark.parametrize(
    "runtime_path",
    [
        "/absolute/runtime",
        "../../escape",
        ".promptdeploy//bundles/ponytail/runtimes/" + "a" * 64,
        ".promptdeploy\\bundles\\ponytail\\runtimes\\" + "a" * 64,
        ".promptdeploy/bundles/ponytail/runtimes/" + "a" * 63 + "\n",
        ".promptdeploy/bundles/ponytail/runtimes/e\u0301" + "a" * 62,
    ],
)
def test_receipt_validator_rejects_runtime_outside_owned_namespace(
    runtime_path: str,
) -> None:
    forged = replace(_receipt(), runtime_path=runtime_path)
    with pytest.raises(m.UnsafeManifestError, match="owned namespace"):
        m.validate_bundle_manifest_receipt(forged)


def test_v1_v2_migrate_with_no_receipt(tmp_path: Path) -> None:
    for version in (1, 2):
        path = tmp_path / f"v{version}.json"
        _write(
            path,
            {
                "version": version,
                "items": {"bundles": {"ponytail": {"source_hash": "legacy"}}},
            },
        )
        loaded = m.load_manifest_strict(path)
        assert loaded.items["bundles"]["ponytail"].bundle_receipt is None
        m.save_manifest(loaded, path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["version"] == m.MANIFEST_VERSION
        assert "bundle_receipt" not in saved["items"]["bundles"]["ponytail"]
