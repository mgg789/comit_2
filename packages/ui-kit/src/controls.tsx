import type { ButtonHTMLAttributes } from "react";
import { cx } from "./cx";
import { Icon, type IconName } from "./icon";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent" | "dark";
export type ButtonSize = "sm" | "md" | "lg" | "xl";
export type ButtonShape = "chip" | "field" | "pill";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * primary — light pill (main action on dark), secondary — glass, ghost — transparent,
   * danger — red, accent — blue→violet gradient, dark — graphite button placed on light cards.
   */
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Corner radius; defaults by size (sm → chip, md → field, lg → pill). Use this instead of rounded-* classes. */
  shape?: ButtonShape;
  /** Shows a spinner and blocks repeated submits while a request is in flight. */
  loading?: boolean;
}

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-light text-on-light hover:bg-white",
  secondary: "glass text-text hover:bg-white/10",
  ghost: "bg-transparent text-text hover:bg-white/5",
  danger: "bg-danger text-white hover:brightness-110",
  accent: "bg-accent-gradient text-white hover:brightness-110",
  dark: "bg-dark-button text-white hover:brightness-125",
};

const SIZE: Record<ButtonSize, string> = {
  sm: "h-10 px-4 text-sm",
  md: "h-12 px-6 text-base",
  lg: "h-14 px-8 text-lg",
  /** Design form submit / primary actions: 67px tall, 20px text. */
  xl: "h-14 px-8 text-lg tracking-[-0.0375rem] lg:h-[4.1875rem] lg:px-[2.5rem] lg:text-[1.25rem]",
};

const DEFAULT_SHAPE: Record<ButtonSize, ButtonShape> = { sm: "chip", md: "field", lg: "pill", xl: "chip" };

const SHAPE: Record<ButtonShape, string> = {
  chip: "rounded-chip",
  field: "rounded-field",
  pill: "rounded-pill",
};

/** Single button component for all apps; variants follow the COMIT Figma file. */
export function Button({ variant = "primary", size = "md", shape, loading = false, disabled, className, children, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        "inline-flex cursor-pointer items-center justify-center gap-2 font-semibold whitespace-nowrap",
        "transition-[background-color,filter,opacity,scale] duration-150 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
        VARIANT[variant],
        SIZE[size],
        SHAPE[shape ?? DEFAULT_SHAPE[size]],
        className,
      )}
    >
      {loading && <Spinner size={16} />}
      {children}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  /** Required accessible name: icon-only controls have no visible text. */
  label: string;
  /** Diameter in px; nav buttons are 66px in the design, compact ones 46px. */
  size?: number;
  iconSize?: number;
  tone?: "glass" | "outline" | "light";
}

/** Round icon-only button (nav items, back arrow, card "open" chevron). */
export function IconButton({ icon, label, size = 56, iconSize = 24, tone = "glass", className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      style={{ width: `${size / 16}rem`, height: `${size / 16}rem` }}
      className={cx(
        "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-pill text-subtle transition-[color,background-color,scale] duration-150 active:scale-95",
        "hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-50",
        tone === "glass" && "glass hover:bg-white/10",
        tone === "outline" && "rounded-field border-2 border-white/40 hover:border-white/70",
        tone === "light" && "bg-light text-on-light hover:text-on-light",
        className,
      )}
    >
      <Icon name={icon} size={iconSize} />
    </button>
  );
}

/** Indeterminate loading indicator in the current text color. */
export function Spinner({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <span
      role="status"
      aria-label="Загрузка"
      style={{ width: `${size / 16}rem`, height: `${size / 16}rem` }}
      className={cx("inline-block animate-spin rounded-full border-2 border-current border-r-transparent", className)}
    />
  );
}
