"""Driver for the silver -> gold refresh.

The same arrangement as app/silver.py: the transform is Postgres functions
(sql/012_gold_finished.sql, sql/016_matchup_trends.sql) and this module only
calls them, one RPC at a time,
so no single statement gambles against the 8s PostgREST timeout.

Gold is finished tables - every number the frontend draws, computed here once
per period rather than summed on every request - so the unit of work is the
period, not the tournament:

    periods         resolve each window's dates and the minimum matches a
                    tournament needs to count. Runs once, first, because every
                    step after it reads both.
    decks           the deck dimension (names, icons). Runs once.
    matchup_stats   one call per period.
    deck_stats      one call per period. Rolls up that period's matchup cells
                    into each deck's record, so it runs after them.
    trend_decks     the trend axis: the 'all' period's TREND_DECKS most played
                    decks. Reads deck_stats, so it runs after them.
    matchup_trends  every matchup between two trend decks, per calendar bucket.
                    One call per grain (week, month) and calendar month of
                    bucket starts: a whole weekly grain is ~75k rows, too many
                    to write inside the timeout in one statement.
"""

import argparse
import sys
from datetime import date, timedelta
from typing import Any

import httpx2 as httpx

from app import supabase
from app.limitless import RETAIN_MONTHS, retention_cutoff

# Run once, unbatched, in this order.
SETUP_STEPS = ("periods", "decks")

# Run once per period, in this order.
PERIOD_STEPS = ("matchup_stats", "deck_stats")

# Run after every period step: trend_decks once, matchup_trends once per grain
# and month of the window.
TREND_STEPS = ("trend_decks", "matchup_trends")

STEPS = SETUP_STEPS + PERIOD_STEPS + TREND_STEPS

# The calendar buckets the trends are computed in. Fixed in the SQL as well
# (gold_trend_buckets.grain), so adding one means both.
GRAINS = ("week", "month")

PERIODS_TABLE = "gold_periods"

# A tournament needs at least this many matches (silver_pairings rows) to count
# in gold at all. Below it an event is either a handful of players or one whose
# pairings Limitless never finished recording, and its standings would count in
# full against almost no matches. See sql/015_min_tournament_matches.sql.
MIN_TOURNAMENT_MATCHES = 10

# How many decks the trends follow: the most played of the 'all' period. Every
# matchup between two of them is tracked bucket by bucket, so the row count
# grows with its square. See sql/016_matchup_trends.sql.
TREND_DECKS = 100


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--only",
        choices=STEPS,
        action="append",
        help="Refresh just this step. Repeatable. Defaults to all of them, in order.",
    )
    parser.add_argument(
        "--period",
        action="append",
        help="Refresh just this period (e.g. 30d). Repeatable. Defaults to every "
             "row of gold_periods.",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=RETAIN_MONTHS,
        help="Clamp every period to tournaments from this many months back. "
             "Defaults to RETAIN_MONTHS so gold stays inside the retention "
             "window; pass 0 to use everything silver holds.",
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=MIN_TOURNAMENT_MATCHES,
        help="Leave out tournaments with fewer matches than this. Defaults to "
             "MIN_TOURNAMENT_MATCHES; pass 0 to count every tournament.",
    )
    parser.add_argument("--dry-run", action="store_true")


def periods(client: httpx.Client, base: str, hdrs: dict[str, str]) -> list[str]:
    """Every period gold_periods defines, shortest window first."""
    response = supabase._send(
        client,
        "GET",
        f"{base}/rest/v1/{PERIODS_TABLE}",
        params={"select": "period", "order": "days.asc.nullslast"},
        headers=hdrs,
    )
    response.raise_for_status()
    return [row["period"] for row in response.json()]


