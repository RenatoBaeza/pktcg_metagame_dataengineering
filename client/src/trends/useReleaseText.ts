import { useI18n } from "../i18n";
import type { Release } from "./releases";

/** A release's short label ("Pitch Black", "G rotation") and its full sentence, for a title. */
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
