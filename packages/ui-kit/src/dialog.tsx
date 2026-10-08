import { useEffect, useId, useRef, type ReactNode } from "react";
import { cx } from "./cx";

interface DialogProps {
  /** Accessible dialog name; rendered visually only when `showTitle` is set. */
  title: string;
  showTitle?: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}

/**
 * Modal sheet over a blurred page (create-vacancy pattern from the design).
 * Escape and backdrop click close it; focus moves inside on open and returns to the opener on close.
 */
export function Dialog({ title, showTitle = false, onClose, children, className }: DialogProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  // Latest onClose without re-running the mount effect (which would steal focus) on every parent render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = panel.current?.querySelector<HTMLElement>("input, select, textarea, button, [href]");
    (first ?? panel.current)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      opener?.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 px-4 py-6 backdrop-blur-md sm:items-center sm:py-8">
      <button type="button" aria-label="Закрыть" tabIndex={-1} className="fixed inset-0 cursor-default" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cx("relative w-full max-w-[30.25rem] rounded-[2rem] bg-surface-dialog p-5 shadow-2xl outline-none lg:px-[1.6875rem] lg:pt-[1.6875rem] lg:pb-[1.25rem]", className)}
      >
        <h2 id={titleId} className={showTitle ? "mb-4 text-2xl font-semibold" : "sr-only"}>
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
}
