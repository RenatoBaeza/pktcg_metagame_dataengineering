# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Layout

The product is **PKTCG DuelMeta**. "Limitless" in code and docs means the data
source (the Limitless TCG API, `app/limitless.py`), never the product - keep
that credit where it names the source.

Two projects, one repo. They share nothing but the HTTP contract between them.

```
server/    the ingest pipeline and the FastAPI service (Python, uv)
client/    the Vite + React frontend (TypeScript, npm)
```

Everything under `## Commands` runs from `server/` unless it says otherwise.

## Commands

```bash
uv sync                                    # install deps (Python 3.12, uv-managed)
uv run pytest                              # all tests
uv run pytest tests/test_limitless.py      # one file
uv run pytest tests/test_resilience.py::test_send_retries_transient_5xx   # one test
uv run uvicorn app.main:app --reload       # API on 127.0.0.1:8000, docs at /docs
```

Ingest scripts (all take `--dry-run`; the per-tournament ones also take
`--max-requests N`, `--tournament <id>`, `--restale-hours N`):

```bash
uv run python scripts/ingest_tournaments.py --dry-run
uv run python scripts/ingest_pairings.py --max-requests 400
uv run python scripts/ingest_standings.py --tournament <id>
```

Silver and gold refreshes (both take `--dry-run`, a repeatable `--only <step>`
and `--window-months N`; silver also takes `--tournament <id>` and
`--chunk-size N`, gold a repeatable `--period <3d|7d|30d|60d|90d|all>` and
`--min-matches N`):

```bash
uv run python scripts/refresh_silver.py
uv run python scripts/refresh_gold.py --only matchup_stats --period 30d
```

Retention prune (removes derived rows older than the window; `--dry-run`,
`--window-months N`):

```bash
uv run python scripts/prune_window.py
```

Deck sprite assets, written into `client/public/decks/` (`--dry-run`,
`--force`, `--out`, `--public-base`):

```bash
uv run python scripts/download_deck_sprites.py
```

Metagame analysis - archetype clustering, rock-paper-scissors cycle detection
and meta-shift detection - written to `exports/meta_analysis.md` and `.json`.
Offline and read-only: it writes nothing to the database. Needs the opt-in
`analysis` dependency group (numpy, scipy), which the API does not carry
(`--period`, `--only <archetypes|cycles|shifts>`, `--refresh`; see `--help`):

```bash
uv run --group analysis python scripts/analyze_meta.py
uv run --group analysis pytest tests/test_analysis.py   # skips without the group
```

The first run pulls all of silver (~2.5 min) into `cache/analysis/`, which is
gitignored; later runs read the cache until `--refresh`. A full run (no
`--only`) also publishes `client/public/analysis.json`, the static snapshot the
client's Insights view draws - commit it alongside the code, like the deck
sprites. The methods are
documented at the top of `analysis/archetypes.py`, `cycles.py` and `shifts.py`.

Requires `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in `server/.env` (repo
secrets in CI).

From `client/`:

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to the FastAPI service
npm run build      # tsc -b && vite build
```

## Talking to the database

The **Supabase MCP server** is how you reach the database directly — running a
query, checking a count, applying a migration. It authenticates on its own, so
it works whether or not `server/.env` is filled in, and every call takes the
project id:

```
project_id = qegmyvxfzhlpapozremu     # "supabase-reant", matches SUPABASE_URL
```

That is the only project, and there is no staging copy, so **every MCP call
lands on production**. The tools worth knowing:

- `execute_sql` — read-only inspection, and the first thing to reach for. Before
  any statement that deletes or rewrites rows, run the `count(*)` of what it
  would touch. Send **one statement per call**: a multi-statement query returns
  only the last result set and silently discards the rest, exactly as the web
  editor does. `sql/diagnostics_size.sql` is the standing pair of disk-usage
  queries, and that is why it asks to be run one half at a time.
- `apply_migration` — DDL. Takes a snake_case `name`; use the migration file's
  own stem (`008_prune_pre_2026`) so the tracked history mirrors `sql/`.
- `list_tables`, `list_extensions` — the schema as it actually is, which is what
  to check against before writing a migration, not what `sql/` says it should be.
- `get_advisors` — security and performance notices. Worth a run after any DDL;
  it catches missing RLS policies.
