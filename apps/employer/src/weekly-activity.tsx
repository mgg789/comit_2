import { useEffect, useState } from "react";

/** Accept the server-earned reward only before its exclusive UTC deadline. */
export function hasCurrentWeeklyActivity(expiresAt: string | null | undefined, now: number): boolean {
  if (!expiresAt) return false;
  const expiry = Date.parse(expiresAt);
  return Number.isFinite(expiry) && now < expiry && expiry - now <= 7 * 86400000;
}

/** Explain genuine weekly practice without claiming a grade change or active promotion. */
export function WeeklyActivity({ expiresAt }: { expiresAt: string | null | undefined }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!expiresAt) return;
    const delay = Date.parse(expiresAt) - Date.now();
    if (delay <= 0 || !Number.isFinite(delay) || delay > 7 * 86400000) return;
    const timer = window.setTimeout(() => setNow(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [expiresAt]);
  if (!hasCurrentWeeklyActivity(expiresAt, Math.max(now, Date.now()))) return null;
  return <span className="weekly-activity-flame" tabIndex={0} aria-label="Успешно выполнено задание этой недели">
    <svg aria-hidden="true" width="20" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2c1 5-3 5-3 9-1-1-2-2-2-4-4 4-5 7-4 10 1 4 4 6 8 6s8-3 8-7c0-5-4-8-7-14Zm-1 18c-2 0-3-1-3-3 0-2 2-3 3-5 0 2 3 3 3 5 0 2-1 3-3 3Z" /></svg>
    <span role="tooltip">Кандидат успешно выполнил задание текущей недели. Активность подтверждена сервером и действует до понедельника 00:00 UTC.</span>
  </span>;
}
