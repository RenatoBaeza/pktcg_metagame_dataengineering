import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { fetchCoverage, fetchDecks, fetchMatrix } from "./api";
import { DeckDetail } from "./components/DeckDetail";
import { DeckTable } from "./components/DeckTable";
import { FilterBar } from "./components/FilterBar";
import { Header } from "./components/Header";
import { MatchupMatrix } from "./components/MatchupMatrix";
import { GridIcon, Panel, Skeleton, StackIcon } from "./components/Panel";
import { StatTiles } from "./components/StatTiles";
import type { Period } from "./api";
import { DEFAULT_PERIOD } from "./filters";
import { Backdrop } from "./fx/Backdrop";
import { useI18n } from "./i18n";
import { ViewTabs } from "./components/ViewTabs";
import { Trends } from "./trends/Trends";
import { useTheme } from "./useTheme";
import { useView } from "./useView";

export default function App() {
  const { t } = useI18n();
  const [mode, setMode] = useTheme();
  const [view, setView] = useView();
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
  const [selected, setSelected] = useState<string | null>(null);

  const coverage = useQuery({ queryKey: ["coverage"], queryFn: fetchCoverage });

  // Every panel below scopes to this one period, so their numbers always agree.
  // The server resolves its dates, so nothing here waits on coverage first.
  // It is the only choice: both reads are the period's top 50 decks.
  // Only the metagame view reads these; the trends view has its own reads.
  const decks = useQuery({
    queryKey: ["decks", period],
    queryFn: () => fetchDecks(period),
    placeholderData: (previous) => previous,
    enabled: view === "metagame",
  });

  const matrix = useQuery({
    queryKey: ["matrix", period],
    queryFn: () => fetchMatrix(period),
    placeholderData: (previous) => previous,
    enabled: view === "metagame",
  });

  const selectedDeck = decks.data?.find((deck) => deck.deck_id === selected) ?? null;
  const stale = decks.isPlaceholderData || matrix.isPlaceholderData;
  const failure = (coverage.error ?? decks.error ?? matrix.error) as Error | null;
  const fade = stale ? "stale" : "fresh";

  // Stable, so the memoised matrix grid and deck table skip re-rendering when
  // only the hover or the drawer changes.
  const select = useCallback((deckId: string) => setSelected(deckId), []);
  const canOpen = (deckId: string) => decks.data?.some((deck) => deck.deck_id === deckId) ?? false;

  return (
    <>
      <Backdrop />
      <div className="scroll-progress" aria-hidden="true" />

      <div className="app">
        <Header coverage={coverage.data} mode={mode} onMode={setMode} />

        <ViewTabs view={view} onView={setView} />

        {view === "trends" ? (
          <Trends mode={mode} />
        ) : (
          <>
            <FilterBar period={period} onChange={setPeriod} busy={decks.isFetching || matrix.isFetching} />

            {failure && (
              <p className="error">
                {t("loadError")}
              </p>
            )}

            {coverage.data?.matches === 0 && (
              <p className="notice">
                {t("emptyData")}
              </p>
            )}

            <div className={fade}>
              {decks.data && matrix.data ? (
                <StatTiles decks={decks.data} cells={matrix.data} />
              ) : (
                <div className="tiles">
                  {[0, 1, 2].map((i) => (
                    <span key={i} className="skeleton" style={{ height: 236, borderRadius: 16 }} />
                  ))}
                </div>
              )}
            </div>

            <Panel
              icon={<GridIcon />}
              title={t("matchups")}
              info={t("matchupNote")}
            >
              <div className={fade}>
                {!decks.data || !matrix.data ? (
                  <Skeleton rows={8} height={40} />
                ) : (
                  <div className="matrix-wrap">
                    <MatchupMatrix decks={decks.data} cells={matrix.data} mode={mode} onSelect={select} />
                  </div>
                )}
              </div>
            </Panel>

            <Panel
              icon={<StackIcon />}
              title={t("decksTitle")}
              info={t("deckNote")}
            >
              <div className={fade}>
                {decks.data ? (
                  <DeckTable decks={decks.data} selected={selected} onSelect={select} />
                ) : (
                  <Skeleton rows={10} height={34} />
                )}
              </div>
            </Panel>
          </>
        )}

        <footer className="footer">
          <span>{t("dataSource")}</span>
          <span className="sep" />
          <span>{t("refreshSchedule")}</span>
        </footer>
      </div>

      {view === "metagame" && selectedDeck && (
        <DeckDetail
          deck={selectedDeck}
          period={period}
          canOpen={canOpen}
          onSelect={select}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
