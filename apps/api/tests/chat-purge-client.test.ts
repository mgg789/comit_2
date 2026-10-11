/** QGramm purge acknowledgement and transport failures must prevent source deletion. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { purgeChat } from "../src/chat/client.js";

test("purge sends a private owner-derived context and accepts only deleted=true", async () => {
  const context = { process_id: "process", owner_id: "owner", candidate_id: "candidate" };
  for (const response of [Response.json({ deleted: true }, { status: 503 }), Response.json({ deleted: false }),
    Response.json({}), new Response("x".repeat(1025)), new Response("invalid json")]) {
    await assert.rejects(purgeChat(context, async () => response, "http://chat.internal", "private-token"));
  }
  await assert.rejects(purgeChat(context, fetch), /unavailable/);
  await purgeChat(context, async (input, init) => {
    assert.equal(String(input), "http://chat.internal/internal/chat/purge");
    assert.equal(new Headers(init?.headers).get("x-internal-token"), "private-token");
    assert.deepEqual(JSON.parse(String(init?.body)), { ...context, actor_id: context.owner_id });
    return Response.json({ deleted: true });
  }, "http://chat.internal", "private-token");
});
