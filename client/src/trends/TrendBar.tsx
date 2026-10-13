import type { Grain, TrendBucket, TrendDeck } from "../api";
import { useStuck } from "../components/FilterBar";
import { Segmented } from "../components/Segmented";
import { useI18n } from "../i18n";

/**
 * The trends view's filter row, in the same frame as the metagame one. Two
 * choices: which deck, and how finely to cut time. There is no period - the
 * trends always span the whole retention window, which the right-hand readout
 * states.
 */
export function TrendBar({
  decks,
  deck,
  onDeck,
  grain,
  onGrain,
  buckets,
  busy,
}: {
  decks: TrendDeck[];
  deck: string | null;
  onDeck: (deckId: string) => void;
  grain: Grain;
  onGrain: (grain: Grain) => void;
  buckets: TrendBucket[];
  busy: boolean;
}) {
  const { t, dateRange } = useI18n();
  const [sentinel, stuck] = useStuck();
  const first = buckets[0];
  const last = buckets[buckets.length - 1];

  return (
    <>
      <div ref={sentinel} className="sticky-sentinel" aria-hidden="true" />
      <div className={`filters${stuck ? " is-stuck" : ""}${busy ? " is-busy" : ""}`}>
        <div className="field field-deck">
          <label htmlFor="trend-deck">{t("deck")}</label>
          <div className="select">
            <select
              id="trend-deck"
              value={deck ?? ""}
              disabled={!decks.length}
              onChange={(event) => onDeck(event.target.value)}
            >
              {decks.map((row) => (
                <option key={row.deck_id} value={row.deck_id}>
                  {t("rankedDeck", { rank: row.rank, name: row.deck_name ?? row.deck_id })}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <span className="label" id="grain-label">
            {t("granularity")}
          </span>
          <Segmented
            labelledBy="grain-label"
            value={grain}
            onChange={onGrain}
            options={[
              { value: "week", label: t("grainWeek") },
              { value: "month", label: t("grainMonth") },
            ]}
          />
        </div>

        <div className="field field-range">
          <span className="label">{t("trendWindow")}</span>
          <span className="range-value" aria-live="polite">
            {first && last ? dateRange(first.date_from, last.date_to) : "—"}
          </span>
        </div>

        <span className="busy-bar" aria-hidden="true" />
      </div>
    </>
  );
}
