"""CI release gate: offline RAG / agent / security evaluation. Exits non-zero if any gate fails."""

from __future__ import annotations

import json
import sys

from sentinel.container import Container
from sentinel.eval.harness import GATES, run_all


def main() -> int:
    report = run_all(Container.build())
    print(json.dumps({"metrics": report.metrics, "gates": GATES, "failures": report.failures}, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
