import { useEffect, useRef, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import { designAsset as asset } from "./design-assets";
import { Action, Icon } from "./design";

/** Server receipt needed to render the resend timer; no authentication state is held here. */
export interface EmailLoginReceipt {
  challenge_id: string;
  resend_after: string;
}

/** Verification payload preserves the existing consent version and six-digit API contract. */
export interface EmailLoginVerification {
  email: string;
  challenge_id: string;
  code: string;
  consent: { processing: true; version: string };
}

/** Shared email sign-in with injected role-specific APIs; session cookies remain server-managed. */
export function EmailLogin<Account>({
  requestCode,
  verifyCode,
  onLogin,
  errorText,
  brandSubtitle,
  consentHref = "#/legal/processing-consent",
}: {
  requestCode: (email: string) => Promise<EmailLoginReceipt>;
  verifyCode: (request: EmailLoginVerification) => Promise<Account>;
  onLogin: (account: Account) => void;
  errorText: (reason: unknown) => string;
  brandSubtitle?: string;
  consentHref?: string;
}) {
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [consentError, setConsentError] = useState(false);
  const [receipt, setReceipt] = useState<EmailLoginReceipt | null>(null);
  const [code, setCode] = useState("");
  const [codeFocused, setCodeFocused] = useState(false);
  const [codePosition, setCodePosition] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const input = useRef<HTMLInputElement>(null);
  const consentInput = useRef<HTMLInputElement>(null);
  function requireConsent() {
    if (consent) return true;
    setConsentError(true);
    consentInput.current?.focus();
    return false;
  }
  useEffect(() => {
    if (receipt) input.current?.focus();
  }, [receipt]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !requireConsent() || (receipt && code.length !== 6)) return;
    setBusy(true);
    setError(null);
    try {
      if (!receipt)
        setReceipt(await requestCode(email.trim()));
      else {
        const session = await verifyCode({
          email: email.trim(),
          challenge_id: receipt.challenge_id,
          code,
          consent: { processing: true, version: "account-v1" },
        });
        onLogin(session);
      }
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  const canResend = receipt && now >= Date.parse(receipt.resend_after);
  return (
    <main className="login">
      <a className="login-back" href="/">
        <span className="action glass circle">
          <Icon name="back" />
        </span>
        Назад
      </a>
      <motion.div
        className="login-brand"
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
      >
        <div className="login-mark">
          <img
            className="login-logo-asset"
            src={asset("24395aac95170e79b172ce4e75051e1ad4faeed7.svg")}
            alt=""
          />
        </div>
        <div>
          <p className="login-word">comit</p>
          {brandSubtitle && <p className="login-business">{brandSubtitle}</p>}
        </div>
      </motion.div>
      <motion.form
        className={`login-card glass ${receipt ? "otp-card" : ""}`}
        onSubmit={submit}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12, duration: 0.3 }}
      >
        {receipt && <p className="otp-label">Код подтверждения</p>}
        <h1>
          {receipt ? (
            <>
              Мы отправили вам код на email:
              <br />
              <span>{email}</span>
            </>
          ) : (
            <>
              Зарегистрируйтесь чтобы
              <br />
              открыть весь потенциал{" "}
              <span className="login-mini-word">comit</span>
            </>
          )}
        </h1>
        {receipt ? (
          <>
            <div className="otp-digits">
              <input
                ref={input}
                className="otp-native"
                aria-label="Код подтверждения из письма, 6 цифр"
                autoComplete="one-time-code"
                inputMode="numeric"
                type="text"
                required
                pattern="[0-9]{6}"
                value={code}
                disabled={busy}
                onFocus={() => setCodeFocused(true)}
                onBlur={() => setCodeFocused(false)}
                onSelect={(event) =>
                  setCodePosition(
                    Math.min(
                      event.currentTarget.selectionStart ?? code.length,
                      5,
                    ),
                  )
                }
                onKeyDown={(event) => {
                  if (event.key !== "Backspace") return;
                  const field = event.currentTarget;
                  const start = field.selectionStart ?? code.length;
                  const end = field.selectionEnd ?? start;
                  setCodePosition(Math.min(start === end ? Math.max(0, start - 1) : start, 5));
                }}
                onChange={(event) => {
                  const field = event.currentTarget;
                  const position = field.value.slice(0, field.selectionStart ?? field.value.length).replace(/\D/g, "").length;
                  setCodePosition(Math.min(position, 5));
                  setCode(field.value.replace(/\D/g, "").slice(0, 6));
                }}
              />
              {Array.from({ length: 6 }, (_, index) => (
                <span
                  className={`otp-digit glass ${codeFocused && codePosition === index ? "focused" : ""} ${code[index] ? "filled" : ""}`}
                  key={index}
                  aria-hidden="true"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    const field = input.current;
                    if (!field || busy) return;
                    field.focus();
                    const position = Math.min(index, code.length);
                    field.setSelectionRange(
                      position,
                      Math.min(position + 1, code.length),
                    );
                    setCodePosition(Math.min(position, 5));
                  }}
                >
                  {code[index] || "_"}
                </span>
              ))}
            </div>
            <Action
              className="otp-submit"
              type="submit"
              disabled={busy || code.length !== 6}
            >
              Продолжить
            </Action>
            <p className="otp-note">
              Проверьте папки Рассылки и Спам если не видите письмо во Входящих
            </p>
          </>
        ) : (
          <div className="login-email">
            <div className="login-input-wrap">
              <img
                src={asset("c179aee8cef2e1b481a0ab48e2c6a5af19c286b9.svg")}
                alt=""
              />
              <input
                ref={input}
                aria-label="Email"
                autoComplete="email"
                type="email"
                inputMode="email"
                placeholder="Введите почту"
                required
                disabled={busy}
                maxLength={254}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <Action
              type="submit"
              className="circle"
              disabled={busy}
              onClick={(event) => {
                if (!requireConsent()) event.preventDefault();
              }}
              aria-label="Получить код"
            >
              <img
                className="login-submit-asset"
                src={asset("4ecbfcd7e7f3629f251bb7b396bfec64aecee739.svg")}
                alt=""
              />
            </Action>
          </div>
        )}
        {!receipt && (
          <button
            type="button"
            className="yandex"
            onClick={() => {
              if (requireConsent())
                setError(
                  "Вход через Яндекс пока не подключён. Войдите по почте.",
                );
            }}
            aria-label="Войти через Яндекс"
          >
            <img
              src={asset("a3c8e3c0abb4dfca0ad94e35ca485cbec998042f.svg")}
              alt="Яндекс"
            />
          </button>
        )}
        {receipt && (
          <div className="otp-resend">
            <button
              type="button"
              disabled={!canResend || busy}
              onClick={() => {
                setReceipt(null);
                setCode("");
              }}
            >
              {canResend
                ? "Отправить новый код"
                : `Повторить через ${Math.max(0, Math.ceil((Date.parse(receipt.resend_after) - now) / 1000))} сек.`}
            </button>
          </div>
        )}
        {!receipt && (
          <div
            className={`checkbox login-consent ${consentError ? "has-error" : ""}`}
          >
            <input
              ref={consentInput}
              type="checkbox"
              aria-label="Согласие на обработку данных"
              aria-invalid={consentError}
              aria-describedby={consentError ? "consent-error" : undefined}
              checked={consent}
              disabled={busy}
              onChange={(event) => {
                setConsent(event.target.checked);
                setConsentError(false);
              }}
            />
            <a href={consentHref} target="_blank" rel="noopener">
              Я согласен(а) на обработку данных и получение уведомления о
              доступе
            </a>
          </div>
        )}
        {consentError && (
          <p id="consent-error" className="form-error" role="alert">
            Отметьте согласие на обработку данных, чтобы продолжить.
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {busy && (
          <small role="status">
            {receipt ? "Проверяем код…" : "Отправляем код…"}
          </small>
        )}
      </motion.form>
    </main>
  );
}
