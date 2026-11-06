/**
 * The calendar events that move a metagame: a set launching (new cards enter
 * Standard) and a rotation (old ones leave it). The trend views draw each as a
 * vertical rule, so a matchup turning the week a set landed reads as what it
 * probably is.
 *
 * Hand-maintained, since nothing in the Limitless data says when a set came
 * out. Dates are the English release (the day new cards become legal in
 * Play! Pokémon and on Limitless), and for a rotation the day it took effect
 * at in-person events. Add a row when a set is announced; an entry outside
 * the retention window is simply not drawn, so old ones never need removing.
 */
export type ReleaseKind = "launch" | "rotation";

export type Release = {
  /** The calendar day, "2026-05-22". */
  date: string;
  kind: ReleaseKind;
  /** The set's English name for a launch; the rotated regulation mark for a rotation. */
  name: string;
};

export const RELEASES: readonly Release[] = [
  { date: "2026-01-30", kind: "launch", name: "Ascended Heroes" },
  { date: "2026-03-27", kind: "launch", name: "Perfect Order" },
  // G and every earlier regulation mark left Standard.
  { date: "2026-04-10", kind: "rotation", name: "G" },
  { date: "2026-05-22", kind: "launch", name: "Chaos Rising" },
  { date: "2026-07-17", kind: "launch", name: "Pitch Black" },
  { date: "2026-09-16", kind: "launch", name: "30th Celebration" },
  { date: "2026-11-06", kind: "launch", name: "Delta Reign" },
];

export type PlacedRelease = Release & {
  /** The bucket (column) the event falls in. */
  index: number;
  /** How far into that bucket, 0 at its first day and approaching 1 at its last. */
  at: number;
};

type Bucket = { bucket_start: string; date_from: string; date_to: string };

const DAY = 86_400_000;
const dayNumber = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / DAY;

/** The day after a bucket's nominal span: a week is Monday to Sunday, a month the calendar month. */
function bucketEnd(start: string, grain: "week" | "month") {
  if (grain === "week") return dayNumber(start) + 7;
  const [year, month] = start.split("-").map(Number);
  return Date.UTC(year, month, 1) / DAY;
}

/**
 * Each release that falls inside the data, with the bucket it lands in and
 * where within it. Placement uses the bucket's whole week or month, not the
 * part the window clips it to, so a rule sits on the same day of a column
 * whichever bucket it is - but an event on a clipped-off day is not drawn,
 * since there is no data either side of it to compare.
 */
export function placeReleases(
  buckets: readonly Bucket[],
  grain: "week" | "month",
  releases: readonly Release[] = RELEASES,
): PlacedRelease[] {
  const placed: PlacedRelease[] = [];
  for (const release of releases) {
    const index = buckets.findIndex((b) => b.date_from <= release.date && release.date <= b.date_to);
    if (index < 0) continue;
    const start = dayNumber(buckets[index].bucket_start);
    const at = (dayNumber(release.date) - start) / (bucketEnd(buckets[index].bucket_start, grain) - start);
    placed.push({ ...release, index, at: Math.min(Math.max(at, 0), 0.999) });
  }
  return placed.sort((a, b) => a.index + a.at - (b.index + b.at));
}
