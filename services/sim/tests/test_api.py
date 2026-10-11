"""FastAPI surface tests: health, simulate, mapping and error handling."""

from __future__ import annotations

import stat
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.errors import Codes

from .acceptance_graphs import ACCEPTANCE_BY_ID
from .conftest import HAS_NGSPICE

client = TestClient(main_module.app)


def test_index_lists_endpoints() -> None:
    payload = client.get("/").json()
    assert "/simulate" in payload["endpoints"]


def test_health_reports_ngspice_availability() -> None:
    response = client.get("/health")
    payload = response.json()
    if HAS_NGSPICE:
        assert response.status_code == 200
        assert payload["status"] == "ok"
        assert payload["ngspice"]["available"] is True
    else:  # pragma: no cover - environment dependent
        assert response.status_code == 503
        assert payload["status"] == "degraded"


def test_mapping_endpoint_documents_all_node_types() -> None:
    payload = client.get("/mapping").json()
    assert {row["nodeType"] for row in payload["mapping"]} == {"number", "add", "multiply", "divide", "formula", "display"}


def test_simulate_rejects_unknown_fields() -> None:
    response = client.post("/simulate", json={"graph": {"nodes": [], "edges": []}, "revision": 1, "sneaky": True})
    assert response.status_code == 422


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed")
def test_simulate_returns_node_results_and_netlist() -> None:
    entry = ACCEPTANCE_BY_ID["s2_add"]
    response = client.post("/simulate", json={"graph": entry["graph"], "revision": 5})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["revision"] == 5
    values = {result["nodeId"]: result["value"] for result in payload["results"]}
    assert values["add1"] == pytest.approx(5.0)
    assert payload["netlist"].rstrip().endswith(".end")


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed")
def test_simulate_reports_cycle_and_blocked_nodes() -> None:
    entry = ACCEPTANCE_BY_ID["s7_cycle"]
    payload = client.post("/simulate", json={"graph": entry["graph"], "revision": 3}).json()
    results = {result["nodeId"]: result for result in payload["results"]}
    assert results["cyc_add"]["errorCode"] == Codes.CYCLE_DETECTED
    assert results["d1"]["status"] == "blocked"
    assert payload["topology"]["cyclic"] == ["cyc_add", "cyc_mul"]


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed")
def test_simulate_reports_unsafe_formula_without_running_it() -> None:
    entry = ACCEPTANCE_BY_ID["s8_formula_safety"]
    payload = client.post("/simulate", json={"graph": entry["graph"], "revision": 4}).json()
    results = {result["nodeId"]: result for result in payload["results"]}
    assert results["bad_formula"]["errorCode"] == Codes.FORMULA_DISALLOWED_TOKEN
    assert "__import__" not in (payload["netlist"] or "")  # the payload never reached the deck
    assert results["div0"]["errorCode"] == Codes.DIVIDE_BY_ZERO


def test_simulate_handles_a_missing_ngspice_binary(monkeypatch) -> None:
    """Service stays responsive and answers with structured errors when ngspice is unavailable."""
    broken = replace(main_module.settings, ngspice_bin="definitely-not-ngspice", log_netlists=False)
    monkeypatch.setattr(main_module, "settings", broken)

    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["ngspice"]["available"] is False

    entry = ACCEPTANCE_BY_ID["s2_add"]
    payload = client.post("/simulate", json={"graph": entry["graph"], "revision": 9}).json()
    results = {result["nodeId"]: result for result in payload["results"]}
    assert payload["status"] == "error"
    assert results["add1"]["errorCode"] == Codes.NGSPICE_MISSING
    assert results["d1"]["status"] == "blocked"
    assert all(result["value"] is None for result in payload["results"])


def test_simulate_handles_a_timeout(monkeypatch, tmp_path) -> None:
    """A hung ngspice is killed and every node is cleared — no stale values are returned."""
    binary = tmp_path / "slow-ngspice"
    binary.write_text("#!/bin/sh\nsleep 30\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    slow = replace(main_module.settings, ngspice_bin=str(binary), timeout_seconds=0.5, log_netlists=False)
    monkeypatch.setattr(main_module, "settings", slow)

    entry = ACCEPTANCE_BY_ID["s3_multiply"]
    payload = client.post("/simulate", json={"graph": entry["graph"], "revision": 11}).json()
    results = {result["nodeId"]: result for result in payload["results"]}
    assert payload["status"] == "error"
    assert results["mul1"]["errorCode"] == Codes.NGSPICE_TIMEOUT
    assert results["mul1"]["value"] is None
    assert all(result["value"] is None for result in payload["results"] if result["status"] != "ok")
