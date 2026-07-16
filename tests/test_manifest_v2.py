"""Manifest-v2 provenance, migration, and safety regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from promptdeploy import manifest as m

REVISION = "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"
NAR_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _source(**changes: object) -> m.ManifestSource:
    values: dict[str, object] = {
        "bundle": "ponytail",
        "path": "skills/ponytail/SKILL.md",
        "version": "4.8.4",
        "revision": None,
        "nar_hash": None,
        "mutable": True,
        "transform": "gptel-preset-v1",
        "license": "MIT",
    }
    values.update(changes)
    return m.ManifestSource(
        bundle=cast(str, values["bundle"]),
        path=cast(str, values["path"]),
        version=cast(str, values["version"]),
        revision=cast(str | None, values["revision"]),
        nar_hash=cast(str | None, values["nar_hash"]),
        mutable=cast(bool, values["mutable"]),
        transform=cast(str | None, values["transform"]),
        license=cast(str, values["license"]),
    )


def _source_json(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "bundle": "ponytail",
        "path": "skills/ponytail/SKILL.md",
        "version": "4.8.4",
        "revision": None,
        "narHash": None,
        "mutable": True,
        "transform": "gptel-preset-v1",
        "license": "MIT",
    }
    values.update(changes)
    return values


def _entry(*, source: object = None, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "source_hash": "sha256:content",
        "target_path": "ponytail.md",
    }
    if source is not None:
        values["source"] = source
    values.update(changes)
    return values


def _payload(
    *, version: object = m.MANIFEST_VERSION, entry: object | None = None
) -> dict[str, object]:
    return {
        "version": version,
        "deployed_at": "2026-07-16T00:00:00+00:00",
        "items": {"prompts": {"ponytail": entry or _entry()}},
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v2_imported_source_round_trip(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    mutable = _source()
    immutable = _source(
        path="skills/ponytail-review/SKILL.md",
        revision=REVISION,
        nar_hash=NAR_HASH,
        mutable=False,
        transform=None,
    )
    manifest = m.Manifest(deployed_at="2026-07-16T00:00:00+00:00")
    manifest.items = {
        "prompts": {
            "ponytail": m.ManifestItem(
                source_hash="sha256:prompt",
                target_path="ponytail.md",
                source=mutable,
            )
        },
        "skills": {
            "ponytail-review": m.ManifestItem(
                source_hash="sha256:skill",
                source=immutable,
            )
        },
        "agents": {"primary": m.ManifestItem(source_hash="sha256:primary")},
    }

    m.save_manifest(manifest, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = m.load_manifest_strict(path)

    assert raw["version"] == 2
    assert "source" not in raw["items"]["agents"]["primary"]
    assert loaded.items["prompts"]["ponytail"].source == mutable
    assert loaded.items["skills"]["ponytail-review"].source == immutable
    assert loaded.items["agents"]["primary"].source is None


def test_strict_v1_is_accepted_and_saves_as_v2(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    legacy_entry = {
        "source_hash": "sha256:legacy",
        "target_path": "agents/helper.md",
        "managed_keys": ["kept"],
    }
    _write(
        path,
        {
            "version": 1,
            "deployed_at": "2026-07-15T00:00:00+00:00",
            "items": {"agents": {"helper": legacy_entry}},
        },
    )

    loaded = m.load_manifest_strict(path)
    assert loaded.version == 1
    assert loaded.items["agents"]["helper"].source is None

    m.save_manifest(loaded, path)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["items"]["agents"]["helper"] == legacy_entry


def test_missing_version_remains_strict_legacy_compatible(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, {"items": {"agents": {"helper": {"source_hash": "x"}}}})
    assert m.load_manifest_strict(path).version == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 2},
        {"version": 2, "deployed_at": "2026-07-16T00:00:00+00:00"},
    ],
)
def test_strict_rejects_existing_manifest_without_items(
    payload: dict[str, object], tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, payload)
    with pytest.raises(ValueError, match="object manifest items"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize("version", [1, 2])
def test_strict_rejects_item_without_source_hash(version: int, tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        {
            "version": version,
            "items": {"agents": {"helper": {"target_path": "agents/helper.md"}}},
        },
    )
    with pytest.raises(ValueError, match="source_hash"):
        m.load_manifest_strict(path)


DUPLICATE_MANIFEST_JSON = (
    '{"version":1,"version":2,"items":{}}',
    '{"version":2,"items":{"agents":{},"agents":{}}}',
    (
        '{"version":2,"items":{"agents":{"helper":{"source_hash":"first"},'
        '"helper":{"source_hash":"second"}}}}'
    ),
    (
        '{"version":2,"items":{"prompts":{"ponytail":{"source_hash":"x",'
        '"source":{"bundle":"ponytail","bundle":"other",'
        '"path":"skills/ponytail/SKILL.md","version":"4.8.4",'
        '"revision":null,"narHash":null,"mutable":true,'
        '"transform":"gptel-preset-v1","license":"MIT"}}}}}'
    ),
)


@pytest.mark.parametrize("raw", DUPLICATE_MANIFEST_JSON)
def test_strict_rejects_duplicate_json_keys(raw: str, tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate manifest keys"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize("raw", DUPLICATE_MANIFEST_JSON)
def test_normal_loader_falls_back_on_duplicate_json_keys(
    raw: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    path.write_text(raw, encoding="utf-8")
    assert m.load_manifest(path).items == {}
    assert "duplicate manifest key" in capsys.readouterr().err


def test_strict_nonexistent_returns_empty_v2(tmp_path: Path) -> None:
    manifest = m.load_manifest_strict(tmp_path / "missing.json")
    assert manifest.version == 2
    assert manifest.items == {}


@pytest.mark.parametrize("version", [0, 3, True, "2"])
def test_strict_rejects_unsupported_or_noninteger_version(
    version: object, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=version))
    with pytest.raises(ValueError, match=r"unsupported|integer"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "top",
        "item",
        "source-extra",
        "source-missing",
    ],
)
def test_strict_v2_rejects_unknown_item_and_source_fields(
    mutation: str, tmp_path: Path
) -> None:
    source = _source_json()
    entry = _entry(source=source)
    payload = _payload(entry=entry)
    if mutation == "top":
        payload["future"] = True
    elif mutation == "item":
        entry["future"] = True
    elif mutation == "source-extra":
        source["future"] = True
    else:
        del source["license"]
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, payload)
    with pytest.raises(ValueError, match=r"unknown|known|missing"):
        m.load_manifest_strict(path)


def test_strict_v1_rejects_v2_source_field(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=1, entry=_entry(source=_source_json())))
    with pytest.raises(ValueError, match="unknown manifest item"):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    ("changes", "message", "unsafe"),
    [
        ({"bundle": "Ponytail"}, "source bundle", True),
        ({"bundle": 3}, "source bundle", True),
        ({"path": "/absolute"}, "source path", True),
        ({"path": "skills/../escape"}, "source path", True),
        ({"path": "skills//ponytail"}, "source path", True),
        ({"path": "skills\\ponytail"}, "source path", True),
        ({"path": ""}, "source path", True),
        ({"path": 3}, "source path", True),
        ({"path": "."}, "source path", True),
        ({"path": "skills/control\nname"}, "source path", True),
        ({"path": "skills/e\u0301"}, "source path", True),
        ({"version": ""}, "source version", False),
        ({"version": " 4.8.4"}, "source version", False),
        ({"version": "4.8.4\n"}, "source version", False),
        ({"version": 4}, "source version", False),
        ({"license": ""}, "source license", False),
        ({"license": "MIT\u202e"}, "source license", False),
        ({"mutable": 1}, "mutable must be boolean", False),
        ({"revision": REVISION}, "may not claim", False),
        ({"narHash": NAR_HASH}, "may not claim", False),
        (
            {"mutable": False, "revision": None, "narHash": NAR_HASH},
            "Git revision",
            False,
        ),
        (
            {"mutable": False, "revision": "BAD", "narHash": NAR_HASH},
            "Git revision",
            False,
        ),
        (
            {"mutable": False, "revision": REVISION, "narHash": None},
            "narHash",
            False,
        ),
        (
            {"mutable": False, "revision": REVISION, "narHash": "sha256-bad"},
            "narHash",
            False,
        ),
        ({"transform": 7}, "transform", False),
        ({"transform": "Not Canonical"}, "transform", False),
    ],
)
def test_strict_rejects_invalid_source_provenance(
    changes: dict[str, object], message: str, unsafe: bool, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    source = _source_json(**changes)
    _write(path, _payload(entry=_entry(source=source)))
    exception = m.UnsafeManifestError if unsafe else ValueError
    with pytest.raises(exception, match=message):
        m.load_manifest_strict(path)


@pytest.mark.parametrize(
    "version",
    [1, 2, 3],
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bundle", "../victim"),
        ("path", "../../victim"),
    ],
)
def test_normal_loader_never_ignores_unsafe_source_provenance(
    version: int, field: str, value: str, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        _payload(
            version=version,
            entry=_entry(source=_source_json(**{field: value}), future=True),
        ),
    )
    with pytest.raises(m.UnsafeManifestError):
        m.load_manifest(path)


@pytest.mark.parametrize(
    "source",
    [
        [],
        {1: "not a string key"},
        {"bundle": "ponytail"},
        _source_json(mutable="yes"),
    ],
)
def test_normal_malformed_nonpath_source_falls_back_to_empty(
    source: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(entry=_entry(source=source)))
    assert m.load_manifest(path).items == {}
    assert "WARNING" in capsys.readouterr().err


def test_normal_future_version_keeps_safe_known_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    entry = _entry(source={**_source_json(), "future": {"ignored": True}})
    entry["other_future"] = True
    payload = _payload(version=3, entry=entry)
    payload["top_future"] = True
    _write(path, payload)

    loaded = m.load_manifest(path)
    assert loaded.version == 3
    assert loaded.items["prompts"]["ponytail"].source == _source()
    assert "version" in capsys.readouterr().err


def test_normal_v1_does_not_warn_for_supported_legacy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=1))
    assert m.load_manifest(path).version == 1
    assert capsys.readouterr().err == ""


def test_exact_partial_preserves_unselected_legacy_and_v2_source(
    tmp_path: Path,
) -> None:
    v1_path = tmp_path / "v1.json"
    _write(
        v1_path,
        {
            "version": 1,
            "items": {
                "agents": {
                    "legacy": {
                        "source_hash": "sha256:legacy",
                        "target_path": "agents/legacy.md",
                        "managed_keys": ["old"],
                    }
                }
            },
        },
    )
    legacy = m.load_manifest_strict(v1_path).items["agents"]["legacy"]

    v2_path = tmp_path / "v2.json"
    _write(v2_path, _payload(entry=_entry(source=_source_json())))
    imported = m.load_manifest_strict(v2_path).items["prompts"]["ponytail"]

    partial = m.Manifest(
        items={
            "agents": {"legacy": legacy},
            "prompts": {"ponytail": imported},
            "commands": {"selected": m.ManifestItem("sha256:new")},
        }
    )
    output = tmp_path / "partial.json"
    m.save_manifest(partial, output)
    raw = json.loads(output.read_text(encoding="utf-8"))

    assert raw["version"] == 2
    assert raw["items"]["agents"]["legacy"] == {
        "source_hash": "sha256:legacy",
        "target_path": "agents/legacy.md",
        "managed_keys": ["old"],
    }
    assert raw["items"]["prompts"]["ponytail"]["source"] == _source_json()


def test_v1_primary_hash_remains_current() -> None:
    manifest = m.Manifest(
        version=1,
        items={"agents": {"helper": m.ManifestItem("sha256:same")}},
    )
    assert not m.has_changed(manifest, "agents", "helper", "sha256:same")
    assert not m.has_item_changed(manifest, "agents", "helper", "sha256:same", None)


def test_v1_missing_source_never_matches_import() -> None:
    manifest = m.Manifest(
        version=1,
        items={"skills": {"ponytail": m.ManifestItem("sha256:same")}},
    )
    assert m.has_item_changed(
        manifest, "skills", "ponytail", "sha256:same", _source(transform=None)
    )


@pytest.mark.parametrize(
    ("category", "name", "source_hash", "expected", "changed"),
    [
        ("missing", "item", "sha256:same", None, True),
        ("agents", "missing", "sha256:same", None, True),
        ("agents", "item", "sha256:different", None, True),
        ("agents", "item", "sha256:same", None, False),
        ("agents", "item", "sha256:same", _source(), True),
    ],
)
def test_provenance_currentness_branches(
    category: str,
    name: str,
    source_hash: str,
    expected: m.ManifestSource | None,
    changed: bool,
) -> None:
    manifest = m.Manifest(items={"agents": {"item": m.ManifestItem("sha256:same")}})
    assert (
        m.has_item_changed(manifest, category, name, source_hash, expected) is changed
    )


@pytest.mark.parametrize(
    "field",
    [
        "bundle",
        "path",
        "version",
        "revision",
        "nar_hash",
        "mutable",
        "transform",
        "license",
    ],
)
def test_every_source_field_participates_in_currentness(field: str) -> None:
    current = _source()
    changes: dict[str, object] = {
        "bundle": "other",
        "path": "skills/other/SKILL.md",
        "version": "4.8.5",
        "revision": REVISION,
        "nar_hash": NAR_HASH,
        "mutable": False,
        "transform": None,
        "license": "Apache-2.0",
    }
    if field in {"revision", "nar_hash", "mutable"}:
        expected = _source(
            revision=REVISION,
            nar_hash=NAR_HASH,
            mutable=False,
        )
    else:
        expected = _source(**{field: changes[field]})
    manifest = m.Manifest(
        items={"prompts": {"ponytail": m.ManifestItem("sha256:same", source=current)}}
    )
    assert m.has_item_changed(manifest, "prompts", "ponytail", "sha256:same", expected)


def test_matching_imported_source_is_current() -> None:
    source = _source()
    manifest = m.Manifest(
        items={"prompts": {"ponytail": m.ManifestItem("sha256:same", source=source)}}
    )
    assert not m.has_item_changed(
        manifest, "prompts", "ponytail", "sha256:same", source
    )


@pytest.mark.parametrize("name", ["../victim", "Ponytail", "bad_name"])
def test_bundle_manifest_item_name_is_fail_closed(name: str, tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        {
            "version": 2,
            "items": {"bundles": {name: {"source_hash": "sha256:x"}}},
        },
    )
    with pytest.raises(m.UnsafeManifestError, match="Unsafe bundle name"):
        m.load_manifest(path)


def test_valid_bundle_manifest_item_name_loads(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(
        path,
        {
            "version": 2,
            "items": {"bundles": {"ponytail": {"source_hash": "sha256:x"}}},
        },
    )
    assert "ponytail" in m.load_manifest_strict(path).items["bundles"]


@pytest.mark.parametrize(
    "source",
    [
        _source(bundle="BAD"),
        _source(path="../escape"),
        _source(version=""),
        _source(mutable=False, revision=None, nar_hash=NAR_HASH),
        _source(transform="BAD"),
    ],
)
def test_save_revalidates_source_provenance(
    source: m.ManifestSource, tmp_path: Path
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    manifest = m.Manifest(
        items={"prompts": {"ponytail": m.ManifestItem("sha256:x", source=source)}}
    )
    with pytest.raises(ValueError):
        m.save_manifest(manifest, path)
    assert not path.exists()


@pytest.mark.parametrize(
    "items",
    [
        {1: {}},
        {"agents": []},
        {"agents": {1: m.ManifestItem("x")}},
        {"agents": {"helper": object()}},
        {"agents": {"helper": m.ManifestItem(cast(Any, 7))}},
        {"agents": {"helper": m.ManifestItem("x", managed_keys=[cast(Any, 1)])}},
        {"agents": {"helper": m.ManifestItem("x", target_path="../escape")}},
    ],
)
def test_save_rejects_malformed_in_memory_manifest(items: Any, tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    with pytest.raises(ValueError):
        m.save_manifest(m.Manifest(items=items), path)


def test_save_rejects_nonstring_timestamp(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    manifest = m.Manifest()
    manifest.deployed_at = 3  # type: ignore[assignment]
    with pytest.raises(ValueError, match="deployed_at"):
        m.save_manifest(manifest, path)


def test_save_replace_failure_preserves_original_and_cleans_temp(
    tmp_path: Path,
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    path.write_text("sentinel", encoding="utf-8")
    with (
        patch.object(os, "replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        m.save_manifest(m.Manifest(), path)
    assert path.read_text(encoding="utf-8") == "sentinel"
    assert list(tmp_path.glob("*.tmp")) == []


def test_normal_read_oserror_falls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    path.write_text("{}", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("denied")):
        assert m.load_manifest(path).items == {}
    assert "denied" in capsys.readouterr().err


def test_strict_read_oserror_is_readable_error(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    path.write_text("{}", encoding="utf-8")
    with (
        patch.object(Path, "read_text", side_effect=OSError("denied")),
        pytest.raises(ValueError, match="readable manifest"),
    ):
        m.load_manifest_strict(path)


def test_primary_hash_algorithms_are_unchanged(tmp_path: Path) -> None:
    from promptdeploy import manifest as overlay

    directory = tmp_path / "skill"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(b"---\nname: x\n---\nbody\n")
    (directory / "nested").mkdir()
    (directory / "nested" / "asset.bin").write_bytes(b"asset")

    # Fixed goldens from the version-1 framing algorithm.  These guard against
    # accidentally mixing bundle provenance into primary-source hashes.
    assert overlay.compute_file_hash(b"primary") == (
        "sha256:986a1b7135f4986150aa5fa0028feeaa66cdaf3ed6a00a355dd86e042f7fb494"
    )
    assert overlay.compute_directory_hash(directory) == (
        "sha256:7180e1151138d0c7f6ab61b0e8e9722aef9ddb4d9e5bc46a68fb6f790c1bc586"
    )


def test_normal_noninteger_version_falls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    _write(path, _payload(version=True))
    assert m.load_manifest(path).items == {}
    assert "version must be an integer" in capsys.readouterr().err


def test_strict_source_requires_mapping_with_string_keys(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="object or null"):
        m._manifest_source_from_mapping({1: "x"}, strict=True)


def test_save_cleanup_suppresses_unlink_failure(tmp_path: Path) -> None:
    path = tmp_path / m.MANIFEST_FILENAME
    original_unlink = os.unlink

    def fail_temp_unlink(candidate: str | os.PathLike[str]) -> None:
        if str(candidate).endswith(".tmp"):
            raise OSError("unlink failed")
        original_unlink(candidate)

    with (
        patch.object(os, "replace", side_effect=OSError("replace failed")),
        patch.object(os, "unlink", side_effect=fail_temp_unlink),
        pytest.raises(OSError, match="replace failed"),
    ):
        m.save_manifest(m.Manifest(), path)
