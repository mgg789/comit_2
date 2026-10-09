/** Salary text (TZ §2.2: the range is always shown) and public error messages. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "@comit/api-client";
import { errorText, salary } from "../src/runtime";

test("shows the mandatory salary range in thousands of rubles", () => {
  assert.equal(salary(180000, 240000), "180–240к ₽");
  assert.equal(salary(150500, 230000), "150,5–230к ₽");
});

test("says honestly when a bound is missing instead of inventing one", () => {
  assert.equal(salary(null, 240000), "Зарплата не указана");
  assert.equal(salary(180000, undefined), "Зарплата не указана");
});

test("maps known API failures to user text without leaking upstream payloads", () => {
  assert.equal(errorText(new ApiError(401, "/employer/x", { detail: "x" })), "Сессия закончилась. Войдите снова.");
  assert.equal(errorText(new ApiError(403, "/employer/x", {})), "Нет доступа к этим данным.");
  assert.match(errorText(new ApiError(409, "/employer/x", { detail: "vacancy_lifecycle_pending" })), /завершается в сервисах/);
  const unknown = errorText(new ApiError(500, "/employer/x", { detail: "SELECT * FROM secrets" }));
  assert.doesNotMatch(unknown, /secrets/);
  assert.equal(errorText(new Error("boom")), unknown);
});