- `query_logs` — Postgres and PostgREST logs, for when an ingest or refresh is
  failing on the database side rather than the Limitless side.
- `create_branch` — a throwaway copy of the schema (no data) if a migration
  genuinely needs a rehearsal. It bills by the hour, so it is the exception.

Note that `list_migrations` is **not** the record of what this database has.
Supabase's migration history is empty — every file in `sql/` so far was applied
before this route existed, and none of them will ever appear there. The files
under `sql/` are the record, and the history only starts meaning anything from
the first `apply_migration` onward.

## Architecture

### The medallion layers

The database is three layers, and which one you want is rarely ambiguous:

- **Bronze** (`bronze_tournaments`, `bronze_pairings`, `bronze_standings`) is a
  faithful mirror of the Limitless API, quirks intact. Only the ingest writes
  it. It is the layer to debug against and the only one that can be rebuilt
  solely by re-fetching.
- **Silver** (`silver_tournaments`, `silver_pairings`, plus the
  `silver_standings` *view*) is analysis-shaped: one row per real match,
  restated as winner/loser with each side's deck joined in. `silver_standings`
  is a view over `bronze_standings`, not a table — the transform was a
  column-for-column copy, so materialising it bought nothing and cost 118k
  duplicated rows (`sql/007_shrink.sql`).
- **Gold** (`gold_periods`, `gold_decks`, `gold_deck_stats`,
  `gold_matchup_stats`) is finished tables: every number the frontend draws,
  computed by the refresh for each of a fixed set of periods. The API reads
  them with a primary-key filter and never aggregates (`sql/012_gold_finished.sql`).

Silver and gold are both derived, so both are disposable — dropping and
rebuilding either loses nothing.

Each transform is **SQL, not Python**: `refresh_silver_*` in `sql/005_silver.sql`
and `refresh_gold_*` in `sql/012_gold_finished.sql`. `app/silver.py` and
`app/gold.py` only drive them over PostgREST RPC — silver a batch of tournaments
at a time, gold one period at a time. That split is deliberate —
`silver_pairings` alone is ~290k bronze rows joined twice against standings,
and pulling that through PostgREST to reshape it in Python would spend minutes
of transfer on work Postgres does in place in seconds.
Batching keeps any one statement inside the 8s PostgREST statement timeout; if
a silver call ever times out, lower `--chunk-size` rather than reaching for a
different design. Gold's slowest call is ~2.3s (the `all` period's matchups).

Things about both refreshes that are easy to get wrong:

- **Order matters.** In silver, `silver_pairings` inner-joins
  `silver_tournaments`, so a tournament missing from it yields no pairings rows
  *silently*, not as a foreign-key error. In gold, `periods` resolves the
  dates every later step reads, `deck_stats` rolls up `matchup_stats`, and the
  trend steps read the `all` period's `deck_stats` for their axis, so they run
  last (`trend_decks` once, then `matchup_trends` once per grain).
  `silver.STEPS` and `gold.STEPS` encode the orders — silver has no standings
  step, since the view cannot fall behind.
- **They re-read their whole source every run**, not just what is new. A
  pairing's deck columns come from a standings ingest that usually lands
  *after* the pairing did, so an incremental-by-timestamp refresh would leave
  decks permanently null.
- **Each function is a full reconcile of its slice** (a batch of tournaments
  in silver, a period in gold), deleting rows that no longer qualify before
  writing the rest, inside one call. Re-running is a no-op beyond
  `refreshed_at`.

### The ingest pipeline

`scripts/*.py` are thin `argparse` shims; the real loop is `app/ingest.py:run()`,
a generic driver parameterized by `(resource, table, row_mapper, on_conflict,
key)`. It handles budgeting, pacing, resume-on-restart and per-tournament fault
isolation once for both pairings and standings. `app/limitless.py` is the
Limitless API client plus all row mapping; `app/supabase.py` is a thin PostgREST
layer where every request goes through `_send` (retries timeouts, transport
errors and 5xx/408/429; a 4xx is surfaced immediately as a real bug).

### The FastAPI service

`app/routers/meta.py` is the read API and is deliberately thin: it validates a
query string and hands off to `app/metagame.py`, which reads the matching gold
table with a PostgREST filter (`supabase.select`, paged past the 1000-row cap).
Nothing is summed at request time:

