import { useI18n } from "../i18n";
import type { PlacedRelease, Release } from "./releases";

/** The words for a release: a short label for beside its rule, and a full sentence. */
export function useReleaseText() {
  const { t, calendarDay } = useI18n();
  return {
    label: (release: Release) =>
      release.kind === "launch" ? release.name : t("rotationShort", { mark: release.name }),
    describe: (release: Release) =>
      release.kind === "launch"
        ? t("releaseLaunch", { name: release.name, date: calendarDay(release.date) })
        : t("releaseRotation", { mark: release.name, date: calendarDay(release.date) }),
  };
}

/** The releases a bucket holds, one per line, for a tooltip. */
export function ReleaseList({ releases }: { releases: PlacedRelease[] }) {
  const { describe } = useReleaseText();
  if (!releases.length) return null;
  return (
    <ul className="tip-releases">
      {releases.map((release) => (
        <li key={release.date + release.name}>
          <span className={`release-swatch ${release.kind}`} aria-hidden="true" />
          {describe(release)}
        </li>
      ))}
    </ul>
  );
}

/**
 * The grid's key: a column is too narrow to name the release drawn through
 * it, so the names sit above the grid, each beside its own rule style.
 */
export function ReleaseKey({ releases }: { releases: PlacedRelease[] }) {
  const { t } = useI18n();
  const { describe } = useReleaseText();
  if (!releases.length) return null;
  return (
    <ul className="release-key" aria-label={t("releasesKey")}>
      {releases.map((release) => (
        <li key={release.date + release.name}>
          <span className={`release-swatch ${release.kind}`} aria-hidden="true" />
          {describe(release)}
        </li>
      ))}
    </ul>
  );
}
