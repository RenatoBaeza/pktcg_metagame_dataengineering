"""The metagame endpoints - everything the frontend reads.

Thin by design: each route validates its query string and hands off to
app/metagame.py, which reads the matching gold table. Every number was computed
by the refresh (sql/012_gold_finished.sql), so nothing here or there aggregates.

Every route takes the same `period` - one of the windows in gold_periods - and
scopes to it. It is the only knob: the axis is always the top 50 decks, every
cell with a match is returned, and 'other' is not in gold at all
(sql/014_fixed_view.sql). The trend routes are the exception: they span the
whole retention window, so they take a `grain` - week or month - instead
(sql/016_matchup_trends.sql).

Every route is declared `def` rather than `async def` so FastAPI runs them on
the threadpool - the Supabase client underneath is synchronous, and
awaiting it on the event loop would block every other request.
"""

from fastapi import APIRouter, Query

from app import metagame
from app.models import (
    Coverage,
    DeckMatchup,
    DeckSummary,
    Grain,
    MatchupCell,
    Period,
    PeriodInfo,
    TrendBucket,
    TrendCell,
    TrendDeck,
)

router = APIRouter(tags=["metagame"])

PERIOD = Query("all", description="One of the windows gold is computed for.")
GRAIN = Query("week", description="The calendar bucket the trends are computed in.")


@router.get("/coverage")
def get_coverage() -> Coverage:
    """What the whole dataset spans - the 'all' period's dates and totals."""
    return Coverage(**metagame.coverage())


@router.get("/periods")
def list_periods() -> list[PeriodInfo]:
    """Every period, with the dates it resolved to on the last refresh."""
    return [PeriodInfo(**row) for row in metagame.periods()]


@router.get("/decks")
def list_decks(period: Period = PERIOD) -> list[DeckSummary]:
    """The period's top 50 decks, with meta share and overall record."""
    return [DeckSummary(**row) for row in metagame.decks(period)]


@router.get("/decks/{deck_id}/matchups")
def get_deck_matchups(deck_id: str, period: Period = PERIOD) -> list[DeckMatchup]:
    """One deck against every opponent it faced, most-played opponent first.

    An unknown deck_id is not an error - it is a deck with no matches, and the
    empty list says so without the client having to special-case a 404.
    """
    return [DeckMatchup(**row) for row in metagame.deck_matchups(deck_id, period)]


@router.get("/matchups")
def get_matchups(period: Period = PERIOD) -> list[MatchupCell]:
    """The top-50 matchup grid, as a flat list of the cells that have data.

    Sparse on purpose: a missing (deck_a, deck_b) means those two never met.
    Filling the gaps with zeroes would be a bigger payload saying less.
    """
    return [MatchupCell(**row) for row in metagame.matrix(period)]


@router.get("/trends/decks")
def list_trend_decks() -> list[TrendDeck]:
    """The decks the trends follow - the most played of the retention window."""
    return [TrendDeck(**row) for row in metagame.trend_decks()]


@router.get("/trends/buckets")
def list_trend_buckets(grain: Grain = GRAIN) -> list[TrendBucket]:
    """Every week or month of the retention window, oldest first."""
    return [TrendBucket(**row) for row in metagame.trend_buckets(grain)]


@router.get("/decks/{deck_id}/trends")
def get_deck_trends(deck_id: str, grain: Grain = GRAIN) -> list[TrendCell]:
    """One deck against every trend opponent, bucket by bucket.

    Sparse like /matchups: a missing (deck_b, bucket_start) means the two did
    not meet that week or month. A deck off the trend axis is an empty list.
    """
    return [TrendCell(**row) for row in metagame.deck_trends(deck_id, grain)]
