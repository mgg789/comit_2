import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cx } from "./cx";
import { Icon } from "./icon";

const FIELD =
  "w-full rounded-field bg-field px-4 text-base text-text placeholder:text-subtle outline-none transition-shadow duration-150 " +
  "focus:ring-2 focus:ring-primary/70 aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger";

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
    <div className={cx("flex flex-col gap-2", className)}>
      {label && (
        <label htmlFor={id} className="text-sm font-semibold text-text">
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
          className={cx(FIELD, "h-12 sm:h-[50px]", className)}
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
          className={cx(FIELD, "min-h-24 resize-y py-3", className)}
        />
      )}
    </FieldFrame>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  label?: string;
  error?: string | null;
  hint?: ReactNode;
  options: readonly SelectOption[];
  /** Placeholder shown while value is "" — rendered as a disabled first option. */
  placeholder?: string;
  containerClassName?: string;
  /** "dark" — form field on dark panels; "light" — filter bar pill on the showcase screen. */
  tone?: "dark" | "light";
}

/** Native select (keyboard and mobile pickers for free) styled as the design's dropdown field. */
export function SelectField({
  label,
  error,
  hint,
  options,
  placeholder,
  containerClassName,
  className,
  tone = "dark",
  value,
  ...rest
}: SelectFieldProps) {
  const empty = value === "" || value === undefined;
  return (
    <FieldFrame label={label} error={error} hint={hint} className={containerClassName}>
      {(id, describedBy) => (
        <div className="relative">
          <select
            id={id}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            value={value}
            {...rest}
            className={cx(
              FIELD,
              "h-12 cursor-pointer appearance-none pr-11 sm:h-[50px]",
              tone === "light" && "bg-light text-on-light",
              empty && (tone === "light" ? "text-faint" : "text-subtle"),
              className,
            )}
          >
            {placeholder !== undefined && (
              <option value="" disabled={rest.required}>
                {placeholder}
              </option>
            )}
            {options.map((option) => (
              <option key={option.value} value={option.value} className="text-on-light">
                {option.label}
              </option>
            ))}
          </select>
          <Icon
            name="chevron-down"
            size={18}
            className={cx(
              "pointer-events-none absolute top-1/2 right-4 -translate-y-1/2",
              tone === "light" ? "text-on-light" : "text-text",
            )}
          />
        </div>
      )}
    </FieldFrame>
  );
}
