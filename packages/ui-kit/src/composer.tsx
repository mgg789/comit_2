import type { FormEvent, ReactNode } from "react";
import { useId } from "react";
import { Spinner } from "./controls";
import { cx } from "./cx";
import { Icon } from "./icon";

interface MessageComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  busy?: boolean;
  /** Shows the gradient «+» button; omitted when the channel has no attachments. */
  onAttach?: () => void;
  placeholder?: string;
  maxLength?: number;
  /** Caption under the field («Защищено с QGramm», «Работает на …»). */
  footer?: ReactNode;
  /** Overrides the «has text» rule, e.g. a chat message that carries only attachments. */
  canSubmit?: boolean;
  className?: string;
}

/**
 * Message input pill shared by the candidate chat and the AI agent (design 707×79 glass pill,
 * 60px gradient attach button, 63px glass send button, 20px placeholder).
 */
export function MessageComposer({ value, onChange, onSubmit, busy = false, onAttach, placeholder = "Отправьте сообщение", maxLength, footer, canSubmit, className }: MessageComposerProps) {
  const id = useId();
  const ready = canSubmit ?? value.trim().length > 0;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy && ready) onSubmit();
  }
  return (
    <form onSubmit={submit} className={cx("flex w-full flex-col items-center gap-3 lg:gap-[1rem]", className)}>
      <div className={cx("glass flex h-16 w-full items-center gap-3 rounded-[12.5rem] p-2 lg:h-[4.9375rem] lg:gap-[0.9375rem] lg:p-[0.625rem]", !onAttach && "pl-5 lg:pl-[1.5rem]")}>
        {onAttach && (
          <button
            type="button"
            aria-label="Прикрепить файл"
            disabled={busy}
            onClick={onAttach}
            className="bg-accent-gradient inline-flex size-12 shrink-0 cursor-pointer items-center justify-center rounded-full text-white transition-[filter] duration-150 hover:brightness-110 disabled:opacity-50 lg:size-[3.75rem]"
          >
            <Icon name="plus" size={22} />
          </button>
        )}
        <label htmlFor={id} className="sr-only">
          Сообщение
        </label>
        <input
          id={id}
          value={value}
          maxLength={maxLength}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className="min-w-0 flex-1 bg-transparent text-base font-semibold text-text outline-none placeholder:text-light-strong lg:text-[1.25rem]"
        />
        <button
          type="submit"
          aria-label="Отправить"
          disabled={busy || !ready}
          className="glass inline-flex size-12 shrink-0 cursor-pointer items-center justify-center rounded-full text-text transition-colors duration-150 hover:bg-white/10 disabled:opacity-50 lg:size-[3.9375rem]"
        >
          {busy ? <Spinner size={18} /> : <Icon name="arrow-left" size={30} className="rotate-180" />}
        </button>
      </div>
      {footer && <p className="flex items-center gap-2 text-xs font-medium tracking-[-0.015rem] text-caption lg:text-[0.875rem]">{footer}</p>}
    </form>
  );
}

/** Chat bubble (design: 24/18 padding, radius 20, 20px Inter Medium; own messages dark on the right). */
export function MessageBubble({ mine, children, meta }: { mine: boolean; children: ReactNode; meta?: ReactNode }) {
  return (
    <div
      className={cx(
        "flex max-w-[85%] flex-col gap-2 rounded-[1.25rem] px-5 py-4 text-base font-medium lg:max-w-[42.5rem] lg:px-[1.5rem] lg:py-[1.125rem] lg:text-[1.25rem]",
        mine ? "self-end bg-surface-dialog text-white" : "self-start bg-white text-on-chip",
      )}
    >
      {children}
      {meta && <span className={cx("self-end text-xs font-normal", mine ? "text-white/50" : "text-on-chip/50")}>{meta}</span>}
    </div>
  );
}
