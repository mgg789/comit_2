/** Candidate resume import never accepts a browser-selected owner or persists verification. */
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { AuthStore, type AuthAccount, type AccountRole } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { startPythonHttpFixture } from "./helpers/python-http-fixture.js";
import { candidateResumeImportRoutes } from "../src/routes/candidate-resume-import.js";

class TestStore extends AuthStore {
  override async session(role: AccountRole, accountId: string): Promise<AuthAccount | null> {
    return { id: accountId, role, email: "test@example.invalid", email_verified: true, created_at: new Date().toISOString(), consent: { processing: true, version: "v1", accepted_at: new Date().toISOString() } };
  }
}
test("import requires candidate/Origin/consent and projects only editable fields", async () => {
  const config = loadAuthConfig({ AUTH_SECRET: "synthetic-resume-browser-secret-32chars", AUTH_ALLOWED_ORIGINS: "http://localhost", AUTH_COOKIE_SECURE: "false" });
  const pool = new Pool(); mock.method(pool, "query", async () => ({ rows: [] }));
  const app = Fastify(); const calls: RequestInit[] = [];
  try {
    await app.register(cookie);
    await app.register(candidateResumeImportRoutes, { pool, config, store: new TestStore(pool, config.secret), mailConfigured: false, serviceToken: "private-test-token", transport: async (_url, options) => {
      calls.push(options!); return Response.json({ fields: { display_name: "Anna", fsp_id: "forbidden", evidence: {} }, extracted_text: "Resume text", method: "pdf_text", warnings: [] });
    } });
    const payload = { filename: "resume.pdf", content_base64: "dGVzdA==", processing_consent: true };
    const owner = "12345678-1234-4234-8234-123456789abc";
    const token = await signSession(config, "candidate", owner, "a".repeat(43), new Date(Date.now() + 60000).toISOString());
    const headers = { authorization: `Bearer ${token}`, origin: "http://localhost" };
    const send = (body: unknown, incoming = headers) => app.inject({ method: "POST", url: "/candidate/profile/import", headers: incoming, payload: body });
    assert.equal((await app.inject({ method: "POST", url: "/candidate/profile/import", payload })).statusCode, 401);
    assert.equal((await send({ ...payload, processing_consent: false })).statusCode, 400);
    assert.equal((await send(payload, { ...headers, origin: "https://evil.invalid" })).statusCode, 403);
    assert.equal(calls.length, 0);
    const response = await send(payload);
    assert.equal(response.statusCode, 200); assert.equal(response.headers["cache-control"], "no-store");
    assert.deepEqual(response.json().fields, { display_name: "Anna" });
    const forwarded = new Headers(calls[0]!.headers);
    assert.equal(forwarded.get("x-owner-id"), owner); assert.equal(forwarded.get("authorization"), null); assert.equal(forwarded.get("cookie"), null);
  } finally { await app.close(); await pool.end(); mock.restoreAll(); }
});

/** Build a standards-compliant synthetic text PDF, including real cross-reference offsets. */
function textPdf(): Buffer {
  const content = "BT /F1 18 Tf 30 700 Td (Name: Anna Petrova) Tj 0 -30 Td (City: Moscow) Tj 0 -30 Td (Senior frontend developer) Tj 0 -30 Td (Work experience) Tj 0 -30 Td (Synthetic company - frontend developer) Tj ET";
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", `<< /Length ${content.length} >>\nstream\n${content}\nendstream`];
  let pdf = "%PDF-1.4\n";
  const offsets = objects.map((object, index) => { const offset = Buffer.byteLength(pdf); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; return offset; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.map(offset => String(offset).padStart(10, "0") + " 00000 n ").join("\n")}\ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf);
}

test("candidate API parses actual PDF through private Python HTTP service", async () => {
  const serviceToken = "synthetic-pdf-http-service-token";
  const parser = await startPythonHttpFixture("agents-service", { AGENTS_SERVICE_TOKEN: serviceToken });
  const config = loadAuthConfig({ AUTH_SECRET: "synthetic-resume-http-secret-32chars", AUTH_ALLOWED_ORIGINS: "http://localhost", AUTH_COOKIE_SECURE: "false" });
  const pool = new Pool();
  const app = Fastify();
  try {
    await app.register(cookie);
    await app.register(candidateResumeImportRoutes, { pool, config, store: new TestStore(pool, config.secret), mailConfigured: false, serviceUrl: parser.url, serviceToken });
    const token = await signSession(config, "candidate", "12345678-1234-4234-8234-123456789abc", "b".repeat(43), new Date(Date.now() + 60000).toISOString());
    const headers = { authorization: `Bearer ${token}`, origin: "http://localhost" };
    const payload = { filename: "synthetic.pdf", content_base64: textPdf().toString("base64"), processing_consent: true };
    assert.equal((await fetch(parser.url + "/internal/candidate-resume/parse", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) })).status, 401);
    const response = await app.inject({ method: "POST", url: "/candidate/profile/import", headers, payload });
    assert.equal(response.statusCode, 200, response.body);
    assert.equal(response.headers["cache-control"], "no-store");
    const parsed = response.json();
    assert.equal(parsed.method, "pdf_text");
    assert.equal(parsed.fields.display_name, "Anna Petrova");
    assert.equal(parsed.fields.city, "Moscow");
    assert.equal(parsed.fields.primary_direction, "frontend");
    assert.equal(parsed.fields.declared_grade, "senior");
    assert.match(parsed.fields.experience[0].title, /Synthetic company/);
    assert.equal(parsed.fields.evidence, undefined);
    const invalid = await app.inject({ method: "POST", url: "/candidate/profile/import", headers, payload: { ...payload, content_base64: Buffer.from("invalid PDF").toString("base64") } });
    assert.equal(invalid.statusCode, 422, invalid.body);
    assert.equal(invalid.json().detail, "resume_pdf_invalid");
  } finally { await app.close(); await pool.end(); await parser.close(); }
});
