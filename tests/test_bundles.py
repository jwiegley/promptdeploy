"""External bundle declaration and source-binding tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from promptdeploy import bundles as bundle_module
from promptdeploy.bundles import (
    BundleBindingError,
    BundleConfig,
    BundleDeclaration,
    BundleSchemaError,
    BundleSourceBinding,
    load_bundle_bindings_file,
    parse_bundle_declarations,
    parse_bundle_source_overrides,
    resolve_bundle_configs,
)
from promptdeploy.config import load_config, remap_targets_to_root

REVISION = "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"
NAR_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _manifest(config_root: Path, relative: str = "bundles/ponytail.yaml") -> Path:
    path = config_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema: 1\n", encoding="utf-8")
    return path


def _declaration(config_root: Path, name: str = "ponytail") -> BundleDeclaration:
    path = _manifest(config_root)
    return BundleDeclaration(name=name, manifest_path=path)


def _binding(
    root: Path,
    *,
    name: str = "ponytail",
    mutable: bool = False,
    revision: str | None = REVISION,
    nar_hash: str | None = NAR_HASH,
    version: str | None = "4.8.4",
) -> BundleSourceBinding:
    return BundleSourceBinding(
        name=name,
        source_root=root,
        mutable=mutable,
        revision=revision,
        nar_hash=nar_hash,
        version=version,
        binding_kind="descriptor",
    )


def _write_bindings(
    path: Path,
    source_root: Path,
    *,
    mutable: bool = False,
    revision: object = REVISION,
    nar_hash: object = NAR_HASH,
    version: object = "4.8.4",
) -> None:
    binding: dict[str, object] = {
        "path": str(source_root),
        "mutable": mutable,
        "version": version,
    }
    if revision is not None:
        binding["revision"] = revision
    if nar_hash is not None:
        binding["narHash"] = nar_hash
    path.write_text(
        json.dumps({"schema": 1, "bindings": {"ponytail": binding}}),
        encoding="utf-8",
    )


class TestBundleDeclarations:
    def test_absent_is_empty(self, tmp_path: Path) -> None:
        assert parse_bundle_declarations(None, config_directory=tmp_path) == ()

    def test_valid_manifest_is_resolved_and_ordered(self, tmp_path: Path) -> None:
        first = _manifest(tmp_path, "bundles/alpha.yaml")
        second = _manifest(tmp_path, "bundles/beta.yaml")

        result = parse_bundle_declarations(
            {
                "alpha": {"manifest": "bundles/alpha.yaml"},
                "beta-2": {"manifest": "bundles/beta.yaml"},
            },
            config_directory=tmp_path,
        )

        assert result == (
            BundleDeclaration("alpha", first),
            BundleDeclaration("beta-2", second),
        )

    @pytest.mark.parametrize("raw", [[], {1: {"manifest": "x"}}])
    def test_bundles_must_be_string_mapping(self, tmp_path: Path, raw: object) -> None:
        with pytest.raises(BundleSchemaError, match="mapping with string keys"):
            parse_bundle_declarations(raw, config_directory=tmp_path)

    def test_config_directory_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(BundleSchemaError, match="directory is not readable"):
            parse_bundle_declarations(
                {"ponytail": {"manifest": "bundle.yaml"}},
                config_directory=tmp_path / "missing",
            )

    @pytest.mark.parametrize("name", ["Ponytail", "-bad", "bad_name", "bad--name"])
    def test_name_is_canonical(self, tmp_path: Path, name: str) -> None:
        with pytest.raises(BundleSchemaError, match="canonical bundle name"):
            parse_bundle_declarations(
                {name: {"manifest": "bundle.yaml"}},
                config_directory=tmp_path,
            )

    @pytest.mark.parametrize(
        "declaration",
        ["bundle.yaml", {}, {"manifest": "bundle.yaml", "extra": True}],
    )
    def test_declaration_is_closed(self, tmp_path: Path, declaration: object) -> None:
        with pytest.raises(BundleSchemaError):
            parse_bundle_declarations(
                {"ponytail": declaration},
                config_directory=tmp_path,
            )

    @pytest.mark.parametrize(
        "relative",
        ["", "/absolute", "a\\b", "a//b", "a/../b", "a/./b", "bad\nname"],
    )
    def test_manifest_path_is_canonical(self, tmp_path: Path, relative: str) -> None:
        with pytest.raises(BundleSchemaError, match=r"canonical|non-empty"):
            parse_bundle_declarations(
                {"ponytail": {"manifest": relative}},
                config_directory=tmp_path,
            )

    def test_manifest_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(BundleSchemaError, match="not safely readable"):
            parse_bundle_declarations(
                {"ponytail": {"manifest": "missing.yaml"}},
                config_directory=tmp_path,
            )

    def test_manifest_must_be_regular(self, tmp_path: Path) -> None:
        (tmp_path / "bundle").mkdir()
        with pytest.raises(BundleSchemaError, match="confined regular file"):
            parse_bundle_declarations(
                {"ponytail": {"manifest": "bundle"}},
                config_directory=tmp_path,
            )

    def test_manifest_link_cannot_escape(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside.yaml"
        outside.write_text("schema: 1\n", encoding="utf-8")
        (tmp_path / "bundle.yaml").symlink_to(outside)
        try:
            with pytest.raises(BundleSchemaError, match="confined regular file"):
                parse_bundle_declarations(
                    {"ponytail": {"manifest": "bundle.yaml"}},
                    config_directory=tmp_path,
                )
        finally:
            outside.unlink()

    def test_confined_manifest_link_cannot_be_swapped_after_parse(
        self, tmp_path: Path
    ) -> None:
        safe = tmp_path / "safe.yaml"
        safe.write_text("safe\n", encoding="utf-8")
        outside = tmp_path.parent / f"{tmp_path.name}-outside.yaml"
        outside.write_text("outside\n", encoding="utf-8")
        lexical = tmp_path / "bundle.yaml"
        lexical.symlink_to(safe)
        try:
            (declaration,) = parse_bundle_declarations(
                {"ponytail": {"manifest": "bundle.yaml"}},
                config_directory=tmp_path,
            )
            assert declaration.manifest_path == safe.resolve()

            lexical.unlink()
            lexical.symlink_to(outside)
            assert declaration.manifest_path.read_text(encoding="utf-8") == "safe\n"
        finally:
            outside.unlink()


class TestBundleOverrides:
    def test_valid_override_is_resolved(self, tmp_path: Path) -> None:
        assert parse_bundle_source_overrides([f"ponytail={tmp_path}"]) == {
            "ponytail": tmp_path.resolve()
        }

    @pytest.mark.parametrize("raw", ["ponytail", "=path", "ponytail="])
    def test_override_shape(self, raw: str) -> None:
        with pytest.raises(BundleBindingError, match="expected NAME=ABSOLUTE_PATH"):
            parse_bundle_source_overrides([raw])

    def test_override_name(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="canonical bundle name"):
            parse_bundle_source_overrides([f"BAD={tmp_path}"])

    def test_override_is_unique(self, tmp_path: Path) -> None:
        raw = f"ponytail={tmp_path}"
        with pytest.raises(BundleBindingError, match="Duplicate"):
            parse_bundle_source_overrides([raw, raw])

    def test_override_is_absolute(self) -> None:
        with pytest.raises(BundleBindingError, match="absolute path"):
            parse_bundle_source_overrides(["ponytail=relative"])

    def test_override_must_resolve(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="not safely readable"):
            parse_bundle_source_overrides([f"ponytail={tmp_path / 'missing'}"])

    def test_override_must_be_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(BundleBindingError, match="must be a directory"):
            parse_bundle_source_overrides([f"ponytail={path}"])

    def test_override_unknown_home_is_a_binding_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_expanduser(_path: Path) -> Path:
            raise RuntimeError("unknown user")

        monkeypatch.setattr(Path, "expanduser", fail_expanduser)
        with pytest.raises(BundleBindingError, match="unknown home directory"):
            parse_bundle_source_overrides(["ponytail=~missing/source"])


class TestBindingDescriptor:
    def test_valid_mutable_and_immutable(self, tmp_path: Path) -> None:
        mutable_root = tmp_path / "mutable"
        immutable_root = tmp_path / "immutable"
        mutable_root.mkdir()
        immutable_root.mkdir()
        path = tmp_path / "bindings.json"
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "bindings": {
                        "mutable": {
                            "path": str(mutable_root),
                            "mutable": True,
                            "version": None,
                        },
                        "immutable": {
                            "path": str(immutable_root),
                            "mutable": False,
                            "revision": REVISION,
                            "narHash": NAR_HASH,
                            "version": "4.8.4",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        result = load_bundle_bindings_file(path)

        assert result["mutable"].source_ref == "MUTABLE"
        assert result["mutable"].binding_kind == "descriptor"
        assert result["immutable"].source_ref == REVISION
        assert result["immutable"].nar_hash == NAR_HASH

    def test_immutable_source_ref_requires_revision(self, tmp_path: Path) -> None:
        binding = _binding(tmp_path, revision=None)
        with pytest.raises(BundleBindingError, match="lacks a revision"):
            _ = binding.source_ref

    def test_descriptor_path_is_absolute(self) -> None:
        with pytest.raises(BundleBindingError, match="path must be absolute"):
            load_bundle_bindings_file(Path("bindings.json"))

    def test_descriptor_must_be_regular(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="must be regular"):
            load_bundle_bindings_file(tmp_path)

    def test_descriptor_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="safely readable"):
            load_bundle_bindings_file(tmp_path / "missing.json")

    def test_descriptor_must_be_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.json"
        path.write_bytes(b"\xff")
        with pytest.raises(BundleBindingError, match="valid UTF-8"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize("content", ["{", "[]"])
    def test_descriptor_must_be_object_json(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(
            BundleBindingError, match=r"valid JSON|root must be an object"
        ):
            load_bundle_bindings_file(path)

    def test_descriptor_rejects_duplicate_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.json"
        path.write_text('{"schema": 1, "schema": 1, "bindings": {}}', encoding="utf-8")
        with pytest.raises(BundleBindingError, match="duplicate key"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize(
        "data, message",
        [
            ({"schema": 1}, "only schema and bindings"),
            ({"schema": True, "bindings": {}}, "integer 1"),
            ({"schema": 2, "bindings": {}}, "integer 1"),
            ({"schema": 1, "bindings": []}, "must be an object"),
        ],
    )
    def test_descriptor_root_schema(
        self, tmp_path: Path, data: object, message: str
    ) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(BundleBindingError, match=message):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize(
        "name, value, message",
        [
            ("BAD", {}, "canonical bundle name"),
            ("ponytail", [], "must be an object"),
            ("ponytail", {"path": "/tmp"}, "missing or unknown fields"),
            (
                "ponytail",
                {"path": "/tmp", "mutable": True, "extra": 1},
                "missing or unknown fields",
            ),
        ],
    )
    def test_binding_shape(
        self, tmp_path: Path, name: str, value: object, message: str
    ) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(
            json.dumps({"schema": 1, "bindings": {name: value}}),
            encoding="utf-8",
        )
        with pytest.raises(BundleBindingError, match=message):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize("value", [None, ""])
    def test_binding_path_is_nonempty_string(
        self, tmp_path: Path, value: object
    ) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "bindings": {"ponytail": {"path": value, "mutable": True}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(BundleBindingError, match="path must be a string"):
            load_bundle_bindings_file(path)

    def test_binding_path_is_absolute(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(
            '{"schema":1,"bindings":{"ponytail":{"path":"relative","mutable":true}}}',
            encoding="utf-8",
        )
        with pytest.raises(BundleBindingError, match="path must be absolute"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize("as_file", [False, True])
    def test_binding_root_must_be_directory(
        self, tmp_path: Path, as_file: bool
    ) -> None:
        root = tmp_path / "source"
        if as_file:
            root.write_text("x", encoding="utf-8")
            message = "must be a directory"
        else:
            message = "not safely readable"
        path = tmp_path / "bindings.json"
        _write_bindings(path, root, mutable=True, revision=None, nar_hash=None)
        with pytest.raises(BundleBindingError, match=message):
            load_bundle_bindings_file(path)

    def test_mutable_field_is_boolean(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.json"
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "bindings": {"ponytail": {"path": str(tmp_path), "mutable": 1}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(BundleBindingError, match="mutable must be boolean"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize("version", ["", " spaced ", 4])
    def test_version_shape(self, tmp_path: Path, version: object) -> None:
        path = tmp_path / "bindings.json"
        _write_bindings(
            path,
            tmp_path,
            mutable=True,
            revision=None,
            nar_hash=None,
            version=version,
        )
        with pytest.raises(BundleBindingError, match="version must be"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize("field", ["revision", "narHash"])
    def test_mutable_cannot_claim_immutable_metadata(
        self, tmp_path: Path, field: str
    ) -> None:
        binding: dict[str, object] = {
            "path": str(tmp_path),
            "mutable": True,
            field: REVISION if field == "revision" else NAR_HASH,
        }
        path = tmp_path / "bindings.json"
        path.write_text(
            json.dumps({"schema": 1, "bindings": {"ponytail": binding}}),
            encoding="utf-8",
        )
        with pytest.raises(BundleBindingError, match="may not claim"):
            load_bundle_bindings_file(path)

    @pytest.mark.parametrize(
        "revision, nar_hash, version, message",
        [
            (None, NAR_HASH, "4.8.4", "Git revision"),
            ("BAD", NAR_HASH, "4.8.4", "Git revision"),
            (REVISION, None, "4.8.4", "narHash"),
            (REVISION, "sha256-bad", "4.8.4", "narHash"),
            (REVISION, NAR_HASH, None, "requires a version"),
        ],
    )
    def test_immutable_metadata(
        self,
        tmp_path: Path,
        revision: object,
        nar_hash: object,
        version: object,
        message: str,
    ) -> None:
        path = tmp_path / "bindings.json"
        _write_bindings(
            path,
            tmp_path,
            revision=revision,
            nar_hash=nar_hash,
            version=version,
        )
        with pytest.raises(BundleBindingError, match=message):
            load_bundle_bindings_file(path)


class TestResolveBundleConfigs:
    def test_descriptor_and_override(self, tmp_path: Path) -> None:
        declaration = _declaration(tmp_path)
        descriptor_root = tmp_path / "descriptor"
        override_root = tmp_path / "override"
        descriptor_root.mkdir()
        override_root.mkdir()

        from_descriptor = resolve_bundle_configs(
            [declaration],
            descriptor_bindings={"ponytail": _binding(descriptor_root)},
        )
        from_override = resolve_bundle_configs(
            [declaration],
            descriptor_bindings={"ponytail": _binding(descriptor_root)},
            source_overrides={"ponytail": override_root},
        )

        assert from_descriptor[0].binding.source_root == descriptor_root
        assert from_descriptor[0].binding.source_ref == REVISION
        assert from_override[0].binding.source_root == override_root
        assert from_override[0].binding.source_ref == "MUTABLE"
        assert from_override[0].binding.binding_kind == "cli"

    def test_empty_declarations(self) -> None:
        assert resolve_bundle_configs([], descriptor_bindings={}) == ()

    def test_unknown_override(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="Unknown bundle override"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={},
                source_overrides={"other": tmp_path},
            )

    def test_direct_override_must_be_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="must be absolute"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={},
                source_overrides={"ponytail": Path("relative")},
            )

    def test_missing_binding(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="has no source binding"):
            resolve_bundle_configs([_declaration(tmp_path)], descriptor_bindings={})

    def test_binding_identity_must_match(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="contains identity"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={"ponytail": _binding(tmp_path, name="other")},
            )

    def test_immutable_gate_rejects_mutable(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="mutable source"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={
                    "ponytail": _binding(
                        tmp_path,
                        mutable=True,
                        revision=None,
                        nar_hash=None,
                    )
                },
                require_immutable=True,
            )

    def test_immutable_gate_rejects_non_store(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="not bound under /nix/store"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={"ponytail": _binding(tmp_path)},
                require_immutable=True,
            )

    def test_immutable_gate_requires_provenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_module, "_STORE_PREFIX", tmp_path)
        with pytest.raises(BundleBindingError, match="lacks immutable provenance"):
            resolve_bundle_configs(
                [_declaration(tmp_path)],
                descriptor_bindings={
                    "ponytail": _binding(tmp_path, revision=None, nar_hash=None)
                },
                require_immutable=True,
            )

    def test_immutable_gate_accepts_complete_store_tuple(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_module, "_STORE_PREFIX", tmp_path)
        result = resolve_bundle_configs(
            [_declaration(tmp_path)],
            descriptor_bindings={"ponytail": _binding(tmp_path)},
            require_immutable=True,
        )
        assert result[0].binding.revision == REVISION


class TestConfigIntegration:
    def _config(self, tmp_path: Path, *, bundles: object = None) -> Path:
        data: dict[str, object] = {"source_root": ".", "targets": {}}
        if bundles is not None:
            data["bundles"] = bundles
        path = tmp_path / "deploy.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def test_bundle_free_config_ignores_ambient_descriptor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "PROMPTDEPLOY_BUNDLE_BINDINGS_FILE", str(tmp_path / "missing.json")
        )
        config = load_config(self._config(tmp_path), require_immutable_bundles=True)
        assert config.bundles == ()

    def test_bundle_free_config_rejects_explicit_override(self, tmp_path: Path) -> None:
        with pytest.raises(BundleBindingError, match="expected NAME=ABSOLUTE_PATH"):
            load_config(
                self._config(tmp_path),
                bundle_source_overrides=["malformed"],
            )

        with pytest.raises(BundleBindingError, match="Unknown bundle override"):
            load_config(
                self._config(tmp_path),
                bundle_source_overrides=[f"ponytail={tmp_path}"],
            )

    def test_ambient_descriptor_unknown_home_is_a_binding_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _manifest(tmp_path)
        original_expanduser = Path.expanduser

        def selective_expanduser(path: Path) -> Path:
            if str(path).startswith("~missing"):
                raise RuntimeError("unknown user")
            return original_expanduser(path)

        monkeypatch.setattr(Path, "expanduser", selective_expanduser)
        monkeypatch.setenv(
            "PROMPTDEPLOY_BUNDLE_BINDINGS_FILE", "~missing/bindings.json"
        )
        with pytest.raises(BundleBindingError, match="unknown home directory"):
            load_config(
                self._config(
                    tmp_path,
                    bundles={"ponytail": {"manifest": "bundles/ponytail.yaml"}},
                )
            )

    def test_explicit_descriptor_and_remap(self, tmp_path: Path) -> None:
        manifest = _manifest(tmp_path)
        source = tmp_path / "source"
        source.mkdir()
        descriptor = tmp_path / "bindings.json"
        _write_bindings(descriptor, source)
        config = load_config(
            self._config(
                tmp_path,
                bundles={"ponytail": {"manifest": "bundles/ponytail.yaml"}},
            ),
            bundle_bindings_file=descriptor,
        )

        assert config.bundles == (
            BundleConfig(
                name="ponytail",
                manifest_path=manifest,
                binding=_binding(source),
            ),
        )
        assert remap_targets_to_root(config, tmp_path / "preview").bundles is (
            config.bundles
        )

    def test_ambient_descriptor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _manifest(tmp_path)
        source = tmp_path / "source"
        source.mkdir()
        descriptor = tmp_path / "bindings.json"
        _write_bindings(descriptor, source)
        monkeypatch.setenv("PROMPTDEPLOY_BUNDLE_BINDINGS_FILE", str(descriptor))

        config = load_config(
            self._config(
                tmp_path,
                bundles={"ponytail": {"manifest": "bundles/ponytail.yaml"}},
            )
        )
        assert config.bundles[0].binding.source_root == source

    def test_cli_override_wins(self, tmp_path: Path) -> None:
        _manifest(tmp_path)
        descriptor_source = tmp_path / "descriptor-source"
        override_source = tmp_path / "override-source"
        descriptor_source.mkdir()
        override_source.mkdir()
        descriptor = tmp_path / "bindings.json"
        _write_bindings(descriptor, descriptor_source)

        config = load_config(
            self._config(
                tmp_path,
                bundles={"ponytail": {"manifest": "bundles/ponytail.yaml"}},
            ),
            bundle_bindings_file=descriptor,
            bundle_source_overrides=[f"ponytail={override_source}"],
        )
        assert config.bundles[0].binding.source_root == override_source
        assert config.bundles[0].binding.mutable

    def test_missing_binding_fails(self, tmp_path: Path) -> None:
        _manifest(tmp_path)
        with pytest.raises(BundleBindingError, match="has no source binding"):
            load_config(
                self._config(
                    tmp_path,
                    bundles={"ponytail": {"manifest": "bundles/ponytail.yaml"}},
                )
            )
