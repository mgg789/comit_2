import type { SalaryMarketAggregate } from "@comit/shared";

/** Offline fallback snapshot of Habr Career medians, first half of 2026, whole RUB/month. */
export const salaryMarketSource = "https://habr.com/ru/specials/1060148/";
const directions: Readonly<Record<string, number>> = {
  backend: 240000, frontend: 217000, fullstack: 200000, mobile: 280000,
  qa: 163000, data: 194000, design: 120000,
};
const languages: Readonly<Record<string, number>> = { python: 243000, go: 325000, swift: 326000, kotlin: 320000, cpp: 254000 };

export interface SalaryMarketEstimate {
  referenceRub: number;
  midpointRub: number;
  deviationPercent: number;
  label: "Ниже рынка" | "Средняя по рынку" | "Выше рынка";
  source: "platform" | "habr_fallback";
  basis?: "direction" | "direction_and_language";
}

function compareSalary(referenceRub: number, fromThousands: string, toThousands: string, source: SalaryMarketEstimate["source"], basis?: SalaryMarketEstimate["basis"]): SalaryMarketEstimate | null {
  if (!referenceRub || !fromThousands.trim() || !toThousands.trim()) return null;
  const from = Number(fromThousands) * 1000, to = Number(toThousands) * 1000;
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from <= 0 || to < from) return null;
  const midpointRub = from / 2 + to / 2;
  const ratio = midpointRub / referenceRub;
  return { referenceRub, midpointRub, deviationPercent: Math.round((ratio - 1) * 100),
    label: ratio < 0.8 ? "Ниже рынка" : ratio > 1.2 ? "Выше рынка" : "Средняя по рынку", source, ...(basis ? { basis } : {}) };
}

/** Compare against a platform aggregate when it meets the server's minimum sample size. */
export function estimatePlatformSalaryMarket(market: SalaryMarketAggregate | null, fromThousands: string, toThousands: string): SalaryMarketEstimate | null {
  if (market?.average_salary_rub === null || market?.average_salary_rub === undefined) return null;
  return compareSalary(market.average_salary_rub, fromThousands, toThousands, "platform");
}

/** Approximate an external fallback when the platform has fewer than three comparable vacancies. */
export function estimateSalaryMarket(direction: string, stacks: Readonly<Record<string, number>>, fromThousands: string, toThousands: string): SalaryMarketEstimate | null {
  const base = directions[direction];
  if (!base) return null;
  const stackValues = ["backend", "frontend", "fullstack", "mobile"].includes(direction)
    ? Object.keys(stacks).flatMap(key => { const median = languages[key]; return (stacks[key] ?? 0) > 0 && median !== undefined ? [median] : []; }) : [];
  const referenceRub = stackValues.length ? Math.round((base + stackValues.reduce((sum, value) => sum + value, 0) / stackValues.length) / 2) : base;
  return compareSalary(referenceRub, fromThousands, toThousands, "habr_fallback", stackValues.length ? "direction_and_language" : "direction");
}
