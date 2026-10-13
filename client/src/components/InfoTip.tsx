import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const GAP = 8;
const MARGIN = 8;

/**
 * An "i" button whose explanation opens in a dialog-box tooltip: on hover for
 * a mouse, and pinned open by a click or tap until the next click, Escape, a
 * click elsewhere, or a scroll.
 *
 * Portalled to <body> for the same reason as `Tooltip`: the panel it sits in
 * carries a transform mid-reveal, which would trap anything position: fixed.
 * It opens below the button, or above it when there is no room, and is clamped
 * to the viewport horizontally.
 */
export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [place, setPlace] = useState<{ x: number; y: number } | null>(null);
  const open = hovered || pinned;

  useLayoutEffect(() => {
    if (!open || !button.current || !tip.current) {
      setPlace(null);
      return;
    }
    const at = button.current.getBoundingClientRect();
    const { width, height } = tip.current.getBoundingClientRect();
    const below = at.bottom + GAP;
    const y = below + height > window.innerHeight - MARGIN && at.top - GAP - height > MARGIN ? at.top - GAP - height : below;
    const x = Math.min(Math.max(MARGIN, at.left + at.width / 2 - width / 2), window.innerWidth - width - MARGIN);
    setPlace({ x, y });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = () => {
      setPinned(false);
      setHovered(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    const onPointer = (event: PointerEvent) => {
      if (!button.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("scroll", close, { capture: true, passive: true });
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("scroll", close, { capture: true });
      window.removeEventListener("resize", close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        className="info-button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => setPinned((value) => !value)}
        onPointerEnter={(event) => event.pointerType === "mouse" && setHovered(true)}
        onPointerLeave={(event) => event.pointerType === "mouse" && setHovered(false)}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="8" cy="4.25" r="1.25" fill="currentColor" />
          <path d="M8 7.25v5.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
      {open &&
        createPortal(
          <div
            ref={tip}
            id={id}
            role="tooltip"
            className="tooltip info-tip"
            style={
              place
                ? { transform: `translate3d(${place.x}px, ${place.y}px, 0)` }
                : { visibility: "hidden" }
            }
          >
            <div className="tooltip-inner">{children}</div>
          </div>,
          document.body,
        )}
    </>
  );
}
