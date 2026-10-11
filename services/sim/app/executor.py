"""
Subprocess execution of ngspice in batch mode.

Safety properties
-----------------
* argv list only — no shell, no string interpolation, no user-controlled arguments
* the deck is written by us into a private temp directory; ngspice is passed a file path
* hard wall-clock timeout (SIM_TIMEOUT_S); on expiry the whole process group is SIGKILLed
* output is captured through ngspice's own `-o` log file, then size-capped before reading
* temp files are removed in a `finally` block (SIM_KEEP_DECKS=true keeps them for debugging)
* missing binary -> NGSPICE_MISSING instead of an unhandled OSError
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .errors import Codes, NgspiceFailed, NgspiceNotFound, NgspiceTimeout

logger = logging.getLogger("lng.sim.executor")

DECK_FILENAME = "deck.cir"
LOG_FILENAME = "ngspice.log"
RAW_FILENAME = "operating_point.raw"
SPICEINIT_FILENAME = ".spiceinit"
# `set filetype=ascii` makes `-r` emit a text raw file with full double precision (the batch .op
# table is rounded to 6 significant digits). Written by us, in the private working directory only.
SPICEINIT_CONTENT = "set filetype=ascii\nset numdgt=15\n"  # two real commands, newline separated


@dataclass
class RunResult:
    ok: bool
    exit_code: int | None
    duration_ms: float
    log: str = ""
    log_truncated: bool = False
    raw: str = ""
    raw_truncated: bool = False
    timed_out: bool = False
    killed: bool = False
    error_code: str | None = None
    message: str | None = None
    argv: list[str] = field(default_factory=list)
    workdir: str | None = None


def ngspice_binary(path: str) -> str | None:
    """Resolve the ngspice executable (absolute path or PATH lookup)."""
    if not path:
        return None
    if os.path.isabs(path):
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    return shutil.which(path)


def ngspice_version(settings: Settings) -> tuple[str | None, str | None]:
    """Return (version_string, error_message). Never raises."""
    binary = ngspice_binary(settings.ngspice_bin)
    if binary is None:
        return None, f'ngspice binary "{settings.ngspice_bin}" not found on PATH'
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=settings.version_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover - environment dependent
        return None, f"failed to run ngspice --version: {error}"
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    version_line = next((line for line in lines if "ngspice" in line.lower()), None)
    return (version_line or (lines[0] if lines else None)), None


def run_batch(netlist_text: str, settings: Settings, *, label: str = "graph") -> RunResult:
    """Write the deck, run `ngspice -b -o <log> <deck>`, return the captured log.

    Raises NgspiceNotFound / NgspiceTimeout / NgspiceFailed? No — timeouts and failures are returned
    as RunResult so the caller can attach them to every affected node. Missing binary raises, because
    that is a service-level misconfiguration (surfaced by /health as well).
    """
    binary = ngspice_binary(settings.ngspice_bin)
    if binary is None:
        raise NgspiceNotFound(f'ngspice binary "{settings.ngspice_bin}" not found on PATH', Codes.NGSPICE_MISSING)

    workdir = Path(tempfile.mkdtemp(prefix="lng-sim-", dir=str(settings.workdir) if settings.workdir else None))
    deck_path = workdir / DECK_FILENAME
    log_path = workdir / LOG_FILENAME
    raw_path = workdir / RAW_FILENAME
    argv = [binary, "-b", "-o", str(log_path), "-r", str(raw_path), str(deck_path)]
    started = time.perf_counter()

    try:
        deck_path.write_text(netlist_text, encoding="utf-8")
        (workdir / SPICEINIT_FILENAME).write_text(SPICEINIT_CONTENT, encoding="utf-8")
        if settings.log_netlists:
            logger.info(
                "running ngspice label=%s devices=%d workdir=%s",
                label,
                sum(1 for line in netlist_text.splitlines() if line and line[0] in "VB"),
                workdir,
            )
            logger.debug("netlist:\n%s", netlist_text)

        # HOME is redirected so a stray ~/.spiceinit cannot change simulation semantics
        environment = {**os.environ, "HOME": str(workdir)}
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=str(workdir),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,  # own process group so a timeout can kill children too
            )
        except FileNotFoundError as error:  # pragma: no cover - guarded by ngspice_binary()
            raise NgspiceNotFound(f"failed to start ngspice: {error}", Codes.NGSPICE_MISSING) from error

        timed_out = False
        killed = False
        try:
            stdout, _ = process.communicate(timeout=settings.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            killed = _terminate_tree(process)
            try:
                stdout, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - extremely unlikely
                stdout = ""

        duration_ms = (time.perf_counter() - started) * 1000.0
        log_text, truncated = _read_log(log_path, settings.max_output_bytes)
        if not log_text and stdout:
            log_text, truncated = _cap(stdout, settings.max_output_bytes)
        raw_text, raw_truncated = _read_log(raw_path, settings.max_output_bytes)

        if timed_out:
            message = f"ngspice exceeded the {settings.timeout_seconds:g}s timeout and was killed"
            logger.warning("%s (label=%s, killed=%s)", message, label, killed)
            return RunResult(
                ok=False,
                exit_code=process.returncode,
                duration_ms=duration_ms,
                log=log_text,
                log_truncated=truncated,
                raw=raw_text,
                raw_truncated=raw_truncated,
                timed_out=True,
                killed=killed,
                error_code=Codes.NGSPICE_TIMEOUT,
                message=message,
                argv=argv,
                workdir=str(workdir) if settings.keep_decks else None,
            )

        exit_code = process.returncode
        if exit_code != 0:
            message = f"ngspice exited with code {exit_code}"
            logger.warning("%s (label=%s)", message, label)
            return RunResult(
                ok=False,
                exit_code=exit_code,
                duration_ms=duration_ms,
                log=log_text,
                log_truncated=truncated,
                raw=raw_text,
                raw_truncated=raw_truncated,
                error_code=Codes.NGSPICE_ERROR,
                message=message,
                argv=argv,
                workdir=str(workdir) if settings.keep_decks else None,
            )

        return RunResult(
            ok=True,
            exit_code=exit_code,
            duration_ms=duration_ms,
            log=log_text,
            log_truncated=truncated,
            raw=raw_text,
            raw_truncated=raw_truncated,
            argv=argv,
            workdir=str(workdir) if settings.keep_decks else None,
        )
    finally:
        if not settings.keep_decks:
            shutil.rmtree(workdir, ignore_errors=True)


def _terminate_tree(process: subprocess.Popen[str]) -> bool:
    """Kill the process group; returns True when the signal was delivered."""
    killed = False
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        killed = True
    except (ProcessLookupError, PermissionError):  # pragma: no cover
        try:
            process.kill()
            killed = True
        except OSError:  # pragma: no cover
            killed = False
    return killed


def _read_log(path: Path, max_bytes: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            return handle.read(), True
        return handle.read(), False


def _cap(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[-max_bytes:].decode("utf-8", errors="replace"), True


def failure_from(result: RunResult) -> Exception:
    """Map a failed RunResult onto the matching exception type."""
    if result.error_code == Codes.NGSPICE_TIMEOUT:
        return NgspiceTimeout(result.message or "ngspice timed out", Codes.NGSPICE_TIMEOUT, result.log[-2000:])
    return NgspiceFailed(result.message or "ngspice failed", Codes.NGSPICE_ERROR, result.log[-2000:])
