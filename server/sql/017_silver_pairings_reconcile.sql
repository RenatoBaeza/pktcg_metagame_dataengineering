-- Makes refresh_silver_pairings cheap enough to fit the 8s PostgREST statement
-- timeout again. Safe to re-run. Replaces the definition in sql/007_shrink.sql.
--
-- What broke (2026-11-04): from Nov 1 every silver_pairings batch timed out
-- (57014), on every run, so no match after Oct 28 reached silver and gold kept
-- counting its periods back from Oct 28. The function had averaged 5.5s of
-- its 8s for a long time (pg_stat_statements: 887 calls, mean 5540ms, max
-- 7991ms), and bronze_pairings growing to 467k rows tipped it over. Measured
-- on one 200-tournament batch:
--
--   orphan delete   4.6s cold, 1.2s warm. A per-row probe of bronze_pairings'
--                   primary key for each of the batch's ~13k silver rows -
--                   53k buffers, and the key is a sha1, so every probe is a
--                   random read. It found nothing to delete.
--   upsert          read side 0.7s, then a rewrite of all ~22.5k rows whether
--                   or not anything in them changed - which is where the
--                   13.7 GB of WAL silver had written came from.
--
-- Two changes, same results:
--
--   * Orphans are found as a set. The batch's silver ids EXCEPT the batch's
--     bronze ids, both read through their tournament_id index. Restricting
--     bronze to the batch's tournaments is exact, not an approximation: the id
--     hashes tournament_id (limitless.pairing_id), so a bronze row with a
--     silver row's id is always in that row's tournament. Measured: 35ms
--     warm against 1249ms for the old form, 4.9k buffers against 53k.
--
--   * Only rows that differ are written. The freshly derived rows EXCEPT what
--     silver already holds for the batch - EXCEPT, not a column-by-column
--     comparison, because it treats two nulls as equal, which is what an
--     unknown deck_id on both sides should mean. Unchanged rows never reach
--     the upsert, so they are neither rewritten nor even row-locked by
--     ON CONFLICT. On the batch above: 0.8s to find 9567 rows to write (the
--     missing Oct 29+ matches). In steady state that is near zero.
--
-- What changes for a reader: refreshed_at is now when a row last changed, not
-- when a refresh last passed over it, and the return value counts rows written
-- rather than rows visited. Nothing reads either. Re-running is now a true
-- no-op rather than "a no-op beyond refreshed_at".
--
-- plan_cache_mode = force_custom_plan, because every predicate here is
-- `p_tournaments is null or tournament_id = any(p_tournaments)`. A cached
-- generic plan cannot know which side of that `or` holds, so it is a seq
-- scan of all of bronze_pairings (verified with explain (generic_plan)). A
-- custom plan folds the null test away and uses the index. Planning costs a
-- few milliseconds a call.

create or replace function public.refresh_silver_pairings(p_tournaments text[] default null)
returns bigint
language plpgsql
security invoker
set search_path = public
set plan_cache_mode = force_custom_plan
as $fn$
declare
    n bigint;
begin
    delete from public.silver_pairings s
    using (
        select x.id
        from public.silver_pairings x
        where p_tournaments is null or x.tournament_id = any(p_tournaments)
        except
        select b.id
        from public.bronze_pairings b
        where b.player2 is not null
          and (p_tournaments is null or b.tournament_id = any(p_tournaments))
    ) gone
    where s.id = gone.id;

    insert into public.silver_pairings
        (id, tournament_id, phase, round, winner, loser,
         winner_deck_id, loser_deck_id, is_tie, refreshed_at)
    select c.id,
           c.tournament_id,
           c.phase,
           c.round,
           c.winner,
           c.loser,
           c.winner_deck_id,
           c.loser_deck_id,
           c.is_tie,
           now()
    from (
        select m.id,
               m.tournament_id,
               m.phase,
               m.round,
               m.winner,
               m.loser,
               w.deck_id,
               l.deck_id,
               m.is_tie
        from (
            select b.id,
                   b.tournament_id,
                   b.phase,
                   b.round,
                   coalesce(b.winner, b.player1) as winner,
                   case when b.winner is null          then b.player2
                        when b.winner = b.player1      then b.player2
                        when b.winner = b.player2      then b.player1
                   end                            as loser,
                   (b.winner is null)             as is_tie
            from public.bronze_pairings b
            where b.player2 is not null
              and (p_tournaments is null or b.tournament_id = any(p_tournaments))
        ) m
        join public.silver_tournaments t on t.id = m.tournament_id
        left join public.bronze_standings w
               on w.tournament_id = m.tournament_id and w.player = m.winner
        left join public.bronze_standings l
               on l.tournament_id = m.tournament_id and l.player = m.loser
        where m.loser is not null
        except
        select s.id,
               s.tournament_id,
               s.phase,
               s.round,
               s.winner,
               s.loser,
               s.winner_deck_id,
               s.loser_deck_id,
               s.is_tie
        from public.silver_pairings s
        where p_tournaments is null or s.tournament_id = any(p_tournaments)
    ) c (id, tournament_id, phase, round, winner, loser,
         winner_deck_id, loser_deck_id, is_tie)
    on conflict (id) do update set
        tournament_id    = excluded.tournament_id,
        phase            = excluded.phase,
        round            = excluded.round,
        winner           = excluded.winner,
        loser            = excluded.loser,
        winner_deck_id   = excluded.winner_deck_id,
        loser_deck_id    = excluded.loser_deck_id,
        is_tie           = excluded.is_tie,
        refreshed_at     = excluded.refreshed_at;

    get diagnostics n = row_count;
    return n;
end;
$fn$;

-- create or replace keeps the grants from sql/005, restated so this file
-- stands on its own.
revoke all on function public.refresh_silver_pairings(text[]) from public;
grant execute on function public.refresh_silver_pairings(text[]) to service_role;
