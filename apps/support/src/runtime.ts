import { ApiError, createApiClient } from "@comit/api-client";
import { useCallback, useEffect, useRef, useState } from "react";

/** Same-origin gateway; the operator session lives in an HttpOnly cookie. */
export const api = createApiClient();

/** Human-readable errors without exposing private upstream payloads. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.body && typeof error.body === "object" && "detail" in error.body ? error.body.detail : null;
    const messages: Record<string, string> = {
      support_login_rejected: "Неверная почта или ключ доступа.",
      support_login_throttled: "Слишком много неудачных попыток. Подождите немного и попробуйте снова.",
      support_not_configured: "Консоль поддержки не настроена на этом стенде.",
      support_unavailable: "Сервис поддержки временно недоступен.",
      origin_forbidden: "Запрос отклонён: недопустимый источник.",
      account_not_found: "Диалог не найден.",
      invalid_system_notification: "Сообщение пустое или слишком длинное (максимум 2000 символов).",
    };
    if (typeof detail === "string" && Object.hasOwn(messages, detail)) return messages[detail]!;
    if (error.status === 404 && error.url.endsWith("/support/login")) return messages.support_not_configured!;
    if (error.status === 401) return "Сессия закончилась. Войдите снова.";
    if (error.status === 403) return "Нет доступа к этому действию.";
    if (error.status === 404) return "Диалог не найден.";
    if (error.status === 422 || error.status === 400) return "Проверьте заполненные поля.";
    if (error.status === 429) return "Слишком много запросов. Попробуйте немного позже.";
    if (error.status === 503) return "Сервис временно недоступен. Попробуйте ещё раз.";
  }
  return "Не удалось выполнить запрос. Проверьте соединение и повторите.";
}

/** Fetch current data, ignoring stale responses after navigation or unmount. */
export function useResource<T>(load: () => Promise<T>, dependencies: readonly unknown[] = []) {
  const loader = useRef(load);
  loader.current = load;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setData(null);
    loader.current().then(value => {
      if (active) setData(value);
    }).catch(reason => {
      if (active) setError(errorText(reason));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [...dependencies, revision]);
  return { data, setData, error, loading, refresh };
}

/** Hash routing, same contract as the role cabinets: "#/chats", "#/chat/<account_id>". */
export function useRoute() {
  const read = () => window.location.hash.slice(1) || "/chats";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const update = () => setRoute(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const navigate = useCallback((path: string) => { window.location.hash = path; }, []);
  return { route, navigate };
}
