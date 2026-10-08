/// <reference path="./assets.d.ts" />
import type { HTMLAttributes, ReactNode } from "react";
import authLinesA from "./assets/backgrounds/auth-lines-a.png";
import authLinesB from "./assets/backgrounds/auth-lines-b.png";
import linesA from "./assets/backgrounds/lines-a.png";
import linesB from "./assets/backgrounds/lines-b.png";
import logoMark from "./assets/brand/logo-mark.svg";
import { Spinner } from "./controls";
import { cx } from "./cx";

/** Frosted panel: `glass` for floating blocks, `card` for list tiles, `solid` for dense content. */
export function Panel({
  tone = "glass",
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { tone?: "glass" | "card" | "solid" | "light" }) {
  return (
    <div
      {...rest}
      className={cx(
        "rounded-card",
        tone === "glass" && "glass border border-glass-border",
        tone === "card" && "border border-glass-border bg-glass-card backdrop-blur-[100px]",
        tone === "solid" && "bg-surface",
        tone === "light" && "bg-light text-on-light",
        className,
      )}
    />
  );
}

export type ChipTone = "light" | "info" | "glass" | "dark" | "success" | "danger" | "muted";

const CHIP_TONE: Record<ChipTone, string> = {
  light: "bg-light-strong text-on-chip",
  info: "bg-chip-info text-on-chip",
  glass: "glass text-text",
  dark: "bg-dark-button text-white",
  success: "bg-success-soft text-success-text",
  danger: "bg-danger-soft text-white",
  muted: "bg-black/5 text-faint",
};

/** Tag/stack chip; `dot` prepends a status point as in the pipeline stepper. */
export function Chip({
  tone = "light",
  dot = false,
  className,
  children,
}: {
  tone?: ChipTone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-2 rounded-chip px-3.5 py-1.5 text-sm font-medium whitespace-nowrap sm:text-base",
        CHIP_TONE[tone],
        className,
      )}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** Colored status point used in vacancy state badges. */
export function StatusDot({ tone }: { tone: "success" | "warning" | "danger" | "muted" }) {
  return (
    <span
      aria-hidden
      className={cx(
        "inline-block size-3.5 shrink-0 rounded-full",
        tone === "success" && "bg-success",
        tone === "warning" && "bg-warning",
        tone === "danger" && "bg-danger",
        tone === "muted" && "bg-faint",
      )}
    />
  );
}

/** Determinate bar; `value` is clamped to 0..1. */
export function ProgressBar({ value, label, className }: { value: number; label?: string; className?: string }) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
      className={cx("h-3 w-full overflow-hidden rounded-pill bg-white/10", className)}
    >
      <div className="h-full rounded-pill bg-success transition-[width] duration-300" style={{ width: `${clamped * 100}%` }} />
    </div>
  );
}

/** Green match-percentage tab pinned to the top-left of candidate rows and cards. */
export function MatchBadge({ percent, className }: { percent: number; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-t-[18px] rounded-br-[18px] bg-success-strong px-3.5 py-1 font-display text-lg leading-none text-white",
        className,
      )}
    >
      {Math.round(percent)}%
    </span>
  );
}

/** Deterministic initials avatar: public candidate projections carry no photos. */
export function Avatar({ name, size = 52, className }: { name: string; size?: number; className?: string }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, background: `linear-gradient(135deg, hsl(${hue} 70% 62%), hsl(${(hue + 50) % 360} 65% 45%))` }}
      className={cx("inline-flex shrink-0 items-center justify-center rounded-[14px] font-semibold text-white", className)}
    >
      <span style={{ fontSize: size * 0.36 }}>{initials || "?"}</span>
    </span>
  );
}

/** Loading / empty / error placeholder; every data view renders one of these instead of a blank area. */
export function StateMessage({
  kind,
  title,
  description,
  action,
  className,
}: {
  kind: "loading" | "empty" | "error";
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={kind === "error" ? "alert" : undefined}
      className={cx("flex flex-col items-center justify-center gap-3 px-6 py-12 text-center", className)}
    >
      {kind === "loading" && <Spinner size={28} className="text-subtle" />}
      <p className={cx("text-lg font-semibold", kind === "error" ? "text-danger-soft" : "text-text")}>{title}</p>
      {description && <p className="max-w-md text-sm text-muted">{description}</p>}
      {action}
    </div>
  );
}

/** COMIT mark + wordmark. The wordmark font (Moon Walk) is pending; text keeps the name readable meanwhile. */
export function Logo({ size = 38, withWordmark = true, className }: { size?: number; withWordmark?: boolean; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-3 text-text", className)}>
      <img src={logoMark} alt="" width={size} height={size} className="shrink-0" />
      {withWordmark && (
        <span className="font-display tracking-[0.04em] uppercase" style={{ fontSize: size * 0.72 }}>
          comit
        </span>
      )}
    </span>
  );
}

/**
 * Fixed full-viewport backdrop: two blurred light-trail layers from the design over #010101.
 * Purely decorative and pointer-transparent; "auth" uses the brighter sign-in variant.
 */
export function Backdrop({ variant = "app" }: { variant?: "app" | "auth" }) {
  const [first, second] = variant === "auth" ? [authLinesA, authLinesB] : [linesA, linesB];
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-bg">
      <img
        src={first}
        alt=""
        className="absolute top-[30%] left-[-35%] w-[95vw] max-w-none rotate-[156deg] opacity-40 blur-[15px]"
      />
      <img
        src={second}
        alt=""
        className="absolute top-[-40%] left-[18%] w-[100vw] max-w-none rotate-[4deg] opacity-30 blur-[15px]"
      />
    </div>
  );
}
