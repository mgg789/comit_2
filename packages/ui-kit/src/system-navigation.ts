/** Only internal known application routes may be opened from notification metadata. */
export function systemNotificationPath(value: string): string | null {
  return /^\/(?:resume|settings|offers|vacancies|statistics)$/.test(value) || /^\/(?:offer|chat|vacancy)\/[a-zA-Z0-9-]+(?:\/resumes)?$/.test(value) ? value : null;
}

