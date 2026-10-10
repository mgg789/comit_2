/** Private metadata transport for the global badge; never fetches message contents. */
export interface UnreadChatGrant { process_id: string; owner_id: string; candidate_id: string; actor_id: string }
/** Count every authorized process in bounded batches; any invalid/outage batch makes the total unavailable. */
export async function fetchGlobalRecruitmentUnread(grants: UnreadChatGrant[],
  configuration: { url: string; token: string } | null, transport: typeof fetch): Promise<number | null> {
  if (!grants.length) return 0;
  if (!configuration) return null;
  let total = 0;
  try {
    for (let offset = 0; offset < grants.length; offset += 100) {
      const chats = grants.slice(offset, offset + 100);
      const response = await transport(new URL("/internal/chat/unread", configuration.url), {
        method: "POST", headers: { "content-type": "application/json", "x-internal-token": configuration.token },
        body: JSON.stringify({ chats }), signal: AbortSignal.timeout(2500), redirect: "error",
      });
      if (!response.ok) return null;
      const reader = response.body?.getReader();
      if (!reader) return null;
      const chunks: Uint8Array[] = []; let size = 0;
      while (true) {
        const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > 65536) { await reader.cancel(); return null; }
        chunks.push(part.value);
      }
      const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!parsed || typeof parsed !== "object" || !("items" in parsed) || !Array.isArray(parsed.items) || parsed.items.length !== chats.length) return null;
      const expected = new Set(chats.map(chat => chat.process_id)), seen = new Set<string>();
      for (const item of parsed.items as unknown[]) {
        if (!item || typeof item !== "object" || !("process_id" in item) || !("unread_count" in item) || !("read_sequence" in item)
          || typeof item.process_id !== "string" || !expected.has(item.process_id) || seen.has(item.process_id)
          || typeof item.unread_count !== "number" || !Number.isSafeInteger(item.unread_count) || item.unread_count < 0
          || typeof item.read_sequence !== "number" || !Number.isSafeInteger(item.read_sequence) || item.read_sequence < 0) return null;
        seen.add(item.process_id); total += item.unread_count;
        if (!Number.isSafeInteger(total)) return null;
      }
    }
    return total;
  } catch { return null; }
}