```
GET /coverage                     the 'all' period's dates and totals
GET /periods                      every period and the dates it resolved to
GET /decks                        gold_deck_stats, top 50 by rank
GET /decks/{deck_id}/matchups     gold_matchup_stats, one deck_a slice
GET /matchups                     gold_matchup_stats, top-50 deck_a x deck_b
GET /trends/decks                 gold_trend_decks, the trend axis by rank
GET /trends/buckets               gold_trend_buckets, one grain's buckets
GET /decks/{deck_id}/trends       gold_matchup_trends, one deck_a slice
```

All but the trend routes take the same `?period=` (`3d`, `7d`, `30d`, `60d`,
`90d`, `all`; anything else is a 422), and **nothing else**. The trend routes
span the whole window and take `?grain=` (`week`, the default, or `month`)
instead. The view is otherwise fixed:
the axis is always the top 50 decks (`metagame.TOP_DECKS`), every cell with at
least one match is returned (a thin cell is told apart by its Wilson interval,
not hidden by a cutoff), and `'other'` is not in gold at all. The retired
`limit` / `min_matches` / `include_other` parameters are simply ignored if an
old client still sends them. There is no arbitrary date range — a period is the
unit gold is computed in. Results are memoed for 5 minutes in
`metagame._cached` — gold only changes every six hours, so the memo costs
nothing and makes filter-flipping instant.

`app/routers/admin.py` exposes `GET /admin/ingest/{secret}` — the secret in the
URL selects which job runs (`ADMIN_KEY_PAIRINGS` / `ADMIN_KEY_STANDINGS` /
`ADMIN_KEY_SILVER` / `ADMIN_KEY_GOLD` env vars) and dispatches it to a FastAPI
background task, since an ingest run takes ~30 minutes.

### What silver decides

Documented at the top of `sql/005_silver.sql`. The judgement calls worth knowing
here: `silver_pairings` drops every row with no `player2` (byes and unpaired
players alike — neither is a match with a loser to name), restates
`player1`/`player2`/`winner` as `winner`/`loser`, and sets `is_tie` when bronze
recorded no winner. On tie rows `winner`/`loser` hold `player1`/`player2` in
that order so both decks stay queryable, so **filter on `is_tie` before
computing win rates**.

### What gold decides

Documented at the top of `sql/012_gold_finished.sql`, reshaped by
`sql/014_fixed_view.sql`. Gold is finished tables, computed once per **period**
— a row of `gold_periods` (`3d`, `7d`, `30d`, `60d`, `90d`, `all`), each
counted back from the last event in the data and clamped to the retention
cutoff. (`all` is the six-month retention window, shown as "All time" / "Todo
el período", so a `180d` period duplicated it to within three days and was
dropped in `sql/013`.)

```
gold_periods        one row per period: its resolved dates and totals
gold_decks          the deck dimension (name, icons, first/last seen)
gold_deck_stats     (period, deck_id): rank, entries, meta share, record,
                    rates, Wilson interval — the deck table
gold_matchup_stats  (period, deck_a, deck_b): one matrix cell, as drawn
```

~40k rows in all. It replaced a tournament-grain fact (`gold_matchups`, 312k
rows) that every request re-summed: at 1.55 matches per row it was bigger than
the silver it came from. The price is that only these periods can be asked
for; adding one is a row in `gold_periods`, a member of `models.Period` and a
preset in `client/src/filters.ts`.

The **matchup trends** (`sql/016_matchup_trends.sql`) are the one part of gold
not cut by period. They span the whole retention window in calendar buckets -
a **grain**, `week` (ISO, Monday first) or `month` - so a bucket means the same
thing from one refresh to the next:

```
gold_trend_decks     the axis: the 'all' period's TREND_DECKS (100) most
                     played decks, frozen at refresh time
gold_trend_buckets   (grain, bucket_start): the dates the bucket covers once
                     clamped to the window, `complete`, and its totals
gold_matchup_trends  (grain, deck_a, deck_b, bucket_start): one cell, with
                     the same columns and rules as gold_matchup_stats
```

~108k rows (75k weekly, 32k monthly), and thin by nature - the median weekly
cell is two matches, which is what the muted fill is for. Only matches between
two axis decks count, so a bucket's `matches` is not the bucket's every match.
Adding a grain means the check on `gold_trend_buckets.grain`, `gold.GRAINS`
and `models.Grain`.

