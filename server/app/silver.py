"""Driver for the bronze -> silver refresh.

The transform itself is two Postgres functions (sql/005_silver.sql); this
module only calls them. Keeping the work server-side is the whole point:
silver_pairings is derived from ~470k bronze rows joined twice against
standings, and dragging that through PostgREST to reshape it in Python would
spend minutes of transfer on what Postgres does in place in seconds.

Work is submitted a batch of tournaments at a time rather than as one call over
the whole table. That keeps any single statement well inside the database's
timeout, bounds how much a failure costs, and gives the run something to print
while it works. The functions are full reconciles of whatever slice they are
handed, so a batch that fails is simply redone by the next run.
"""

import argparse
import sys
from datetime import date
from typing import Any

import httpx2 as httpx

from app import supabase
from app.ingest import TOURNAMENTS_TABLE
from app.limitless import RETAIN_MONTHS, retention_cutoff

# Order matters. silver_pairings carries a foreign key onto silver_tournaments
# and inner-joins it, so a tournament missing from silver_tournaments yields no
# pairings rows at all - silently, not as an error.
#
# There is no standings step: silver_standings is a view over bronze_standings
# (sql/007_shrink.sql), so it needs no refreshing and cannot fall behind.
STEPS = ("tournaments", "pairings")

# Was 200 until 2026-11. By then a 200-tournament call averaged 5.5s of the 8s
# PostgREST statement timeout, and late-October growth pushed every batch past
# it. sql/017 took most of the cost out of the call; the smaller batch is the
# margin on top. Time per run is the same either way, only spread thinner.
CHUNK_SIZE = 50

# PostgREST caps a response at 1000 rows regardless of the limit asked for.
PAGE_SIZE = 1000


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
        help="Tournaments per refresh call. Lower it if a call hits a statement timeout.",
    )
    parser.add_argument("--tournament", help="Refresh a single tournament id and exit.")
    parser.add_argument(
        "--only",
        choices=STEPS,
        action="append",
        help="Refresh just this table. Repeatable. Defaults to all three, in order.",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=RETAIN_MONTHS,
        help="Only refresh tournaments from this many months back. Defaults to "
             "RETAIN_MONTHS so the derived layer stays inside the retention "
             "window; pass 0 to refresh everything.",
    )
    parser.add_argument("--dry-run", action="store_true")


def tournament_ids(
    client: httpx.Client, base: str, hdrs: dict[str, str], since: date | None = None
) -> list[str]:
    """Every bronze tournament id in scope, paged out.

    `since`, when given, filters to tournaments on or after that date. The
    refreshes are full reconciles of whatever slice they are handed, so
    handing them only in-window tournaments is what stops the derived layers
    re-adding rows the retention prune removed.
    """
    ids: list[str] = []
    while True:
        params: dict[str, str] = {"select": "id", "order": "id",
                                  "offset": str(len(ids)), "limit": str(PAGE_SIZE)}
        if since is not None:
            params["date"] = f"gte.{since.isoformat()}"
        response = supabase._send(
            client,
            "GET",
            f"{base}/rest/v1/{TOURNAMENTS_TABLE}",
            params=params,
            headers=hdrs,
        )
        response.raise_for_status()
        page = response.json()
        ids.extend(row["id"] for row in page)
        if len(page) < PAGE_SIZE:
            return ids


def run(args: argparse.Namespace) -> int:
    steps = [step for step in STEPS if not args.only or step in args.only]

    # A window of 0 means "everything", which is how a whole-database rebuild
    # is done. Otherwise the retention cutoff (today minus window-months) names
    # the oldest tournament the refresh is allowed to touch.
    since = None if args.window_months <= 0 else retention_cutoff(args.window_months)

    try:
        base, secret_key = supabase.credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hdrs = supabase.headers(secret_key)

    # Generous: a batch is a few seconds of database work, but the first build
    # of an empty silver layer is the slow case.
    with httpx.Client(timeout=300.0) as client:
        ids = [args.tournament] if args.tournament else tournament_ids(client, base, hdrs, since)
        batches = [ids[i : i + args.chunk_size] for i in range(0, len(ids), args.chunk_size)]
        print(f"{len(ids)} tournaments in {len(batches)} batches of up to {args.chunk_size}")
        if since is not None:
            print(f"retention window: tournaments on or after {since.isoformat()}")

        before = {step: supabase.count_rows(client, base, hdrs, f"silver_{step}") for step in steps}
        for step in steps:
            print(f"silver_{step}: {before[step]} rows before")

        if args.dry_run:
            print(f"dry run - would call {', '.join('refresh_silver_' + s for s in steps)}")
            return 0

        failed: list[str] = []
        for step in steps:
            written = 0
            for i, batch in enumerate(batches, start=1):
                try:
                    written += _refresh(client, base, hdrs, step, batch)
                except Exception as exc:  # next run redoes this batch
                    failed.append(f"{step}[{i}]")
                    print(f"  [{i}/{len(batches)}] silver_{step} FAILED: {exc}")
                    continue
                print(f"  [{i}/{len(batches)}] silver_{step} {written:>7} rows so far")

            after = supabase.count_rows(client, base, hdrs, f"silver_{step}")
            print(
                f"silver_{step}: {after} rows after (+{after - before[step]} new, "
                f"{written - (after - before[step])} updated in place)"
            )

        if failed:
            print(f"\nfailed batches: {', '.join(failed)}")

    # Same tolerance as the ingest loop: the odd failed batch self-heals next
    # run, a large share of them means something is actually broken.
    return 1 if len(failed) > max(1, len(batches) * len(steps) // 10) else 0


def _refresh(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    step: str,
    batch: list[str],
) -> int:
    result: Any = supabase.rpc(
        client, base, hdrs, f"refresh_silver_{step}", {"p_tournaments": batch}
    )
    return int(result or 0)
