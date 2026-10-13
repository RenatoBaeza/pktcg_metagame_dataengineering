"""Read side of the gold layer - the questions the frontend asks.

Every answer is already a row in gold (sql/012_gold_finished.sql, reshaped by
sql/014_fixed_view.sql, and the trends of sql/016_matchup_trends.sql): the refresh
computes each period's deck table and matchup cells once, so a read here is a
PostgREST filter on a primary key and nothing is summed, ranked or rated at
request time. The columns are selected under the names the response models use,
so rows pass straight through.

The view is fixed apart from the period. Gold holds no 'other' rows at all
(sql/014), every cell with at least one match is returned - a thin cell is
told apart by its Wilson interval, not hidden - and the axis is TOP_DECKS.

Two things this module owns that the router should not:

`_client` - one httpx.Client for the process. The service is long-lived, so
reconnecting per request would throw away the connection pool.

`_cached` - a short time-to-live memo. Gold is rebuilt every six hours, so a
result is good for far longer than the 5 minutes cached here. The point is a
frontend where flipping a filter re-asks the same handful of questions.
"""

import threading
import time
from typing import Any

import httpx2 as httpx

from app import supabase

CACHE_TTL_SECONDS = 300

# Small because the key space is small: six periods, three reads each, plus
# whichever decks get opened, in the matrix or on the trends (two grains, up to
# 100 decks). Well past that, evicting everything beats tracking ages.
CACHE_MAX_ENTRIES = 512

# The matrix axis and the deck table are always the period's top 50 decks by
# entries. It is fixed, not a parameter: the frontend offers no other size.
TOP_DECKS = 50

RATE_COLUMNS = "matches,wins,losses,ties,win_rate,score_rate,score_low,score_high"

DECK_COLUMNS = (
    "deck_id,deck_name,rank,entries,tournaments,meta_share,mirror_matches,"
    f"champions,top8,{RATE_COLUMNS}"
)
CELL_COLUMNS = f"deck_a,deck_b,{RATE_COLUMNS}"
OPPONENT_COLUMNS = f"deck_b,deck_name:deck_b_name,{RATE_COLUMNS}"
PERIOD_COLUMNS = (
    "period,days,date_from,date_to,tournaments,entries,matches,decks,min_matches,"
    "refreshed_at"
)
COVERAGE_COLUMNS = (
    "first_event:date_from,last_event:date_to,tournaments,decks,matches,min_matches,"
    "refreshed_at"
)
TREND_DECK_COLUMNS = "deck_id,deck_name,rank,entries"
TREND_BUCKET_COLUMNS = "bucket_start,date_from,date_to,complete,tournaments,matches"
TREND_CELL_COLUMNS = f"bucket_start,deck_b,{RATE_COLUMNS}"

# The longest window, which is what the header describes.
ALL = "all"

_lock = threading.Lock()
_cache: dict[tuple, tuple[float, Any]] = {}
_client: httpx.Client | None = None
_connection: tuple[str, dict[str, str]] | None = None


def _connect() -> tuple[httpx.Client, str, dict[str, str]]:
    """The process-wide client and credentials, opened on first use.

    Deliberately not opened at import: credentials() raises when the
    environment is not configured, and that should surface on a request rather
    than stop the app from starting.
    """
    global _client, _connection
    with _lock:
        if _client is None or _connection is None:
            base, secret_key = supabase.credentials()
            _connection = (base, supabase.headers(secret_key))
            _client = httpx.Client(timeout=60.0)
        return _client, _connection[0], _connection[1]


def _fetch(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    client, base, hdrs = _connect()
    return supabase.select(client, base, hdrs, table, params)


def _cached(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    key = (table, tuple(sorted(params.items())))
    now = time.monotonic()

    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    result = _fetch(table, params)

    with _lock:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (now + CACHE_TTL_SECONDS, result)
    return result


def clear_cache() -> None:
    """Drop every memo. Called after a refresh so the next read sees new data."""
    with _lock:
        _cache.clear()


def coverage() -> dict[str, Any]:
    """What the dataset spans: the 'all' period's dates and totals."""
    rows = _cached(
        "gold_periods", {"select": COVERAGE_COLUMNS, "period": f"eq.{ALL}", "order": "period"}
    )
    return rows[0] if rows else {}


def periods() -> list[dict[str, Any]]:
    """Every period, with the dates it resolved to on the last refresh."""
    return _cached("gold_periods", {"select": PERIOD_COLUMNS, "order": "days.asc.nullslast"})


def decks(period: str) -> list[dict[str, Any]]:
    """The period's TOP_DECKS most-played decks, with meta share and record."""
    return _cached(
        "gold_deck_stats",
        {
            "select": DECK_COLUMNS,
            "period": f"eq.{period}",
            "rank": f"lte.{TOP_DECKS}",
            "order": "rank",
        },
    )


def matrix(period: str) -> list[dict[str, Any]]:
    """The matchup grid with the period's top decks on both axes. Only cells
    with data come back - a missing (a, b) pair means those two never met, not
    that the result was 0."""
    axis = [row["deck_id"] for row in decks(period)]
    if not axis:
        return []

    members = _in(axis)
    return _cached(
        "gold_matchup_stats",
        {
            "select": CELL_COLUMNS,
            "period": f"eq.{period}",
            "deck_a": members,
            "deck_b": members,
            "order": "deck_a,deck_b",
        },
    )


def deck_matchups(deck_id: str, period: str) -> list[dict[str, Any]]:
    """One deck against every opponent it faced, most-played opponent first."""
    return _cached(
        "gold_matchup_stats",
        {
            "select": OPPONENT_COLUMNS,
            "period": f"eq.{period}",
            "deck_a": f"eq.{deck_id}",
            "order": "matches.desc,deck_b",
        },
    )


def trend_decks() -> list[dict[str, Any]]:
    """The decks the trends follow, most played first. Fixed across every
    bucket and both grains (sql/016_matchup_trends.sql)."""
    return _cached("gold_trend_decks", {"select": TREND_DECK_COLUMNS, "order": "rank"})


def trend_buckets(grain: str) -> list[dict[str, Any]]:
    """Every calendar bucket of one grain, oldest first."""
    return _cached(
        "gold_trend_buckets",
        {"select": TREND_BUCKET_COLUMNS, "grain": f"eq.{grain}", "order": "bucket_start"},
    )


def deck_trends(deck_id: str, grain: str) -> list[dict[str, Any]]:
    """One deck against every trend opponent, bucket by bucket. Sparse like the
    matrix: a missing (opponent, bucket) means they did not meet that bucket."""
    return _cached(
        "gold_matchup_trends",
        {
            "select": TREND_CELL_COLUMNS,
            "grain": f"eq.{grain}",
            "deck_a": f"eq.{deck_id}",
            "order": "deck_b,bucket_start",
        },
    )


def _in(values: list[str]) -> str:
    """A PostgREST `in.()` filter. Each value is double-quoted so a comma or
    parenthesis inside a deck id cannot split the list."""
    quoted = (v.replace("\\", "\\\\").replace('"', '\\"') for v in values)
    return "in.(" + ",".join(f'"{v}"' for v in quoted) + ")"
