/** Parameterized server-owned practice bank. Practice is not proctored grade evidence. */
import { createHash } from "node:crypto";
import type { WeeklyChallengeView, WeeklyQuestion } from "@comit/shared";
export interface WeeklyTask extends WeeklyQuestion { answer: string }
/** Return the UTC Monday containing now and the exclusive next-Monday deadline. */
export function weeklyWindow(now: Date): { week: string; expires: Date } {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  start.setUTCDate(start.getUTCDate() - (start.getUTCDay() + 6) % 7);
  return { week: start.toISOString().slice(0,10), expires: new Date(start.getTime()+7*86400000) };
}
/** Stable per-account variants avoid rerolling through reloads; only the server sees answers. */
export function weeklyTasks(account: string, week: string): WeeklyTask[] {
  const seed = createHash("sha256").update(`${account}:${week}:practice-v1`).digest();
  const count = 4 + seed[0]! % 13, size = 3 + seed[1]! % 9, excluded = 1 + seed[2]! % 3;
  const question = (id: string, prompt: string, answer: number, alternatives: number[]): WeeklyTask => {
    const choices = [...new Set([answer,...alternatives])].map(String);
    const shift = seed[3]! % choices.length;
    return { id, prompt, answer: String(answer), choices: [...choices.slice(shift),...choices.slice(0,shift)] };
  };
  return [
    question("rows", `В таблице ${count} строк с уникальным id. Условие WHERE исключило ${excluded} строки, затем LIMIT ${count} ограничил результат. Сколько строк вернётся?`, count-excluded,[count,count+excluded,0]),
    question("batches", `Нужно отправить ${count*size+1} событий пакетами не более ${size} событий. Сколько пакетов понадобится?`, count+1,[count,count+size,size]),
    question("search", `Двоичный поиск каждый раз делит область пополам. Сколько делений нужно, чтобы из ${2**(excluded+3)} вариантов остался один?`, excluded+3,[excluded+2,excluded+4,2**(excluded+3)]),
  ];
}
/** Strip answer keys from every response, including completed tasks. */
export function weeklyView(week: string, expires: Date, row?: { questions: WeeklyTask[]; correct: number | null; completed_at: Date | null }): WeeklyChallengeView {
  return { week, expires_at: expires.toISOString(), status: !row ? "available" : row.completed_at ? "completed" : "started",
    questions: row?.questions.map(({id,prompt,choices}) => ({id,prompt,choices})) ?? [],
    correct: row?.correct ?? null, passed: row?.correct == null ? null : row.correct >= 2,
    completed_at: row?.completed_at?.toISOString() ?? null };
}
