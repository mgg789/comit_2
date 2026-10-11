import { ApiError, createApiClient } from "@comit/api-client";
import { withChatOrigin } from "@comit/ui-kit/chat-navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CandidateProfileView, RecruitmentView } from "@comit/shared";
/** Candidate APIs use the same-origin gateway and role-specific HttpOnly session. */
export const api = createApiClient();
/** Explain known HTTP states without showing upstream internals or personal data. */
export function errorText(reason: unknown): string {
  if (reason instanceof ApiError) {
    const detail = reason.body && typeof reason.body === "object" && "detail" in reason.body ? reason.body.detail : null;
    const fileMessages: Record<string, string> = {
      invalid_system_file: "Поддерживаются PDF, TXT, MD, PNG, JPEG, GIF и WebP размером до 8 МБ; проверьте тип и содержимое файла.",
      invalid_system_files: "В одном сообщении можно отправить не более 8 вложений.",
      system_file_storage_limit: "Достигнут лимит неотправленных вложений 64 МБ. Отправьте подготовленные файлы или повторите загрузку через 24 часа.",
      system_file_account_limit: "Достигнут общий лимит вложений 256 МБ. Продолжите переписку текстом.",
      system_file_idempotency_conflict: "Повторная загрузка содержит изменённый файл. Выберите файл заново.",
      system_file_not_found: "Вложение больше недоступно.",
    };
    if (typeof detail === "string" && Object.hasOwn(fileMessages, detail)) return fileMessages[detail]!;
    if (reason.status === 401)
      return "Сессия закончилась. Войдите снова.";
    if (reason.status === 403)
      return "Нет доступа к этим данным.";
    if (reason.status === 409)
      return "Данные изменились. Обновите страницу перед повторной попыткой.";
    if (reason.status === 429)
      return "Слишком много запросов. Попробуйте позже.";
    if (reason.status === 413)
      return "Вложение слишком большое. Один файл — до 8 МБ, общий объём диалога — до 256 МБ.";
    if (reason.status === 400 || reason.status === 422)
      return "Проверьте заполненные поля.";
    if (reason.status === 404)
      return "Данные пока не найдены.";
  }
  return "Не удалось загрузить данные. Попробуйте ещё раз.";
}
/** Resource loading follows employer stale-response cancellation and explicit refresh. */
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
    loader.current().then(value => { if (active)
      setData(value); })
      .catch(reason => { if (active)
      setError(errorText(reason)); })
      .finally(() => { if (active)
      setLoading(false); });
    return () => { active = false; };
  }, [...dependencies, revision]);
  return { data, setData, error, loading, refresh };
}
/** An absent source profile is the create state, while server failures remain visible. */
export async function readProfile(): Promise<CandidateProfileView | null> {
  try {
    return await api.getCandidateProfile();
  }
  catch (reason) {
    if (reason instanceof ApiError && reason.status === 404)
      return null;
    throw reason;
  }
}
/** Hash routes preserve direct links under the candidate nginx mount. */
export function useRoute() {
  const read = () => window.location.hash.slice(1) || "/resume";
  const [route, setRoute] = useState(read);
  useEffect(() => { const update = () => setRoute(read()); window.addEventListener("hashchange", update); update(); return () => window.removeEventListener("hashchange", update); }, []);
  const navigate = useCallback((path: string) => { window.location.hash = withChatOrigin(path, window.location.hash.slice(1) || "/resume", "/resume"); }, []);
  return { route, navigate };
}
/** Whole RUB salary comes from the immutable invitation or the private profile. */
export function salary(from: number, to: number | null): string {
  const format = (value: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value / 1000);
  return to === null ? `от ${format(from)}к ₽` : `${format(from)}–${format(to)}к ₽`;
}
/** Read every owner-scoped invitation page before computing any visible counts. */
export async function readInvitations(): Promise<RecruitmentView[]> {
  const items: RecruitmentView[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  let offset = 0;
  for (;;) {
    const page = await api.getCandidateRecruitment(100, offset, cursor);
    const previousSize = seen.size;
    for (const item of page.items)
      if (!seen.has(item.id)) {
        seen.add(item.id);
        items.push(item);
      }
    if (!page.has_more)
      return items;
    if (!page.items.length || seen.size === previousSize)
      throw new Error("invitation_page_did_not_advance");
    if (page.next_cursor) {
      if (page.next_cursor === cursor)
        throw new Error("invitation_cursor_did_not_advance");
      cursor = page.next_cursor;
    }
    else {
      if (cursor)
        throw new Error("invitation_cursor_missing");
      offset += page.items.length;
    }
  }
}
