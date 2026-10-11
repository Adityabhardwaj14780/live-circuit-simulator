"""Shared pytest fixtures for the simulation service."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace

import pytest

from app.config import Settings, get_settings

NGSPICE_BIN = os.getenv("NGSPICE_BIN", "ngspice")
HAS_NGSPICE = shutil.which(NGSPICE_BIN) is not None
requires_ngspice = pytest.mark.skipif(not HAS_NGSPICE, reason=f"{NGSPICE_BIN} is not installed")


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Isolated settings: short timeout, decks kept inside the pytest temp dir."""
    base = get_settings()
    return replace(
        base,
        ngspice_bin=base.ngspice_bin,
        timeout_seconds=10.0,
        workdir=None,
        keep_decks=False,
        log_netlists=False,
    )
