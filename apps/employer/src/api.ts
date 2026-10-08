import { ApiError, createApiClient } from "@comit/api-client";

/**
 * Employer app API client. Auth rides on the HttpOnly role cookie set by `/auth/employer/verify`,
 * so no token is kept in JS (XSS cannot exfiltrate the session).
 */
export const api = createApiClient();

/** UUID v4 for idempotency keys; every mutating call that accepts one must reuse it on retry. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** True when the server rejected the session (expired, revoked, or never signed in). */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** Best-effort human message for an API failure; server `detail` strings are machine codes, not UI copy. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Сессия истекла — войдите снова.";
    if (error.status === 403) return "Недостаточно прав для этого действия.";
    if (error.status === 404) return "Запись не найдена или недоступна.";
    if (error.status === 409) return "Данные изменились на сервере — обновите страницу и повторите.";
    if (error.status === 429) return "Слишком много попыток — подождите немного.";
    if (error.status >= 500) return "Сервис временно недоступен. Повторите позже.";
    return "Запрос отклонён сервером.";
  }
  if (error instanceof TypeError) return "Нет связи с сервером.";
  return "Неизвестная ошибка.";
}

/** Server-provided `detail` code, when present, for branching on specific conflicts. */
export function errorDetail(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const body = error.body;
  if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") return body.detail;
  return null;
}
