/** Scoped metadata transport regression; QGramm durability is covered by bridge acceptance. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RecruitmentView } from "@comit/shared";
import { enrichRecruitmentUnread } from "../src/chat/unread.js";

const view = (id: string, chatAvailable = true): RecruitmentView => ({
  id, owner_id: "owner", candidate_id: "candidate", vacancy_id: "vacancy", draft_id: "draft",
  profile_revision: 1, revision: 1, state: "sent", terms: null, invited_at: "2026-10-10T00:00:00Z",
  closure: null, interviews: [], offers: [], allowed_actions: [], chat_available: chatAvailable,
  chat_writable: chatAvailable, created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
});
const config = { url: "http://private.test", token: "synthetic-private-service-token" };

test("unread enrichment only sends authorized currently readable processes in one bounded batch", async () => {
  const visible = view("visible"), unavailable = view("unavailable", false), foreign = { ...view("foreign"), owner_id: "other" };
  let requests = 0;
  const transport: typeof fetch = async (_input, init) => {
    requests++;
    assert.deepEqual(JSON.parse(String(init?.body)), { chats: [{ process_id: "visible", owner_id: "owner", candidate_id: "candidate", actor_id: "owner" }] });
    return Response.json({ items: [{ process_id: "visible", read_sequence: 5, unread_count: 2 }] });
  };
  await enrichRecruitmentUnread([visible, unavailable, foreign], "employer", "owner", config, transport);
  assert.equal(requests, 1);
  assert.equal(visible.chat_unread_count, 2);
  assert.equal(unavailable.chat_unread_count, null);
  assert.equal(foreign.chat_unread_count, null);
});

test("unavailable, forged, duplicated or invalid private counters never become fake zero", async () => {
  const responses: unknown[] = [
    { items: [{ process_id: "foreign", read_sequence: 0, unread_count: 1 }] },
    { items: [{ process_id: "visible", read_sequence: 0, unread_count: -1 }] },
    { items: [{ process_id: "visible", read_sequence: 0, unread_count: 1.5 }] },
    { items: [{ process_id: "visible", read_sequence: 0, unread_count: Number.MAX_SAFE_INTEGER + 1 }] },
    { items: [{ process_id: "visible", read_sequence: 0, unread_count: 0 }, { process_id: "visible", read_sequence: 0, unread_count: 1 }] },
  ];
  for (const response of responses) {
    const item = view("visible");
    await enrichRecruitmentUnread([item], "employer", "owner", config, async () => Response.json(response));
    assert.equal(item.chat_unread_count, null);
  }
  for (const transport of [async () => { throw new Error("offline"); }, async () => new Response("x".repeat(65537))]) {
    const item = view("visible");
    await enrichRecruitmentUnread([item], "employer", "owner", config, transport);
    assert.equal(item.chat_unread_count, null);
  }
});

test("candidate counters preserve verified zero and lack of configuration stays unavailable", async () => {
  const item = view("visible");
  await enrichRecruitmentUnread([item], "candidate", "candidate", config, async (_input, init) => {
    assert.equal(JSON.parse(String(init?.body)).chats[0].actor_id, "candidate");
    return Response.json({ items: [{ process_id: "visible", read_sequence: 10, unread_count: 0 }] });
  });
  assert.equal(item.chat_unread_count, 0);
  await enrichRecruitmentUnread([item], "candidate", "candidate", null, async () => { throw new Error("must not call"); });
  assert.equal(item.chat_unread_count, null);
});
