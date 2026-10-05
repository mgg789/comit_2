/** Secure, bounded SMTP delivery for email verification challenges. */
import nodemailer from "nodemailer";

/** The two account roles that receive verification challenges. */
export type MailRole = "candidate" | "employer";

/** Data persisted by the auth store and rendered into one verification email. */
export interface VerificationMail {
  email: string;
  code: string;
  role: MailRole;
  challenge_id: string;
  expires_at: Date | string;
}

/** Mail transport boundary; test transports can implement this without SMTP. */
export interface MailTransport {
  sendMail(options: {
    from: string;
    to: string;
    subject: string;
    text: string;
  }): Promise<unknown>;
  verify(): Promise<unknown>;
}

/** Application-facing mailer contract used by auth handlers and the worker. */
export interface Mailer {
  send(message: VerificationMail): Promise<void>;
  verify(): Promise<boolean>;
}

/** Safe error categories that the durable mail job may persist. */
export type MailDeliveryCode = "smtp_unavailable" | "rejected";

/** Provider-independent delivery failure; raw SMTP errors never cross this boundary. */
export class MailDeliveryError extends Error {
  readonly code: MailDeliveryCode;
  readonly retryable: boolean;

  constructor(code: MailDeliveryCode, retryable: boolean) {
    super(code);
    this.name = "MailDeliveryError";
    this.code = code;
    this.retryable = retryable;
  }
}

/** Safe configuration error used when SMTP is absent or incomplete. */
export class MailConfigurationError extends Error {
  readonly code = "smtp_unavailable" as const;
  readonly retryable = true as const;

  constructor() {
    super("mail delivery is not configured");
    this.name = "MailConfigurationError";
  }
}

/** Normalized Nodemailer configuration with production-safe TLS and timeouts. */
export interface SmtpConfig {
  host: string;
  port: number;
  user: string;
  password: string;
  from: string;
  tlsServername: string;
}

const RETRYABLE_PROVIDER_CODES = new Set([
  "ECONNECTION",
  "ECONNRESET",
  "ECONNREFUSED",
  "ETIMEDOUT",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "ENOTFOUND",
  "EAI_AGAIN",
  "ESOCKET",
  "ETLS",
]);

function nonEmpty(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function validateSmtpConfig(config: SmtpConfig): SmtpConfig {
  if (!nonEmpty(config.host) || !nonEmpty(config.user) || !nonEmpty(config.password)
    || !nonEmpty(config.from) || !nonEmpty(config.tlsServername)
    || !Number.isInteger(config.port) || config.port < 1 || config.port > 65535) {
    throw new MailConfigurationError();
  }
  return config;
}

/** Read and validate SMTP settings without exposing their values in errors. */
export function readSmtpConfig(env: NodeJS.ProcessEnv = process.env): SmtpConfig {
  const host = nonEmpty(env.SMTP_HOST);
  const user = nonEmpty(env.SMTP_USER);
  const password = nonEmpty(env.SMTP_PASSWORD);
  const from = nonEmpty(env.SMTP_FROM) ?? "Comit <noreply@mail.droidje.com>";
  const tlsServername = nonEmpty(env.SMTP_TLS_SERVERNAME) ?? "mail.droidje.com";
  const portValue = nonEmpty(env.SMTP_PORT) ?? "587";
  const port = Number(portValue);
  if (!host || !user || !password || !/^\d+$/.test(portValue) || !Number.isInteger(port) || port < 1 || port > 65535 || !from || !tlsServername) {
    throw new MailConfigurationError();
  }
  return validateSmtpConfig({ host, port, user, password, from, tlsServername });
}

function providerCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const candidate = (error as { code?: unknown }).code;
  return typeof candidate === "string" ? candidate.toUpperCase() : undefined;
}

function responseCode(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const candidate = (error as { responseCode?: unknown }).responseCode;
  return typeof candidate === "number" && Number.isInteger(candidate) ? candidate : undefined;
}

function isRejected(error: unknown): boolean {
  const status = responseCode(error);
  return status !== undefined && status >= 500 && status <= 599;
}

function mapDeliveryError(error: unknown): MailDeliveryError {
  if (isRejected(error)) return new MailDeliveryError("rejected", false);
  const code = providerCode(error);
  return new MailDeliveryError("smtp_unavailable", !code || RETRYABLE_PROVIDER_CODES.has(code));
}

function expiryText(expiresAt: Date | string): string {
  const date = expiresAt instanceof Date ? expiresAt : new Date(expiresAt);
  if (Number.isNaN(date.getTime())) throw new TypeError("invalid mail expiry");
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

function messageText(message: VerificationMail): string {
  if (!/^\d{6}$/.test(message.code) || !message.email || !message.challenge_id) {
    throw new TypeError("invalid verification mail");
  }
  const expiry = expiryText(message.expires_at);
  if (message.role === "candidate") {
    return `Здравствуйте!\n\nВаш код подтверждения Comit для кандидата: ${message.code}.\nКод действителен до ${expiry}.\n\nЕсли вы не запрашивали код, проигнорируйте это письмо.`;
  }
  if (message.role === "employer") {
    return `Здравствуйте!\n\nВаш код подтверждения Comit для работодателя: ${message.code}.\nКод действителен до ${expiry}.\n\nЕсли вы не запрашивали код, проигнорируйте это письмо.`;
  }
  throw new TypeError("invalid verification mail");
}

/** Nodemailer-backed mailer with mandatory TLS, bounded sockets, and safe failures. */
export class SmtpMailer implements Mailer {
  private readonly transport: MailTransport;
  private readonly config: SmtpConfig;

  constructor(config: SmtpConfig, transport?: MailTransport) {
    this.config = validateSmtpConfig(config);
    this.transport = transport ?? nodemailer.createTransport({
      host: this.config.host,
      port: this.config.port,
      secure: this.config.port === 465,
      auth: { user: this.config.user, pass: this.config.password },
      requireTLS: this.config.port !== 465,
      tls: { servername: this.config.tlsServername, rejectUnauthorized: true },
      disableFileAccess: true,
      disableUrlAccess: true,
      logger: false,
      debug: false,
      connectionTimeout: 10_000,
      greetingTimeout: 10_000,
      socketTimeout: 15_000,
    });
  }

  /** Send one challenge without exposing the challenge id or provider response. */
  async send(message: VerificationMail): Promise<void> {
    const text = messageText(message);
    try {
      await this.transport.sendMail({
        from: this.config.from,
        to: message.email,
        subject: "Код подтверждения Comit",
        text,
      });
    } catch (error) {
      throw mapDeliveryError(error);
    }
  }

  /** Check provider reachability; provider errors are deliberately reduced to false. */
  async verify(): Promise<boolean> {
    try {
      return (await this.transport.verify()) === true;
    } catch {
      return false;
    }
  }
}

/** Create a configured SMTP mailer; partially configured environments fail closed. */
export function createSmtpMailer(env: NodeJS.ProcessEnv = process.env, transport?: MailTransport): Mailer {
  return new SmtpMailer(readSmtpConfig(env), transport);
}

/** Return null only when SMTP is entirely absent, allowing handlers to fail closed cleanly. */
export function createConfiguredMailer(env: NodeJS.ProcessEnv = process.env, transport?: MailTransport): Mailer | null {
  const configured = [env.SMTP_HOST, env.SMTP_USER, env.SMTP_PASSWORD].some(value => nonEmpty(value));
  if (!configured) return null;
  return createSmtpMailer(env, transport);
}
