/** Private QGramm deletion boundary; the service owns encrypted records and attachment bytes. */
export interface PurgeChatContext {
  process_id: string;
  owner_id: string;
  candidate_id: string;
}

/** Erase one complete process chat idempotently; reject missing wiring or an invalid acknowledgement. */
export async function purgeChat(context: PurgeChatContext, transport: typeof fetch,
  url?: string, token?: string): Promise<void> {
  if (!url || !token) throw new Error("chat_deletion_unavailable");
  const response = await transport(new URL("/internal/chat/purge", url), {
    method: "POST", headers: { "content-type": "application/json", "x-internal-token": token },
    body: JSON.stringify({ ...context, actor_id: context.owner_id }),
    signal: AbortSignal.timeout(10000), redirect: "error",
  });
  if (!response.ok) throw new Error("chat_deletion_unavailable");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("chat_deletion_invalid_response");
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const item = await reader.read();
    if (item.done) break;
    size += item.value.byteLength;
    if (size > 1024) { await reader.cancel(); throw new Error("chat_deletion_invalid_response"); }
    chunks.push(item.value);
  }
  const acknowledgement: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!acknowledgement || typeof acknowledgement !== "object" || !("deleted" in acknowledgement)
    || acknowledgement.deleted !== true) throw new Error("chat_deletion_invalid_response");
}
