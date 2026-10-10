/** Hiring clocks count whole weekday hours, pausing on Saturday/Sunday in Moscow. */
export const HIRING_TIME_ZONE = "Europe/Moscow" as const;
const DAY = 86_400_000;
const OFFSET = 3 * 3_600_000;
const weekend = (local: number) => [0, 6].includes(new Date(local).getUTCDay());
const midnightAfter = (local: number) => Math.floor(local / DAY) * DAY + DAY;

/** Add 24-hour working days; weekends pause the clock, public holidays are not excluded. */
export function addWorkingDays(start: Date | string, days: number): Date {
  const instant = new Date(start).getTime();
  if (!Number.isFinite(instant) || !Number.isInteger(days) || days < 0 || days > 30) throw new RangeError("invalid_business_interval");
  let local = instant + OFFSET, remaining = days * DAY;
  while (remaining > 0) {
    const boundary = midnightAfter(local);
    if (weekend(local)) { local = boundary; continue; }
    const consumed = Math.min(remaining, boundary - local);
    local += consumed; remaining -= consumed;
  }
  return new Date(local - OFFSET);
}

/** Remaining weekday milliseconds for displaying a server deadline; never triggers transitions. */
export function workingMillisecondsBetween(start: Date | string, end: Date | string): number {
  let local = new Date(start).getTime() + OFFSET;
  const until = new Date(end).getTime() + OFFSET;
  if (!Number.isFinite(local) || !Number.isFinite(until) || until - local > 366 * 10 * DAY) throw new RangeError("invalid_business_interval");
  let result = 0;
  while (local < until) {
    const boundary = Math.min(midnightAfter(local), until);
    if (!weekend(local)) result += boundary - local;
    local = boundary;
  }
  return result;
}
