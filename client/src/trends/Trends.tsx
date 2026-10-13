import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import { fetchDeckMatchups, fetchDeckTrends, fetchTrendBuckets, fetchTrendDecks } from "../api";
import type { DeckMatchup, Grain, TrendCell } from "../api";
import { DeckIcon } from "../components/DeckIcon";
import { Panel, Skeleton } from "../components/Panel";
import { reducedMotion } from "../fx/motion";
import { useI18n } from "../i18n";
import type { Mode } from "../scale";
import { TrendBar } from "./TrendBar";
import { TrendChart } from "./TrendChart";
import { TrendGrid } from "./TrendGrid";

/** Rows shown before "show all" - the opponents a deck meets most. */
const ROW_LIMIT = 20;

/**
 * The trends view: how one deck's matchups against the other top decks have
 * moved, week by week or month by month, across the whole retention window.
 *
 * Two panels read the same slice. The chart is one matchup over time, the
 * question the view exists to answer. The grid below it is every opponent at
 * once - the overview, and the table view of the chart, since each of its
 * rows is one chart's points as numbers. Clicking a row charts it.
 *
 * Everything here is a gold read (sql/016_matchup_trends.sql). The "whole
 * window" column is not summed from the buckets: it is the 'all' period's
 * matchup cell, the same number the metagame view's matrix shows.
 */
