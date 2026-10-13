import { useCallback, useEffect, useState } from "react";

/**
 * Which of the two views is showing, kept in the URL hash so each has a link
 * of its own and the back button moves between them. A hash rather than a
 * path because the client is a static build with no server-side routing.
 */
export type View = "metagame" | "trends";

const HASH: Record<View, string> = { metagame: "#/", trends: "#/trends" };

const read = (): View => (window.location.hash.startsWith("#/trends") ? "trends" : "metagame");

export const viewHref = (view: View) => HASH[view];

export function useView(): [View, (view: View) => void] {
  const [view, setView] = useState<View>(read);

  useEffect(() => {
    const sync = () => setView(read());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const go = useCallback((next: View) => {
    if (read() !== next) window.location.hash = HASH[next];
    window.scrollTo({ top: 0 });
  }, []);

  return [view, go];
}