Six decisions everything downstream depends on:

- **Every match is counted twice**, once from each side, so the matrix is
  antisymmetric by construction and a deck's whole record is one deck_a slice.
  Both directions are stored, so neither read has to flip a row.
  `gold_periods.matches` halves the total to report distinct matches.
- **Ties are their own column**, never folded into either side. Two rates come
  out of that: `win_rate` = wins/(wins+losses), and `score_rate` =
  (wins + ties/2)/matches. Ties are ~6% of matches, so they genuinely differ.
  `score_rate` is the headline; `win_rate` exists because it is what most
  people mean by "win rate" and hiding it invites someone to recompute it
  wrongly.
- **Mirrors are excluded.** A deck against itself is 50% by definition and the
  two-perspective fan-out would land a win and a loss in the same cell. The
  count lives on `gold_deck_stats.mirror_matches` instead, so a frontend can
  still label the diagonal.
- **Both decks must be known.** ~4% of `silver_pairings` rows still have a null
  deck on one side; a matchup against "unknown" is not a matchup.
- **A tournament needs at least `MIN_TOURNAMENT_MATCHES` matches to count**
  (`app/gold.py`, default 10, `--min-matches`; `sql/015`). A match is a
  `silver_pairings` row. Below it the tournament is out of everything at once -
  entries, rank, meta share, matchup cells, period totals, and which event a
  period counts back from - because dropping it from one table and not another
  would let the deck table and the matrix disagree. The rule lives in one SQL
  function, `gold_tournaments(from, to, min_matches)`, which every refresh
  joins instead of `silver_tournaments`. The threshold is passed to
  `refresh_gold_periods` and stored on each `gold_periods` row, where the later
  steps read it beside the dates, and `/coverage` and `/periods` return it.
  It catches both tiny locals and events whose pairings Limitless never
  finished recording (30-player events with under 5 matches).
- **`deck_id = 'other'` is excluded at the source.** It is Limitless's
  catch-all for unclassified lists, not an archetype, and the frontend never
  shows it, so every refresh function skips it (`sql/014`): it holds no rank or
  meta share, a match against it counts in neither deck's record, it has no
  matchup cells, and the period totals leave it out too. Until `sql/014` both
  variants were computed under an `include_other` key column; that column is
  gone, and bringing 'other' back means re-adding it to every refresh step.

Every rate is stored with a 95% Wilson interval beside it (`score_low`,
`score_high`) — a 3-match cell at 100% and a 300-match cell at 55% are not the
same claim, and the interval is what stops them being drawn as if they were.

Each period total on `gold_periods` is written by the step that has just
computed the rows it sums (matches by `matchup_stats`; entries, decks and
tournaments by `deck_stats`), so it cannot disagree with the table beside it.
`refresh_gold_periods` itself only resolves dates, through index probes: an
earlier draft that scanned `silver_pairings` inside an `UPDATE` (which Postgres
will not parallelise) ran 4-8s against the 8s timeout.

### Deck sprites

`scripts/download_deck_sprites.py` builds `client/public/decks/`: one composited
PNG per deck plus an `index.json`, and nothing else. Files are keyed on
`deck_id`, not `deck_name` — names are not unique (two unrelated decks are both
"Alakazam"), and `deck_id` is what `silver_pairings` and `gold_matchup_stats`
carry.
The deck-to-sprite mapping comes from `silver_standings.deck_icons`, so nothing
is scraped or name-matched; only the images themselves are fetched, from the
same CDN limitlesstcg.com uses. See `app/sprites.py`.

The individual Pokemon sprites are **not** served. They go to `server/.sprite-cache/`,
gitignored, purely so a re-run refetches nothing — every pixel of them is
already inside a composite, and 105 of the 206 decks have one icon, so their
composite *is* that sprite re-saved. Serving both meant handing the browser a
second copy of every image. Delete the cache freely; the next run rebuilds it.
The client only ever loads `/decks/<deck_id>.png` (`api.ts` `deckImage()` →
`DeckIcon.tsx`); nothing reads `index.json`, so it is documentation of the
mapping rather than a runtime dependency.

### Adding a new per-tournament resource

The driver couples column names to the *resource* by convention: `app/ingest.py`
derives `{resource}_ingested_at` and `{resource}_count` and writes both on
`bronze_tournaments`. (Derived from the resource, not the table, so the progress
columns kept their bare names when the tables gained the `bronze_` prefix.) So a
new resource needs, in order:

