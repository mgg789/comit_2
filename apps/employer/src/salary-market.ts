/** Offline snapshot of published Habr Career medians, first half of 2026, whole RUB/month. */
export const salaryMarketSource = "https://habr.com/ru/specials/1060148/";
const directions: Readonly<Record<string, number>> = {
  backend: 240000, frontend: 217000, fullstack: 200000, mobile: 280000,
  qa: 163000, data: 194000, design: 120000,
};
// Only languages with a directly verified numeric median in the report are used.
const languages: Readonly<Record<string, number>> = { python: 243000, go: 325000, swift: 326000, kotlin: 320000, cpp: 254000 };
export interface SalaryMarketEstimate {
  referenceRub: number;
  midpointRub: number;
  deviationPercent: number;
  label: "Ниже рынка" | "Средняя по рынку" | "Выше рынка";
  basis: "direction" | "direction_and_language";
}

/** Compare interval midpoint to a direction/language heuristic; ±20% is neutral, invalid input has no estimate.
 * Inputs are thousands of RUB as in the vacancy editor. Independent survey medians are not a joint sample.
 * This informational reference never changes salary values, validation, matching, or candidate ranking.
 */
export function estimateSalaryMarket(direction: string, stacks: Readonly<Record<string, number>>, fromThousands: string, toThousands: string): SalaryMarketEstimate | null {
  const base = directions[direction];
  if (!base || !fromThousands.trim() || !toThousands.trim()) return null;
  const from = Number(fromThousands) * 1000, to = Number(toThousands) * 1000;
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from <= 0 || to < from) return null;
  const stackValues = ["backend", "frontend", "fullstack", "mobile"].includes(direction)
    ? Object.keys(stacks).flatMap(key => { const median = languages[key]; return (stacks[key] ?? 0) > 0 && median !== undefined ? [median] : []; }) : [];
  const referenceRub = stackValues.length ? Math.round((base + stackValues.reduce((sum, value) => sum + value, 0) / stackValues.length) / 2) : base;
  const midpointRub = from / 2 + to / 2;
  const ratio = midpointRub / referenceRub;
  return { referenceRub, midpointRub, deviationPercent: Math.round((ratio - 1) * 100),
    label: ratio < 0.8 ? "Ниже рынка" : ratio > 1.2 ? "Выше рынка" : "Средняя по рынку",
    basis: stackValues.length ? "direction_and_language" : "direction" };
}
