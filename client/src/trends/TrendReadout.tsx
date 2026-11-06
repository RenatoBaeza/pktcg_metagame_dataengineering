import type { CSSProperties } from "react";
import type { Grain, TrendBucket } from "../api";
import { DeckIcon } from "../components/DeckIcon";
import { useI18n } from "../i18n";
import type { Mode } from "../scale";
import { scoreColor } from "../scale";

type Rated = {
  matches: number;
  wins: number;
  losses: number;
  ties: number;
  win_rate: number | null;
  score_rate: number | null;
};

/**
 * One matchup in one bucket, for the grid's and the chart's tooltips. Value
 * leads, labels follow, as in the matrix readout - plus the dates, since a
 * column heading names only where the bucket starts. A null bucket is the
 * whole window. A null cell is a bucket where the two did not meet.
 */
export function TrendReadout({
  deckId,
  opponentId,
  deckName,
  opponentName,
  bucket,
  grain,
  cell,
  mode,
}: {
  deckId: string;
  opponentId: string;
  deckName: string;
  opponentName: string;
  bucket: TrendBucket | null;
  grain: Grain;
  cell: Rated | null | undefined;
  mode: Mode;
}) {
  const { t, count, percentSign, record, dateRange } = useI18n();
  const when = bucket
    ? t(grain === "week" ? "weekOf" : "monthOf", { range: dateRange(bucket.date_from, bucket.date_to) })
    : t("wholeWindow");

  return (
    <>
      <div className="tip-matchup">
        <DeckIcon deckId={deckId} />
        <span className="tip-vs">vs</span>
        <DeckIcon deckId={opponentId} />
      </div>
      {cell && cell.score_rate != null ? (
        <div
          className="headline"
          style={{ "--c": scoreColor(cell.score_rate, mode).background } as CSSProperties}
        >
          {percentSign(cell.score_rate)}
        </div>
      ) : (
        <div className="tip-none">{t("didNotMeet")}</div>
      )}
      <div className="who">
        {deckName} <span className="muted">vs</span> {opponentName}
      </div>
      <div className="tip-when">
        {when}
        {bucket && !bucket.complete && <span className="muted"> · {t("partial")}</span>}
      </div>
      {cell && cell.score_rate != null && (
        <dl>
          <dt>{t("recordTitle")}</dt>
          <dd>{record(cell.wins, cell.losses, cell.ties)}</dd>
          <dt>{t("matchesTitle")}</dt>
          <dd>{count(cell.matches)}</dd>
          <dt>{t("excludingTies")}</dt>
          <dd>{percentSign(cell.win_rate)}</dd>
        </dl>
      )}
    </>
  );
}
