import { useEffect, useRef, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import type { AccountView, EmailCodeReceipt } from "@comit/shared";
import { api, asset, errorText } from "./runtime";
import { Action, Icon } from "./ui";

/** Employer email OTP sign-in; consent is explicit, tokens remain in HttpOnly cookies. */
export function Login({
  onLogin,
}: {
  onLogin: (account: AccountView) => void;
}) {
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [consentError, setConsentError] = useState(false);
  const [receipt, setReceipt] = useState<EmailCodeReceipt | null>(null);
  const [code, setCode] = useState("");
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
    if (busy || !requireConsent()) return;
    setBusy(true);
    setError(null);
    try {
      if (!receipt)
        setReceipt(await api.requestLoginCode("employer", email.trim()));
      else {
        const session = await api.verifyLoginCode("employer", {
          email: email.trim(),
          challenge_id: receipt.challenge_id,
          code,
          consent: { processing: true, version: "account-v1" },
        });
        onLogin(session.account);
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
          <p className="login-business">Для бизнеса</p>
        </div>
      </motion.div>
      <motion.form
        className="login-card glass"
        onSubmit={submit}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12, duration: 0.3 }}
      >
        <h1>
          {receipt ? (
            "Введите код из письма"
          ) : (
            <>
              Зарегистрируйтесь чтобы
              <br />
              открыть весь потенциал{" "}
              <span className="login-mini-word">comit</span>
            </>
          )}
        </h1>
        <div className="login-email">
          <div className="login-input-wrap">
            <img
              src={asset("c179aee8cef2e1b481a0ab48e2c6a5af19c286b9.svg")}
              alt=""
            />
            <input
              ref={input}
              aria-label={receipt ? "Код из письма" : "Email"}
              autoComplete={receipt ? "one-time-code" : "email"}
              type={receipt ? "text" : "email"}
              inputMode={receipt ? "numeric" : "email"}
              placeholder={receipt ? "Код из письма" : "Введите почту"}
              required
              maxLength={receipt ? 6 : 254}
              pattern={receipt ? "[0-9]{6}" : undefined}
              value={receipt ? code : email}
              onChange={(event) =>
                receipt
                  ? setCode(event.target.value.replace(/\D/g, ""))
                  : setEmail(event.target.value)
              }
            />
          </div>
          <Action
            type="submit"
            className="circle"
            disabled={busy}
            onClick={(event) => {
              if (!requireConsent()) event.preventDefault();
            }}
            aria-label={receipt ? "Войти" : "Получить код"}
          >
            <img
              className="login-submit-asset"
              src={asset("4ecbfcd7e7f3629f251bb7b396bfec64aecee739.svg")}
              alt=""
            />
          </Action>
        </div>
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
          <>
            <small>Код отправлен на {email}</small>
            <Action
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
            </Action>
          </>
        )}
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
            onChange={(event) => {
              setConsent(event.target.checked);
              setConsentError(false);
            }}
          />
          <a href="#/legal/privacy-policy" target="_blank" rel="noopener">
            Я согласен(а) на обработку данных и получение уведомления о доступе
          </a>
        </div>
        <small className="login-consent-details">
          <a href="#/legal/processing-consent" target="_blank" rel="noopener">
            Текст согласия на обработку данных
          </a>
        </small>
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
