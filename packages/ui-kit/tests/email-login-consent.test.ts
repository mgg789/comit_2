/** Both sign-in methods stay unavailable until the processing consent is accepted. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { EmailLogin } from "../src/email-login.js";

test("initial email action and dev Yandex button are disabled before consent", () => {
  const html = renderToStaticMarkup(createElement(EmailLogin, {
    requestCode: async () => { throw new Error("must not call API while rendering"); },
    verifyCode: async () => { throw new Error("must not call API while rendering"); },
    onLogin: () => {}, errorText: () => "error", oauthRole: "candidate", oauthEnabled: false,
  }));
  assert.match(html, /<button[^>]*disabled=""[^>]*aria-label="Получить код"/);
  assert.match(html, /<button[^>]*class="yandex"[^>]*disabled=""[^>]*title="Вход через Яндекс отключён в dev"/);
  assert.match(html, /aria-label="Согласие на обработку данных"/);
});
