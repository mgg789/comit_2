/** Calendar cell for a completed test count; null means the source is unavailable. */
export interface TestingActivityDay { date: string; count: number | null; }

/** Build the inclusive 30-day calendar in UTC from server day keys, padding preceding weekdays. */
export function testingActivityCalendar(days: readonly TestingActivityDay[]): Array<TestingActivityDay | null> {
  const start = days[0];
  if (!start) return [];
  const first = new Date(`${start.date}T00:00:00Z`);
  if (!Number.isFinite(first.getTime())) return [];
  const leading = (first.getUTCDay() + 6) % 7;
  return [...Array<null>(leading).fill(null), ...days];
}

/** Activity intensity uses counts rather than grade values; unavailable days have no intensity. */
export function testingActivityIntensity(count: number | null): number {
  if (count === null || count <= 0) return 0;
  return Math.min(4, Math.floor(count));
}
