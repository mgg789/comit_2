/** Server-side Yandex authorization-code exchange; tokens never reach browser or logs. */
import type { AuthConfig } from "./config.js";
import { AuthError } from "./store.js";
import { requireEmail } from "./crypto.js";

/** Canonical callback for the public nginx /api proxy. */
export function yandexCallback(config: NonNullable<AuthConfig["yandex"]>, role: "candidate" | "employer"): string {
  return `${config.siteOrigin}/api/auth/${role}/yandex/callback`;
}

/** Validate provider response and restrict all requests to fixed HTTPS endpoints with timeouts. */
export async function yandexIdentity(config: NonNullable<AuthConfig["yandex"]>, role: "candidate" | "employer", code: string,
  transport: typeof fetch = fetch): Promise<{ subject: string; email: string }> {
  try {
    const tokenResponse = await transport("https://oauth.yandex.ru/token", { method: "POST", redirect: "error",
      headers: { "content-type": "application/x-www-form-urlencoded" }, signal: AbortSignal.timeout(10000),
      body: new URLSearchParams({ grant_type: "authorization_code", code, client_id: config.clientId,
        client_secret: config.clientSecret, redirect_uri: yandexCallback(config, role) }) });
    if (!tokenResponse.ok) throw new Error("token_rejected");
    const token: unknown = await tokenResponse.json();
    if (!token || typeof token !== "object" || !("access_token" in token) || typeof token.access_token !== "string"
      || !token.access_token || token.access_token.length > 4096) throw new Error("token_invalid");
    const identityResponse = await transport("https://login.yandex.ru/info?format=json", { redirect: "error",
      headers: { authorization: `OAuth ${token.access_token}` }, signal: AbortSignal.timeout(10000) });
    if (!identityResponse.ok) throw new Error("identity_rejected");
    const identity: unknown = await identityResponse.json();
    if (!identity || typeof identity !== "object" || !("id" in identity) || typeof identity.id !== "string"
      || !/^[0-9]{1,64}$/.test(identity.id) || !("client_id" in identity) || identity.client_id !== config.clientId
      || !("default_email" in identity) || typeof identity.default_email !== "string") throw new Error("identity_invalid");
    return { subject: identity.id, email: requireEmail(identity.default_email) };
  } catch { throw new AuthError(503, "oauth_provider_unavailable"); }
}
