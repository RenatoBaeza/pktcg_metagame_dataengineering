import assert from "node:assert/strict";
import { test } from "node:test";
import { placeReleases, RELEASES, type Release } from "../src/trends/releases.ts";

const week = (start: string, from = start, to?: string) => {
  const end = new Date(`${start}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + 6);
  return { bucket_start: start, date_from: from, date_to: to ?? end.toISOString().slice(0, 10) };
};

const launch = (date: string, name = date): Release => ({ date, kind: "launch", name });

test("a release lands in its bucket, at its day within the week", () => {
  const buckets = [week("2026-05-04", "2026-05-06"), week("2026-05-11"), week("2026-05-18")];
  const [placed] = placeReleases(buckets, "week", [launch("2026-05-22")]);
  assert.equal(placed.index, 2);
  assert.equal(placed.at, 4 / 7); // Friday
});

test("a month's position uses the whole calendar month", () => {
  const buckets = [
    { bucket_start: "2026-05-01", date_from: "2026-05-06", date_to: "2026-05-31" },
    { bucket_start: "2026-07-01", date_from: "2026-07-01", date_to: "2026-07-31" },
  ];
  const placed = placeReleases(buckets, "month", [launch("2026-07-17"), launch("2026-05-22")]);
  assert.deepEqual(placed.map((release) => [release.index, release.at]), [[0, 21 / 31], [1, 16 / 31]]);
});

test("a release outside the data is not drawn, even inside a clipped bucket", () => {
  const buckets = [week("2026-05-04", "2026-05-06")];
  assert.deepEqual(placeReleases(buckets, "week", [launch("2026-05-05"), launch("2026-04-10")]), []);
});

test("the last day of the window still places, inside its column", () => {
  const buckets = [week("2026-11-02", "2026-11-02", "2026-11-06")];
  const [placed] = placeReleases(buckets, "week", [launch("2026-11-06")]);
  assert.equal(placed.index, 0);
  assert.ok(placed.at > 0.5 && placed.at < 1);
});

test("the release list is ISO-dated and in order", () => {
  for (const release of RELEASES) assert.match(release.date, /^\d{4}-\d{2}-\d{2}$/);
  const dates = RELEASES.map((release) => release.date);
  assert.deepEqual([...dates].sort(), dates);
});
