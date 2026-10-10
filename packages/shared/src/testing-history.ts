/** Owner-only calendar of trusted confirmed test results, never unfinished attempts. */
export interface CandidateTestingHistory {
  timezone: "Europe/Moscow";
  period: { from: string; to: string };
  kind: "confirmed_results";
  daily: Array<{ date: string; count: number }>;
  total: number;
}
