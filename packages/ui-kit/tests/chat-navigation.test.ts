import { strict as assert } from "node:assert";
import { test } from "node:test";
import { chatReturnRoute, withChatOrigin } from "../src/chat-navigation.js";

test("conversation preserves the exact caller including filters after reload", () => {
  const origin = "/vacancy/one/resumes?filter=liked&search=react";
  const route = withChatOrigin("/chat/process", origin, "/vacancies");
  assert.equal(chatReturnRoute(route, "/chat"), origin);
  assert.equal(withChatOrigin("/chat/process", "/chat", "/vacancies"), "/chat/process?from=%2Fchat");
  assert.equal(withChatOrigin("/chat/second", route, "/vacancies"), "/chat/second?from=" + encodeURIComponent(origin));
  assert.equal(withChatOrigin("/offer/process", origin, "/resume"), "/offer/process");
});

test("untrusted and recursive return URLs stay inside the cabinet", () => {
  for (const origin of ["https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/chat/other", "/resume#broken", "/resume\u0000"]) {
    assert.equal(chatReturnRoute("/chat/p?from=" + encodeURIComponent(origin), "/chat"), "/chat");
  }
  assert.equal(chatReturnRoute("/chat/process", "/chat"), "/chat");
});
