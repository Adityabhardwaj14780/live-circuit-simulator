"""
Cross-language contract test.

packages/graph-contract/graph.schema.json is the single source of truth. The TypeScript package
derives its registries from it at runtime; the Python service keeps its own mirror (a Python service
cannot read a TS module), so this test fails whenever the mirror drifts.

Skipped (not failed) when the schema file is not present in the build context.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from app.errors import Codes
from app.formula import MAX_DEPTH, MAX_EXPRESSION_LENGTH, MAX_TOKENS
from app.graph import FORMULA_MAX_LENGTH, NODE_REGISTRY, NUMBER_MAX, NUMBER_MIN

def _find_repo_root() -> pathlib.Path | None:
    current = pathlib.Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "packages" / "graph-contract" / "graph.schema.json").is_file():
            return parent
    return None


def _schema_candidates() -> list[str]:
    candidates: list[str] = []
    if os.getenv("CONTRACT_SCHEMA_PATH"):
        candidates.append(os.environ["CONTRACT_SCHEMA_PATH"])
    candidates.append("/app/graph.schema.json")
    repo_root = _find_repo_root()
    if repo_root:
        candidates.append(str(repo_root / "packages" / "graph-contract" / "graph.schema.json"))
    return candidates


CANDIDATES = _schema_candidates()


def _load_schema() -> dict:
    for candidate in CANDIDATES:
        if candidate and pathlib.Path(candidate).is_file():
            return json.loads(pathlib.Path(candidate).read_text())
    pytest.skip("graph.schema.json is not part of this build context")


def test_node_registry_matches_the_json_schema() -> None:
    schema = _load_schema()
    handles = schema["x-node-handles"]
    assert set(NODE_REGISTRY) == set(handles)

    for node_type, spec in handles.items():
        mirror = NODE_REGISTRY[node_type]
        assert list(mirror.inputs) == list(spec["inputs"]), node_type
        assert list(mirror.outputs) == list(spec["outputs"]), node_type
        expected_required = () if node_type == "formula" else tuple(spec["inputs"])
        assert tuple(mirror.required_inputs) == expected_required, node_type

    assert NODE_REGISTRY["number"].number_data["value"] == (NUMBER_MIN, NUMBER_MAX)
    assert NODE_REGISTRY["formula"].formula_data == ("expression",)
    assert NUMBER_MIN == schema["x-limits"]["maxNodes"] * -5e6 or NUMBER_MIN == -1.0e9


def test_error_codes_match_the_json_schema() -> None:
    schema = _load_schema()
    declared = set(schema["x-error-codes"])
    implemented = {value for name, value in vars(Codes).items() if not name.startswith("_") and isinstance(value, str)}
    assert declared == implemented


def test_limits_match_the_json_schema() -> None:
    schema = _load_schema()
    limits = schema["x-limits"]
    assert limits["maxFormulaLength"] == MAX_EXPRESSION_LENGTH == FORMULA_MAX_LENGTH
    assert limits["maxFormulaTokens"] == MAX_TOKENS
    assert limits["maxFormulaDepth"] == MAX_DEPTH
    assert limits["maxNodes"] == 200
    assert limits["maxEdges"] == 500
