import type { MouseEvent } from "react";
import { useI18n } from "../i18n";
import { viewHref, type View } from "../useView";

/**
 * The two views, as links rather than buttons: each has its own URL, so it
 * can be bookmarked, shared or opened in a new tab. Drawn as the menu tabs of
 * a game screen - the current one is the yellow card standing forward.
 */
export function ViewTabs({ view, onView }: { view: View; onView: (view: View) => void }) {
  const { t } = useI18n();
  const tabs: Array<{ value: View; label: string }> = [
    { value: "metagame", label: t("viewMetagame") },
    { value: "trends", label: t("viewTrends") },
  ];

  const go = (event: MouseEvent<HTMLAnchorElement>, value: View) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    onView(value);
  };

  return (
    <nav className="view-tabs" aria-label={t("views")}>
      {tabs.map((tab) => (
        <a
          key={tab.value}
          href={viewHref(tab.value)}
          aria-current={view === tab.value ? "page" : undefined}
          onClick={(event) => go(event, tab.value)}
        >
          {tab.label}
        </a>
      ))}
    </nav>
  );
}
