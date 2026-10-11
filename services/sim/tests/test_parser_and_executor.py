"""Output parsing and subprocess safety tests (timeout, kill, missing binary)."""

from __future__ import annotations

import os
import stat
import time
from dataclasses import replace

import pytest

from app import executor, parser
from app.errors import Codes, NgspiceNotFound, OutputParseError

REAL_LOG = """
Note: No compatibility mode selected!


Circuit: * probe: number + add + divide

Doing analysis at TEMP = 27.000000 and TNOM = 27.000000

Using SPARSE 1.3 as Direct Linear Solver

No. of Data Rows : 1
\tNode                                  Voltage
\t----                                  -------
\t----\t-------
\tn4                              1.666667e+00
\tn3                              8.000000e+00
\tn2                              3.000000e+00
\tn1                              5.000000e+00

\tSource\tCurrent
\t------\t-------

\tv1#branch                        0.000000e+00

Total elapsed time (seconds) = 0.002
"""

SINGULAR_LOG = """
Warning: singular matrix:  check node n7
Warning: singular matrix:  check node n7
Last Node Voltages
------------------

Node                                   Last Voltage        Previous Iter
----                                   ------------        -------------
n1                                                0                    0
"""


def test_parses_the_real_op_table() -> None:
    point = parser.parse_op_output(REAL_LOG)
    assert point.voltages == {"n1": 5.0, "n2": 3.0, "n3": 8.0, "n4": 1.666667}
    assert point.data_rows == 1
    assert point.nodes() == ["n1", "n2", "n3", "n4"]


def test_collects_warnings_and_errors() -> None:
    point = parser.parse_op_output(SINGULAR_LOG)
    assert point.voltages == {}  # "Last Node Voltages" is not an .op table
    assert any("singular matrix" in warning.lower() for warning in point.warnings)
    assert parser.looks_like_ngspice_error("Error: unknown parameter foo") is True


def test_parse_or_raise_reports_missing_nets() -> None:
    with pytest.raises(OutputParseError) as info:
        parser.parse_or_raise(REAL_LOG, expect_nets=["n1", "n99"])
    assert info.value.code == Codes.PARSE_ERROR
    assert "n99" in info.value.message


def test_parse_or_raise_surfaces_ngspice_errors() -> None:
    with pytest.raises(OutputParseError) as info:
        parser.parse_or_raise("YYparse error, syntax error, in expression: parsing stopped at line 3", expect_nets=["n1"])
    assert info.value.code == Codes.NGSPICE_ERROR


def test_missing_binary_raises_ngspice_missing(settings) -> None:
    broken = replace(settings, ngspice_bin="definitely-not-ngspice")
    with pytest.raises(NgspiceNotFound):
        executor.run_batch("* nothing\n.op\n.end\n", broken)
    version, error = executor.ngspice_version(broken)
    assert version is None and "not found" in (error or "")


def _deck_argument() -> str:
    """The deck is the last argv entry regardless of the flags we add."""
    return 'for arg in "$@"; do deck="$arg"; done'


def _fake_binary(tmp_path, body: str) -> str:
    path = tmp_path / "fake-ngspice"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_timeout_kills_the_process_group(tmp_path, settings) -> None:
    binary = _fake_binary(tmp_path, "sleep 30 & sleep 30")
    slow = replace(settings, ngspice_bin=binary, timeout_seconds=0.5)
    started = time.perf_counter()
    result = executor.run_batch("* fake\n.op\n.end\n", slow, label="timeout-test")
    elapsed = time.perf_counter() - started

    assert result.ok is False
    assert result.timed_out is True
    assert result.killed is True
    assert result.error_code == Codes.NGSPICE_TIMEOUT
    assert elapsed < 6.0, f"timeout path took {elapsed:.1f}s"
    assert result.workdir is None  # temp dir removed unless SIM_KEEP_DECKS


def test_non_zero_exit_is_reported_as_ngspice_error(tmp_path, settings) -> None:
    binary = _fake_binary(tmp_path, 'echo "Error: something went wrong" >&2\nexit 3')
    failing = replace(settings, ngspice_bin=binary)
    result = executor.run_batch("* fake\n.op\n.end\n", failing)
    assert result.ok is False
    assert result.exit_code == 3
    assert result.error_code == Codes.NGSPICE_ERROR


def test_deck_is_written_and_cleaned_up(tmp_path, settings) -> None:
    binary = _fake_binary(tmp_path, _deck_argument() + '\ngrep -c "^V1" "$deck"')
    settings_with_dir = replace(settings, ngspice_bin=binary, workdir=tmp_path)
    result = executor.run_batch("* fake\nV1 n1 0 DC 1\n.op\n.end\n", settings_with_dir)
    assert result.ok is True
    assert result.log.strip() == "1"  # the fake binary saw our deck
    assert not any(path.name.startswith("lng-sim-") for path in tmp_path.iterdir())


def test_kept_decks_are_exposed_for_debugging(tmp_path, settings) -> None:
    binary = _fake_binary(tmp_path, "exit 0")
    keep = replace(settings, ngspice_bin=binary, workdir=tmp_path, keep_decks=True)
    result = executor.run_batch("* fake\n.op\n.end\n", keep)
    assert result.ok is True and result.workdir is not None
    assert os.path.isfile(os.path.join(result.workdir, executor.DECK_FILENAME))
