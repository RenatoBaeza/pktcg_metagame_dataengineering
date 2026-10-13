"""The gold refresh driver.

The transform is SQL and is not exercised here. What is worth pinning down is
the driver's contract with it: periods are resolved first and the dimension
second, the stats run once per period with matchups before decks, the trends
run last and once per grain, and a call that blows up costs only itself.
"""

import argparse

import pytest

from app import gold, supabase


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Serves the period listing and records every refresh call.

    `fail_on` names steps whose calls should raise, to stand in for a call that
    hits a statement timeout. `window` is the 'all' period's (date_from,
    date_to), which the trend step reads to split its calls; None stands in for
    a window the periods step has not resolved yet.
    """

    def __init__(self, periods=("30d", "all"), fail_on=(), rows_per_call=7, window=None):
        self.periods = list(periods)
        self.window = window
        self.fail_on = set(fail_on)
        self.rows_per_call = rows_per_call
        self.calls: list[tuple[str, dict]] = []
        self.listings: list[dict] = []

    def request(self, method, url, **kwargs):
        if method == "POST":
            step = url.rsplit("/rpc/refresh_gold_", 1)[1]
            self.calls.append((step, kwargs["json"]))
            if step in self.fail_on:
                raise RuntimeError("statement timeout")
            return FakeResponse(200, json_data=self.rows_per_call)

        params = kwargs.get("params", {})
        if url.endswith("/gold_periods") and params.get("select") == "period":
            self.listings.append(params)
            return FakeResponse(200, json_data=[{"period": p} for p in self.periods])
        if url.endswith("/gold_periods") and params.get("period") == "eq.all":
            rows = [] if self.window is None else [dict(zip(("date_from", "date_to"), self.window))]
            return FakeResponse(200, json_data=rows)

        # Anything else is a count_rows probe.
        return FakeResponse(200, headers={"content-range": "0-0/0"})


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(supabase, "credentials", lambda: ("http://db", "secret"))
    monkeypatch.setattr(supabase.time, "sleep", lambda _: None)


@pytest.fixture
def _client(monkeypatch):
    def install(client):
        monkeypatch.setattr(gold.httpx, "Client", lambda **_: _NoopContext(client))
        return client

    return install


class _NoopContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *exc):
        return False


def _args(**overrides):
    defaults = dict(
        only=None,
        period=None,
        window_months=gold.RETAIN_MONTHS,
        min_matches=gold.MIN_TOURNAMENT_MATCHES,
        dry_run=False,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def test_steps_run_in_dependency_order(_client):
    """Every step reads the dates refresh_gold_periods resolves, deck_stats
    rolls up matchup_stats, and the trend axis is read off deck_stats - so the
    order is fixed."""
    client = _client(FakeClient(periods=["30d", "all"]))

    assert gold.run(_args()) == 0

    assert [step for step, _ in client.calls] == [
        "periods",
        "decks",
        "matchup_stats",
        "matchup_stats",
        "deck_stats",
        "deck_stats",
        "trend_decks",
        "matchup_trends",
        "matchup_trends",
    ]


def test_stats_run_once_per_period(_client):
    client = _client(FakeClient(periods=["30d", "90d", "all"]))

    gold.run(_args())

    for step in gold.PERIOD_STEPS:
        payloads = [payload for s, payload in client.calls if s == step]
        assert payloads == [{"p_period": "30d"}, {"p_period": "90d"}, {"p_period": "all"}]


def test_periods_are_clamped_to_the_retention_window(_client):
    """gold must not count tournaments the prune is about to delete."""
    client = _client(FakeClient())

    gold.run(_args(window_months=3))

    assert client.calls[0][1]["p_since"] == gold.retention_cutoff(3).isoformat()


def test_window_months_zero_uses_everything(_client):
    """0 disables the clamp - the escape hatch for a whole-database rebuild."""
    client = _client(FakeClient())

    gold.run(_args(window_months=0))

    assert client.calls[0][1]["p_since"] is None


def test_min_matches_reaches_the_periods_step(_client):
    """The threshold is resolved once, with the dates, so every later step
    reads the same one."""
    client = _client(FakeClient())

    gold.run(_args(min_matches=25))

    assert client.calls[0] == (
        "periods",
        {"p_since": gold.retention_cutoff().isoformat(), "p_min_matches": 25},
    )
    assert all("p_min_matches" not in payload for _, payload in client.calls[1:])


def test_min_matches_defaults_and_floors_at_zero(_client):
    client = _client(FakeClient())
    gold.run(_args())
    assert client.calls[0][1]["p_min_matches"] == gold.MIN_TOURNAMENT_MATCHES

    client = _client(FakeClient())
    gold.run(_args(min_matches=-3))
    assert client.calls[0][1]["p_min_matches"] == 0


def test_period_narrows_the_stats_without_listing(_client):
    client = _client(FakeClient(periods=["30d", "90d", "all"]))

    gold.run(_args(period=["90d"]))

    assert client.listings == []
    assert [payload for step, payload in client.calls if step in gold.PERIOD_STEPS] == [
        {"p_period": "90d"},
        {"p_period": "90d"},
    ]


def test_one_failed_call_does_not_stop_the_run(_client):
    """A timed-out call is redone by the next run - it must not abort this one."""
    client = _client(FakeClient(periods=["30d", "90d", "all"], fail_on=["matchup_stats"]))

    # 3 of 11 calls failing is over the tolerance, so the run reports failure...
    assert gold.run(_args()) == 1
    # ...but the later steps still ran to completion.
    assert [step for step, _ in client.calls].count("deck_stats") == 3


def test_occasional_failure_is_tolerated(_client):
    """A single failed call is expected now and then and self-heals."""
    _client(FakeClient(fail_on=["decks"]))

    assert gold.run(_args()) == 0


def test_only_limits_the_steps(_client):
    client = _client(FakeClient(periods=["all"]))

    gold.run(_args(only=["deck_stats"]))

    assert client.calls == [("deck_stats", {"p_period": "all"})]


def test_dry_run_writes_nothing(_client):
    client = _client(FakeClient())

    assert gold.run(_args(dry_run=True)) == 0
    assert client.calls == []


def test_trends_run_once_per_grain_without_a_window(_client):
    """Before the window resolves there is nothing to split: one open range."""
    client = _client(FakeClient(periods=["30d", "all"]))

    gold.run(_args())

    assert [payload for step, payload in client.calls if step == "matchup_trends"] == [
        {"p_grain": "week", "p_from": None, "p_to": None},
        {"p_grain": "month", "p_from": None, "p_to": None},
    ]


def test_trends_run_a_month_of_buckets_per_call(_client):
    """A whole weekly grain is too many rows for one statement, so each call
    covers one calendar month of bucket starts, open at both ends so a bucket
    straddling the window's edge still belongs to exactly one call."""
    client = _client(FakeClient(window=("2026-04-13", "2026-06-02")))

    gold.run(_args(only=["matchup_trends"]))

    ranges = [(None, "2026-04-30"), ("2026-05-01", "2026-05-31"), ("2026-06-01", None)]
    assert [payload for _, payload in client.calls] == [
        {"p_grain": grain, "p_from": lo, "p_to": hi} for grain in gold.GRAINS for lo, hi in ranges
    ]


def test_trend_ranges_cross_the_year():
    client = FakeClient(window=("2026-12-20", "2027-01-05"))

    ranges = gold.trend_ranges(client, "http://db", {})

    assert ranges == [("2026-12", None, "2026-12-31"), ("2027-01", "2027-01-01", None)]


def test_trend_axis_is_the_top_trend_decks(_client):
    client = _client(FakeClient())

    gold.run(_args(only=["trend_decks"]))

    assert client.calls == [("trend_decks", {"p_top": gold.TREND_DECKS})]


def test_period_does_not_narrow_the_trends(_client):
    """The trends span the whole window, so a --period has nothing to scope."""
    client = _client(FakeClient(periods=["30d", "all"]))

    gold.run(_args(period=["30d"], only=["matchup_trends"]))

    assert [payload for _, payload in client.calls] == [
        {"p_grain": g, "p_from": None, "p_to": None} for g in gold.GRAINS
    ]
