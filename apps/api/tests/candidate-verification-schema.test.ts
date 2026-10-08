import assert from "node:assert/strict";
import { test } from "node:test";
import { candidateVerificationRequestSchema } from "../src/routes/candidate-verification.js";
import { decodeVerificationDocument } from "../src/routes/candidate-verification.js";

test("verification request schema requires CAS and separates provider origins", () => {
  assert.deepEqual(candidateVerificationRequestSchema.required, ["expected_revision"]);
  assert.deepEqual(candidateVerificationRequestSchema.properties.origin.enum, ["mock", "live_unbound"]);
  assert.equal(candidateVerificationRequestSchema.additionalProperties, false);
});

test("document base64 decoder rejects noncanonical and over-limit payloads", () => {
  assert.deepEqual(decodeVerificationDocument(Buffer.from("proof").toString("base64")), Buffer.from("proof"));
  assert.throws(() => decodeVerificationDocument("YWJj\n"), /invalid_document_encoding/);
  assert.throws(() => decodeVerificationDocument("A".repeat(14_000_000)), /invalid_document_encoding/);
});
