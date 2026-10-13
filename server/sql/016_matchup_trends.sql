-- Matchup trends: how each matchup between the most played decks has moved
-- week by week and month by month. Safe to re-run.
--
-- Every other gold table answers "how does A do against B" for one period,
-- counted back from the last event. This one answers it per calendar bucket
-- across the whole retention window, so a reader can watch a matchup shift as
-- lists adapt. Three tables, all finished, read with a primary-key filter like
-- the rest of gold:
--
--   gold_trend_decks     the axis: the TREND_DECKS (app/gold.py, default 100)
--                        most played decks of the 'all' period, frozen at
--                        refresh time. Fixed across every bucket, so a line is
--                        always the same two decks from start to end.
--
--   gold_trend_buckets   one row per (grain, bucket): its calendar start, the
--                        dates it actually covers once clamped to the window,
--                        whether that is the whole bucket, and its totals.
--
--   gold_matchup_trends  one row per (grain, deck_a, deck_b, bucket): the cell,
--                        with the same columns and rules as gold_matchup_stats.
--
-- The grains are 'week' (ISO, Monday first) and 'month', both calendar
-- buckets from date_trunc. Calendar rather than counted back from the last
-- event, as the periods are, because a trend is read across buckets and a
-- bucket has to mean the same thing next refresh as it did this one. The
-- price is that the first and last buckets are usually clipped - by the
-- retention cutoff and by the last event - which `complete` records so the
-- client can say so rather than draw a three-day week as if it were seven.
--
-- Every rule from sql/012, sql/014 and sql/015 holds unchanged: both
-- directions stored, ties their own column, mirrors and unknown decks and
-- 'other' left out, gold_tournaments() deciding which events count, and a 95%
-- Wilson interval on every rate. Only decks on the trend axis are counted, so
-- a bucket's `matches` is matches between two top decks, not every match.
--
-- Measured before applying (2026-10-13, window 2026-04-13 - 2026-10-13, top
-- 100 decks, min_matches 10): 253,352 matches between axis decks,
--
--   grain  buckets  rows (both directions)  median matches per cell
--   week        27                  75,496                        2
--   month        7                  32,414                        3
--
-- Thin cells are the norm at a weekly grain. That is what the interval is for:
-- the client mutes any cell whose interval still spans 50%, exactly as the
-- matrix does, rather than hiding it behind a cutoff.
--
-- Each call refreshes one range of buckets, not a whole grain, to stay inside
-- the 8s PostgREST statement timeout. A whole grain was one call at first:
-- monthly took 4.4s, but weekly's 75k rows timed out on the write alone (about
-- 100us a row, both directions, two indexes and the foreign key). app/gold.py
-- now passes one calendar month of bucket starts per call (p_from, p_to), so
-- a weekly call writes ~12k rows and scans only that month's tournaments.
-- Every call also deletes the buckets the window has moved past, so the
-- grain still ends each run as a full reconcile. A call with no range is the
-- whole grain, which is what refresh_gold() below uses.
--
-- The aggregate itself is cheap only because the Wilson interval is computed
-- inline, in double precision. Calling wilson_interval() per cell,
-- as gold_matchup_stats does for its ~11k, took the weekly grain's 75k cells
-- to 7.0s: the function's `set search_path` stops the planner inlining it, so
-- every cell paid a full function call, and its numeric arithmetic was most of
-- the rest (3.3s inline in numeric, 1.4s in double precision). The formula and
-- the rounding to 4 places are wilson_interval's own. The function also gets
-- enough work_mem for that aggregate not to spill to disk.
--
-- The transform runs after the per-period steps, in this order:
--
--   refresh_gold_trend_decks(p_top)       reads gold_deck_stats ('all'), so it
--                                         needs that period's deck_stats first.
--   refresh_gold_matchup_trends(p_grain, p_from, p_to)
--                                         one call per grain and month of
--                                         bucket starts. Writes the buckets,
--                                         then the cells, then the bucket
--                                         totals from those cells.
--
-- refresh_gold() below is the one-call rebuild for a SQL session. It is
-- recreated here to run the trend steps too, and because it predated sql/015
-- and quietly refreshed with a minimum of 0 matches. Its signature is kept
-- (changing it would mean a DROP), so it reuses the threshold the last refresh
-- stored on gold_periods.

create table if not exists public.gold_trend_decks (
    deck_id      text        primary key,
    rank         integer     not null,
    deck_name    text,
    entries      integer     not null,
    refreshed_at timestamptz not null default now()
);

create table if not exists public.gold_trend_buckets (
    grain        text        not null check (grain in ('week', 'month')),
    bucket_start date        not null,
    date_from    date        not null,
    date_to      date        not null,
    complete     boolean     not null,
    tournaments  integer     not null default 0,
    matches      integer     not null default 0,
    refreshed_at timestamptz not null default now(),
    primary key (grain, bucket_start)
);