1. A `sql/00N_<resource>.sql` migration adding those two columns to
   `bronze_tournaments`, the partial index on
   `... where <resource>_ingested_at is null`, and the `bronze_<resource>` table.
2. A `to_<resource>_row(tournament_id, item)` mapper in `app/limitless.py`.
3. A ~15-line `scripts/ingest_<resource>.py` calling `ingest.run(...)`.
4. Optionally a silver counterpart: a table, a `refresh_silver_<resource>`
   function alongside the others, and its name added to `silver.STEPS`.

**SQL migrations are applied through the Supabase MCP `apply_migration` tool** —
nothing in this repo runs them, and there is no local Postgres to run them
against. The files under `sql/` remain the source of truth; the MCP call is
only how they reach the database. They are written to be idempotent and
re-runnable, so a half-applied migration is fixed by fixing the file and
applying it whole again, not by hand-patching the difference.

Because it applies straight to production (see *Talking to the database*), read
before you write: `list_tables` for the shape the database is in now, and
`execute_sql` for the row counts a destructive statement would touch.
`sql/008_prune_pre_2026.sql` is the example — its header records the before and
after row counts of the live run, which is the shape a destructive migration
should be documented in.

### The retention window

The derived layers are a rolling window, not the full dataset. Bronze keeps
everything within `MIN_TOURNAMENT_DATE` (it is the only layer that costs API
time to rebuild), but `silver_*` and `gold_*` hold only the last
`RETAIN_MONTHS` (default 6). Two halves, both reading the cutoff from
`app/limitless.py:retention_cutoff` so they cannot drift:

- **The refreshes stay inside the window.** `silver.py` filters its bronze
  listing to `date >= cutoff` via `--window-months` (default `RETAIN_MONTHS`;
  `0` refreshes everything, the whole-database-rebuild escape hatch), and since
  it is a full reconcile of whatever slice it is handed, that is what stops it
  re-adding rows the prune removed. `gold.py` passes the same cutoff to
  `refresh_gold_periods(p_since)`, which clamps every period to it — so gold
  never counts an out-of-window tournament even before the prune has run.
- **`scripts/prune_window.py` deletes what the window excludes.** It calls
  `prune_derived_before(cutoff)`, which deletes the out-of-window
  `silver_tournaments` rows, cascading to `silver_pairings`. Gold has no
  per-tournament rows and no foreign key to silver, so nothing cascades into it
  and nothing needs re-rolling.

The six-hourly workflow runs the refresh then the prune, in that order.
Dropping rows does not return disk, so a one-off `vacuum full` on the pruned
tables is what reclaims the space after a big prune. (`sql/011` also tuned
autovacuum on the old gold tables. `sql/012` dropped them, and the new ones are
small and rewritten whole each run, so the default threshold is fine.)

Semicolons inside SQL comments are safe over MCP: the file is handed to Postgres
as one string and parsed properly, comments and all. The old ban on them was a
Supabase SQL editor quirk — the editor split statements on semicolons without
regard for comments, so one inside a comment truncated the statement. That is
why `sql/005_silver.sql` and `sql/006_gold.sql` carry no comments *inside* their
function bodies; new SQL does not need that restraint unless you plan to paste
it into the web editor.

Since nothing runs the migrations, there is no schema test either.
`apply_migration` at least fails loudly on a bad statement — Postgres reports
the real error and the transaction rolls back — but for a long file it is
cheaper to catch a typo before touching production:

```bash
uv run --with pglast python -c "import pglast,sys; pglast.parse_sql(open(sys.argv[1]).read())" sql/012_gold_finished.sql
```

That parses the outer statements but treats each `$fn$ ... $fn$` body as an
opaque string, so it will not catch a typo inside a function. `pglast.parse_sql`
on a `language sql` body and `pglast.parse_plpgsql` on a plpgsql one do.

### Rate limiting is the dominant constraint

The Limitless API allows **50 requests / 5 minutes per IP, shared across the
whole API**. `RATE_LIMIT_INTERVAL` (~6.2s) is the pacing budget, and
`ingest.run` sleeps it between every tournament. Consequences that are easy to
break accidentally:

