import { useLayoutEffect, useRef, useState, type FormEventHandler, type ReactNode } from "react";
import { Action, Icon } from "./design";
import "./conversation.css";

/** The AI chat composer shared verbatim by recruitment chats; transport and permissions stay with callers. */
export function ChatComposer({ text, onTextChange, onSubmit, messageLabel, sendLabel = "Отправить сообщение", placeholder = "Отправьте сообщение", maxLength, disabled, canSubmit, attachDisabled, onAttach, attachLabel = "Прикрепить файл", uploading, sending, submitWithEnter = true, autoFocus = false, children }: {
  text: string; onTextChange: (text: string) => void; onSubmit: FormEventHandler<HTMLFormElement>;
  messageLabel: string; sendLabel?: string; placeholder?: string; maxLength: number;
  disabled?: boolean; canSubmit: boolean; attachDisabled?: boolean; onAttach: () => void;
  attachLabel?: string; uploading?: boolean; sending?: boolean; submitWithEnter?: boolean; autoFocus?: boolean; children?: ReactNode;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  const [multiline, setMultiline] = useState(false);
  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    const fit = () => {
      element.style.height = "0px";
      const line = Number.parseFloat(getComputedStyle(element).lineHeight) || 24;
      const height = element.scrollHeight;
      setMultiline(height > line + 1);
      element.style.height = `${Math.max(line, Math.min(height, line * 8))}px`;
      element.style.overflowY = height > line * 8 + 1 ? "auto" : "hidden";
    };
    fit(); window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [text]);
  return <form className={`communication-composer message-composer glass${multiline ? " multiline" : ""}`} onSubmit={onSubmit} onMouseDown={event => {
    if (!(event.target instanceof Element) || event.target.closest("button, textarea")) return;
    event.preventDefault(); field.current?.focus();
  }}>
    {children}
    <button className="attach-button" type="button" aria-label={attachLabel} disabled={attachDisabled} onClick={onAttach}>{uploading ? <span className="spinner" /> : <Icon name="plus" />}</button>
    <textarea ref={field} rows={1} autoFocus={autoFocus} aria-label={messageLabel} placeholder={placeholder} value={text} maxLength={maxLength} disabled={disabled} onChange={event => onTextChange(event.target.value)} onKeyDown={event => {
      if (event.key !== "Enter" || !submitWithEnter || event.shiftKey || event.nativeEvent.isComposing) return;
      event.preventDefault(); event.currentTarget.form?.requestSubmit();
    }} />
    <Action className="circle send-button" type="submit" aria-label={sendLabel} disabled={!canSubmit}>{sending ? <span className="spinner" /> : <Icon name="send" />}</Action>
  </form>;
}