export function Trends({ mode }: { mode: Mode }) {
  const { t } = useI18n();
  const [grain, setGrain] = useState<Grain>("week");
  const [picked, setPicked] = useState<string | null>(null);
  const [opponentPick, setOpponentPick] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const chart = useRef<HTMLDivElement>(null);

  const decks = useQuery({ queryKey: ["trend-decks"], queryFn: fetchTrendDecks });
  const deck = decks.data?.find((row) => row.deck_id === picked) ?? decks.data?.[0] ?? null;
  const deckId = deck?.deck_id ?? null;

  const buckets = useQuery({
    queryKey: ["trend-buckets", grain],
    queryFn: () => fetchTrendBuckets(grain),
    placeholderData: (previous) => previous,
  });

  const cells = useQuery({
    queryKey: ["deck-trends", deckId, grain],
    queryFn: () => fetchDeckTrends(deckId!, grain),
    enabled: deckId != null,
    placeholderData: (previous) => previous,
  });

  // Shares its cache with the deck drawer's 'all' period.
  const overall = useQuery({
    queryKey: ["deck-matchups", deckId, "all"],
    queryFn: () => fetchDeckMatchups(deckId!, "all"),
    enabled: deckId != null,
    placeholderData: (previous) => previous,
  });

  const names = useMemo(
    () => new Map((decks.data ?? []).map((row) => [row.deck_id, row.deck_name ?? row.deck_id])),
    [decks.data],
  );

  // Opponents on the trend axis that met the deck at least once, the ones it
  // met most first. The whole-window count orders them, so the order does not
  // change between weekly and monthly.
  const { rows, byOpponent, overallBy } = useMemo(() => {
    const byOpponent = new Map<string, Map<string, TrendCell>>();
    for (const cell of cells.data ?? []) {
      if (cell.deck_b === deckId || !names.has(cell.deck_b)) continue;
      let series = byOpponent.get(cell.deck_b);
      if (!series) byOpponent.set(cell.deck_b, (series = new Map()));
      series.set(cell.bucket_start, cell);
    }
    const overallBy = new Map<string, DeckMatchup>(
      (overall.data ?? []).filter((row) => names.has(row.deck_b)).map((row) => [row.deck_b, row]),
    );
    const total = (id: string) =>
      overallBy.get(id)?.matches ??
      [...(byOpponent.get(id)?.values() ?? [])].reduce((sum, cell) => sum + cell.matches, 0);
    const rows = [...byOpponent.keys()].sort((a, b) => total(b) - total(a) || a.localeCompare(b));
    return { rows, byOpponent, overallBy };
  }, [cells.data, overall.data, deckId, names]);

  const opponent = opponentPick && rows.includes(opponentPick) ? opponentPick : rows[0] ?? null;
  const shown = showAll ? rows : rows.slice(0, ROW_LIMIT);

  // A row far down the grid charts in the panel above it, so bring that panel
  // back into view.
  const chartOpponent = useCallback((id: string) => {
    setOpponentPick(id);
    const element = chart.current;
    if (element && element.getBoundingClientRect().top < 0) {
      element.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    }
  }, []);

  const pickDeck = (id: string) => {
    setPicked(id);
    setOpponentPick(null);
  };

  const stale = buckets.isPlaceholderData || cells.isPlaceholderData || overall.isPlaceholderData;
  const fade = stale ? "stale" : "fresh";
  const failure = (decks.error ?? buckets.error ?? cells.error ?? overall.error) as Error | null;
  const ready = Boolean(buckets.data && cells.data && overall.data && deck);
  const deckName = deck ? names.get(deck.deck_id) ?? deck.deck_id : "";

  return (
    <>
      <TrendBar
        decks={decks.data ?? []}
        deck={deckId}
        onDeck={pickDeck}
        grain={grain}
        onGrain={setGrain}
        buckets={buckets.data ?? []}
        busy={buckets.isFetching || cells.isFetching || overall.isFetching}
      />

      {failure && <p className="error">{t("loadError")}</p>}

      {decks.data?.length === 0 && <p className="notice">{t("emptyTrends")}</p>}

      <div ref={chart} className="trend-anchor">
        <Panel
          icon={<LineIcon />}
          title={t("trendChartTitle")}
          info={t("trendChartNote")}
          actions={
            rows.length > 0 && (
              <div className="field field-opponent">
                <label htmlFor="trend-opponent">{t("opponent")}</label>
                <div className="select">
                  <select
                    id="trend-opponent"
                    value={opponent ?? ""}
                    onChange={(event) => setOpponentPick(event.target.value)}
                  >
                    {rows.map((id) => (
                      <option key={id} value={id}>
                        {names.get(id) ?? id}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )
          }
        >
          <div className={fade}>
            {!ready ? (
              <Skeleton rows={6} height={40} />
            ) : !opponent ? (
              <p className="notice">{t("noTrendOpponents")}</p>
            ) : (
              <>
                <div className="trend-pair">
                  <DeckIcon deckId={deck!.deck_id} />
                  <span className="trend-pair-name">{deckName}</span>
                  <span className="tip-vs">vs</span>
                  <DeckIcon deckId={opponent} />
                  <span className="trend-pair-name">{names.get(opponent) ?? opponent}</span>
                  <WholeWindow cell={overallBy.get(opponent)} />
                </div>
                <TrendChart
                  deckId={deck!.deck_id}
                  opponentId={opponent}
                  buckets={buckets.data!}
                  series={byOpponent.get(opponent) ?? new Map()}
                  grain={grain}
                  mode={mode}
                  deckName={deckName}
                  opponentName={names.get(opponent) ?? opponent}
                />
              </>
            )}
          </div>
        </Panel>
      </div>

      <Panel icon={<GridIcon />} title={t("trendsTitle")} info={t("trendsNote")}>
        <div className={fade}>
          {!ready ? (
            <Skeleton rows={10} height={40} />
          ) : rows.length === 0 ? (
            <p className="notice">{t("noTrendOpponents")}</p>
          ) : (
            <>
              <TrendGrid
                deckId={deck!.deck_id}
                deckName={deckName}
                rows={shown}
                buckets={buckets.data!}
                series={byOpponent}
                overall={overallBy}
                names={names}
                grain={grain}
                mode={mode}
                selected={opponent}
                onSelect={chartOpponent}
              />
              {rows.length > ROW_LIMIT && (
                <button type="button" className="show-more" onClick={() => setShowAll((value) => !value)}>
                  {showAll ? t("showFewer") : t("showAllOpponents", { count: rows.length })}
                </button>
              )}
            </>
          )}
        </div>
      </Panel>
    </>
  );
}

/** The matchup over the whole window, as the metagame view's matrix has it. */
function WholeWindow({ cell }: { cell: DeckMatchup | undefined }) {
  const { t, percentSign } = useI18n();
  if (!cell) return null;
  return (
    <span className="trend-pair-total">
      <span className="muted">{t("wholeWindow")}</span> <strong>{percentSign(cell.score_rate)}</strong>{" "}
      <span className="muted">· {t("matchCount", { count: cell.matches })}</span>
    </span>
  );
}

const LineIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M2.5 14.5l4.5-5 3.5 3 6.5-8" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M2.5 17.5h15" opacity="0.45" strokeLinecap="round" />
  </svg>
);

const GridIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18">
    {[0, 1, 2].flatMap((r) =>
      [0, 1, 2, 3].map((c) => (
        <rect
          key={`${r}${c}`}
          x={1.5 + c * 4.4}
          y={3 + r * 5}
          width="3.4"
          height="4"
          rx="1"
          fill="currentColor"
          opacity={0.3 + ((r * 2 + c) % 4) * 0.22}
        />
      )),
    )}
  </svg>
);