def trend_ranges(
    client: httpx.Client, base: str, hdrs: dict[str, str]
) -> list[tuple[str, str | None, str | None]]:
    """The bucket-start ranges to refresh the trends in: one calendar month of
    the 'all' window each, as (label, p_from, p_to).

    The first range is open at the start and the last at the end, so a bucket
    that starts before the window's first month (a week straddling it) or
    after its last still belongs to exactly one call. With no window resolved
    yet it is a single open range - the whole grain in one call.
    """
    response = supabase._send(
        client,
        "GET",
        f"{base}/rest/v1/{PERIODS_TABLE}",
        params={"select": "date_from,date_to", "period": "eq.all"},
        headers=hdrs,
    )
    response.raise_for_status()
    rows = response.json() or []
    if not rows or not rows[0].get("date_from") or not rows[0].get("date_to"):
        return [("all", None, None)]

    first = date.fromisoformat(rows[0]["date_from"]).replace(day=1)
    last = date.fromisoformat(rows[0]["date_to"]).replace(day=1)
    months = [first]
    while months[-1] < last:
        months.append((months[-1] + timedelta(days=32)).replace(day=1))

    return [
        (
            month.strftime("%Y-%m"),
            None if i == 0 else month.isoformat(),
            None if i == len(months) - 1 else (months[i + 1] - timedelta(days=1)).isoformat(),
        )
        for i, month in enumerate(months)
    ]


def run(args: argparse.Namespace) -> int:
    steps = [step for step in STEPS if not args.only or step in args.only]

    # A window of 0 means "everything silver holds". Otherwise the retention
    # cutoff clamps every period, so gold never reaches past the window even
    # before scripts/prune_window.py has trimmed silver.
    since = None if args.window_months <= 0 else retention_cutoff(args.window_months)
    min_matches = max(args.min_matches, 0)

    try:
        base, secret_key = supabase.credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hdrs = supabase.headers(secret_key)

    with httpx.Client(timeout=60.0) as client:
        scope = args.period or periods(client, base, hdrs)
        print(f"periods: {', '.join(scope)}")
        if since is not None:
            print(f"retention window: tournaments on or after {since.isoformat()}")
        print(f"minimum matches per tournament: {min_matches}")

        before = {step: supabase.count_rows(client, base, hdrs, f"gold_{step}") for step in steps}
        for step in steps:
            print(f"gold_{step}: {before[step]} rows before")

        if args.dry_run:
            print(f"dry run - would call {', '.join('refresh_gold_' + s for s in steps)}")
            return 0

        failed: list[str] = []
        attempted = 0
        for step in steps:
            if step == "periods":
                calls = [(step, {
                    "p_since": since.isoformat() if since else None,
                    "p_min_matches": min_matches,
                })]
            elif step == "decks":
                calls = [(step, {})]
            elif step == "trend_decks":
                calls = [(step, {"p_top": TREND_DECKS})]
            elif step == "matchup_trends":
                # Read here, not up front: the periods step may just have
                # moved the window.
                ranges = trend_ranges(client, base, hdrs)
                calls = [
                    (f"{step}[{grain} {label}]", {"p_grain": grain, "p_from": lo, "p_to": hi})
                    for grain in GRAINS
                    for label, lo, hi in ranges
                ]
            else:
                calls = [(f"{step}[{period}]", {"p_period": period}) for period in scope]

            for label, payload in calls:
                attempted += 1
                try:
                    written = _refresh(client, base, hdrs, step, payload)
                except Exception as exc:  # the next run redoes it
                    failed.append(label)
                    print(f"  gold_{label} FAILED: {exc}")
                    continue
                print(f"  gold_{label}: {written} rows written")

            after = supabase.count_rows(client, base, hdrs, f"gold_{step}")
            print(f"gold_{step}: {after} rows after")

        if failed:
            print(f"\nfailed calls: {', '.join(failed)}")

    # Same tolerance as the ingest and silver loops: the odd failed call
    # self-heals next run, a large share of them means something is broken.
    return 1 if len(failed) > max(1, attempted // 10) else 0


def _refresh(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    step: str,
    payload: dict[str, Any],
) -> int:
    result: Any = supabase.rpc(client, base, hdrs, f"refresh_gold_{step}", payload)
    return int(result or 0)
