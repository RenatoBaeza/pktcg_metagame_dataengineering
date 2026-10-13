import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Grain, TrendBucket, TrendCell } from "../api";
import { Tooltip, type Anchor } from "../components/Tooltip";
import { spansEven } from "../format";
import { useI18n } from "../i18n";
import type { Mode } from "../scale";
import { scoreColor } from "../scale";
import { TrendReadout } from "./TrendReadout";

const PAD = { top: 12, right: 52, left: 44 };
const PLOT_HEIGHT = 220;
const STRIP_GAP = 16;
const STRIP_HEIGHT = 40;
const AXIS_HEIGHT = 24;
const HEIGHT = PAD.top + PLOT_HEIGHT + STRIP_GAP + STRIP_HEIGHT + AXIS_HEIGHT;
const LABEL_WIDTH = 58;

/**
 * One matchup's score rate, bucket by bucket - a single series, so no legend:
 * the panel heading names the two decks.
 *
 * The line is ink rather than a data hue. Its position against the 50% rule
 * is the polarity, and the two faint washes either side of that rule are the
 * matrix's blue and red, so "above the line" and "a blue cell" mean the same
 * thing. A point is filled when its interval clears 50% and hollow when it
 * does not - the chart's version of the matrix's muted cell; the interval
 * itself is not drawn, as it is nowhere on the site. The line breaks across a
 * bucket where the two did not meet rather than inventing a value for it.
 *
 * Under the plot, a strip of columns gives each point's match count on its
 * own scale. A second y-axis on the plot would invent a relation between the
 * two; a separate strip just says how much each point stands on.
 *
 * A crosshair snaps to the nearest bucket and reads it out. The arrow keys
 * move it when the chart has focus, and the grid below holds every value as
 * a number, so nothing is gated behind hovering.
 */
