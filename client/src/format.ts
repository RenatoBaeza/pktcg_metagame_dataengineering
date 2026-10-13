/** Locale-bound formatters shared by every table, chart and readout. */
export function createFormat(locale: string) {
  const numbers = new Intl.NumberFormat(locale);
  const dates = new Intl.DateTimeFormat(locale, { timeZone: "UTC" });
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "always", style: "short" });
  const rates = new Map<string, Intl.NumberFormat>();
  const rate = (value: number | null | undefined, digits: number, sign: boolean) => {
    if (value == null) return "—";
    const key = `${digits}-${sign}`;
    let formatter = rates.get(key);
    if (!formatter) {
      formatter = new Intl.NumberFormat(locale, {
        style: sign ? "percent" : "decimal",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
        useGrouping: false,
      });
      rates.set(key, formatter);
    }
    return formatter.format(sign ? value : value * 100);
  };
  const count = (value: number) => numbers.format(value);
  const days = new Map<string, Intl.DateTimeFormat>();
  // A calendar day ("2026-10-04"), formatted on that day whatever the zone.
  const day = (iso: string, options: Intl.DateTimeFormatOptions) => {
    const key = JSON.stringify(options);
    let formatter = days.get(key);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" });
      days.set(key, formatter);
    }
    return formatter.format(new Date(`${iso}T00:00:00Z`));
  };
  return {
    count,
    percent: (value: number | null | undefined, digits = 0) => rate(value, digits, false),
    percentSign: (value: number | null | undefined, digits = 1) => rate(value, digits, true),
    record: (wins: number, losses: number, ties: number) => `${count(wins)}-${count(losses)}-${count(ties)}`,
    date: (iso: string | null) => {
      if (!iso) return "—";
      const value = new Date(iso);
      return Number.isNaN(value.getTime()) ? "—" : dates.format(value);
    },
    /** "Sep 5 – Oct 4, 2026": the year is stated once, and only when it differs. */
    dateRange: (from: string, to: string) => {
      const sameYear = from.slice(0, 4) === to.slice(0, 4);
      const start = day(from, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
      return `${start} – ${day(to, { day: "numeric", month: "short", year: "numeric" })}`;
    },
    /** A trend column's heading: "Sep 8" for a week, "Sep" for a month. */
    bucketLabel: (iso: string, grain: "week" | "month") =>
      day(iso, grain === "week" ? { day: "numeric", month: "short" } : { month: "short" }),
    relativeTime: (iso: string | null) => {
      if (!iso) return null;
      const timestamp = new Date(iso).getTime();
      if (!Number.isFinite(timestamp)) return null;
      const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
      if (minutes < 60) return relative.format(-minutes, "minute");
      if (minutes < 1440) return relative.format(-Math.round(minutes / 60), "hour");
      return relative.format(-Math.round(minutes / 1440), "day");
    },
  };
}

/**
 * A rate is only news if its interval clears even. Anything spanning 50% is a
 * matchup we cannot call yet, however extreme the point estimate looks.
 */
export const spansEven = (low: number | null, high: number | null) =>
  low == null || high == null || (low <= 0.5 && high >= 0.5);
