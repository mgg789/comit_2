/** Display labels and exact test bindings; membership is owned by the active matching schema. */
import type { TestingGrade, TestingSpecialization } from "./testing.js";
// Canonical stack keys from matching_engine/strict.py INITIAL_GROUPS; keep labels human-readable.
export const stackChoices = [
  ["python", "Python"],
  ["node", "Node.js"],
  ["go", "Go"],
  ["php", "PHP"],
  ["react", "React"],
  ["vue", "Vue"],
  ["angular", "Angular"],
  ["html_css_jquery", "HTML / CSS / jQuery"],
  ["tailwind", "Tailwind CSS"],
  ["electron", "Electron"],
  ["figma", "Figma"],
  ["postgres", "PostgreSQL"],
  ["mongo", "MongoDB"],
  ["sqlite", "SQLite"],
  ["mysql", "MySQL"],
  ["docker", "Docker"],
] as const;

export const domainChoices = [
  ["ux_userflow", "UX и пользовательские сценарии"],
  ["architecture_thinking", "Архитектура систем"],
  ["rest_api", "REST API"],
  ["sql", "SQL"],
  ["doc_databases", "Документные базы данных"],
  ["data_analytics_stats", "Аналитика и статистика"],
  ["math", "Математика"],
  ["algorithms", "Алгоритмы"],
  ["lowlevel_optimization", "Низкоуровневая оптимизация"],
  ["cybersecurity", "Информационная безопасность"],
  ["devops", "DevOps"],
  ["ai_agents", "AI-агенты"],
  ["nlp_and_arbitrary_data", "NLP и неструктурированные данные"],
  ["neural_nets", "Нейронные сети"],
  ["classical_ml", "Классическое машинное обучение"],
] as const;
export const softChoices = [
  ["teamwork", "Командная работа"],
  ["representation", "Презентация и коммуникация"],
  ["leadership", "Лидерство"],
  ["project_management", "Управление проектами"],
  ["time_management", "Управление временем"],
  ["self_organization", "Самоорганизация"],
] as const;


export const SKILL_LEVELS = ["Intern", "Junior", "Junior+", "Middle", "Middle+", "Senior", "Senior+"] as const;
export type CandidateSkillGroup = "domains" | "stacks" | "soft_skills";
export const SKILL_GROUP_LABELS: Record<CandidateSkillGroup, string> = { domains: "Области знаний", stacks: "Технологии", soft_skills: "Гибкие навыки" };
export const TEST_SKILL_COORDINATES: Record<TestingSpecialization, { group: CandidateSkillGroup; key: string }> = {
  algorithms: { group: "domains", key: "algorithms" }, sql: { group: "domains", key: "sql" }, rest_api: { group: "domains", key: "rest_api" },
  python: { group: "stacks", key: "python" }, react: { group: "stacks", key: "react" }, docker: { group: "stacks", key: "docker" },
  teamwork: { group: "soft_skills", key: "teamwork" },
};
export const TEST_GRADE_LEVELS: Record<TestingGrade, number> = { intern: 1, junior: 2, middle: 4, senior: 6 };
/** Match both group and key; aliases cannot confer evidence on another coordinate. */
export function skillTestSubject(group: CandidateSkillGroup, key: string): TestingSpecialization | null {
  return (Object.keys(TEST_SKILL_COORDINATES) as TestingSpecialization[]).find(subject => {
    const coordinate = TEST_SKILL_COORDINATES[subject]; return coordinate.group === group && coordinate.key === key;
  }) ?? null;
}
/** Unknown legacy keys stay readable until the candidate explicitly replaces or removes them. */
export function skillLabel(group: CandidateSkillGroup, key: string): string {
  const choices = { domains: domainChoices, stacks: stackChoices, soft_skills: softChoices }[group];
  return choices.find(([value]) => value === key)?.[1] ?? key;
}
/** Named ordinal level; zero is retained only for legacy source compatibility. */
export function skillLevelLabel(level: number): string { return SKILL_LEVELS[level - 1] ?? "Уровень не выбран"; }
export interface CandidateSkillCatalogItem {
  group: CandidateSkillGroup; key: string; label: string; test_subject: TestingSpecialization | null;
  confirmed_level: number | null; next_attempt_at: string | null; upgrade_at: string | null;
}
export interface CandidateSkillCatalog { version: number; schema_hash: string; skills: CandidateSkillCatalogItem[] }
