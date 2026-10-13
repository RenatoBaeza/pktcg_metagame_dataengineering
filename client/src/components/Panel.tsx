import { useI18n } from "../i18n";
import type { ReactNode } from "react";
import { useReveal } from "../fx/motion";
import { InfoTip } from "./InfoTip";

/**
 * A framed card with a heading, drawn like a game menu window. Rises into
 * place the first time it scrolls into view, and otherwise holds still.
 * `info` is the panel's explanation, kept behind an info button beside the
 * heading rather than printed under it.
 */
export function Panel({
  title,
  info,
  icon,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  info?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const { t } = useI18n();
  const ref = useReveal<HTMLElement>();

  return (
    <section ref={ref} className={`panel reveal${className ? ` ${className}` : ""}`}>
      <div className="panel-head">
        <div className="panel-title">
          {icon && (
            <span className="panel-icon" aria-hidden="true">
              {icon}
            </span>
          )}
          <div className="panel-heading">
            <h2>{title}</h2>
            {info && <InfoTip label={t("aboutPanel")}>{info}</InfoTip>}
          </div>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Placeholder blocks that shimmer while the first result is on its way. */
export function Skeleton({ rows = 6, height = 36 }: { rows?: number; height?: number }) {
  const { t } = useI18n();
  return (
    <div className="skeleton-stack" aria-busy="true" aria-label={t("loading")}>
      {Array.from({ length: rows }, (_, i) => (
        <span
          key={i}
          className="skeleton"
          style={{ height, width: `${100 - ((i * 13) % 30)}%`, animationDelay: `${i * 80}ms` }}
        />
      ))}
    </div>
  );
}

export const GridIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18">
    {[0, 1, 2].flatMap((r) =>
      [0, 1, 2].map((c) => (
        <rect
          key={`${r}${c}`}
          x={2 + c * 5.6}
          y={2 + r * 5.6}
          width="4.4"
          height="4.4"
          rx="1.2"
          fill="currentColor"
          opacity={0.35 + ((r + c) % 3) * 0.3}
        />
      )),
    )}
  </svg>
);

export const StackIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="4" y="2.5" width="12" height="15" rx="2" opacity="0.45" transform="rotate(-8 10 10)" />
    <rect x="4" y="2.5" width="12" height="15" rx="2" />
    <path d="M7.5 7h5M7.5 10h5M7.5 13h3" strokeLinecap="round" />
  </svg>
);
