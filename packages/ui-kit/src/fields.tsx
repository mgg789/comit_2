import { useEffect, useId, useRef, useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cx } from "./cx";
import { Icon } from "./icon";

/** Shape and focus only; colors are added per tone so no two utilities fight over one property. */
/** Design field: 67px tall, radius 20, 20px Inter Medium, 20px side padding (vacancy form). */
const FIELD =
  "w-full rounded-chip px-4 text-base font-medium tracking-[-0.0375rem] outline-none transition-shadow duration-150 lg:px-[1.25rem] lg:text-[1.25rem] " +
  "focus:ring-2 focus:ring-primary/70 aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger";
const DARK_FIELD = "bg-field text-text placeholder:text-white/60";

interface FieldFrameProps {
  label?: string;
  /** Validation message; also marks the control aria-invalid. */
  error?: string | null;
  hint?: ReactNode;
  className?: string;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}

function FieldFrame({ label, error, hint, className, children }: FieldFrameProps) {
  const id = useId();
  const messageId = `${id}-message`;
  const message = error ?? hint;
  return (
    <div className={cx("flex flex-col gap-2 lg:gap-[0.75rem]", className)}>
      {label && (
        <label htmlFor={id} className="text-sm font-semibold tracking-[-0.019rem] text-white/60 lg:text-[1.125rem]">
          {label}
        </label>
      )}
      {children(id, message ? messageId : undefined)}
      {message && (
        <p id={messageId} className={cx("text-xs", error ? "text-danger-soft" : "text-muted")}>
          {message}
        </p>
      )}
    </div>
  );
}

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string | null;
  hint?: ReactNode;
  /** Wrapper class; `className` styles the input itself. */
  containerClassName?: string;
}

/** Filled dark input from the vacancy form. */
export function TextField({ label, error, hint, containerClassName, className, ...rest }: TextFieldProps) {
  return (
    <FieldFrame label={label} error={error} hint={hint} className={containerClassName}>
      {(id, describedBy) => (
        <input
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
          className={cx(FIELD, DARK_FIELD, "h-12 lg:h-[4.1875rem]", className)}
        />
      )}
    </FieldFrame>
  );
}

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string | null;
  hint?: ReactNode;
  containerClassName?: string;
}

/** Multi-line variant of TextField. */
export function TextArea({ label, error, hint, containerClassName, className, ...rest }: TextAreaProps) {
  return (
    <FieldFrame label={label} error={error} hint={hint} className={containerClassName}>
      {(id, describedBy) => (
        <textarea
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
          className={cx(FIELD, DARK_FIELD, "min-h-24 resize-y py-3 lg:min-h-[8.75rem] lg:py-[1.25rem]", className)}
        />
      )}
    </FieldFrame>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectFieldProps {
  label?: string;
  error?: string | null;
  hint?: ReactNode;
  options: readonly SelectOption[];
  value: string;
  /** Receives the chosen option value ("" for the placeholder entry). */
  onChange: (value: string) => void;
  /** Text shown while value is ""; also offered as a «reset» entry unless `required`. */
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  /** Accessible name when there is no visible label. */
  "aria-label"?: string;
  containerClassName?: string;
  className?: string;
  /** "dark" — form field on dark panels; "light" — filter bar pill on the showcase screen. */
  tone?: "dark" | "light";
}

/**
 * Dropdown field with an app-styled glass listbox (native <select> popups cannot be themed).
 * Keyboard: ↑/↓/Home/End move, Enter/Space choose, Escape/Tab close, letters jump by prefix.
 */
export function SelectField({
  label,
  error,
  hint,
  options,
  value,
  onChange,
  placeholder,
  required,
  disabled,
  containerClassName,
  className,
  tone = "dark",
  "aria-label": ariaLabel,
}: SelectFieldProps) {
  const entries: SelectOption[] = placeholder !== undefined && !required ? [{ value: "", label: placeholder }, ...options] : [...options];
  const selected = options.find((option) => option.value === value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typeahead = useRef({ text: "", at: 0 });
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  function show() {
    const index = entries.findIndex((entry) => entry.value === value);
    setActive(index >= 0 ? index : 0);
    setOpen(true);
  }

  function choose(index: number) {
    const entry = entries[index];
    if (entry) onChange(entry.value);
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    const last = entries.length - 1;
    if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      show();
      return;
    }
    if (!open) return;
    if (event.key === "ArrowDown") setActive((index) => Math.min(last, index + 1));
    else if (event.key === "ArrowUp") setActive((index) => Math.max(0, index - 1));
    else if (event.key === "Home") setActive(0);
    else if (event.key === "End") setActive(last);
    else if (event.key === "Enter" || event.key === " ") choose(active);
    else if (event.key === "Escape" || event.key === "Tab") {
      setOpen(false);
      return;
    } else if (event.key.length === 1) {
      const now = Date.now();
      typeahead.current = { text: (now - typeahead.current.at < 600 ? typeahead.current.text : "") + event.key.toLowerCase(), at: now };
      const match = entries.findIndex((entry) => entry.label.toLowerCase().startsWith(typeahead.current.text));
      if (match >= 0) setActive(match);
      return;
    } else return;
    event.preventDefault();
  }

  const light = tone === "light";
  return (
    <FieldFrame label={label} error={error} hint={hint} className={containerClassName}>
      {(id, describedBy) => (
        <div ref={root} className="relative">
          <button
            id={id}
            type="button"
            role="combobox"
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={listId}
            aria-activedescendant={open ? `${listId}-${active}` : undefined}
            aria-label={ariaLabel}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            disabled={disabled}
            onClick={() => (open ? setOpen(false) : show())}
            onKeyDown={onKeyDown}
            className={cx(
              FIELD,
              "flex h-12 cursor-pointer items-center justify-between gap-3 text-left disabled:cursor-not-allowed disabled:opacity-50 lg:h-[4.1875rem]",
              light ? "bg-light" : "bg-field",
              light ? (selected ? "text-on-light" : "text-faint") : selected ? "text-text" : "text-white/60",
              className,
            )}
          >
            <span className="truncate">{selected?.label ?? placeholder ?? ""}</span>
            <Icon name="chevron-down" size={20} className={cx("transition-transform duration-150", open && "rotate-180", light ? "text-on-light" : "text-text")} />
          </button>
          {open && (
            <ul
              ref={list}
              id={listId}
              role="listbox"
              aria-label={label ?? ariaLabel}
              className="glass absolute top-full right-0 left-0 z-50 mt-2 max-h-72 overflow-y-auto rounded-field p-1.5"
            >
              {entries.map((entry, index) => {
                const isSelected = entry.value === value;
                return (
                  <li
                    key={entry.value || "__placeholder"}
                    id={`${listId}-${index}`}
                    data-index={index}
                    role="option"
                    aria-selected={isSelected}
                    onPointerEnter={() => setActive(index)}
                    onPointerDown={(event) => event.preventDefault()}
                    onClick={() => choose(index)}
                    className={cx(
                      "flex cursor-pointer items-center justify-between gap-3 rounded-[0.625rem] px-3 py-2.5 text-base",
                      index === active ? "bg-white/15 text-text" : "text-subtle",
                      entry.value === "" && "text-faint",
                    )}
                  >
                    <span className="truncate">{entry.label}</span>
                    {isSelected && <span aria-hidden className="size-2 shrink-0 rounded-full bg-text" />}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </FieldFrame>
  );
}
