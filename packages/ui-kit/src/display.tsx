/// <reference path="./assets.d.ts" />
import type { HTMLAttributes, ReactNode } from "react";
import authLinesA from "./assets/backgrounds/auth-lines-a.png";
import authLinesB from "./assets/backgrounds/auth-lines-b.png";
import linesA from "./assets/backgrounds/lines-a.png";
import linesB from "./assets/backgrounds/lines-b.png";
import logoMark from "./assets/brand/logo-mark.svg";
import wordmark from "./assets/brand/wordmark.svg";
import { Spinner } from "./controls";
import { cx } from "./cx";

/** Frosted panel: `glass` for floating blocks, `card` for list tiles, `solid` for dense content. */
export function Panel({
  tone = "glass",
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { tone?: "glass" | "card" | "solid" | "dialog" | "light" }) {
  return (
    <div
      {...rest}
      className={cx(
        "rounded-card",
        tone === "glass" && "glass",
        tone === "card" && "glass-card",
        tone === "solid" && "bg-surface",
        tone === "dialog" && "bg-surface-dialog",
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
        "inline-flex h-9 items-center gap-2 rounded-chip px-3.5 text-sm font-medium tracking-[-0.021rem] whitespace-nowrap lg:h-[2.75rem] lg:px-[1.25rem] lg:text-[1.25rem]",
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
        "inline-block size-3.5 shrink-0 rounded-full lg:size-[1.125rem]",
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

/** Green match-percentage label: `tab` hugs a tile's top-left corner (design 111×41, Murs 24), `pill` floats inline. */
export function MatchBadge({ percent, shape = "tab", className }: { percent: number; shape?: "tab" | "pill"; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex h-8 items-center justify-center bg-success-strong px-4 font-display text-lg leading-none text-white lg:h-[2.5625rem] lg:min-w-[6.9375rem] lg:px-[2rem] lg:text-[1.5rem]",
        shape === "tab" ? "rounded-tl-[3.125rem] rounded-tr-[12.5rem] rounded-br-[12.5rem]" : "rounded-[1.25rem]",
        className,
      )}
    >
      {Math.round(percent)}%
    </span>
  );
}

/** Light counter next to section titles («Понравились в ленте 12»). */
export function CountBadge({ value }: { value: number }) {
  return (
    <span className="inline-flex min-w-7 items-center justify-center rounded-chip bg-light p-1 font-sans text-sm leading-none font-medium tracking-[-0.0625rem] text-on-light lg:min-w-[2rem] lg:p-[0.375rem] lg:text-[1.25rem]">
      {value}
    </span>
  );
}

/**
 * Light 888×120 row from the design (welcome entries, interviews, résumé list): leading visual,
 * Murs Gothic title, secondary line, dark action button on the right.
 */
export function ActionRow({
  leading,
  title,
  subtitle,
  action,
  badge,
  children,
  className,
}: {
  leading: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  /** Pinned to the top-left corner (match percent tab). */
  badge?: ReactNode;
  /** Middle content between the text block and the action (e.g. the funnel stepper). */
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "relative flex flex-col gap-4 overflow-hidden rounded-[2rem] bg-light p-5 text-on-chip sm:flex-row sm:items-center sm:gap-0 sm:p-0 sm:pr-[1.5rem] sm:pl-[1.5625rem] lg:min-h-[7.5rem]",
        className,
      )}
    >
      {badge && <span className="absolute top-0 left-0">{badge}</span>}
      <div className={cx("flex min-w-0 items-center gap-4 sm:gap-[1.5rem]", children ? "sm:w-[30rem] sm:shrink-0" : "flex-1")}>
        {leading}
        <div className="flex min-w-0 flex-col gap-1 lg:gap-[0.5rem]">
          <span className="truncate font-display text-xl leading-tight lg:text-[1.75rem]">{title}</span>
          {subtitle && <span className="truncate text-base font-semibold tracking-[-0.021rem] text-on-chip/80 lg:text-[1.25rem]">{subtitle}</span>}
        </div>
      </div>
      {children && <div className="min-w-0 flex-1">{children}</div>}
      {action}
    </div>
  );
}

/** Dark action button used inside ActionRow (design: #272930, radius 20, 64/24 padding, 20px semibold). */
export const ACTION_ROW_BUTTON =
  "inline-flex h-14 shrink-0 items-center justify-center rounded-[1.25rem] bg-dark-button px-8 text-base font-semibold tracking-[-0.0375rem] text-white transition-[filter,scale] duration-150 hover:brightness-125 active:scale-[0.97] lg:h-[4.5rem] lg:px-[4rem] lg:text-[1.25rem]";

/**
 * Deterministic initials avatar: public candidate projections carry no photos.
 * `shape` sets the corner radius relative to size (rounded ≈ the design's 14px at 52px).
 */
export function Avatar({
  name,
  size = 52,
  shape = "rounded",
  className,
}: {
  name: string;
  size?: number;
  shape?: "rounded" | "circle" | "square";
  className?: string;
}) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  const radius = shape === "circle" ? "50%" : shape === "square" ? 0 : Math.round(size * 0.27);
  return (
    <span
      aria-hidden
      style={{
        width: `${size / 16}rem`,
        height: `${size / 16}rem`,
        borderRadius: typeof radius === "number" ? `${radius / 16}rem` : radius,
        background: `linear-gradient(135deg, hsl(${hue} 70% 62%), hsl(${(hue + 50) % 360} 65% 45%))`,
      }}
      className={cx("inline-flex shrink-0 items-center justify-center font-semibold text-white", className)}
    >
      <span style={{ fontSize: `${(size * 0.36) / 16}rem` }}>{initials || "?"}</span>
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

/**
 * COMIT wordmark as vector outlines exported from Figma (Moon Walk 200px → 655×144), tinted via mask.
 * `height` is in design px; width follows the 655:144 aspect ratio.
 */
export function Wordmark({ height, className }: { height: number; className?: string }) {
  const url = `url("${wordmark}")`;
  return (
    <span
      role="img"
      aria-label="COMIT"
      className={cx("inline-block shrink-0 bg-current", className)}
      style={{
        height: `${height / 16}rem`,
        width: `${(height * 655) / 144 / 16}rem`,
        maskImage: url,
        WebkitMaskImage: url,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
      }}
    />
  );
}

/**
 * Light rounded tile with the dark dotted mark (welcome and sign-in screens; design 300×297, radius 100,
 * mark inset ≈17%). `size` is the tile width in design px.
 */
export function LogoTile({ size = 300, className }: { size?: number; className?: string }) {
  const url = `url("${logoMark}")`;
  const rem = (value: number) => `${value / 16}rem`;
  return (
    <span
      aria-hidden
      className={cx("inline-flex shrink-0 items-center justify-center bg-light text-on-light", className)}
      style={{ width: rem(size), height: rem((size * 297) / 300), borderRadius: rem(size / 3) }}
    >
      <span
        className="bg-current"
        style={{
          width: rem(size * 0.6594),
          height: rem(size * 0.6594),
          maskImage: url,
          WebkitMaskImage: url,
          maskSize: "contain",
          WebkitMaskSize: "contain",
          maskRepeat: "no-repeat",
          WebkitMaskRepeat: "no-repeat",
        }}
      />
    </span>
  );
}

/** Header logo: 38px mark + wordmark 23px high with a 12px gap (Figma «Библиотека» pill), scaled by `size`. */
export function Logo({ size = 38, withWordmark = true, className }: { size?: number; withWordmark?: boolean; className?: string }) {
  return (
    <span className={cx("inline-flex items-center text-text", className)} style={{ gap: `${(size * 12) / 38 / 16}rem` }}>
      <img src={logoMark} alt="" className="shrink-0" style={{ width: `${size / 16}rem`, height: `${size / 16}rem` }} />
      {withWordmark && <Wordmark height={(size * 23) / 38} />}
    </span>
  );
}

/**
 * Fixed full-viewport backdrop: the two blurred light-trail layers of every Figma frame over #010101,
 * positioned in rem on the 1920 artboard so they scale with the page. Decorative and pointer-transparent.
 */
export function Backdrop({ variant = "app" }: { variant?: "app" | "auth" }) {
  const [first, second] = variant === "auth" ? [authLinesA, authLinesB] : [linesA, linesB];
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-bg">
      <div className="absolute top-[20.875rem] left-[-44rem] flex h-[114.4445rem] w-[133.4761rem] items-center justify-center">
        <img
          src={first}
          alt=""
          className={cx("h-[75.1104rem] w-[112.6656rem] max-w-none rotate-[156deg] object-cover blur-[0.9375rem]", variant === "auth" ? "opacity-40" : "opacity-35")}
        />
      </div>
      <div className="absolute top-[-27.8125rem] left-[21.6875rem] flex h-[86.6286rem] w-[118.9611rem] items-center justify-center">
        <img
          src={second}
          alt=""
          className={cx("h-[75.1104rem] w-[112.6656rem] max-w-none rotate-[4.07deg] object-cover blur-[0.9375rem]", variant === "auth" ? "opacity-40" : "opacity-28")}
        />
      </div>
    </div>
  );
}