export function TrendChart({
  deckId,
  opponentId,
  buckets,
  series,
  grain,
  mode,
  deckName,
  opponentName,
}: {
  deckId: string;
  opponentId: string;
  buckets: TrendBucket[];
  series: Map<string, TrendCell>;
  grain: Grain;
  mode: Mode;
  deckName: string;
  opponentName: string;
}) {
  const { t, percentSign, count, bucketLabel } = useI18n();
  const wrap = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<{ index: number; anchor: Anchor } | null>(null);

  useLayoutEffect(() => {
    const element = wrap.current;
    if (!element) return;
    const measure = () => setWidth(element.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const n = buckets.length;
  const plotWidth = Math.max(0, width - PAD.left - PAD.right);
  const band = n ? plotWidth / n : 0;
  const x = (i: number) => PAD.left + band * (i + 0.5);

  const points = buckets.map((bucket) => series.get(bucket.bucket_start) ?? null);
  const rates = points.flatMap((cell) => (cell?.score_rate != null ? [cell.score_rate] : []));

  // At least 30-70%, widened to whole tenths around the data. Past a 60-point
  // spread the clean 0-100% scale reads better than odd tenths.
  let lo = Math.min(3, Math.floor(Math.min(...rates, 0.5) * 10)) / 10;
  let hi = Math.max(7, Math.ceil(Math.max(...rates, 0.5) * 10)) / 10;
  const wide = hi - lo > 0.6;
  if (wide) [lo, hi] = [0, 1];
  const ticks = wide
    ? [0, 0.25, 0.5, 0.75, 1]
    : Array.from({ length: Math.round((hi - lo) * 10) + 1 }, (_, k) => Math.round(lo * 10 + k) / 10);

  const plotTop = PAD.top;
  const plotBottom = PAD.top + PLOT_HEIGHT;
  const y = (rate: number) => plotTop + ((hi - rate) / (hi - lo)) * PLOT_HEIGHT;
  const even = y(0.5);

  const stripBase = plotBottom + STRIP_GAP + STRIP_HEIGHT;
  const most = Math.max(1, ...points.map((cell) => cell?.matches ?? 0));
  const barWidth = Math.max(2, Math.min(24, band * 0.6));
  const radius = band < 14 ? 3 : 4.5;

  // Consecutive buckets with a value are joined; a gap breaks the line.
  let path = "";
  points.forEach((cell, i) => {
    if (cell?.score_rate == null) return;
    const joined = i > 0 && points[i - 1]?.score_rate != null;
    path += `${joined ? "L" : "M"}${x(i).toFixed(1)},${y(cell.score_rate).toFixed(1)}`;
  });

  // Every heading the band has room for, counted back from the newest so the
  // latest bucket is always labelled.
  const every = Math.max(1, Math.ceil(LABEL_WIDTH / Math.max(band, 1)));
  const lastIndex = points.reduce((last, cell, i) => (cell?.score_rate != null ? i : last), -1);

  const favoured = scoreColor(0.75, mode).edge;
  const unfavoured = scoreColor(0.25, mode).edge;

  const indexAt = (clientX: number) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || !band) return null;
    const i = Math.floor((clientX - box.left - PAD.left) / band);
    return i < 0 || i >= n ? null : i;
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const index = indexAt(event.clientX);
    setActive(index == null ? null : { index, anchor: { x: event.clientX, y: event.clientY } });
  };

  const anchorFor = (index: number): Anchor => {
    const box = svg.current?.getBoundingClientRect();
    return { x: (box?.left ?? 0) + x(index), y: (box?.top ?? 0) + plotTop + PLOT_HEIGHT / 3 };
  };

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) {
      if (event.key === "Escape") setActive(null);
      return;
    }
    event.preventDefault();
    const from = active?.index ?? (step > 0 ? -1 : n);
    const index = Math.min(n - 1, Math.max(0, from + step));
    setActive({ index, anchor: anchorFor(index) });
  };

  const bucket = active ? buckets[active.index] : null;

  return (
    <div ref={wrap} className="trend-chart">
      {width > 0 && n > 0 && (
        <svg
          ref={svg}
          width={width}
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          role="img"
          tabIndex={0}
          aria-label={t("trendChartLabel", { deck: deckName, opponent: opponentName })}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setActive(null)}
          onKeyDown={onKeyDown}
          onFocus={() => lastIndex >= 0 && setActive({ index: lastIndex, anchor: anchorFor(lastIndex) })}
          onBlur={() => setActive(null)}
        >
          <rect
            x={PAD.left}
            y={plotTop}
            width={plotWidth}
            height={even - plotTop}
            fill={favoured}
            className="zone"
          />
          <rect
            x={PAD.left}
            y={even}
            width={plotWidth}
            height={plotBottom - even}
            fill={unfavoured}
            className="zone"
          />
          <text x={PAD.left + 8} y={plotTop + 15} className="zone-label">
            {t("favoured")}
          </text>
          <text x={PAD.left + 8} y={plotBottom - 8} className="zone-label">
            {t("unfavoured")}
          </text>

          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={PAD.left}
                x2={PAD.left + plotWidth}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0.5 ? "even-rule" : "grid-rule"}
              />
              <text x={PAD.left - 8} y={y(tick) + 4} className="tick" textAnchor="end">
                {percentSign(tick, 0)}
              </text>
            </g>
          ))}
          {!ticks.includes(0.5) && (
            <line x1={PAD.left} x2={PAD.left + plotWidth} y1={even} y2={even} className="even-rule" />
          )}

          {active && (
            <line
              x1={x(active.index)}
              x2={x(active.index)}
              y1={plotTop}
              y2={stripBase}
              className="crosshair"
            />
          )}

          <path d={path} className="trend-line" />

          {points.map((cell, i) => {
            if (cell?.score_rate == null) return null;
            const inconclusive = spansEven(cell.score_low, cell.score_high);
            return (
              <circle
                key={buckets[i].bucket_start}
                cx={x(i)}
                cy={y(cell.score_rate)}
                r={radius}
                className={`trend-point${inconclusive ? " hollow" : ""}${active?.index === i ? " is-active" : ""}`}
              />
            );
          })}

          {lastIndex >= 0 && points[lastIndex] && (
            <text x={x(lastIndex) + radius + 6} y={y(points[lastIndex]!.score_rate!) + 4} className="end-label">
              {percentSign(points[lastIndex]!.score_rate, 0)}
            </text>
          )}

          <text x={PAD.left - 8} y={stripBase - STRIP_HEIGHT / 2 + 4} className="tick" textAnchor="end">
            {count(most)}
          </text>
          <line x1={PAD.left} x2={PAD.left + plotWidth} y1={stripBase} y2={stripBase} className="grid-rule" />
          {points.map((cell, i) => {
            if (!cell?.matches) return null;
            const height = Math.max(1, (cell.matches / most) * STRIP_HEIGHT);
            return (
              <path
                key={buckets[i].bucket_start}
                d={column(x(i) - barWidth / 2, stripBase, barWidth, height)}
                className={`strip-bar${active?.index === i ? " is-active" : ""}`}
              />
            );
          })}
          <text x={PAD.left + plotWidth + 8} y={stripBase - 2} className="tick">
            {t("matchesTitle")}
          </text>

          {buckets.map((row, i) =>
            (n - 1 - i) % every === 0 ? (
              <text
                key={row.bucket_start}
                x={x(i)}
                y={stripBase + 17}
                textAnchor="middle"
                className={`tick${row.complete ? "" : " partial"}`}
              >
                {bucketLabel(row.bucket_start, grain)}
              </text>
            ) : null,
          )}
        </svg>
      )}

      {active && bucket && (
        <Tooltip anchor={active.anchor}>
          <TrendReadout
            deckId={deckId}
            opponentId={opponentId}
            deckName={deckName}
            opponentName={opponentName}
            bucket={bucket}
            grain={grain}
            cell={points[active.index]}
            mode={mode}
          />
        </Tooltip>
      )}
    </div>
  );
}

/** A column rising from the baseline, square at the base and rounded at the cap. */
function column(left: number, base: number, width: number, height: number) {
  const r = Math.min(4, width / 2, height);
  const top = base - height;
  const right = left + width;
  return `M${left},${base}V${top + r}Q${left},${top} ${left + r},${top}H${right - r}Q${right},${top} ${right},${top + r}V${base}Z`;
}
