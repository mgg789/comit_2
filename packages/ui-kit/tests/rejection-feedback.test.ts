import assert from "node:assert/strict";
import { test } from "node:test";
import { rejectionFeedbackError } from "../src/rejection-feedback";

test("detailed feedback requires length, word count and a valid unique category", () => {
  const feedback = "Для этой вакансии нам требуется опыт самостоятельной разработки и поддержки крупных проектов с большим числом пользователей.";
  assert.equal(rejectionFeedbackError(feedback, ["experience"]), null);
  assert.match(rejectionFeedbackError("x".repeat(100), ["experience"]), /10 слов/);
  assert.match(rejectionFeedbackError("a b c d e f g h i j", ["experience"]), /80/);
  assert.match(rejectionFeedbackError(feedback, []), /причину/);
  assert.match(rejectionFeedbackError(feedback, ["experience", "experience"]), /причину/);
  assert.match(rejectionFeedbackError(" ".repeat(80) + "short", ["experience"]), /80/);
});
