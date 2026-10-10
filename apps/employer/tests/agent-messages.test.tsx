/** Regression source for public-turn grouping and safe Markdown; no browser or provider calls. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { AgentsOrganizationMessage } from "@comit/shared";
import { AGENT_HISTORY_START, AgentMarkdown, AgentReply, agentHistoryProgress, groupAgentMessages, thoughtLabel } from "../src/agent-messages";

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

test("history starts before sequence 0 so the first question of a new chat is fetched", () => {
  // The server pages with sequence > cursor and numbers a new chat from 0.
  const first = { ...message("q", "user", "Первый вопрос"), sequence: 0 };
  assert.ok(first.sequence > AGENT_HISTORY_START);
  assert.deepEqual(agentHistoryProgress({ items: [first], next_sequence: null }, AGENT_HISTORY_START), { cursor: 0, hasMore: false });
});

test("terminal full page advances the live cursor and finishes history recovery", () => {
  const items = Array.from({ length: 100 }, (_, index) => ({ ...message(String(index), "user", "test"), sequence: index + 1 }));
  assert.deepEqual(agentHistoryProgress({ items, next_sequence: null }, 0), { cursor: 100, hasMore: false });
  assert.deepEqual(agentHistoryProgress({ items: [], next_sequence: null }, 100), { cursor: 100, hasMore: false });
  assert.deepEqual(agentHistoryProgress({ items, next_sequence: 100 }, 0), { cursor: 100, hasMore: true });
});

test("active reply uses Navix three-dot capsule and hides unfinished tool narration", () => {
  const html = renderToStaticMarkup(<AgentReply group={{ id: "turn", startedAt: "2026-10-08T12:00:00Z",
    messages: [message("step", "assistant", "Проверю вакансии", [{ id: "call" }])] }} active now={Date.parse("2026-10-08T12:00:23Z")} />);
  assert.equal((html.match(/class="agent-think-dot"/g) || []).length, 3);
  assert.match(html, /Агент думает/);
  assert.equal(html.includes("Думаю 23"), false);
  assert.equal(html.includes("Проверю вакансии"), false);
});

test("finished reply replaces waiting capsule with measured pill and actual answer", () => {
  const final = { ...message("final", "assistant", "**Готово**"), created_at: "2026-10-08T12:00:23Z" };
  const html = renderToStaticMarkup(<AgentReply group={{ id: "turn", startedAt: "2026-10-08T12:00:00Z", messages: [final] }}
    active={false} now={Date.parse("2026-10-08T13:00:00Z")} />);
  assert.match(html, /Думал 23 секунды/);
  assert.match(html, /<strong>Готово<\/strong>/);
  assert.equal(html.includes("agent-think-dot"), false);
  assert.equal(html.includes("<summary>"), false);
});

test("reduced motion renders a finished answer immediately without entrance opacity", () => {
  const html = renderToStaticMarkup(<AgentReply group={{ id: "turn", startedAt: null, messages: [message("final", "assistant", "Готово")] }}
    active={false} now={0} reducedMotion />);
  assert.match(html, /agent-final-answer/);
  assert.equal(html.includes("opacity:0"), false);
});

test("finished tool turn has no expandable thinking, narration, or rating controls", () => {
  const html = renderToStaticMarkup(<AgentReply group={{ id: "turn", startedAt: "2026-10-08T12:00:00Z", messages: [
    message("step", "assistant", "Посмотрю список вакансий", [{ id: "call" }]),
    { ...message("final", "assistant", "Две вакансии"), created_at: "2026-10-08T12:00:06Z" },
  ] }} active={false} now={0} reducedMotion />);
  assert.match(html, /Думал 6 секунд/);
  assert.match(html, /Две вакансии/);
  for (const hidden of ["<details", "<summary", "Посмотрю список вакансий", "Ответ помог", "Ответ не помог"]) {
    assert.equal(html.includes(hidden), false, hidden);
  }
});
