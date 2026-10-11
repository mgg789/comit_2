import { withChatOrigin } from "@comit/ui-kit/chat-navigation";
import { ApiError, createApiClient } from "@comit/api-client";
import { useCallback, useEffect, useRef, useState } from "react";

/** Same-origin gateway; HttpOnly cookies keep credentials out of browser storage. */
export const api = createApiClient();

/** Resolve packaged design assets under both the Vite and nginx mount paths. */
export function asset(name: string): string {
  return `${import.meta.env?.BASE_URL ?? "/employer/"}design/${name}`;
}

/** Human-readable errors without dumping private upstream response payloads. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.body && typeof error.body === "object" && "detail" in error.body ? error.body.detail : null;
    const lifecycleMessages: Record<string, string> = {
      organization_turn_active: "Агент ещё обрабатывает предыдущее сообщение. Дождитесь ответа и отправьте следующее.",
      agent_temporarily_unavailable: "Не удалось связаться с AI. Сообщение сохранено в поле ввода — попробуйте отправить его ещё раз через несколько секунд.",
      agent_provider_rejected: "Провайдер AI отклонил подключение. Нужна проверка настройки AI на этом стенде.",
      vacancy_lifecycle_pending: "Действие сохранено и завершается в сервисах. Подбор и новые сообщения уже заблокированы. Попробуйте повторить действие позже.",
      vacancy_lifecycle_conflict: "Вакансия изменилась до подтверждения. Обновите страницу и выберите действие заново.",
      vacancy_not_published: "Действие доступно только для опубликованной вакансии.",
      chat_read_only: "Этот чат доступен только для чтения.",
      invalid_system_file: "Поддерживаются PDF, TXT, MD, PNG, JPEG, GIF и WebP размером до 8 МБ; проверьте тип и содержимое файла.",
      invalid_system_files: "В одном сообщении можно отправить не более 8 вложений.",
      system_file_storage_limit: "Достигнут лимит неотправленных вложений 64 МБ. Отправьте подготовленные файлы или повторите загрузку через 24 часа.",
      system_file_account_limit: "Достигнут общий лимит вложений 256 МБ. Продолжите переписку текстом.",
      system_file_idempotency_conflict: "Повторная загрузка содержит изменённый файл. Выберите файл заново.",
      system_file_not_found: "Вложение больше недоступно.",
    };
    if (typeof detail === "string" && Object.hasOwn(lifecycleMessages, detail)) return lifecycleMessages[detail]!;
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
    if (error.status === 413)
      return "Вложение слишком большое. Один файл — до 8 МБ, общий объём диалога — до 256 МБ.";
    if (error.status === 429)
      return "Слишком много запросов. Попробуйте немного позже.";
    if (error.status === 503)
      return "Сервис временно недоступен. Попробуйте ещё раз.";
    if (error.status === 404) return "Данные пока не найдены.";
  }
  return "Не удалось выполнить запрос. Проверьте соединение и повторите.";
}

/** Explain upload failures using only known public codes, never arbitrary upstream text. */
export function documentErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.body && typeof error.body === "object" && "detail" in error.body
      ? error.body.detail : null;
    const messages: Record<string, string> = {
      document_name_invalid: "Недопустимое имя файла. Переименуйте его и попробуйте снова.",
      document_size_invalid: "Файл пустой или больше 5 МБ.",
      document_type_unsupported: "Поддерживаются только PDF, TXT и MD.",
      document_pdf_invalid: "Не удалось прочитать PDF. Проверьте файл или сохраните его заново.",
      document_pdf_encrypted_or_too_long: "Нужен PDF без пароля, не более 100 страниц.",
      document_pdf_stream_too_large: "PDF слишком сложный для обработки. Сохраните облегчённую копию.",
      document_text_too_long: "В документе слишком много текста. Максимум — 200 000 символов.",
      document_text_invalid: "Нужен текст в UTF-8 без служебных нулевых символов, до 200 000 символов.",
    };
    const message = typeof detail === "string" ? messages[detail] : undefined;
    if (typeof detail === "string" && Object.hasOwn(messages, detail) && message) return message;
    if (error.status === 413) return "Файл слишком большой. Максимум — 5 МБ.";
    if (error.status === 400 || error.status === 422)
      return "Не удалось прочитать файл. Для TXT и MD используйте UTF-8, для PDF — документ без пароля.";
  }
  return errorText(error);
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
  if (from == null) return "Зарплата не указана";
  const format = (value: number) =>
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
      value / 1000,
    );
  return to == null ? `от ${format(from)}к ₽` : `${format(from)}–${format(to)}к ₽`;
}

/** Read SPA state from a hash so deep links work under the existing nginx base. */
export function useRoute() {
  const read = () =>
    window.location.hash.slice(1) || "/vacancies";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const update = () => setRoute(read());
    window.addEventListener("hashchange", update); update();
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const navigate = useCallback((path: string) => {
    window.location.hash = withChatOrigin(path, window.location.hash.slice(1), "/vacancies");
  }, []);
  return { route, navigate };
}