- Never run two per-tournament ingests concurrently — the GitHub workflow steps
  are deliberately sequential for this reason, and the `concurrency` group stops
  overlapping workflow runs.
- A full backfill of ~2000 tournaments is hours of wall time spread over many
  runs, which is why every run is bounded and resumable.
- The silver and gold refreshes are *not* subject to this. They talk only to
  Supabase and the work happens inside Postgres, so they cost a minute, not
  thirty.

### Idempotency and resumption

Everything is designed to be re-run safely:

- All writes are PostgREST upserts. `ingest.run` also collapses duplicate keys
  *within* one payload, because PostgREST rejects a batch touching the same key
  twice — that's what the `key` callable is for.
- `bronze_pairings.id` is a sha1 of
  `tournament_id|phase|round|match|table_number|player1` (see
  `limitless.pairing_id`), a surrogate key because `match` and `table_number`
  are nullable and can't be part of a Postgres PK. Changing that hash recipe
  orphans every existing row, in silver as well as bronze — `silver_pairings`
  carries the same id. `bronze_standings` needs no surrogate:
  `(tournament_id, player)` is naturally unique.
- Progress lives in `bronze_tournaments.<resource>_ingested_at`. Anything from the last
  `--restale-hours` (default 48) is re-fetched even once ingested, so a
  tournament captured mid-event doesn't keep partial data forever.
- One failing tournament is caught and skipped, leaving its progress column
  null so the next run retries it. `run()` returns exit 1 only when more than
  10% of a run's tournaments failed — occasional failures are expected and
  self-healing. `silver.run()` and `gold.run()` apply the same tolerance to
  failed batches.

### API payload quirks worth knowing before touching the mappers

- `pairings.winner` is a union: usually a username, sometimes the bare integer
  `-1` or `0`. Split into `winner` (text) and `result_code` (int); `-1` appears
  for both unpaired players and two-player matches, so it is stored verbatim
  rather than interpreted as a draw.
- `pairings.match` is a top-cut bracket slot (`"T8-1"`), absent during swiss —
  top-cut rows share a round number and have no table, so `match` is what
  distinguishes them. `table` and `player2` are both nullable.
- `standings.deck` is occasionally an empty object, and `placing`, `country`,
  `drop` are all nullable, so `to_standing_row` reads everything defensively.
  The API's `placing` is stored as `placement`.
- **`standings.decklist` is deliberately dropped** in `to_standing_row` — it is
  ~2 orders of magnitude larger than the rest of the payload and nothing here
  depends on it. Don't "restore" it without a concrete consumer.

## The client

Vite + React + TypeScript, three runtime dependencies (react, react-dom,
`@tanstack/react-query`). No component library and no charting library: the
heatmaps are `<table>`s of coloured cells and the one line chart
(`trends/TrendChart.tsx`) is hand-drawn SVG, and a library would cost more
than it saved.

- `src/api.ts` types the endpoints. Base URL is `/api` in dev, which
  `vite.config.ts` proxies to the FastAPI service so the browser stays on one
  origin; set `VITE_API_URL` to point at a deployed API instead.
- Two views, switched by `ViewTabs` and kept in the URL hash (`#/`,
  `#/trends`; `src/useView.ts`), since the static build has no server-side
  routing. **Metagame** is everything below. **Trends** (`src/trends/`) is one
  deck's matchups against the other trend decks over time: a filter row of its
  own (deck and weekly/monthly - no period, it always spans the window), a
  line chart of one matchup, and a grid of every opponent x bucket that is
  also the chart's table view. The chart's line is ink, not a data hue: its
  position against the 50% rule is the polarity, the washes either side are
  the matrix's blue/red, and a point is hollow where the matrix would mute
  the cell. The grid's cells are the matrix's cells (`scoreColor`, muted,
  dot for "did not meet"). Its "All" column is the `all` period's
  `gold_matchup_stats` cell, never a sum of the buckets.
