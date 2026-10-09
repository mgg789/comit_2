/// <reference path="./assets.d.ts" />
import type { CSSProperties } from "react";
import {
  ArrowUpRight,
  BrainCircuit,
  Check,
  Copy,
  Gauge,
  Heart,
  MessageCircle,
  Paperclip,
  Pencil,
  Plus,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
import angleDown from "./assets/icons/angle-down.svg";
import arrowLeft from "./assets/icons/arrow-left.svg";
import at from "./assets/icons/at.svg";
import barsSort from "./assets/icons/bars-sort.svg";
import bell from "./assets/icons/bell.svg";
import chartLine from "./assets/icons/chart-line.svg";
import chevronDown from "./assets/icons/chevron-down.svg";
import clockRotate from "./assets/icons/clock-rotate.svg";
import gear from "./assets/icons/gear.svg";
import magnifier from "./assets/icons/magnifier.svg";
import people from "./assets/icons/people.svg";
import usage from "./assets/icons/usage.svg";
import resumes from "./assets/icons/resumes.svg";
import { cx } from "./cx";

/** Icon set exported from the COMIT Figma file; geometry is untouched, color comes from CSS. */
export const ICONS = {
  "angle-down": angleDown,
  "arrow-left": arrowLeft,
  at,
  "bars-sort": barsSort,
  bell,
  "chart-line": chartLine,
  "chevron-down": chevronDown,
  "clock-rotate": clockRotate,
  gear,
  magnifier,
  people,
  resumes,
  usage,
} as const;

/** The design draws the rest of its icons from lucide; these render as stroke icons in currentColor. */
const LUCIDE = {
  "arrow-up-right": ArrowUpRight,
  brain: BrainCircuit,
  chat: MessageCircle,
  check: Check,
  close: X,
  copy: Copy,
  gauge: Gauge,
  heart: Heart,
  "heart-filled": Heart,
  paperclip: Paperclip,
  pencil: Pencil,
  plus: Plus,
  trash: Trash2,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS | keyof typeof LUCIDE;

function isLucide(name: IconName): name is keyof typeof LUCIDE {
  return name in LUCIDE;
}

export interface IconProps {
  name: IconName;
  /** Square size in design px (1920 artboard); rendered in rem so it follows the fluid scale. */
  size?: number;
  className?: string;
  /** Accessible label; omitted icons are decorative and hidden from assistive tech. */
  label?: string;
}

/**
 * Single-color icon rendered as a CSS mask so it inherits `currentColor`
 * (active nav pills switch the same icon from light to dark).
 */
export function Icon({ name, size = 24, className, label }: IconProps) {
  if (isLucide(name)) {
    const Glyph = LUCIDE[name];
    return (
      <Glyph
        role={label ? "img" : undefined}
        aria-label={label}
        aria-hidden={label ? undefined : true}
        strokeWidth={2}
        fill={name === "heart-filled" ? "currentColor" : "none"}
        className={cx("inline-block shrink-0", className)}
        style={{ width: `${size / 16}rem`, height: `${size / 16}rem` }}
      />
    );
  }
  const url = `url("${ICONS[name]}")`;
  const style: CSSProperties = {
    width: `${size / 16}rem`,
    height: `${size / 16}rem`,
    backgroundColor: "currentColor",
    maskImage: url,
    WebkitMaskImage: url,
    maskSize: "contain",
    WebkitMaskSize: "contain",
    maskRepeat: "no-repeat",
    WebkitMaskRepeat: "no-repeat",
    maskPosition: "center",
    WebkitMaskPosition: "center",
  };
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cx("inline-block shrink-0", className)}
      style={style}
    />
  );
}
