import { useEffect, useRef, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import { designAsset as asset } from "./design-assets";
import { Action, Checkbox, Icon } from "./design";

/** Server receipt needed to render the resend timer; no authentication state is held here. */
export interface EmailLoginReceipt {
  challenge_id: string;
  resend_after: string;
  /** Present only when API development delivery is explicitly enabled. */
  dev_code?: string;
}

/** Verification payload preserves the existing consent version and six-digit API contract. */
export interface EmailLoginVerification {
  email: string;
  challenge_id: string;
  code: string;
  consent: { processing: true; version: string };
}

/** Digits in the api-core email code. */
export const OTP_LENGTH = 6;

/** Keep only digits, capped at the code length: typed, pasted and autofilled codes alike. */
export function otpDigits(value: string): string {
  return value.replace(/\D/g, "").slice(0, OTP_LENGTH);
}

/** Use only a complete server-issued development code; production receipts clear stale input. */
export function receiptCode(receipt: EmailLoginReceipt): string {
  return receipt.dev_code && /^[0-9]{6}$/.test(receipt.dev_code) ? receipt.dev_code : "";
}

/** Shared email sign-in with injected role-specific APIs; session cookies remain server-managed. */
export function EmailLogin<Account>({
  requestCode,
  verifyCode,
  onLogin,
  errorText,
  brandSubtitle,
  homeHref = "/",
  consentHref = "#/legal/processing-consent",
  oauthRole,
  oauthEnabled = false,
}: {
  requestCode: (email: string) => Promise<EmailLoginReceipt>;
  verifyCode: (request: EmailLoginVerification) => Promise<Account>;
  onLogin: (account: Account) => void;
  errorText: (reason: unknown) => string;
  brandSubtitle?: string;
  homeHref?: string;
  consentHref?: string;
  /** Production consumers opt in; server readiness still controls provider availability. */
  oauthRole?: "candidate" | "employer";
  oauthEnabled?: boolean;
}) {
  const [yandexReady, setYandexReady] = useState(false);
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
    const errors: Record<string, string> = {
      oauth_email_conflict: "Этот email уже зарегистрирован. Войдите по почте.",
      role_mismatch: "Аккаунт зарегистрирован для другой роли. Войдите в соответствующий кабинет.",
      oauth_state_invalid: "Срок входа через Яндекс истёк. Попробуйте ещё раз.",
      oauth_denied: "Вход через Яндекс отменён. Можно войти по почте.",
      oauth_provider_unavailable: "Яндекс временно недоступен. Попробуйте ещё раз или войдите по почте.",
    };
    const reason = new URLSearchParams(window.location.hash.slice(1)).get("oauth-error");
    if (reason && errors[reason]) {
      setError(errors[reason]);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    if (!oauthEnabled || !oauthRole) return;
    const abort = new AbortController();
    void fetch(`/api/auth/${oauthRole}/providers`, { credentials: "same-origin", signal: abort.signal })
      .then(async response => {
        if (!response.ok) return;
        const providers: unknown = await response.json();
        setYandexReady(Boolean(providers && typeof providers === "object" && "yandex" in providers && providers.yandex === true));
      }).catch(() => { /* Email remains usable when provider discovery fails. */ });
    return () => abort.abort();
  }, [oauthEnabled, oauthRole]);
  async function startYandex() {
    if (busy || !consent || !yandexReady || !oauthRole) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/auth/${oauthRole}/yandex/start`, { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: JSON.stringify({ consent: { processing: true, version: "account-v1" } }) });
      const body: unknown = await response.json();
      if (!response.ok || !body || typeof body !== "object" || !("authorization_url" in body) || typeof body.authorization_url !== "string")
        throw new Error("oauth_unavailable");
      const destination = new URL(body.authorization_url);
      if (destination.origin !== "https://oauth.yandex.ru" || destination.pathname !== "/authorize") throw new Error("oauth_invalid_redirect");
      window.location.assign(destination.href);
    } catch {
      setError("Вход через Яндекс временно недоступен. Войдите по почте.");
      setBusy(false);
    }
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
      if (!receipt) {
        const issued = await requestCode(email.trim());
        setReceipt(issued);
        setCode(receiptCode(issued));
        setCodePosition(0);
      } else {
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
      <a className="login-back" href={homeHref}>
        <span className="action glass circle">
          <Icon name="back" />
        </span>
        Назад
      </a>
      <motion.a
        href={homeHref}
        aria-label="Comit — главная"
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
      </motion.a>
      <motion.form
        className={`login-card glass ${receipt ? "otp-card" : ""}`}
        onSubmit={submit}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12, duration: 0.3 }}
      >
        <h1>
          {receipt ? (
            <>
              {receipt.dev_code ? "Код для входа в dev уже подставлен:" : "Мы отправили вам код на email:"}
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
                  setCode(otpDigits(field.value));
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
                  {code[index] ?? ""}
                </span>
              ))}
            </div>
            <Action
              className="otp-submit"
              type="submit"
              disabled={busy || !consent || code.length !== 6}
            >
              Продолжить
            </Action>
            <p className="otp-note">
              {receipt.dev_code ? "Режим разработки: нажмите Продолжить для входа." : "Проверьте папки Рассылки и Спам если не видите письмо во Входящих"}
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
              disabled={busy || !consent}
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
            disabled={busy || !consent || !yandexReady}
            title={!oauthEnabled ? "Вход через Яндекс отключён в dev" : !yandexReady ? "Вход через Яндекс пока не настроен" : !consent ? "Отметьте согласие на обработку данных" : undefined}
            onClick={() => { void startYandex(); }}
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
            <Checkbox
              ref={consentInput}
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
