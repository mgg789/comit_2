/** Regression: autofilled or pasted email codes must keep every digit (api-core sends six). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { OTP_LENGTH, otpDigits } from "../src/email-login";

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