- `src/scale.ts` is the diverging colour scale, and it is the file to read
  before changing any colour. Blue is favourable, red unfavourable, neutral
  gray at 50%, saturating at 25%/75% because real matchups between decks anyone
  plays live between roughly 35% and 65%. The stops mirror the design system's
  blue ramp step for step in OKLCH lightness and were validated (monotone
  lightness, adjacent ΔL ≥ 0.06, single hue per arm). It is deliberately *not*
  green/red: those two collapse under the most common colour blindness, which
  is exactly what a matchup chart cannot afford. Dark mode is a separate set of
  stops, not an inversion — on a dark surface the neutral end has to recede
  toward the surface, which is the opposite direction of travel.
  A cell whose interval still spans 50% is drawn **muted**:
  `scoreColor(rate, mode, true)` mixes it toward the matrix board and returns
  ink measured against the mixed colour. The full-strength colour survives
  only as its outline (`edge`). So set a cell's background from `scoreColor`,
  never with a CSS opacity or `color-mix`, or the ink stops being measured.
- Filters live in one row above everything they scope, and every panel reads
  the same period, so the numbers on screen always agree. The date presets are
  the server's periods, passed as `?period=`. The server resolves their dates,
  counting back from the last event in the data rather than from today —
  results land days after an event happens, so "last 30 days" from today's
  date would quietly clip the most recent weekend.
- The period is the only control. The matrix is always the top 50 decks,
  every cell with at least one match is drawn, and 'other' never appears —
  all three are fixed on the server, so there is no deck-count, minimum-match,
  'other' or matrix/table toggle to add back on the client alone.
- Every rate is drawn with its match count reachable (the cell's number, its
  tooltip, its aria-label), so nothing is encoded by colour alone. The Wilson
  interval is **not shown** anywhere - readers found it confusing - but it still
  decides things: with no minimum-match cutoff, the muted fill for an interval
  that still spans 50% is what keeps a 2-match cell from reading like a finding,
  and the stat tiles skip anything that inconclusive.
- The look is Pokémon: yellow and navy chrome on warm card stock (a slate
  night in dark mode), the stat tiles drawn as TCG cards, tooltips as game
  dialog boxes, the deck drawer as a Pokédex entry. Poké Ball red appears only
  on literal Poké Ball / Pokédex hardware (the mark, the drawer's band),
  never beside a matchup cell. No purple anywhere.
- Two faces: Pixelify Sans for headings and wordy labels, Inter for body text
  and **anything with a digit in it** - the pixel 5 reads as an S, which is
  why the stat values, the table headers ("95% range") and the Pokédex number
  are Inter.
- `src/fx/` is the motion layer, still with no dependency added: `Backdrop.tsx`
  (a static dot grid and Poké Ball watermark - nothing moves), `motion.ts`
  (count-up, scroll reveal, the reduced-motion check) and `useFlip.ts` (rows
  glide on re-sort). The stylesheet is split by area under `src/styles/`,
  imported in order by `styles.css`. Rules that are easy to break:
  - **Hover changes a colour, an underline or an outline - it never moves,
    scales, tilts, spins or plays an animation.** No cursor spotlights, no
    sheens, no pointer-tracking CSS variables. The matrix crosshair dims the
    rest of the grid, which is the one hover effect that earns its keep.
  - **Every effect honours `prefers-reduced-motion`**, in CSS (`motion.css`)
    and in JS (`reducedMotion()`): same page, movement removed.
  - **The matrix grid is memoised and never re-renders on hover.** The
    crosshair is a two-selector `<style>` rule written from the hovered
    indices, and hover is one delegated listener on the `<table>`.
  - **Anything `position: fixed` inside a panel must be portalled.** A panel
    mid-reveal carries a `transform` and a `filter`, and either makes it the
    containing block for fixed descendants. `Tooltip` and `DeckDetail` both
    use `createPortal`.
  - **Entrance keyframes declare only `from` and run with fill-mode
    `backwards`**, so a finished entrance never pins a `transform` that a
    later state (an `:active` press, a re-sort glide) needs.

## Conventions

- The HTTP library is `httpx2`, imported as `import httpx2 as httpx` throughout.
- Scripts prepend `server/` to `sys.path` before importing `app`, hence the
  `# noqa: E402` on those imports.
- Tests use hand-rolled `FakeClient`/`FakeResponse` fakes (see
  `tests/test_resilience.py`) and monkeypatch `time.sleep` away rather than
  mocking libraries.
- `.github/workflows/ingest-tournaments.yml` runs all three ingests every 6
  hours, then the silver refresh, then the gold refresh, and is manually
  dispatchable with `dry_run` / `max_requests` inputs. Its steps all run with
  `working-directory: server`. The silver and gold steps are skipped on a dry
  run, since there would be nothing new to derive from.
