"""The metagame endpoints.

The gold tables they read are filled by SQL that is not exercised here. What
these pin down is the layer between: that the query string is validated, that
it becomes the right filter on the right table, and that the rows coming back
survive the response models intact.
"""

import pytest
from fastapi.testclient import TestClient

from app import metagame
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_cache():
    """Each test asserts on its own reads, so no test may see another's memo."""
    metagame.clear_cache()
    yield
    metagame.clear_cache()


@pytest.fixture
def reads(monkeypatch):
    """Capture the (table, params) each endpoint reads, and reply with rows."""
    calls: list[tuple[str, dict]] = []
    replies: dict[str, list] = {}

    def fake_fetch(table, params):
        calls.append((table, params))
        return replies.get(table, [])

    monkeypatch.setattr(metagame, "_fetch", fake_fetch)
    return calls, replies


def _deck(deck_id, rank, **overrides):
    return {
        "deck_id": deck_id,
        "deck_name": deck_id.replace("-", " ").title(),
        "rank": rank,
        "entries": 100,
        "tournaments": 10,
        "meta_share": 0.05,
        "mirror_matches": 3,
        "champions": 1,
        "top8": 4,
        "matches": 200,
        "wins": 100,
        "losses": 90,
        "ties": 10,
        "win_rate": 0.5263,
        "score_rate": 0.525,
        "score_low": 0.456,
        "score_high": 0.593,
        **overrides,
    }


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_decks_read_the_top_50_of_one_period(reads):
    calls, _ = reads

    client.get("/decks?period=30d")

    table, params = calls[0]
    assert table == "gold_deck_stats"
    assert params["period"] == "eq.30d"
    assert params["rank"] == "lte.50"
    assert params["order"] == "rank"
    # 'other' is not in gold at all (sql/014), so there is nothing to filter.
    assert "include_other" not in params


@pytest.mark.parametrize("period", ["3d", "7d", "30d", "60d", "90d", "all"])
def test_every_period_is_accepted(reads, period):
    calls, _ = reads

    assert client.get(f"/decks?period={period}").status_code == 200
    assert calls[0][1]["period"] == f"eq.{period}"


def test_the_retired_knobs_change_nothing(reads):
    """Size, minimum and 'other' are fixed now. An old client still sending them
    gets the fixed view rather than an error."""
    calls, _ = reads

    client.get("/decks?period=30d&limit=8&include_other=true")
    client.get("/decks?period=30d")

    assert len(calls) == 1
    assert calls[0][1]["rank"] == "lte.50"


def test_an_absent_period_means_all(reads):
    calls, _ = reads

    client.get("/decks")

    assert calls[0][1]["period"] == "eq.all"


def test_an_unknown_period_is_rejected(reads):
    """There is no row for it to read - a typo must not come back as no data."""
    assert client.get("/decks?period=45d").status_code == 422
    assert client.get("/matchups?period=last-month").status_code == 422
    # Dropped in sql/013 - it duplicated 'all' under a six-month retention.
    assert client.get("/decks?period=180d").status_code == 422


def test_matrix_axis_is_the_period_top_50(reads):
    """The axis is read from the deck ranking, then both axes filter the cells.
    Every cell comes back, however few matches it has."""
    calls, replies = reads
    replies["gold_deck_stats"] = [_deck("dragapult-ex", 1), _deck("n-zoroark", 2)]

    response = client.get("/matchups?period=90d")

    assert response.status_code == 200
    assert [table for table, _ in calls] == ["gold_deck_stats", "gold_matchup_stats"]
    assert calls[0][1]["rank"] == "lte.50"
    cells = calls[1][1]
    assert cells["period"] == "eq.90d"
    assert cells["deck_a"] == cells["deck_b"] == 'in.("dragapult-ex","n-zoroark")'
    assert "matches" not in cells


def test_an_empty_axis_reads_no_cells(reads):
    """An empty period has no decks, and `in.()` would match nothing anyway."""
    calls, _ = reads

    assert client.get("/matchups").json() == []
    assert [table for table, _ in calls] == ["gold_deck_stats"]


def test_deck_rows_survive_the_response_model(reads):
    _, replies = reads
    replies["gold_deck_stats"] = [_deck("dragapult-dusknoir", 1, meta_share=0.0835)]

    deck = client.get("/decks").json()[0]

    assert deck["deck_id"] == "dragapult-dusknoir"
    assert deck["rank"] == 1
    assert deck["mirror_matches"] == 3
    assert deck["score_low"] < deck["score_rate"] < deck["score_high"]


def test_a_null_rate_stays_null(reads):
    """A cell with only ties has no win_rate - it must not become 0."""
    _, replies = reads
    replies["gold_deck_stats"] = [_deck("a", 1), _deck("b", 2)]
    replies["gold_matchup_stats"] = [
        {
            "deck_a": "a",
            "deck_b": "b",
            "matches": 2,
            "wins": 0,
            "losses": 0,
            "ties": 2,
            "win_rate": None,
            "score_rate": 0.5,
            "score_low": 0.0947,
            "score_high": 0.9053,
        }
    ]

    cell = client.get("/matchups").json()[0]

    assert cell["win_rate"] is None
    assert cell["score_rate"] == 0.5


