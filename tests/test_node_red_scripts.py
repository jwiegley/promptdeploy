"""Tests for the node-red skill's bundled scripts.

These scripts live in skills/node-red/scripts/ and are run standalone by the
skill, so they are loaded here by file path rather than imported as a package.
They are intentionally outside the promptdeploy coverage gate (coverage
source is the ``promptdeploy`` package).
"""

import importlib.util
import json
import string
from pathlib import Path
from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "node-red" / "scripts"


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGenerateUuid:
    def test_node_id_is_16_hex_chars(self):
        mod = _load_script("generate_uuid")
        node_id = mod.generate_node_id()
        assert len(node_id) == 16
        assert all(c in string.hexdigits for c in node_id)

    def test_node_ids_are_unique(self):
        mod = _load_script("generate_uuid")
        ids = {mod.generate_node_id() for _ in range(100)}
        assert len(ids) == 100


class TestCreateFlowTemplate:
    def test_generated_ids_are_16_hex_chars(self):
        mod = _load_script("create_flow_template")
        node_id = mod.generate_id()
        assert len(node_id) == 16
        assert all(c in string.hexdigits for c in node_id)

    def test_every_template_passes_validation(self, tmp_path):
        templates = _load_script("create_flow_template")
        validator = _load_script("validate_flow")
        for name, factory in templates.TEMPLATES.items():
            flow_file = tmp_path / f"{name}.json"
            flow_file.write_text(json.dumps(factory()))
            is_valid, message = validator.validate_flow(str(flow_file))
            assert is_valid, f"template {name}: {message}"


class TestValidateFlow:
    def _validate(self, tmp_path, flow):
        validator = _load_script("validate_flow")
        flow_file = tmp_path / "flow.json"
        flow_file.write_text(json.dumps(flow))
        return validator.validate_flow(str(flow_file))

    def test_accepts_config_nodes_and_subflows(self, tmp_path):
        """Config nodes and subflow definitions are valid without z or x/y,
        and nodes inside a subflow reference the subflow id via z."""
        flow = [
            {"id": "tab1tab1tab1tab1", "type": "tab", "label": "Office"},
            {
                # Subflow definition: a container like a tab, no z, no x/y.
                "id": "sub1sub1sub1sub1",
                "type": "subflow",
                "name": "Act until observed",
                "in": [],
                "out": [],
            },
            {
                # Config node: global scope, no z, no canvas coordinates.
                "id": "cfg1cfg1cfg1cfg1",
                "type": "server",
                "name": "Home Assistant",
            },
            {
                # Node inside the subflow definition.
                "id": "node1node1node1a",
                "type": "function",
                "z": "sub1sub1sub1sub1",
                "func": "return msg;",
                "x": 100,
                "y": 100,
                "wires": [[]],
            },
            {
                # Regular node on the tab.
                "id": "node2node2node2b",
                "type": "inject",
                "z": "tab1tab1tab1tab1",
                "x": 100,
                "y": 100,
                "wires": [["node1node1node1a"]],
            },
        ]
        is_valid, message = self._validate(tmp_path, flow)
        assert is_valid, message
        assert message == "Valid"

    def test_regular_node_missing_z_still_errors(self, tmp_path):
        flow = [
            {"id": "tab1tab1tab1tab1", "type": "tab", "label": "Office"},
            {
                "id": "node1node1node1a",
                "type": "inject",
                "x": 100,
                "y": 100,
                "wires": [[]],
            },
        ]
        is_valid, _ = self._validate(tmp_path, flow)
        assert not is_valid

    def test_z_referencing_unknown_container_errors(self, tmp_path):
        flow = [
            {"id": "tab1tab1tab1tab1", "type": "tab", "label": "Office"},
            {
                "id": "node1node1node1a",
                "type": "inject",
                "z": "missingmissing00",
                "x": 100,
                "y": 100,
                "wires": [[]],
            },
        ]
        is_valid, _ = self._validate(tmp_path, flow)
        assert not is_valid

    def test_wire_to_nonexistent_node_errors(self, tmp_path):
        flow = [
            {"id": "tab1tab1tab1tab1", "type": "tab", "label": "Office"},
            {
                "id": "node1node1node1a",
                "type": "inject",
                "z": "tab1tab1tab1tab1",
                "x": 100,
                "y": 100,
                "wires": [["missingmissing00"]],
            },
        ]
        is_valid, _ = self._validate(tmp_path, flow)
        assert not is_valid
