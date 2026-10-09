import type { EmailCodeReceipt } from "@comit/shared";
import { Backdrop, Button, Icon, LogoTile, Panel, Wordmark, cx } from "@comit/ui-kit";
import { useEffect, useState, type FormEvent } from "react";
import { api, describeError, errorDetail } from "../api";
import { CodeInput, OTP_LENGTH } from "./CodeInput";
import { useSession } from "./session";

/** Account consent version accepted by api-core (ACCOUNT_CONSENT_VERSION, docs/legal/processing-consent.md). */
const ACCOUNT_CONSENT_VERSION = "account-v1";

const AUTH_MESSAGES: Record<string, string> = {
  role_mismatch: "Этот email уже зарегистрирован как соискатель. Используйте другой адрес для кабинета компании.",
  invalid_code: "Неверный код. Проверьте письмо и попробуйте ещё раз.",
  invalid_challenge: "Код больше не действует — запросите новый.",
  challenge_expired: "Срок действия кода истёк — запросите новый.",
  challenge_exhausted: "Слишком много неверных попыток — запросите новый код.",
  consent_required: "Для регистрации нужно согласие на обработку данных.",
  resend_cooldown: "Код уже отправлен. Повторно запросить можно чуть позже.",
  email_rate_limited: "Слишком много запросов кода на этот адрес. Подождите 15 минут.",
  ip_rate_limited: "Слишком много запросов с вашей сети. Подождите немного.",
  verification_rate_limited: "Слишком много проверок кода. Подождите немного.",
  invalid_auth_input: "Проверьте адрес почты.",
};

function authMessage(error: unknown): string {
  const detail = errorDetail(error);
  return (detail && AUTH_MESSAGES[detail]) || describeError(error);
}

/** Seconds left until `iso`, never negative. */
function secondsUntil(iso: string): number {
  return Math.max(0, Math.ceil((Date.parse(iso) - Date.now()) / 1000));
}