def test_deck_matchups_scopes_to_the_deck(reads):
    calls, _ = reads

    client.get("/decks/n-zoroark/matchups?period=30d")

    table, params = calls[0]
    assert table == "gold_matchup_stats"
    assert params["period"] == "eq.30d"
    assert params["deck_a"] == "eq.n-zoroark"
    assert "matches" not in params
    assert "deck_b" not in params
    # The opponent's display name is stored on the cell, under its own column.
    assert "deck_name:deck_b_name" in params["select"]


def test_an_unknown_deck_is_an_empty_list_not_a_404(reads):
    response = client.get("/decks/not-a-deck/matchups")

    assert response.status_code == 200
    assert response.json() == []


def test_coverage_is_the_all_period(reads):
    calls, replies = reads
    replies["gold_periods"] = [
        {
            "first_event": "2026-04-02",
            "last_event": "2026-10-02",
            "tournaments": 1693,
            "decks": 151,
            "matches": 242061,
            "refreshed_at": "2026-10-02T22:00:00+00:00",
        }
    ]

    body = client.get("/coverage").json()

    assert calls[0][1]["period"] == "eq.all"
    assert body["last_event"] == "2026-10-02"
    assert body["matches"] == 242061


def test_coverage_tolerates_an_empty_gold_layer(reads):
    """Before the first refresh there is nothing to read - that is not an error."""
    response = client.get("/coverage")

    assert response.status_code == 200
    assert response.json()["tournaments"] == 0


def test_periods_list_every_window(reads):
    _, replies = reads
    replies["gold_periods"] = [
        {"period": "30d", "days": 30, "date_from": "2026-09-02", "date_to": "2026-10-02"},
        {"period": "all", "days": None, "date_from": "2026-04-02", "date_to": "2026-10-02"},
    ]

    body = client.get("/periods").json()

    assert [p["period"] for p in body] == ["30d", "all"]
    assert body[1]["days"] is None


def test_results_are_memoed_across_requests(reads):
    calls, _ = reads

    client.get("/decks")
    client.get("/decks")

    assert len(calls) == 1


def test_different_periods_are_memoed_apart(reads):
    calls, _ = reads

    client.get("/decks?period=30d")
    client.get("/decks?period=90d")

    assert len(calls) == 2


def test_trend_decks_are_the_frozen_axis(reads):
    calls, replies = reads
    replies["gold_trend_decks"] = [
        {"deck_id": "dragapult-ex", "deck_name": "Dragapult ex", "rank": 1, "entries": 9000}
    ]

    body = client.get("/trends/decks").json()

    assert calls[0][0] == "gold_trend_decks"
    assert calls[0][1]["order"] == "rank"
    assert body[0]["deck_id"] == "dragapult-ex"


def test_trend_buckets_read_one_grain(reads):
    calls, replies = reads
    replies["gold_trend_buckets"] = [
        {
            "bucket_start": "2026-04-13",
            "date_from": "2026-04-13",
            "date_to": "2026-04-19",
            "complete": True,
            "tournaments": 70,
            "matches": 9000,
        }
    ]

    body = client.get("/trends/buckets?grain=month").json()

    assert calls[0][1]["grain"] == "eq.month"
    assert calls[0][1]["order"] == "bucket_start"
    assert body[0]["complete"] is True


def test_deck_trends_scope_to_the_deck_and_grain(reads):
    calls, _ = reads

    client.get("/decks/n-zoroark/trends?grain=week")

    table, params = calls[0]
    assert table == "gold_matchup_trends"
    assert params["grain"] == "eq.week"
    assert params["deck_a"] == "eq.n-zoroark"
    assert params["order"] == "deck_b,bucket_start"


def test_an_absent_grain_means_week(reads):
    calls, _ = reads

    client.get("/trends/buckets")

    assert calls[0][1]["grain"] == "eq.week"


def test_an_unknown_grain_is_rejected(reads):
    assert client.get("/trends/buckets?grain=day").status_code == 422
    assert client.get("/decks/a/trends?grain=year").status_code == 422


def test_trend_cells_survive_the_response_model(reads):
    _, replies = reads
    replies["gold_matchup_trends"] = [
        {
            "bucket_start": "2026-09-07",
            "deck_b": "gardevoir-ex",
            "matches": 12,
            "wins": 7,
            "losses": 4,
            "ties": 1,
            "win_rate": 0.6364,
            "score_rate": 0.625,
            "score_low": 0.3543,
            "score_high": 0.8401,
        }
    ]

    cell = client.get("/decks/a/trends").json()[0]

    assert cell["bucket_start"] == "2026-09-07"
    assert cell["score_low"] < cell["score_rate"] < cell["score_high"]