-- Keyed for the one read the API makes: a grain and a deck_a, every opponent
-- and bucket under it.
create table if not exists public.gold_matchup_trends (
    grain        text        not null,
    bucket_start date        not null,
    deck_a       text        not null,
    deck_b       text        not null,
    matches      integer     not null,
    wins         integer     not null,
    losses       integer     not null,
    ties         integer     not null,
    win_rate     numeric,
    score_rate   numeric     not null,
    score_low    numeric,
    score_high   numeric,
    refreshed_at timestamptz not null default now(),
    primary key (grain, deck_a, deck_b, bucket_start),
    foreign key (grain, bucket_start)
        references public.gold_trend_buckets (grain, bucket_start) on delete cascade
);

-- The foreign key's own index, so deleting a grain's buckets does not scan
-- every cell to cascade.
create index if not exists gold_matchup_trends_bucket_idx
    on public.gold_matchup_trends (grain, bucket_start);

alter table public.gold_trend_decks    enable row level security;
alter table public.gold_trend_buckets  enable row level security;
alter table public.gold_matchup_trends enable row level security;

-- Created only when missing rather than dropped and recreated, so re-running
-- the file never leaves a table briefly without its policy.
do $do$
declare
    t text;
begin
    foreach t in array array['gold_trend_decks', 'gold_trend_buckets', 'gold_matchup_trends'] loop
        if not exists (
            select 1 from pg_policies
            where schemaname = 'public' and tablename = t and policyname = t || '_public_read'
        ) then
            execute format(
                'create policy %I on public.%I for select to anon, authenticated using (true)',
                t || '_public_read', t
            );
        end if;
    end loop;
end;
$do$;

create or replace function public.refresh_gold_trend_decks(p_top integer default 100)
returns bigint
language plpgsql
security invoker
set search_path = public
as $fn$
declare
    n bigint;
begin
    delete from public.gold_trend_decks d where d.deck_id is not null;

    insert into public.gold_trend_decks (deck_id, rank, deck_name, entries, refreshed_at)
    select s.deck_id, s.rank, s.deck_name, s.entries, now()
    from public.gold_deck_stats s
    where s.period = 'all'
      and s.rank <= greatest(coalesce(p_top, 0), 0);

    get diagnostics n = row_count;
    return n;
end;
$fn$;

-- The signature grew a bucket range, so the one-argument version has to go or
-- a call naming only p_grain would have two candidates.
drop function if exists public.refresh_gold_matchup_trends(text);

create or replace function public.refresh_gold_matchup_trends(
    p_grain text,
    p_from date default null,
    p_to date default null
)
returns bigint
language plpgsql
security invoker
set search_path = public
set work_mem = '32MB'
as $fn$
declare
    n bigint;
    v_from date;
    v_to date;
    v_min integer;
    v_step interval;
    v_lo date;
    v_hi date;
