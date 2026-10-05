/** Passwordless configuration: explicit origins, a strong secret and secure production cookies. */
export interface AuthConfig {
  secret: string;
  origins: Set<string>;
  secureCookies: boolean;
  issuer: string;
  audience: string;
  keyId: string;
  consentVersion: string;
}

/** Load process configuration without exposing secret values or silently enabling insecure production. */
export function loadAuthConfig(env: NodeJS.ProcessEnv = process.env): AuthConfig {
  const secret = (env.AUTH_SECRET?.trim() || env.JWT_SECRET?.trim() || "");
  if (Buffer.byteLength(secret) < 32 || /changeme|replace.me/i.test(secret)) throw new Error("auth_secret_missing");
  const secureCookies = env.AUTH_COOKIE_SECURE !== "false";
  if (env.NODE_ENV === "production" && !secureCookies) throw new Error("auth_secure_cookie_required");
  const origins = new Set((env.AUTH_ALLOWED_ORIGINS ?? "").split(",").map(value => value.trim()).filter(Boolean).map(value => {
    const url = new URL(value.trim());
    if (url.origin !== value.trim() || url.username || url.password || !["https:", "http:"].includes(url.protocol)) {
      throw new Error("auth_origin_invalid");
    }
    if (env.NODE_ENV === "production" && url.protocol !== "https:") throw new Error("auth_https_origin_required");
    if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) {
      throw new Error("auth_local_http_only");
    }
    return url.origin;
  }));
  if (!origins.size) throw new Error("auth_origins_missing");
  return { secret, origins, secureCookies, issuer: env.AUTH_ISSUER ?? "comit",
    audience: env.AUTH_AUDIENCE ?? "comit-api", keyId: env.AUTH_KEY_ID ?? "v1",
    consentVersion: env.ACCOUNT_CONSENT_VERSION ?? "account-v1" };
}
