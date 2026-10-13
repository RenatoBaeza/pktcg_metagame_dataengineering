import { memo, useCallback, useState, type CSSProperties, type FocusEvent, type PointerEvent } from "react";
import type { DeckMatchup, Grain, TrendBucket, TrendCell } from "../api";
import { DeckIcon } from "../components/DeckIcon";
import { Tooltip, type Anchor } from "../components/Tooltip";
import { spansEven } from "../format";
import { useVersion } from "../fx/motion";
import { useI18n } from "../i18n";
import type { Mode } from "../scale";
import { scoreColor } from "../scale";
import { TrendReadout } from "./TrendReadout";

type Rate = Pick<TrendCell, "score_rate" | "score_low" | "score_high" | "matches">;

/**
 * One deck against every opponent it met, bucket by bucket. A row is an
 * opponent, a column a week or month, and the last column the whole window.
 *
 * The cells are the matrix's cells - the same diverging fill from scale.ts,
 * muted when the interval still spans 50%, a dot where the two did not meet -
 * so a reader who learned the matrix already reads this. Weekly cells are
 * mostly thin (a median of two matches), and the muting is what keeps a 2-0
 * week from reading as a finding.
 *
 * A column whose bucket the window clips (the first and last, usually) has its
 * heading drawn quieter and says so in its tooltip.
 *
 * The grid is memoised and hover is one delegated listener, as in the matrix,
 * so the tooltip following the pointer never re-renders the cells.
 */
export function TrendGrid({
  deckId,
  deckName,
  rows,
  buckets,
  series,
  overall,
  names,
  grain,
  mode,
  selected,
  onSelect,
}: {
  deckId: string;
  deckName: string;
  rows: string[];
  buckets: TrendBucket[];
  series: Map<string, Map<string, TrendCell>>;
  overall: Map<string, DeckMatchup>;
  names: Map<string, string>;
  grain: Grain;
  mode: Mode;
  selected: string | null;
  onSelect: (opponent: string) => void;
}) {
  const [hovered, setHovered] = useState<{ r: number; c: number; anchor: Anchor } | null>(null);
  const version = useVersion(series);

  const onHover = useCallback((r: number, c: number, anchor: Anchor) => setHovered({ r, c, anchor }), []);
  const onLeave = useCallback(() => setHovered(null), []);

  const opponent = hovered ? rows[hovered.r] : undefined;
  const bucket = hovered && hovered.c < buckets.length ? buckets[hovered.c] : null;
  const cell = opponent ? (bucket ? series.get(opponent)?.get(bucket.bucket_start) : overall.get(opponent)) : undefined;

  return (
    <>
      <div className="trend-scroll">
        <Grid
          key={version}
          deckId={deckId}
          rows={rows}
          buckets={buckets}
          series={series}
          overall={overall}
          names={names}
          grain={grain}
          mode={mode}
          selected={selected}
          onSelect={onSelect}
          onHover={onHover}
          onLeave={onLeave}
        />
      </div>

      {hovered && opponent && cell && (
        <Tooltip anchor={hovered.anchor}>
          <TrendReadout
            deckId={deckId}
            opponentId={opponent}
            deckName={deckName}
            opponentName={names.get(opponent) ?? opponent}
            bucket={bucket}
            grain={grain}
            cell={cell}
            mode={mode}
          />
        </Tooltip>
      )}
    </>
  );
}

