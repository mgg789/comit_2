import { cx } from "@comit/ui-kit";
import { useRef, type ClipboardEvent, type KeyboardEvent } from "react";

/** Length enforced by api-core (`/^\d{6}$/` in auth/crypto.ts). */
export const OTP_LENGTH = 6;

/**
 * Apply raw text entered into cell `index` to the current code. A single digit replaces that cell;
 * several digits (OS one-time-code autofill, IME insert) are spread from `index` on.
 * Returns null when the input holds no digits. `focus` is the cell to focus next.
 */
export function applyCellInput(current: string, index: number, raw: string): { next: string; focus: number } | null {
  const typed = raw.replace(/\D/g, "");
  if (!typed) return null;
  const room = OTP_LENGTH - index;
  const inserted = typed.length > 1 && typed.length >= room ? typed.slice(-room) : typed.slice(-1);
  const next = (current.slice(0, index) + inserted + current.slice(index + inserted.length)).slice(0, OTP_LENGTH);
  return { next, focus: Math.min(index + inserted.length, OTP_LENGTH - 1) };
}

interface CodeInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Called once all cells are filled — lets the form submit without an extra click. */
  onComplete?: (value: string) => void;
  disabled?: boolean;
  invalid?: boolean;
}

/** Segmented one-time-code input: digits only, auto-advance, Backspace steps back, full-code paste. */
export function CodeInput({ value, onChange, onComplete, disabled, invalid }: CodeInputProps) {
  const cells = useRef<Array<HTMLInputElement | null>>([]);
  // Latest value between fast consecutive events, before React re-renders with the new prop.
  const latest = useRef(value);
  latest.current = value;
  const digits = Array.from({ length: OTP_LENGTH }, (_, index) => value[index] ?? "");

  function commit(next: string, focusIndex: number) {
    const clean = next.replace(/\D/g, "").slice(0, OTP_LENGTH);
    latest.current = clean;
    onChange(clean);
    cells.current[Math.min(focusIndex, OTP_LENGTH - 1)]?.focus();
    if (clean.length === OTP_LENGTH) onComplete?.(clean);
  }

  function handleInput(index: number, raw: string) {
    const applied = applyCellInput(latest.current, index, raw);
    if (applied) commit(applied.next, applied.focus);
  }

  function handleKeyDown(index: number, event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace") {
      event.preventDefault();
      const current = latest.current;
      const target = current[index] ? index : Math.max(0, index - 1);
      commit(current.slice(0, target) + current.slice(target + 1), target);
    } else if (event.key === "ArrowLeft") {
      cells.current[Math.max(0, index - 1)]?.focus();
    } else if (event.key === "ArrowRight") {
      cells.current[Math.min(OTP_LENGTH - 1, index + 1)]?.focus();
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLInputElement>) {
    const pasted = event.clipboardData.getData("text").replace(/\D/g, "");
    if (!pasted) return;
    event.preventDefault();
    commit(pasted, pasted.length);
  }

  return (
    <div role="group" aria-label="Код из письма" className="flex justify-center gap-2 sm:gap-4">
      {digits.map((digit, index) => (
        <input
          key={index}
          ref={(node) => {
            cells.current[index] = node;
          }}
          value={digit}
          onChange={(event) => handleInput(index, event.target.value)}
          onKeyDown={(event) => handleKeyDown(index, event)}
          onPaste={handlePaste}
          onFocus={(event) => event.target.select()}
          disabled={disabled}
          inputMode="numeric"
          autoComplete={index === 0 ? "one-time-code" : "off"}
          aria-label={`Цифра ${index + 1}`}
          aria-invalid={invalid || undefined}
          placeholder="_"
          className={cx(
            "glass h-14 w-11 rounded-pill border border-glass-border text-center text-2xl font-medium text-text sm:h-[63px] sm:w-[60px]",
            "placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-primary/70 disabled:opacity-50",
            invalid && "ring-2 ring-danger",
          )}
        />
      ))}
    </div>
  );
}
