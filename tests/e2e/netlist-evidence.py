#!/usr/bin/env python3
"""
Independent ngspice evidence check.

For every acceptance scenario this script

  1. asks the RUNNING stack (Express → FastAPI) for the generated netlist,
  2. writes that deck to a temp file and executes it with `ngspice -b` DIRECTLY, bypassing the
     simulation service's executor entirely,
  3. parses ngspice's own raw output with a small independent parser,
  4. compares those voltages with the values the API returned.

If the API were returning canned or mocked numbers, step 4 would disagree. Exit code is non-zero on
any mismatch.

    python3 tests/e2e/netlist-evidence.py
    STACK_URL=http://localhost:5173 python3 tests/e2e/netlist-evidence.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STACK_URL = os.environ.get("STACK_URL", "http://localhost:5173").rstrip("/")
NGSPICE = os.environ.get("NGSPICE_BIN", "ngspice")
SPICEINIT = "set filetype=ascii\nset numdgt=15\n"


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{STACK_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def load_scenarios() -> list[dict]:
    output = subprocess.run(
        [sys.executable, str(REPO_ROOT / "services/sim/tests/export_acceptance.py")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(output)["scenarios"]


def run_ngspice_directly(netlist: str) -> dict[str, float]:
    """Execute a deck with ngspice in batch mode and read the ASCII raw file back."""
    with tempfile.TemporaryDirectory(prefix="evidence-") as workdir:
        work = pathlib.Path(workdir)
        (work / "deck.cir").write_text(netlist)
        (work / ".spiceinit").write_text(SPICEINIT)
        completed = subprocess.run(
            [NGSPICE, "-b", "-o", str(work / "log.txt"), "-r", str(work / "op.raw"), str(work / "deck.cir")],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HOME": str(work)},
        )
        if completed.returncode != 0:
            raise RuntimeError(f"ngspice exited {completed.returncode}: {completed.stderr[-500:]}")

        raw = (work / "op.raw").read_text()
        lines = raw.splitlines()
        variables_start = next(i for i, line in enumerate(lines) if line.strip().startswith("Variables:"))
        values_start = next(i for i, line in enumerate(lines) if line.strip().startswith("Values:"))
        names: list[str | None] = []
        for line in lines[variables_start + 1 : values_start]:
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                names.append(parts[1][2:-1] if parts[1].startswith("v(") else None)
        values: list[float] = []
        for line in lines[values_start + 1 :]:
            parts = line.split()
            if not parts:
                continue
            candidate = parts[1] if len(parts) == 2 else parts[0]
            try:
                values.append(float(candidate))
            except ValueError:
                break
            if len(values) >= len(names):
                break
        return {name: value for name, value in zip(names, values) if name is not None}


def main() -> int:
    scenarios = load_scenarios()
    print(f"independent ngspice evidence check — {len(scenarios)} scenarios via {STACK_URL}")
    print(f"ngspice binary: {NGSPICE}\n")

    mismatches: list[str] = []
    for scenario in scenarios:
        body = post("/api/graph/execute", {"graph": scenario["graph"], "revision": 4242})
        netlist = body.get("netlist") or ""
        api_values = {entry["nodeId"]: (entry["value"], entry["net"]) for entry in body["results"]}

        if not netlist.strip() or body["timings"]["ngspiceMs"] == 0:
            print(f"  {scenario['id']:22s} no device executed (nothing evaluable) — skipped")
            continue

        direct = run_ngspice_directly(netlist)
        compared = 0
        for node_id, (api_value, net) in api_values.items():
            if api_value is None or net is None:
                continue
            independent_value = direct.get(net)
            if independent_value is None:
                mismatches.append(f"{scenario['id']}/{node_id}: net {net} missing from ngspice output")
                continue
            compared += 1
            if abs(independent_value - api_value) > 1e-9 * max(1.0, abs(api_value)):
                mismatches.append(
                    f"{scenario['id']}/{node_id}: api={api_value!r} vs independent ngspice={independent_value!r}"
                )
        print(
            f"  {scenario['id']:22s} status={body['status']:8s} compared {compared} value(s): "
            + ", ".join(f"{net}={direct[net]:.6g}" for net in sorted(direct))
        )

    print()
    if mismatches:
        print("MISMATCHES — the API values do not match an independent ngspice run:")
        for mismatch in mismatches:
            print("  ✗", mismatch)
        return 1
    print("OK: every reported value matches an independent `ngspice -b` run of the same deck")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