const Grid = memo(function Grid({
  deckId,
  rows,
  buckets,
  series,
  overall,
  names,
  grain,
  mode,
  selected,
  onSelect,
  onHover,
  onLeave,
}: {
  deckId: string;
  rows: string[];
  buckets: TrendBucket[];
  series: Map<string, Map<string, TrendCell>>;
  overall: Map<string, DeckMatchup>;
  names: Map<string, string>;
  grain: Grain;
  mode: Mode;
  selected: string | null;
  onSelect: (opponent: string) => void;
  onHover: (r: number, c: number, anchor: Anchor) => void;
  onLeave: () => void;
}) {
  const { t, percent, percentSign, bucketLabel, dateRange } = useI18n();
  const name = (id: string) => names.get(id) ?? id;
  const deck = name(deckId);

  // One delegated listener for the whole grid rather than four per cell.
  const locate = (target: EventTarget) => {
    const td = (target as HTMLElement).closest<HTMLElement>("td.cell");
    if (!td) return null;
    return { r: Number(td.dataset.r), c: Number(td.dataset.c), td };
  };

  const onPointerMove = (event: PointerEvent<HTMLTableElement>) => {
    const hit = locate(event.target);
    if (hit) onHover(hit.r, hit.c, { x: event.clientX, y: event.clientY });
    else onLeave();
  };

  const onFocus = (event: FocusEvent<HTMLTableElement>) => {
    const hit = locate(event.target);
    if (!hit) return;
    const box = hit.td.getBoundingClientRect();
    onHover(hit.r, hit.c, { x: box.right, y: box.bottom });
  };

  // No year: the filter row states the window, and a heading's tooltip its dates.
  const label = (bucket: TrendBucket) => bucketLabel(bucket.bucket_start, grain);

  const paint = (rate: Rate, wave: CSSProperties, description: string, row: string) => {
    if (rate.score_rate == null) return null;
    const inconclusive = spansEven(rate.score_low, rate.score_high);
    const { background, ink, edge } = scoreColor(rate.score_rate, mode, inconclusive);
    return (
      <button
        type="button"
        className={inconclusive ? "muted" : undefined}
        style={{ ...wave, backgroundColor: background, color: ink, "--edge": edge } as CSSProperties}
        aria-label={t("cellDescription", {
          row: deck,
          column: description,
          rate: percentSign(rate.score_rate),
          matches: t("matchCount", { count: rate.matches }),
          uncertainty: inconclusive ? t("uncertainty") : "",
        })}
        onClick={() => onSelect(row)}
      >
        {percent(rate.score_rate)}
      </button>
    );
  };

  return (
    <table
      className={`trend-grid trend-${grain}`}
      onPointerMove={onPointerMove}
      onPointerLeave={onLeave}
      onFocus={onFocus}
      onBlur={onLeave}
    >
      <caption className="sr-only">{t("trendCaption", { name: deck })}</caption>
      <thead>
        <tr>
          <th className="corner" scope="col">
            <span>{t("opponent")}</span>
          </th>
          {buckets.map((bucket) => (
            <th
              key={bucket.bucket_start}
              scope="col"
              className={`bucket-head${bucket.complete ? "" : " partial"}`}
              title={bucket.complete ? dateRange(bucket.date_from, bucket.date_to) : t("partialBucket", {
                range: dateRange(bucket.date_from, bucket.date_to),
              })}
            >
              {label(bucket)}
            </th>
          ))}
          <th scope="col" className="bucket-head whole">
            {t("wholeWindowShort")}
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, r) => {
          const cells = series.get(row);
          const total = overall.get(row);
          return (
            <tr key={row} className={row === selected ? "is-selected" : undefined}>
              <th scope="row" className="row-head" style={{ "--d": r } as CSSProperties}>
                <button
                  type="button"
                  aria-pressed={row === selected}
                  title={t("chartThis", { name: name(row) })}
                  onClick={() => onSelect(row)}
                >
                  <DeckIcon deckId={row} />
                  <span className="name">{name(row)}</span>
                </button>
              </th>

              {buckets.map((bucket, c) => {
                const wave = { "--d": r + c } as CSSProperties;
                const cell = cells?.get(bucket.bucket_start);
                const when = label(bucket);
                if (!cell || cell.score_rate == null) {
                  return (
                    <td key={bucket.bucket_start} className="cell empty" data-r={r} data-c={c}>
                      <div style={wave} title={t("noTrendCell", { row: deck, column: name(row), bucket: when })} />
                    </td>
                  );
                }
                return (
                  <td key={bucket.bucket_start} className="cell" data-r={r} data-c={c}>
                    {paint(cell, wave, `${name(row)}, ${when}`, row)}
                  </td>
                );
              })}

              <td
                className={`cell whole${total?.score_rate != null ? "" : " empty"}`}
                data-r={r}
                data-c={buckets.length}
              >
                {total && total.score_rate != null ? (
                  paint(total, { "--d": r + buckets.length } as CSSProperties, `${name(row)}, ${t("wholeWindow")}`, row)
                ) : (
                  <div style={{ "--d": r + buckets.length } as CSSProperties} />
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
});
