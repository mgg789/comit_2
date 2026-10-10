import type { AgentsOrganizationBudget } from "@comit/shared";

/**
 * Share of the rolling AI budget still available, 0–100 whole percent.
 * Micro-USD amounts arrive as decimal strings; reserves are already excluded
 * from `available_micro_usd`. Returns null when no limit is configured.
 */
export function budgetRemainingPercent(
  budget: Pick<AgentsOrganizationBudget, "limit_micro_usd" | "available_micro_usd">,
): number | null {
  const limit = Number(budget.limit_micro_usd);
  if (!(limit > 0)) return null;
  const share = Math.round((Number(budget.available_micro_usd) / limit) * 100);
  return Number.isFinite(share) ? Math.max(0, Math.min(100, share)) : null;
}
