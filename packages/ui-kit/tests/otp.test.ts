/** Regression: autofilled or pasted email codes must keep every digit (api-core sends six). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { OTP_LENGTH, otpDigits, receiptCode } from "../src/email-login";

test("matches the api-core code length", () => {
  assert.equal(OTP_LENGTH, 6);
});

test("keeps a full autofilled or pasted code", () => {
  assert.equal(otpDigits("123456"), "123456");
  assert.equal(otpDigits("123 456"), "123456");
  assert.equal(otpDigits("Код: 12-34-56"), "123456");
});

test("ignores non-digit input and never grows past the code length", () => {
  assert.equal(otpDigits("abc"), "");
  assert.equal(otpDigits("12345678"), "123456");
  assert.equal(otpDigits("4"), "4");
});


test("each dev receipt autofills its own code and production or malformed receipts clear it", () => {
  const receipt = { challenge_id: "first", resend_after: "2026-01-01T00:00:00Z" };
  assert.equal(receiptCode({ ...receipt, dev_code: "012345" }), "012345");
  assert.equal(receiptCode({ ...receipt, challenge_id: "second", dev_code: "987654" }), "987654");
  assert.equal(receiptCode(receipt), "");
  for (const dev_code of ["12345", "1234567", "12 3456", "abcdef"]) {
    assert.equal(receiptCode({ ...receipt, dev_code }), "");
  }
});
