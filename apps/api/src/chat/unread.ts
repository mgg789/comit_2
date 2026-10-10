/** Bounded metadata enrichment after recruitment ownership and consent checks. */
import type { AccountRole, RecruitmentView } from "@comit/shared";

/** Return caller-specific counters without suppressing lifecycle data on chat outages.
 * Inputs must come from RecruitmentStore's participant/processing-consent checks.
 * No message contents are fetched; null distinguishes unavailable from zero unread.
 */
export async function enrichRecruitmentUnread(views: RecruitmentView[], role: AccountRole, actorId: string,
  configuration: { url: string; token: string } | null, transport: typeof fetch): Promise<void> {
  for (const view of views) view.chat_unread_count = null;
  const available = views.filter(view => view.chat_available
    && (role === "candidate" ? view.candidate_id : view.owner_id) === actorId);
  if (!configuration || !available.length || available.length > 100) return;
  try {
    const response = await transport(new URL("/internal/chat/unread", configuration.url), {
      method: "POST", headers: { "content-type": "application/json", "x-internal-token": configuration.token },
      body: JSON.stringify({ chats: available.map(view => ({ process_id: view.id, owner_id: view.owner_id,
        candidate_id: view.candidate_id, actor_id: actorId })) }),
      signal: AbortSignal.timeout(2500), redirect: "error",
    });
    if (!response.ok) return;
    const reader = response.body?.getReader();
    if (!reader) return;
    const chunks: Uint8Array[] = []; let size = 0;
    while (true) {
      const part = await reader.read(); if (part.done) break;
      size += part.value.byteLength;
      if (size > 65536) { await reader.cancel(); return; }
      chunks.push(part.value);
    }
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!parsed || typeof parsed !== "object" || !("items" in parsed) || !Array.isArray(parsed.items)
      || parsed.items.length !== available.length) return;
    const counts = new Map<string, number>();
    const ids = new Set(available.map(view => view.id));
    for (const item of parsed.items as unknown[]) {
      if (!item || typeof item !== "object" || !("process_id" in item) || !("unread_count" in item)
        || !("read_sequence" in item) || typeof item.process_id !== "string" || !ids.has(item.process_id)
        || counts.has(item.process_id) || typeof item.unread_count !== "number" || !Number.isSafeInteger(item.unread_count)
        || item.unread_count < 0 || typeof item.read_sequence !== "number" || !Number.isSafeInteger(item.read_sequence)
        || item.read_sequence < 0) return;
      counts.set(item.process_id, item.unread_count);
    }
    for (const view of available) view.chat_unread_count = counts.get(view.id) ?? null;
  } catch { /* Independent private chat outage: preserve the validated recruitment snapshot. */ }
}
