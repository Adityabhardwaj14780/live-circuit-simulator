"""
ngspice batch-output parsing.

Only the `.op` node-voltage table is read. Node names in the table are always our generated net
names (``n1``, ``n2``, ...), which makes the match unambiguous; the caller maps nets back to node ids.

Handled shapes (verified against ngspice 44.2):

    No. of Data Rows : 1

        Node                                  Voltage
        ----                                  -------
        ----    -------
        n3                              8.000000e+00
        n1                              5.000000e+00

        Source        Current
        ------        -------
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import Codes, OutputParseError

VARIABLE_RE = re.compile(r"^v\((?P<net>[A-Za-z_]\w*)\)$", re.IGNORECASE)
RAW_BINARY_MARKER = "Binary:"
RAW_ASCII_MARKER = "Values:"
NUMBER_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
NET_NAME_RE = re.compile(r"^n(?P<index>\d+)$")
ROW_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$")
TABLE_HEADER_RE = re.compile(r"^\s*Node\s+Voltage\s*$", re.IGNORECASE)
SECTION_HEADER_RE = re.compile(r"^\s*Source\s+Current\s*$", re.IGNORECASE)
SEPARATOR_RE = re.compile(r"^\s*-{2,}[\s\t]*-{2,}\s*$")
DATA_ROWS_RE = re.compile(r"No\. of Data Rows\s*:\s*(?P<count>\d+)", re.IGNORECASE)
ERROR_RE = re.compile(r"\b(error|fatal|aborted|cannot|unable|too many|no such)\b", re.IGNORECASE)
WARNING_RE = re.compile(r"\b(warning|singular matrix|doAnalyses)\b", re.IGNORECASE)


@dataclass
class OpPoint:
    voltages: dict[str, float] = field(default_factory=dict)
    data_rows: int | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    def nodes(self) -> list[str]:
        return sorted(self.voltages.keys(), key=_net_sort_key)


def _net_sort_key(net: str) -> tuple[int, str]:
    match = NET_NAME_RE.match(net)
    return (int(match.group("index")), net) if match else (10**9, net)


def parse_op_output(text: str) -> OpPoint:
    """Extract node voltages and diagnostics from an ngspice batch log."""
    if text is None:
        raise OutputParseError("no ngspice output to parse", Codes.PARSE_ERROR)

    point = OpPoint()
    in_table = False
    rows_seen = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")

        data_rows = DATA_ROWS_RE.search(line)
        if data_rows:
            point.data_rows = int(data_rows.group("count"))
            continue

        if TABLE_HEADER_RE.match(line):
            in_table = True
            continue

        if in_table:
            if not line.strip():
                continue
            if SEPARATOR_RE.match(line):
                continue
            if SECTION_HEADER_RE.match(line):
                in_table = False
                continue
            row = ROW_RE.match(line.strip())
            if row:
                name = row.group("name")
                if NET_NAME_RE.match(name):
                    try:
                        point.voltages[name] = float(row.group("value"))
                    except ValueError:  # pragma: no cover - regex guarantees a parseable float
                        continue
                rows_seen += 1
                continue
            # anything unexpected ends the table
            in_table = False

        if ERROR_RE.search(line):
            message = line.strip()
            if message and message not in point.errors:
                point.errors.append(message[:300])
        elif WARNING_RE.search(line):
            message = line.strip()
            if message and message not in point.warnings:
                point.warnings.append(message[:300])

    return point


def parse_ascii_raw(text: str) -> dict[str, float] | None:
    """Parse an ASCII ngspice raw file produced with `-r` + `set filetype=ascii`.

    Layout (tab separated, one row per variable):

        Variables:
            0    v(n1)    voltage
            1    v(n2)    voltage
        Values:
        0        2.200000000000000e+01
            7.000000000000000e+00

    Variables are read positionally (node voltages and branch currents interleave in ngspice's own
    order), values are matched to that same order, and only `v(<net>)` entries are returned.

    Returns None when the file is binary, empty or malformed so the caller can fall back to the
    rounded `.op` table in the log.
    """
    if not text or RAW_ASCII_MARKER not in text:
        return None
    if RAW_BINARY_MARKER in text:
        return None

    lines = text.splitlines()
    try:
        variables_start = next(i for i, line in enumerate(lines) if line.strip().startswith("Variables:"))
        values_start = next(i for i, line in enumerate(lines) if line.strip().startswith(RAW_ASCII_MARKER))
    except StopIteration:
        return None

    variables: list[str | None] = []
    for line in lines[variables_start + 1 : values_start]:
        parts = line.strip().split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        match = VARIABLE_RE.match(parts[1])
        variables.append(match.group("net") if match else None)
    if not variables:
        return None

    values: list[float] = []
    for line in lines[values_start + 1 :]:
        parts = line.strip().split()
        if not parts:
            continue
        # the first row carries the index ("0  2.2e+01"); following rows are "\t7.0e+00"
        if len(parts) == 2 and parts[0].isdigit():
            candidate = parts[1]
        elif len(parts) == 1:
            candidate = parts[0]
        else:
            break
        if not NUMBER_RE.match(candidate):
            break
        try:
            values.append(float(candidate))
        except ValueError:  # pragma: no cover - NUMBER_RE guarantees a parseable float
            break
        if len(values) >= len(variables):
            break

    if len(values) < len(variables):
        return None
    voltages = {net: value for net, value in zip(variables, values) if net is not None}
    return voltages or None


def looks_like_ngspice_error(text: str) -> bool:
    """True when the log contains a fatal parsing/spice error marker."""
    markers = (
        "error:",
        "fatal error",
        "yyparse error",
        "no such file or directory",
        "unknown parameter",
        "syntax error",
    )
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def parse_run_output(
    log_text: str,
    raw_text: str,
    *,
    expect_nets: list[str],
    truncated: bool = False,
) -> tuple[OpPoint, list[str], bool]:
    """Parse one ngspice run.

    `ngspice -b -r <file>` writes the operating point to the raw file and omits the node table from
    the log, so the ASCII raw file (full double precision) is the preferred source; the rounded
    `.op` table in the log stays as a fallback and as a cross-check.

    Returns (point, warnings, used_raw_file).
    """
    point = parse_op_output(log_text)
    point.truncated = truncated
    warnings: list[str] = []
    raw_voltages = parse_ascii_raw(raw_text)
    used_raw = False

    if raw_voltages:
        used_raw = True
        for net, raw_value in raw_voltages.items():
            table_value = point.voltages.get(net)
            if table_value is not None and not _close_enough(table_value, raw_value, tolerance=1e-5):
                warnings.append(
                    f'net "{net}": the .op table reports {table_value:g} while the raw file reports {raw_value:g}'
                )
        point.voltages = raw_voltages
    elif looks_like_ngspice_error(log_text) and not point.voltages:
        raise OutputParseError(
            "ngspice reported an error and produced no node voltages",
            Codes.NGSPICE_ERROR,
            detail="\n".join(point.errors[:5]) or log_text[-2000:],
        )

    missing = [net for net in expect_nets if net not in point.voltages]
    if missing:
        raise OutputParseError(
            f"ngspice output is missing {len(missing)} expected net(s): {', '.join(missing[:10])}",
            Codes.PARSE_ERROR,
            detail="\n".join(point.errors[:5]) or log_text[-2000:],
        )
    return point, warnings, used_raw


def _close_enough(left: float, right: float, tolerance: float) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def parse_or_raise(text: str, *, expect_nets: list[str], truncated: bool = False) -> OpPoint:
    """Parse and verify that every expected net is present; raises OutputParseError otherwise."""
    point = parse_op_output(text)
    point.truncated = truncated
    if looks_like_ngspice_error(text) and not point.voltages:
        raise OutputParseError(
            "ngspice reported an error and produced no node voltages",
            Codes.NGSPICE_ERROR,
            detail="\n".join(point.errors[:5]) or text[-2000:],
        )
    missing = [net for net in expect_nets if net not in point.voltages]
    if missing:
        raise OutputParseError(
            f"ngspice output is missing {len(missing)} expected net(s): {', '.join(missing[:10])}",
            Codes.PARSE_ERROR,
            detail="\n".join(point.errors[:5]) or text[-2000:],
        )
    return point
