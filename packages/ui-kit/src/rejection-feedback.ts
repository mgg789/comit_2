import { rejectionReasonCategories, type RejectionReasonCategory } from "@comit/shared";

/** Explain the same detailed employer-feedback requirements enforced by the API. */
export function rejectionFeedbackError(reason: string, categories: readonly RejectionReasonCategory[]): string | null {
  const text = reason.trim();
  if (text.length < 80 || text.length > 4000 || text.split(/\s+/).filter(Boolean).length < 10) return "Добавьте подробную обратную связь: от 80 до 4000 символов и не менее 10 слов.";
  if (!categories.length || categories.length > 7 || new Set(categories).size !== categories.length || !categories.every(value => rejectionReasonCategories.includes(value))) return "Выберите хотя бы одну причину отказа.";
  return null;
}
