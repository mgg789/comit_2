/// <reference path="./assets.d.ts" />
import type { CSSProperties } from "react";
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
} as const;

export type IconName = keyof typeof ICONS;

export interface IconProps {
  name: IconName;
  /** Square size in px. */
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
  const url = `url("${ICONS[name]}")`;
  const style: CSSProperties = {
    width: size,
    height: size,
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
