"""Environment-driven settings (never hard-code URLs or paths in module code)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    service_name: str
    port: int
    log_level: str
    ngspice_bin: str
    timeout_seconds: float
    max_output_bytes: int
    workdir: Path | None
    keep_decks: bool
    max_nodes: int
    max_edges: int
    version_timeout_seconds: float
    log_netlists: bool

    @staticmethod
    def from_env() -> "Settings":
        workdir = os.getenv("SIM_WORKDIR", "").strip()
        return Settings(
            service_name="simulation",
            port=_env_int("SIM_PORT", 8000),
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            ngspice_bin=os.getenv("NGSPICE_BIN", "ngspice").strip() or "ngspice",
            timeout_seconds=_env_float("SIM_TIMEOUT_S", 5.0),
            max_output_bytes=_env_int("SIM_MAX_OUTPUT_BYTES", 262_144),
            workdir=Path(workdir) if workdir else None,
            keep_decks=os.getenv("SIM_KEEP_DECKS", "false").lower() in {"1", "true", "yes"},
            max_nodes=_env_int("SIM_MAX_NODES", 200),
            max_edges=_env_int("SIM_MAX_EDGES", 500),
            version_timeout_seconds=_env_float("SIM_VERSION_TIMEOUT_S", 5.0),
            log_netlists=os.getenv("SIM_LOG_NETLISTS", "true").lower() in {"1", "true", "yes"},
        )

    def public(self) -> dict[str, object]:
        """Safe-to-expose configuration for the health endpoint."""
        return {
            "ngspiceBin": self.ngspice_bin,
            "timeoutSeconds": self.timeout_seconds,
            "maxOutputBytes": self.max_output_bytes,
            "maxNodes": self.max_nodes,
            "maxEdges": self.max_edges,
            "keepDecks": self.keep_decks,
            "logNetlists": self.log_netlists,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
