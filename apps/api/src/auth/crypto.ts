import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, randomInt, timingSafeEqual } from "node:crypto";
import { domainToASCII } from "node:url";

/** Number of decimal digits in an email verification code. */
export const OTP_LENGTH = 6;

/** Generate an OTP using the operating system's cryptographically secure RNG. */
export function createOtp(): string {
  return randomInt(0, 1_000_000).toString().padStart(OTP_LENGTH, "0");
}

/** Normalize the account key used by the independent candidate/employer namespaces. */
export function normalizeEmail(email: string): string {
  const trimmed = email.trim();
  const at = trimmed.lastIndexOf("@");
  if (at <= 0 || at === trimmed.length - 1) return trimmed.toLowerCase();
  const local = trimmed.slice(0, at).toLowerCase();
  const domain = domainToASCII(trimmed.slice(at + 1).toLowerCase());
  return `${local}@${domain}`;
}

/** Validate and normalize an address before it enters a database key. */
export function requireEmail(email: string): string {
  const normalized = normalizeEmail(email);
  const at = normalized.lastIndexOf("@");
  const local = at > 0 ? normalized.slice(0, at) : "";
  const domain = at > 0 ? normalized.slice(at + 1) : "";
  if (normalized.length < 3 || normalized.length > 254 || local.length > 64
    || !/^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/.test(local)
    || domain.length === 0 || domain.length > 253 || !domain.includes(".") || domain.startsWith(".") || domain.endsWith(".")
    || domain.includes("..") || !/^[a-z0-9.-]+$/.test(domain)
    || domain.split(".").some(label => label.length === 0 || label.length > 63 || label.startsWith("-") || label.endsWith("-"))) {
    throw new Error("invalid email");
  }
  return normalized;
}

/** Validate the two roles that own independent account records. */
export function requireRole(role: string): "candidate" | "employer" {
  if (role !== "candidate" && role !== "employer") throw new Error("invalid role");
  return role;
}

/** Derive a purpose-specific 256-bit key from the application secret. */
export function purposeKey(secret: string, purpose: string): Buffer {
  if (Buffer.byteLength(secret, "utf8") < 32) throw new Error("AUTH_SECRET must be at least 32 bytes");
  return createHmac("sha256", secret).update(`comit/auth/${purpose}`, "utf8").digest();
}

/** Hash a client address without persisting the raw network identifier. */
export function hashIp(secret: string, ip: string): Buffer {
  const normalized = ip.trim();
  if (normalized.length === 0 || normalized.length > 255) throw new Error("invalid ip");
  return createHmac("sha256", purposeKey(secret, "ip-hash")).update(normalized, "utf8").digest();
}

/** Hash an OTP for comparison; the plaintext is never written to PostgreSQL. */
export function hashOtp(secret: string, challengeId: string, code: string): Buffer {
  return createHmac("sha256", purposeKey(secret, "otp-hash"))
    .update(challengeId, "utf8").update("\0", "utf8").update(code, "utf8").digest();
}

/** Compare two secret values without leaking length or prefix information. */
export function safeEqual(left: Buffer, right: Buffer): boolean {
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export interface EncryptedOtpPayload {
  ciphertext: Buffer;
  nonce: Buffer;
}

/** Encrypt the SMTP payload with AES-GCM under a purpose-separated key. */
export function encryptOtpPayload(secret: string, payload: { email: string; code: string; challengeId: string }): EncryptedOtpPayload {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", purposeKey(secret, "smtp-otp"), nonce);
  cipher.setAAD(Buffer.from("comit:smtp-otp:v1", "utf8"));
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  const ciphertext = Buffer.concat([cipher.update(body), cipher.final(), cipher.getAuthTag()]);
  return { ciphertext, nonce };
}

/** Decrypt and validate an SMTP payload only in the in-memory mail worker. */
export function decryptOtpPayload(secret: string, encrypted: EncryptedOtpPayload): { email: string; code: string; challengeId: string } {
  if (encrypted.nonce.length !== 12 || encrypted.ciphertext.length <= 16) throw new Error("invalid encrypted OTP payload");
  const decipher = createDecipheriv("aes-256-gcm", purposeKey(secret, "smtp-otp"), encrypted.nonce);
  decipher.setAAD(Buffer.from("comit:smtp-otp:v1", "utf8"));
  const body = encrypted.ciphertext.subarray(0, -16);
  decipher.setAuthTag(encrypted.ciphertext.subarray(-16));
  const decoded: unknown = JSON.parse(Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8"));
  if (decoded === null || typeof decoded !== "object") throw new Error("invalid encrypted OTP payload");
  const value = decoded as Record<string, unknown>;
  if (typeof value.email !== "string" || typeof value.code !== "string" || typeof value.challengeId !== "string"
    || !/^\d{6}$/.test(value.code) || value.email.length === 0 || value.challengeId.length === 0) {
    throw new Error("invalid encrypted OTP payload");
  }
  return { email: value.email, code: value.code, challengeId: value.challengeId };
}

/** SHA-256 hash used for revocable, non-recoverable session identifiers. */
export function hashSessionId(sessionId: string): Buffer {
  return createHash("sha256").update(sessionId, "utf8").digest();
}

/** Generate a bearer session identifier with 256 bits of entropy. */
export function createSessionId(): string {
  return randomBytes(32).toString("base64url");
}

/** Generate a stable signed PostgreSQL advisory-lock key. */
export function advisoryKey(value: string): bigint {
  const bytes = createHash("sha256").update(`comit/advisory/${value}`, "utf8").digest();
  return bytes.readBigInt64BE(0);
}
