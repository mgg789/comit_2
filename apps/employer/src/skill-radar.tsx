import { useId, useRef, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import "./skill-radar.css";
import { useEmployerPreferences } from "./preferences";

/** One real skill axis; value is the required grade on the integer scale 1–7. */
export interface SkillRadarAxis {
  key: string;
  label: string;
  value: number;
}

/** Controlled skill editor. Changes are local callbacks; persistence belongs to the caller. */
export interface SkillRadarProps {
  axes: SkillRadarAxis[];
  disabled?: boolean;
  onChange: (key: string, value: number) => void;
  onRemove?: (key: string) => void;
  removeIcon?: ReactNode;
  children?: ReactNode;
  ariaLabel: string;
}

const GRADES = ["intern", "junior", "junior+", "middle", "middle+", "senior", "senior+"];
const CENTER = { x: 450, y: 330 };
const RADIUS = 200;

function gradeValue(value: number): number {
  return Number.isFinite(value) ? Math.max(1, Math.min(7, Math.round(value))) : 1;
}

/** Returns the grade name for a selected 1–7 level, rounding and clamping invalid input. */
export function gradeLabel(level: number): string {
  return GRADES[gradeValue(level) - 1] ?? "intern";
}

function direction(index: number, count: number): { x: number; y: number } {
  const angle = -Math.PI / 2 + (index * Math.PI * 2) / count;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function wrapLabel(label: string, characters: number): string[] {
  const chunks = label.trim().split(/\s+/).flatMap((word) => {
    const letters = Array.from(word);
    return Array.from({ length: Math.ceil(letters.length / characters) }, (_, index) =>
      letters.slice(index * characters, (index + 1) * characters).join(""));
  });
  const lines: string[] = [];
  for (const chunk of chunks) {
    const last = lines.length - 1;
    if (last >= 0 && (lines[last]?.length ?? 0) + chunk.length + 1 <= characters) {
      lines[last] += ` ${chunk}`;
    } else lines.push(chunk);
  }
  return lines.length ? lines : [label];
}

/**
 * Radial grade editor preserving the caller's axis order, including one or two skills.
 * Dragging projects SVG coordinates onto the selected axis; keyboard handles provide
 * the same 1–7 scale. The parent supplies stable unique keys and optional add UI children.
 */
export function SkillRadar({ axes, disabled = false, onChange, onRemove, removeIcon, children, ariaLabel }: SkillRadarProps) {
  const id = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragging = useRef<{ key: string; pointerId: number; x: number; y: number } | null>(null);
  const systemReducedMotion = useReducedMotion();
  const { preferences } = useEmployerPreferences();
  const reducedMotion = systemReducedMotion || preferences.reducedMotion;
  const transition = reducedMotion ? { duration: 0 } : { type: "spring" as const, stiffness: 410, damping: 35 };
  const points = axes.map((axis, index) => {
    const unit = direction(index, axes.length);
    const radius = (gradeValue(axis.value) / 7) * RADIUS;
    return { ...axis, unit, x: CENTER.x + unit.x * radius, y: CENTER.y + unit.y * radius };
  });
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ")
    + (points.length > 2 ? " Z" : "");

  function updateFromPointer(event: PointerEvent<SVGElement>) {
    const active = dragging.current;
    const svg = svgRef.current;
    if (!active || active.pointerId !== event.pointerId || disabled || !svg) return;
    const matrix = svg.getScreenCTM();
    if (!matrix || !axes.some((axis) => axis.key === active.key)) return;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(matrix.inverse());
    const projection = (local.x - CENTER.x) * active.x + (local.y - CENTER.y) * active.y;
    const next = gradeValue((projection / RADIUS) * 7);
    if (next !== gradeValue(axes.find((axis) => axis.key === active.key)?.value ?? 1)) {
      onChange(active.key, next);
    }
  }

  function finishPointer(event: PointerEvent<SVGSVGElement>) {
    if (dragging.current?.pointerId !== event.pointerId) return;
    dragging.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function changeFromKeyboard(event: KeyboardEvent<SVGGElement>, axis: SkillRadarAxis) {
    if (disabled) return;
    const current = gradeValue(axis.value);
    const next = event.key === "Home" ? 1 : event.key === "End" ? 7
      : event.key === "ArrowUp" || event.key === "ArrowRight" ? gradeValue(current + 1)
      : event.key === "ArrowDown" || event.key === "ArrowLeft" ? gradeValue(current - 1) : null;
    if (next === null) return;
    event.preventDefault();
    if (next !== current) onChange(axis.key, next);
  }

  if (axes.length === 0) {
    return <section className="skill-radar skill-radar--empty" aria-label={ariaLabel}>
      <p role="status">Добавьте навыки, чтобы настроить требования к грейду.</p>
      {children}
    </section>;
  }

  return (
    <section className="skill-radar" aria-label={ariaLabel}>
      <p className="skill-radar__hint" id={`${id}-hint`}>
        Перетащите точки: intern ближе к центру, senior+ — на внешнем круге.
      </p>
      <svg ref={svgRef} className="skill-radar__chart" viewBox="0 0 900 660"
        role="group" aria-label={ariaLabel} aria-describedby={`${id}-hint`}
        onPointerMove={updateFromPointer} onPointerUp={finishPointer} onPointerCancel={finishPointer}
        onLostPointerCapture={(event) => {
          if (dragging.current?.pointerId === event.pointerId) dragging.current = null;
        }}>
        <defs>
          <radialGradient id={`${id}-fill`}>
            <stop offset="0%" stopColor="var(--employer-blue)" stopOpacity="0.08" />
            <stop offset="100%" stopColor="var(--employer-violet)" stopOpacity="0.3" />
          </radialGradient>
        </defs>
        <g aria-hidden="true" className="skill-radar__grid">
          {GRADES.map((grade, index) => <circle key={grade} cx={CENTER.x} cy={CENTER.y} r={((index + 1) / 7) * RADIUS} />)}
          {points.map((point) => <line key={point.key} x1={CENTER.x} y1={CENTER.y}
            x2={CENTER.x + point.unit.x * RADIUS} y2={CENTER.y + point.unit.y * RADIUS} />)}
        </g>
        <motion.path className={`skill-radar__shape${points.length < 3 ? " skill-radar__shape--line" : ""}`}
          fill={`url(#${id}-fill)`} initial={false} animate={{ d: path }} transition={transition} aria-hidden="true" />
        {points.map((point) => {
          const value = gradeValue(point.value);
          const labelX = CENTER.x + point.unit.x * (RADIUS + 44);
          const labelY = CENTER.y + point.unit.y * (RADIUS + 44);
          const anchor = point.unit.x > 0.1 ? "start" : point.unit.x < -0.1 ? "end" : "middle";
          const lines = wrapLabel(point.label, 13);
          const height = Math.max(22, lines.length * 18 * 1.2);
          const position = {
            x: labelX - (anchor === "end" ? 180 : anchor === "middle" ? 90 : 0),
            y: point.unit.y < -0.7 ? labelY - height : point.unit.y > 0.7 ? labelY : labelY - height / 2,
            height,
            lines,
            alignment: anchor,
          };
          return <g key={point.key}>
            <foreignObject x={position.x} y={position.y} width={180} height={position.height} className="skill-radar__label-box">
              <div className={`skill-radar__label skill-radar__label--${position.alignment}`}>
                <span title={`${point.label}: ${gradeLabel(value)}`}>{position.lines.map((line, index) =>
                  <span className="skill-radar__label-line" key={index}>{line}</span>)}</span>
                {onRemove && <button type="button" className="skill-radar__remove" disabled={disabled}
                  aria-label={`Удалить навык ${point.label}`} title={`Удалить ${point.label}`}
                  onClick={() => { if (!disabled) onRemove(point.key); }}>{removeIcon ?? "×"}</button>}
              </div>
            </foreignObject>
            <motion.g className="skill-radar__handle" initial={false} animate={{ x: point.x, y: point.y }} transition={transition}
              tabIndex={disabled ? -1 : 0} role="slider" aria-label={point.label} aria-valuemin={1} aria-valuemax={7}
              aria-valuenow={value} aria-valuetext={gradeLabel(value)} aria-disabled={disabled}
              onKeyDown={(event) => changeFromKeyboard(event, point)}
              onPointerDown={(event) => {
                if (disabled || dragging.current || event.button !== 0) return;
                event.preventDefault();
                event.currentTarget.focus();
                dragging.current = { key: point.key, pointerId: event.pointerId, ...point.unit };
                svgRef.current?.setPointerCapture(event.pointerId);
                updateFromPointer(event);
              }}>
              <circle className="skill-radar__target" r="20" />
              <circle className="skill-radar__halo" r="12" />
              <circle className="skill-radar__dot" r="6" />
              <title>{`${point.label}: ${gradeLabel(value)}`}</title>
            </motion.g>
          </g>;
        })}
      </svg>
      {children}
    </section>
  );
}
