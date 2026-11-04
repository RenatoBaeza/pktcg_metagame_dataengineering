"""Rebuild the silver layer from bronze.

Cheap and safe to run as often as you like: the refresh is a full reconcile,
not an append, and it writes only rows that changed. It reads all of bronze
every time rather than just what is new, because a pairing's deck columns are
filled in by a standings ingest that lands later than the pairing itself.

Run it after the ingests, not before - it can only reflect what bronze already
has. See app/silver.py for the loop and sql/005_silver.sql for the transform.

Usage:
    uv run python scripts/refresh_silver.py [--dry-run]
    uv run python scripts/refresh_silver.py --only pairings
    uv run python scripts/refresh_silver.py --tournament <id>
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import silver  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    silver.add_arguments(parser)
    return silver.run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
