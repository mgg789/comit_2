import { ApiError, createApiClient } from "@comit/api-client";
import { useCallback, useEffect, useRef, useState } from "react";

/** Same-origin gateway; HttpOnly cookies keep credentials out of browser storage. */
export const api = createApiClient();

/** Resolve packaged design assets under both the Vite and nginx mount paths. */
export function asset(name: string): string {
  return `${import.meta.env.BASE_URL}design/${name}`;
}

/** Human-readable errors without dumping private upstream response payloads. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (
      error.body &&
      typeof error.body === "object" &&
      "detail" in error.body &&
      error.body.detail === "agent_disabled_manual_available"
    )
      return "AI-агент на этом стенде не подключён. Отправка сообщений станет доступна после подключения AI.";
    if (error.status === 401) return "Сессия закончилась. Войдите снова.";
    if (error.status === 403) return "Нет доступа к этим данным.";
    if (error.status === 409)
      return "Данные изменились. Обновите страницу и повторите действие.";
    if (error.status === 422 || error.status === 400)
      return "Проверьте заполненные поля и обязательные требования.";
    if (error.status === 429)
      return "Слишком много запросов. Попробуйте немного позже.";
    if (error.status === 503)
      return "Сервис временно недоступен. Попробуйте ещё раз.";
    if (error.status === 404) return "Данные пока не найдены.";
  }
  return "Не удалось выполнить запрос. Проверьте соединение и повторите.";
}

/** Fetch current data, ignoring stale responses after navigation or unmount. */
export function useResource<T>(
  load: () => Promise<T>,
  dependencies: readonly unknown[] = [],
) {
  const loader = useRef(load);
  loader.current = load;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setData(null);
    loader
      .current()
      .then((value) => {
        if (active) setData(value);
      })
      .catch((reason) => {
        if (active) setError(errorText(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [...dependencies, revision]);
  return { data, error, loading, refresh, setData };
}

/** Whole-RUB salary interval; figures are always supplied by the backend. */
export function salary(from?: number | null, to?: number | null): string {
  if (from == null || to == null) return "Зарплата не указана";
  const format = (value: number) =>
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
      value / 1000,
    );
  return `${format(from)}–${format(to)}к ₽`;
}

/** Read SPA state from a hash so deep links work under the existing nginx base. */
export function useRoute() {
  const read = () =>
    decodeURIComponent(window.location.hash.slice(1) || "/vacancies");
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const update = () => setRoute(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const navigate = useCallback((path: string) => {
    window.location.hash = path;
  }, []);
  return { route, navigate };
}
