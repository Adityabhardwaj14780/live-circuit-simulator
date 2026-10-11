"""
Export the acceptance scenarios as JSON so other runners (the HTTP/browser end-to-end checks) can
drive exactly the same graphs and expectations as the pytest suite — one source of truth.

    python3 services/sim/tests/export_acceptance.py > /tmp/acceptance.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_graphs import ACCEPTANCE_GRAPHS  # noqa: E402


def main() -> int:
    payload = {"count": len(ACCEPTANCE_GRAPHS), "scenarios": ACCEPTANCE_GRAPHS}
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
