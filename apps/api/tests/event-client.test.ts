/** Typed SSE client keeps bearer authentication and advances only processed durable cursors. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError, createApiClient } from "../../../packages/api-client/src/index.js";
import type { ChatEvent } from "@comit/shared";

test("event client processes chunked Unicode messages, hidden cursors and bounded reconnect", async () => {
  const original = globalThis.fetch;
  const message = { id: "message", sequence: 2, sender_id: "candidate", created_at: "2026-10-07T00:00:00Z", text: "Привет", files: [] };
  const body = `retry: 1000\n\nid: 2\nevent: message.created\ndata: ${JSON.stringify({ sequence: 2, type: "message.created", message })}\n\nid: 5\nevent: cursor\ndata: {"sequence":5,"type":"cursor"}\n\nevent: reconnect\ndata: {"after_sequence":5}\n\n`;
  try {
    globalThis.fetch = async (input, init) => {
      assert.equal(String(input), "http://api.test/candidate/recruitment/process/chat/events?after_sequence=1");
      assert.equal(new Headers(init?.headers).get("authorization"), "Bearer synthetic-token");
      const bytes = new TextEncoder().encode(body);
      return new Response(new ReadableStream({ start(controller) {
        for (let index = 0; index < bytes.length; index += 7) controller.enqueue(bytes.slice(index, index + 7));
        controller.close();
      } }), { headers: { "content-type": "text/event-stream" } });
    };
    const events: ChatEvent[] = [];
    const client = createApiClient({ baseUrl: "http://api.test", getAuthToken: () => "synthetic-token" });
    const cursor = await client.streamChatEvents("candidate", "process", async event => { events.push(event); }, { afterSequence: 1 });
    assert.equal(cursor, 5); assert.deepEqual(events.map(event => event.sequence), [2, 5]);
    assert.equal(events[0]?.type === "message.created" && events[0].message.text, "Привет");
    globalThis.fetch = async () => new Response("event: error\ndata: {\"status\":410,\"code\":\"chat_resync_required\"}\n\n", { headers: { "content-type": "text/event-stream" } });
    await assert.rejects(client.streamChatEvents("candidate", "process", () => undefined), error => error instanceof ApiError && error.status === 410);
    globalThis.fetch = async () => new Response("event: recruitment.changed\ndata: {\"sequence\":1,\"type\":\"recruitment.changed\"}\n\n", { headers: { "content-type": "text/event-stream" } });
    await assert.rejects(client.streamChatEvents("candidate", "process", () => undefined), error => error instanceof ApiError && error.status === 503);
  } finally { globalThis.fetch = original; }
});
