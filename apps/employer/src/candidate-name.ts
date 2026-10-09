/**
 * Public candidate name. Matching publishes anonymous profiles as `Candidate <uuid>`
 * (names and contacts stay private until an invitation is accepted, TZ §2.2); they are
 * shown as a short readable handle so the raw identifier never wraps across a card.
 */
export function candidateName(displayName: string | null | undefined, fallback = "Кандидат"): string {
  const name = displayName?.trim();
  if (!name) return fallback;
  const anonymous = /^Candidate ([0-9a-f]{8})-[0-9a-f-]{27}$/i.exec(name);
  return anonymous?.[1] ? `Кандидат ${anonymous[1].slice(0, 4).toUpperCase()}` : name;
}