/** Passwordless employer sign-in / registration: email → one-time code (Figma «Регистрация/вход», «Код подтверждения»). */
export function LoginPage() {
  const { signedIn } = useSession();
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [receipt, setReceipt] = useState<EmailCodeReceipt | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resendIn, setResendIn] = useState(0);

  useEffect(() => {
    if (!receipt) return;
    setResendIn(secondsUntil(receipt.resend_after));
    const timer = window.setInterval(() => setResendIn(secondsUntil(receipt.resend_after)), 1000);
    return () => window.clearInterval(timer);
  }, [receipt]);

  async function requestCode(event?: FormEvent) {
    event?.preventDefault();
    if (!consent) {
      setError(AUTH_MESSAGES.consent_required ?? null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setReceipt(await api.requestLoginCode("employer", email.trim()));
      setCode("");
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function verify(value: string) {
    if (!receipt || value.length !== OTP_LENGTH || busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.verifyLoginCode("employer", {
        email: email.trim(),
        challenge_id: receipt.challenge_id,
        code: value,
        consent: { processing: true, version: ACCOUNT_CONSENT_VERSION },
      });
      signedIn(session.account);
    } catch (failure) {
      setError(authMessage(failure));
      const detail = errorDetail(failure);
      if (detail === "challenge_expired" || detail === "challenge_exhausted" || detail === "invalid_challenge") setReceipt(null);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative flex min-h-dvh flex-col items-center px-4 py-6 lg:pt-[2.25rem]">
      <Backdrop variant="auth" />
      <a href="/" className="flex items-center gap-4 self-start text-text lg:ml-[2.5625rem] lg:gap-[1.25rem]">
        <span className="glass inline-flex size-12 items-center justify-center rounded-pill lg:size-[4.125rem]">
          <Icon name="arrow-left" size={30} />
        </span>
        <span className="font-display text-2xl leading-none lg:text-[2.5rem]">Назад</span>
      </a>

      <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row lg:mt-[3.5rem] lg:gap-[4.375rem]">
        <LogoTile size={300} className="hidden sm:inline-flex" />
        <LogoTile size={120} className="sm:hidden" />
        <div className="flex flex-col items-center gap-3 sm:items-start lg:gap-[1.75rem]">
          <h1 className="text-text">
            <Wordmark height={144} className="hidden sm:inline-block" />
            <Wordmark height={56} className="sm:hidden" />
          </h1>
          <span className="font-display text-xl leading-none lg:text-[2.5rem]">Для бизнеса</span>
        </div>
      </div>

      <Panel className="mt-8 w-full max-w-[43.125rem] px-5 py-8 sm:px-10 lg:mt-[3.75rem]">
        {receipt === null ? (
          <form onSubmit={requestCode} className="flex flex-col items-center gap-6" noValidate>
            <h1 className="max-w-[33.9375rem] text-center text-2xl font-semibold sm:text-[2rem] sm:leading-tight">
              Зарегистрируйтесь, чтобы открыть весь потенциал COMIT
            </h1>
            <div className="flex w-full max-w-[33.25rem] items-center gap-3">
              <label className="glass flex h-14 flex-1 items-center gap-3 rounded-pill pr-6 pl-4 focus-within:ring-2 focus-within:ring-primary/70 sm:h-[3.9375rem]">
                <Icon name="at" size={24} className="text-subtle" />
                <span className="sr-only">Рабочая почта</span>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="Введите почту"
                  className="w-full bg-transparent text-lg text-text outline-none placeholder:text-faint"
                />
              </label>
              <button
                type="submit"
                disabled={busy || !email.trim()}
                aria-label="Получить код"
                className="glass inline-flex size-14 shrink-0 cursor-pointer items-center justify-center rounded-pill text-text transition-colors duration-150 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50 sm:size-[3.9375rem]"
              >
                <Icon name="arrow-left" size={26} className="rotate-180" />
              </button>
            </div>
            <label className="flex max-w-[29rem] cursor-pointer items-start gap-4 text-left text-base leading-snug">
              <input
                type="checkbox"
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
                className="mt-0.5 size-6 shrink-0 cursor-pointer accent-primary"
              />
              <span>Я согласен(а) на обработку данных и получение уведомления о доступе</span>
            </label>
          </form>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void verify(code);
            }}
            className="flex flex-col items-center gap-6"
          >
            <h1 className="text-center text-2xl font-semibold sm:text-[2rem] sm:leading-tight">
              Мы отправили вам код на email:
              <br />
              <span className="break-all">{email.trim()}</span>
            </h1>
            <CodeInput value={code} onChange={setCode} onComplete={(value) => void verify(value)} disabled={busy} invalid={Boolean(error)} />
            <Button type="submit" variant="secondary" size="lg" className="w-full max-w-[28.4375rem]" loading={busy} disabled={code.length !== OTP_LENGTH}>
              Продолжить
            </Button>
            <p className="max-w-[29rem] text-center text-base">Проверьте папки «Рассылки» и «Спам», если не видите письмо во «Входящих»</p>
            <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm">
              <button
                type="button"
                className={cx("cursor-pointer text-subtle underline-offset-4 hover:underline disabled:cursor-default disabled:no-underline")}
                disabled={resendIn > 0 || busy}
                onClick={() => void requestCode()}
              >
                {resendIn > 0 ? `Отправить код повторно через ${resendIn} с` : "Отправить код повторно"}
              </button>
              <button
                type="button"
                className="cursor-pointer text-subtle underline-offset-4 hover:underline"
                onClick={() => {
                  setReceipt(null);
                  setCode("");
                  setError(null);
                }}
              >
                Изменить почту
              </button>
            </div>
          </form>
        )}
        {error && (
          <p role="alert" className="mt-5 text-center text-sm text-danger-soft">
            {error}
          </p>
        )}
      </Panel>
    </main>
  );
}
