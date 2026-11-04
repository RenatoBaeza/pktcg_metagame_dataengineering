"""Shared driver for the per-tournament ingests (pairings, standings).

Both resources have the same shape of problem: one API call per tournament,
a hard rate limit, and far more tournaments than fit in a single run. The loop
below is the part that must behave identically for both - budgeting, pacing,
resume-on-restart and per-tournament fault isolation - so it lives here rather
than being copied per script.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx2 as httpx

from app import supabase
from app.limitless import RATE_LIMIT_INTERVAL, fetch_tournament_resource

CHUNK_SIZE = 500

# The tournament list doubles as the ingest's own progress ledger, so the loop
# reads and writes it regardless of which resource it is fetching.
TOURNAMENTS_TABLE = "bronze_tournaments"


def add_common_arguments(parser: argparse.ArgumentParser, default_budget: int) -> None:
    parser.add_argument(
        "--max-requests",
        type=int,
        default=default_budget,
        help="How many tournaments to fetch this run (~6.2s each under the rate limit).",
    )
    parser.add_argument(
        "--restale-hours",
        type=int,
        default=48,
        help="Re-fetch tournaments this recent even if already ingested.",
    )
    parser.add_argument("--tournament", help="Ingest a single tournament id and exit.")
    parser.add_argument("--dry-run", action="store_true")


def pending_tournaments(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    progress_column: str,
    budget: int,
    restale_hours: int,
) -> list[dict[str, Any]]:
    """Tournaments still needing this resource: never fetched, or recent enough
    to still be changing.

    A tournament ingested while it was still running would otherwise keep its
    partial data forever, so anything from the last `restale_hours` is re-fetched.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=restale_hours)).isoformat()
    response = supabase._send(
        client,
        "GET",
        f"{base}/rest/v1/{TOURNAMENTS_TABLE}",
        params={
            "select": f"id,name,players,date,{progress_column}",
            "or": f"({progress_column}.is.null,date.gte.{cutoff})",
            "order": "date.desc",
            "limit": str(budget),
        },
        headers=hdrs,
    )
    response.raise_for_status()
    return response.json()


def run(
    args: argparse.Namespace,
    resource: str,
    table: str,
    row_mapper: Callable[[str, dict[str, Any]], dict[str, Any] | None],
    on_conflict: str,
    key: Callable[[dict[str, Any]], Any],
) -> int:
    """Fetch `resource` for each pending tournament and upsert it into `table`.

    `key` extracts a row's identity, used to collapse duplicates inside a single
    payload - PostgREST rejects a batch that touches the same key twice.
    `row_mapper` may return None for an item that carries no row (an empty
    table slot in pairings); those are dropped.
    """
    # Derived from the resource rather than the table: the progress columns name
    # what was fetched, and so kept their bare names when the tables gained the
    # bronze_ prefix. See sql/004_bronze_rename.sql.
    progress_column = f"{resource}_ingested_at"
    count_column = f"{resource}_count"

    try:
        base, secret_key = supabase.credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hdrs = supabase.headers(secret_key)

    with httpx.Client(timeout=60.0) as client:
        if args.tournament:
            targets = [{"id": args.tournament, "name": "<explicit>"}]
        else:
            targets = pending_tournaments(
                client, base, hdrs, progress_column,
                args.max_requests, args.restale_hours,
            )
            remaining = supabase.count_rows(
                client, base, hdrs, TOURNAMENTS_TABLE, {progress_column: "is.null"}
            )
            print(f"{remaining} tournaments still have no {resource}")

        before = supabase.count_rows(client, base, hdrs, table)
        print(f"{table}: {before} rows before")
        print(f"processing {len(targets)} tournaments this run")

        if args.dry_run:
            for t in targets[:10]:
                print(f"  would fetch {t['id']}  {(t.get('name') or '')[:60]}")
            if len(targets) > 10:
                print(f"  ... and {len(targets) - 10} more")
            print("dry run - nothing written")
            return 0

        written = failed = empty = 0
        for i, tournament in enumerate(targets, start=1):
            if i > 1:
                # Pace to stay inside 50 requests / 5 minutes.
                time.sleep(RATE_LIMIT_INTERVAL)

            tid = tournament["id"]
            # The fetch and both writes are guarded together, so one bad
            # tournament costs a single row rather than the rest of the run and
            # is retried next run, its progress column still null.
            try:
                payload = fetch_tournament_resource(client, tid, resource)

                mapped = (row_mapper(tid, item) for item in payload)
                rows = {key(r): r for r in mapped if r is not None}
                batch = list(rows.values())

                for start in range(0, len(batch), CHUNK_SIZE):
                    supabase.upsert(
                        client, base, hdrs, table,
                        batch[start : start + CHUNK_SIZE], on_conflict,
                    )

                supabase.patch(
                    client, base, hdrs, TOURNAMENTS_TABLE,
                    {"id": f"eq.{tid}"},
                    {
                        progress_column: datetime.now(timezone.utc).isoformat(),
                        count_column: len(batch),
                    },
                )
            except Exception as exc:  # keep going; the next run retries this one
                failed += 1
                print(f"  [{i}/{len(targets)}] {tid} FAILED: {exc}")
                continue

            written += len(batch)
            if not batch:
                empty += 1
            print(f"  [{i}/{len(targets)}] {tid} {len(batch):>5} {resource}")

        after = supabase.count_rows(client, base, hdrs, table)
        print(
            f"\n{table}: {after} rows after (+{after - before} new, "
            f"{written - (after - before)} updated in place)"
        )
        print(f"tournaments processed: {len(targets)}, empty: {empty}, failed: {failed}")

        still_pending = supabase.count_rows(
            client, base, hdrs, TOURNAMENTS_TABLE, {progress_column: "is.null"}
        )
        print(f"tournaments still pending: {still_pending}")

    # Occasional failures are expected and self-heal next run; a large share of
    # them means something is actually wrong, so surface it as a job failure.
    return 1 if failed > max(1, len(targets) // 10) else 0
