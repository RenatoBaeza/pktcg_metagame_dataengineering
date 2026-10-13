import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { fetchPeriods } from "../api";
import type { Period } from "../api";
import { PRESETS } from "../filters";
import { useI18n } from "../i18n";
import { Segmented } from "./Segmented";

/**
 * The filter row, above everything it scopes. The period is the only choice a
 * reader makes, so it is the only control. It is presets only: each is a
 * period the server computed ahead of time, and nobody fights a calendar grid
 * for "last 30 days" anyway.
 *
 * The bar sticks to the top of the viewport, so the filters stay in reach
 * while reading the deck table far below them. It picks up a shadow once it
 * is actually stuck, which a sentinel element just above it reports.
 */
export function FilterBar({
  period,
  onChange,
  busy,
}: {
  period: Period;
  onChange: (period: Period) => void;
  busy: boolean;
}) {
  const { t, dateRange } = useI18n();
  const periods = useQuery({ queryKey: ["periods"], queryFn: fetchPeriods });
  const info = periods.data?.find((row) => row.period === period);
  const [sentinel, stuck] = useStuck();

  return (
    <>
      <div ref={sentinel} className="sticky-sentinel" aria-hidden="true" />
      <div className={`filters${stuck ? " is-stuck" : ""}${busy ? " is-busy" : ""}`}>
        <div className="field field-period">
          <span className="label" id="range-label">
            {t("dateRange")}
          </span>
          <Segmented
            labelledBy="range-label"
            value={period}
            onChange={onChange}
            options={(Object.keys(PRESETS) as Period[]).map((preset) => ({
              value: preset,
              label: t(PRESETS[preset].label),
            }))}
          />
        </div>

        <div className="field field-range">
          <span className="label">{t("selectedDates")}</span>
          <span className="range-value" aria-live="polite">
            {info?.date_from && info.date_to ? (
              dateRange(info.date_from, info.date_to)
            ) : (
              "—"
            )}
          </span>
        </div>

        <span className="busy-bar" aria-hidden="true" />
      </div>
    </>
  );
}

/**
 * Whether a sticky bar is actually stuck, reported by a sentinel element
 * placed just above it. The bar picks up its lifted shadow from this.
 */
export function useStuck() {
  const sentinel = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);

  useEffect(() => {
    const element = sentinel.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting), {
      rootMargin: "-1px 0px 0px 0px",
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [sentinel, stuck] as const;
}
