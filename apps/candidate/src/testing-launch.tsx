import { useState } from "react";
import { type CandidateSkillCatalogItem, type TestingPreflight, skillLevelLabel } from "@comit/shared";
import { Dialog } from "@comit/ui-kit/app-shell";
import { Action } from "@comit/ui-kit/design";
import { api, errorText } from "./runtime";

/** Conservative action uses the accepted result and server-owned upgrade cooldown. */
export function skillTestAction(skill: CandidateSkillCatalogItem, now = Date.now()): { label: string; disabled: boolean; mode: "retake" | "upgrade" } {
  const mode = skill.confirmed_level === null ? "retake" : "upgrade";
  if (!skill.test_subject) return { label: "Пока нет теста", disabled: true, mode };
  const availableAt = mode === "upgrade" ? skill.upgrade_at : skill.next_attempt_at;
  const days = availableAt ? Math.ceil((Date.parse(availableAt) - now) / 86400000) : 0;
  return { label: days > 0 ? `Тест через ${days} дн.` : mode === "upgrade" ? "Улучшить" : "Подтвердить", disabled: days > 0, mode };
}

/**
 * The test requires sharing the whole screen; phone browsers (iOS Safari, Android Chrome) have no
 * getDisplayMedia, so the candidate is told up front instead of failing after the camera step.
 */
export function screenShareSupported(devices: { getDisplayMedia?: unknown } | undefined): boolean {
  return typeof devices?.getDisplayMedia === "function";
}

/** Fixed-coordinate launch, revalidated with the authoritative engine before navigation. */
export function TestingLaunch({ skill }: { skill: CandidateSkillCatalogItem }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false);
  const [preflight, setPreflight] = useState<TestingPreflight | null>(null), [error, setError] = useState<string | null>(null);
  const action = skillTestAction(skill);
  const canShareScreen = typeof navigator !== "undefined" && screenShareSupported(navigator.mediaDevices);
  async function prepare() {
    if (!skill.test_subject) return;
    setOpen(true); setBusy(true); setError(null); setPreflight(null);
    try { setPreflight(await api.testingPreflight(skill.test_subject, action.mode)); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  function launch() {
    if (!skill.test_subject || !preflight?.eligible || !canShareScreen) return;
    const target = new URL("/testing/", window.location.origin);
    if (import.meta.env.DEV && window.location.port === "5173") target.port = "5175";
    target.searchParams.set("mode", action.mode); target.searchParams.set("subject", skill.test_subject); target.searchParams.set("ready", "1");
    window.location.assign(target.href);
  }
  return <><Action className="candidate-skill-test" variant="light" disabled={action.disabled} onClick={() => void prepare()}>{action.label}</Action>{open && <Dialog size="wide" title={`Проверка навыка: ${skill.label}`} close={() => setOpen(false)}>
    <p>Результат подтвердит уровень именно навыка «{skill.label}». Тест определяет Intern, Junior, Middle или Senior.</p>
    {skill.confirmed_level !== null && <p>Подтверждённый уровень: {skillLevelLabel(skill.confirmed_level)}.</p>}
    <p>До 30 вопросов. Для прохождения нужен показ всего экрана; камеру можно подключить по желанию. До нажатия «Начать» можно уйти без потери попытки.</p>
    <p>Повторная проверка доступна через 7 дней, повышение — через 30 дней после принятого результата.</p>
    {!canShareScreen && <p role="alert">На этом устройстве тест не пройти: браузер не умеет показывать экран. Откройте Comit на компьютере в Chrome или Firefox — уровень подтвердится в этом же профиле.</p>}
    {busy && <p role="status">Проверяем доступность теста…</p>}{error && <p role="alert">{error}</p>}
    {preflight && !preflight.eligible && <p role="status">{preflight.unavailable_reason ? "Проверка кода временно недоступна. Попробуйте позже — попытка ещё не начата." : `Тест пока недоступен${preflight.next_attempt_at ? ` до ${new Date(preflight.next_attempt_at).toLocaleDateString("ru-RU")}` : ""}.`}</p>}
    <div className="form-actions"><Action onClick={() => setOpen(false)}>Не сейчас</Action>{error ? <Action onClick={() => void prepare()}>Повторить</Action> : <Action variant="primary" disabled={busy || !preflight?.eligible || !canShareScreen} onClick={launch}>Готов, продолжить</Action>}</div>
  </Dialog>}</>;
}
