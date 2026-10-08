/** Regression source for public-turn grouping and safe Markdown; no browser or provider calls. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { AgentsOrganizationMessage } from "@comit/shared";
import { AgentMarkdown, groupAgentMessages, thoughtLabel } from "../src/agent-messages";

const message = (id: string, role: AgentsOrganizationMessage["role"], content: string,
  tool_calls: AgentsOrganizationMessage["tool_calls"] = []): AgentsOrganizationMessage => ({
  message_id: id, chat_id: "chat", turn_id: "turn", role, content, tool_calls, sequence: 1,
  created_at: "2026-10-08T12:00:00Z",
});

test("public tool narration and final answer share one reply; raw tool results stay hidden", () => {
  const rows = groupAgentMessages([message("u", "user", "Вакансии"),
    message("a", "assistant", "Посмотрю вакансии", [{ id: "call" }]),
    message("t", "tool", "private payload"), message("f", "assistant", "**Две вакансии**")]);
  assert.equal(rows.length, 2);
  const reply = rows[1];
  assert.ok(reply && "messages" in reply);
  assert.deepEqual(reply.messages.map((item) => item.message_id), ["a", "f"]);
});

test("Markdown renders emphasis/lists/tables but excludes raw HTML and remote images", () => {
  const html = renderToStaticMarkup(<AgentMarkdown text={"**Ответ**\n\n- пункт\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n<script>alert(1)</script>\n\n![tracker](https://example.invalid/track)"} />);
  assert.match(html, /<strong>Ответ<\/strong>/);
  assert.match(html, /<li>пункт<\/li>/);
  assert.match(html, /<table>/);
  assert.equal(html.includes("<script"), false);
  assert.equal(html.includes("<img"), false);
});

test("thinking time uses Russian plural forms", () => {
  assert.equal(thoughtLabel(1), "Думал 1 секунду");
  assert.equal(thoughtLabel(22), "Думал 22 секунды");
  assert.equal(thoughtLabel(11, true), "Думаю 11 секунд");
});
