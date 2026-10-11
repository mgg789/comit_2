import { ApiError } from "@comit/api-client";
import type { TestingGrade, TestingSpecialization, TestingTask } from "@comit/shared";

/** Public subject names match the actual question bank, never an unsupported specialty. */
export const subjects: Record<TestingSpecialization, string> = { algorithms: "Алгоритмы", sql: "SQL", rest_api: "REST API", python: "Python", react: "React", docker: "Docker", teamwork: "Работа в команде" };
/** Grades are displayed independently for each tested subject. */
export const grades: Record<TestingGrade, string> = { intern: "Стажёр", junior: "Junior", middle: "Middle", senior: "Senior" };
/** Validate untrusted deep links against the supported bank. */
export function readSubject(value: string | null): TestingSpecialization { return value && Object.hasOwn(subjects, value) ? value as TestingSpecialization : "algorithms"; }
/** Answers preserve MCQ index zero; textual answers must contain non-whitespace. */
export function answerReady(task: TestingTask | null, answer: string | number | null): boolean { return !!task && (task.type === "mcq" ? typeof answer === "number" && answer >= 0 && answer < (task.options?.length ?? 0) : typeof answer === "string" && answer.trim().length > 0); }
/** Explain failures without displaying internal engine output or credentials. */
export function testingError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return "Проверка временно недоступна. Повторите отправку: ответ не засчитан и остаётся на экране.";
    if (error.status === 401) return "Войдите как кандидат, чтобы пройти тест.";
    if (error.status === 403) return "Эта попытка принадлежит другому аккаунту.";
    if (error.status === 409) return "Состояние попытки изменилось или срок повторного прохождения ещё не наступил. Обновите данные.";
    if (error.status === 422 || error.status === 400) return "Проверьте ответ и согласия перед продолжением.";
  }
  return "Не удалось связаться с сервисом тестирования. Повторите действие: ваши отправленные ответы сохранены.";
}
/** Render server eligibility timestamps in the candidate's local time zone. */
export function retryDate(value: string | null): string { if (!value) return "после завершения текущей попытки"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "после обновления данных" : new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short" }).format(date); }
/** Encode one bounded media chunk without sending frames anywhere except the authenticated gateway. */
export function mediaBase64(blob: Blob): Promise<string> { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error("Не удалось прочитать запись")); reader.onload = () => typeof reader.result === "string" ? resolve(reader.result.slice(reader.result.indexOf(",") + 1)) : reject(new Error("Не удалось прочитать запись")); reader.readAsDataURL(blob); }); }