begin
    if p_grain is null or p_grain not in ('week', 'month') then
        raise exception 'unknown trend grain: %', p_grain;
    end if;
    v_step := ('1 ' || p_grain)::interval;

    select p.date_from, p.date_to, p.min_matches into v_from, v_to, v_min
    from public.gold_periods p
    where p.period = 'all';
    if not found then
        raise exception 'gold period ''all'' is missing';
    end if;

    -- Buckets the window has moved past, whichever call gets here first.
    -- Cascades to their cells.
    delete from public.gold_trend_buckets b
    where b.grain = p_grain
      and (v_from is null
           or b.bucket_start < date_trunc(p_grain, v_from::timestamp)::date
           or b.bucket_start > v_to);

    -- The buckets this call owns: those starting in [p_from, p_to].
    delete from public.gold_trend_buckets b
    where b.grain = p_grain
      and (p_from is null or b.bucket_start >= p_from)
      and (p_to is null or b.bucket_start <= p_to);

    -- Every calendar bucket in range, so a bucket with no counted match is
    -- still a point on the axis rather than a silent gap.
    insert into public.gold_trend_buckets
        (grain, bucket_start, date_from, date_to, complete, refreshed_at)
    select p_grain,
           g.opens::date,
           greatest(g.opens::date, v_from),
           least((g.opens + v_step)::date - 1, v_to),
           g.opens::date >= v_from and (g.opens + v_step)::date - 1 <= v_to,
           now()
    from generate_series(
        date_trunc(p_grain, v_from::timestamp),
        date_trunc(p_grain, v_to::timestamp),
        v_step
    ) g(opens)
    where (p_from is null or g.opens::date >= p_from)
      and (p_to is null or g.opens::date <= p_to);

    -- The tournament dates those buckets span. Contiguous, so every counted
    -- match lands in one of them.
    select min(b.date_from), max(b.date_to) into v_lo, v_hi
    from public.gold_trend_buckets b
    where b.grain = p_grain
      and (p_from is null or b.bucket_start >= p_from)
      and (p_to is null or b.bucket_start <= p_to);
    if v_lo is null then
        return 0;
    end if;

    insert into public.gold_matchup_trends
        (grain, bucket_start, deck_a, deck_b, matches, wins, losses, ties,
         win_rate, score_rate, score_low, score_high, refreshed_at)
    with counted as (
        select date_trunc(p_grain, t.date::timestamp)::date as bucket_start,
               s.winner_deck_id,
               s.loser_deck_id,
               s.is_tie
        from public.silver_pairings s
        join public.gold_tournaments(v_lo, v_hi, v_min) t on t.id = s.tournament_id
        join public.gold_trend_decks w on w.deck_id = s.winner_deck_id
        join public.gold_trend_decks l on l.deck_id = s.loser_deck_id
        where s.winner_deck_id <> s.loser_deck_id
    )
    select p_grain,
           c.bucket_start,
           c.deck_a,
           c.deck_b,
           c.matches,
           c.wins,
           c.losses,
           c.ties,
           case when c.wins + c.losses > 0
                then round(c.wins::numeric / (c.wins + c.losses), 4) end,
           round((c.wins + c.ties / 2.0) / c.matches, 4),
           round(greatest(0, ci.center - ci.margin)::numeric, 4),
           round(least(1, ci.center + ci.margin)::numeric, 4),
           now()
    from (
        select e.bucket_start,
               e.deck_a,
               e.deck_b,
               count(*)::int                                  as matches,
               (count(*) filter (where e.outcome = 1))::int   as wins,
               (count(*) filter (where e.outcome = -1))::int  as losses,
               (count(*) filter (where e.outcome = 0))::int   as ties
        from (
            select k.bucket_start,
                   k.winner_deck_id as deck_a,
                   k.loser_deck_id  as deck_b,
                   case when k.is_tie then 0 else 1 end as outcome
            from counted k
            union all
            select k.bucket_start,
                   k.loser_deck_id  as deck_a,
                   k.winner_deck_id as deck_b,
                   case when k.is_tie then 0 else -1 end as outcome
            from counted k
        ) e
        group by e.bucket_start, e.deck_a, e.deck_b
    ) c
    cross join lateral (
        select (c.wins + c.ties / 2.0)::float8 / c.matches as p,
               c.matches::float8                          as trials
    ) q
    cross join lateral (
        select (q.p + 1.9208 / q.trials) / (1 + 3.8416 / q.trials) as center,
               (1.96 / (1 + 3.8416 / q.trials))
                   * sqrt(q.p * (1 - q.p) / q.trials + 0.9604 / (q.trials * q.trials)) as margin
    ) ci;

    get diagnostics n = row_count;

    -- Totals come from the rows just written, so they cannot disagree with them.
    update public.gold_trend_buckets b
    set matches = coalesce((
            select sum(m.matches)
            from public.gold_matchup_trends m
            where m.grain = b.grain and m.bucket_start = b.bucket_start
        ), 0) / 2,
        tournaments = (
            select count(*)
            from public.gold_tournaments(b.date_from, b.date_to, v_min) t
        )
    where b.grain = p_grain
      and (p_from is null or b.bucket_start >= p_from)
      and (p_to is null or b.bucket_start <= p_to);

    return n;
end;
$fn$;

-- A whole rebuild in one call, for a SQL session. Together the steps run well
-- past the 8s PostgREST timeout, so the scheduled job drives them one at a time
-- through app/gold.py instead.
create or replace function public.refresh_gold(p_since date default null)
returns table (step text, period text, rows_written bigint)
language plpgsql
security invoker
set search_path = public
as $fn$
declare
    p text;
    v_min integer;
begin
    select coalesce(max(g.min_matches), 0) into v_min from public.gold_periods g;
    return query select 'periods'::text, null::text, public.refresh_gold_periods(p_since, v_min);
    return query select 'decks'::text, null::text, public.refresh_gold_decks();
    for p in select g.period from public.gold_periods g order by g.days nulls last loop
        return query select 'matchup_stats'::text, p, public.refresh_gold_matchup_stats(p);
    end loop;
    for p in select g.period from public.gold_periods g order by g.days nulls last loop
        return query select 'deck_stats'::text, p, public.refresh_gold_deck_stats(p);
    end loop;
    return query select 'trend_decks'::text, null::text, public.refresh_gold_trend_decks(100);
    foreach p in array array['week', 'month'] loop
        return query select 'matchup_trends'::text, p, public.refresh_gold_matchup_trends(p);
    end loop;
end;
$fn$;

revoke all on function public.refresh_gold_trend_decks(integer)     from public, anon, authenticated;
revoke all on function public.refresh_gold_matchup_trends(text, date, date) from public, anon, authenticated;
revoke all on function public.refresh_gold(date)                    from public, anon, authenticated;

grant execute on function public.refresh_gold_trend_decks(integer)  to service_role;
grant execute on function public.refresh_gold_matchup_trends(text, date, date) to service_role;
grant execute on function public.refresh_gold(date)                 to service_role;
