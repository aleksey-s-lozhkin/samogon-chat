#!/usr/bin/env python
"""Container health probes with safe, quiet exit codes."""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config.health import web_is_ready, worker_is_ready


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=("web", "worker"))
    parser.add_argument("--timeout", type=float, default=5.0)
    arguments = parser.parse_args()

    try:
        healthy = (
            web_is_ready(arguments.timeout)
            if arguments.probe == "web"
            else worker_is_ready(arguments.timeout)
        )
    except Exception:
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
