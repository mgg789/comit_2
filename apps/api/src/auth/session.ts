/** Signed, role-bound JWT transport backed by revocable PostgreSQL sessions. */
import { createHmac } from "node:crypto";
import { SignJWT, jwtVerify } from "jose";
import type { FastifyRequest } from "fastify";
import type { AccountRole } from "@comit/shared";
import type { AuthConfig } from "./config.js";
import type { AuthStore } from "./store.js";

/** Session identity used only within API-core; sid is never forwarded to engines. */
export interface SessionIdentity {
  accountId: string;
  sessionId: string;
  role: AccountRole;
  transport: "cookie" | "bearer";
}

/** Stable role cookie names keep candidate and employer logins independent. */
export function cookieName(role: AccountRole, config: AuthConfig): string {
  return `${config.secureCookies ? "__Host-" : ""}comit_${role}_session`;
}

function signingKey(config: AuthConfig): Uint8Array {
  return createHmac("sha256", config.secret).update("comit:jwt:signing:v1").digest();
}

/** Sign Keycloak-compatible identity claims with one explicitly allowed algorithm/key version. */
export async function signSession(config: AuthConfig, role: AccountRole, accountId: string,
  sessionId: string, expiresAt: string): Promise<string> {
  return new SignJWT({ roles: [role], realm_access: { roles: [role] }, email_verified: true, sid: sessionId })
    .setProtectedHeader({ alg: "HS256", typ: "JWT", kid: config.keyId })
    .setSubject(accountId).setIssuer(config.issuer).setAudience(config.audience)
    .setIssuedAt().setExpirationTime(Math.floor(new Date(expiresAt).getTime() / 1000)).sign(signingKey(config));
}

/** Verify signature and role before resolving the non-revoked database session. Bearer takes precedence. */
export async function resolveSession(request: FastifyRequest, role: AccountRole, config: AuthConfig,
  store: AuthStore): Promise<SessionIdentity | null> {
  const authorization = request.headers.authorization;
  const transport = authorization !== undefined ? "bearer" : "cookie";
  const token = authorization !== undefined
    ? (/^Bearer ([^\s]+)$/.exec(authorization)?.[1]) : request.cookies[cookieName(role, config)];
  if (!token || token.length > 4096) return null;
  let identity: SessionIdentity;
  try {
    const result = await jwtVerify(token, signingKey(config), { algorithms: ["HS256"], issuer: config.issuer,
      audience: config.audience, requiredClaims: ["sub", "sid", "iat", "exp", "roles", "email_verified"] });
    const payload = result.payload;
    const realm = payload.realm_access;
    if (typeof realm !== "object" || realm === null || !("roles" in realm)
      || !Array.isArray(realm.roles) || realm.roles.length !== 1 || realm.roles[0] !== role) return null;
    if (result.protectedHeader.kid !== config.keyId || result.protectedHeader.typ !== "JWT"
      || typeof payload.sub !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(payload.sub)
      || typeof payload.sid !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(payload.sid)
      || !Array.isArray(payload.roles) || payload.roles.length !== 1 || payload.roles[0] !== role
      || payload.email_verified !== true) return null;
    identity = { accountId: payload.sub, sessionId: payload.sid, role, transport };
  } catch { return null; }
  return await store.session(role, identity.accountId, identity.sessionId) ? identity : null;
}

/** Cookie mutations require a trusted browser origin; Bearer API clients can omit Origin. */
export function originAllowed(request: FastifyRequest, config: AuthConfig, bearer = false): boolean {
  const origin = request.headers.origin;
  if (origin === undefined) return bearer;
  return config.origins.has(origin);
}
