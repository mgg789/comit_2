import { useState } from "react";
import type { CandidateClaimEvidence, TestingSpecialization } from "@comit/shared";
import { Dialog } from "@comit/ui-kit/app-shell";
import { Action, Checkbox, Select } from "@comit/ui-kit/design";

/** Exact question-bank subjects; a subject grade never stands in for a whole profession. */
export const testingSubjects: Record<TestingSpecialization, string> = { algorithms: "Алгоритмы", sql: "SQL", rest_api: "REST API", python: "Python", react: "React", docker: "Docker", teamwork: "Работа в команде" };
/** Only known skill aliases receive direct test actions; unsupported claims stay self-reported. */
export function skillTestSubject(key: string): TestingSpecialization | null {
  const normalized = key.toLowerCase().replace(/[\s._-]/g, "");
  const aliases: Record<string, TestingSpecialization> = { algorithms: "algorithms", алгоритмы: "algorithms", sql: "sql", restapi: "rest_api", rest: "rest_api", python: "python", react: "react", reactjs: "react", docker: "docker", teamwork: "teamwork", работавкоманде: "teamwork" };
  return aliases[normalized] ?? null;
}
/** Render only a known server-confirmed grade, never session IDs or raw evidence. */
export function confirmedTestGrade(evidence: CandidateClaimEvidence | undefined): string | null {
  const value = evidence?.verified_value;
  if (evidence?.status !== "confirmed" || !value || typeof value !== "object" || Array.isArray(value)) return null;
  const names: Record<string, string> = { intern: "Стажёр", junior: "Junior", middle: "Middle", senior: "Senior" };
  return typeof value.grade === "string" ? names[value.grade] ?? null : null;
}

/** Confirm the subject before navigating to preparation; this action does not consume an attempt. */
export function TestingLaunch({ subject, compact = false }: { subject?: TestingSpecialization; compact?: boolean }) {
  const [upgrade, setUpgrade] = useState(false);
  const [open, setOpen] = useState(false), [selected, setSelected] = useState<TestingSpecialization>(subject ?? "algorithms");
  function launch() {
    const target = new URL("/testing/", window.location.origin);
    if (import.meta.env.DEV && window.location.port === "5173") target.port = "5175";
    target.searchParams.set("mode", upgrade ? "upgrade" : "retake"); target.searchParams.set("subject", selected); target.searchParams.set("ready", "1");
    window.location.assign(target.href);
  }
  return <><Action className={compact ? "candidate-skill-test" : ""} variant="light" onClick={() => setOpen(true)}>Подтвердить грейд</Action>{open && <Dialog title="Готовы пройти тест?" close={() => setOpen(false)}>{!subject && <Select aria-label="Что проверяем" value={selected} onChange={event => { const value = event.target.value; if (Object.hasOwn(testingSubjects,value)) setSelected(value as TestingSpecialization); }}>{Object.entries(testingSubjects).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</Select>}<p>Тест по направлению «{testingSubjects[selected]}»: меньше 36 вопросов с выбором и вводом ответа. Результат подтвердит уровень именно по этому направлению.</p><label className="candidate-test-upgrade"><Checkbox checked={upgrade} onChange={event => setUpgrade(event.target.checked)} /> Хочу повысить ранее подтверждённый грейд</label><p>Повторная проверка доступна через 7 дней, повышение грейда — через 30 дней после предыдущего результата.</p><p>На следующем экране подключите показ всего экрана и, по желанию, камеру. До нажатия «Начать» можно уйти без потери попытки.</p><div className="form-actions"><Action onClick={() => setOpen(false)}>Не сейчас</Action><Action variant="primary" onClick={launch}>Готов, продолжить</Action></div></Dialog>}</>;
}
