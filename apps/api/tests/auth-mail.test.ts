/** SMTP mailer and lease-fenced worker tests; no external provider or credentials are used. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { MailDeliveryError, MailConfigurationError, SmtpMailer, createConfiguredMailer, createSmtpMailer } from "../src/auth/mail.js";
import { MailWorker, type MailJob, type MailJobStore } from "../src/auth/mail-worker.js";

const job: MailJob = {
  job_id: "job-1", lease_token: "lease-1", email: "synthetic@example.test", code: "123456", role: "candidate",
  challenge_id: "challenge-1", expires_at: "2030-01-02T03:04:00.000Z",
};

test("SMTP config fails closed and defaults only documented values", () => {
  assert.throws(() => createSmtpMailer({}), MailConfigurationError);
  const mailer = createSmtpMailer({ SMTP_HOST: "smtp.test", SMTP_USER: "u", SMTP_PASSWORD: "p" }, {
    sendMail: async () => undefined, verify: async () => true,
  });
  assert.ok(mailer);
  assert.equal(createConfiguredMailer({}), null);
});

test("mailer renders Russian role and expiry without links or challenge id", async () => {
  let sent: { to: string; text: string; from: string } | undefined;
  const mailer = createSmtpMailer({ SMTP_HOST: "smtp.test", SMTP_USER: "u", SMTP_PASSWORD: "p" }, {
    sendMail: async options => { sent = options; }, verify: async () => true,
  });
  await mailer.send(job);
  assert.equal(sent?.to, job.email);
  assert.equal(sent?.from, "Comit <noreply@mail.droidje.com>");
  assert.match(sent?.text ?? "", /кандидата/);
  assert.match(sent?.text ?? "", /123456/);
  assert.match(sent?.text ?? "", /UTC/);
  assert.equal(sent?.text.includes(job.challenge_id), false);
  assert.equal(sent?.text.includes("http://"), false);
  assert.equal(sent?.text.includes("https://"), false);
});

test("mailer sanitizes rejected and unavailable provider failures", async () => {
  const rejected = new SmtpMailer({ host: "smtp.test", port: 587, user: "u", password: "p", from: "f", tlsServername: "smtp.test" }, {
    sendMail: async () => { throw Object.assign(new Error("provider secret"), { responseCode: 550, response: "private" }); },
    verify: async () => false,
  });
  await assert.rejects(() => rejected.send(job), (error: unknown) => {
    assert.ok(error instanceof MailDeliveryError);
    assert.equal(error.code, "rejected");
    assert.equal(error.retryable, false);
    assert.equal(error.message.includes("provider"), false);
    return true;
  });
  assert.equal(await rejected.verify(), false);

  const unavailable = new SmtpMailer({ host: "smtp.test", port: 587, user: "u", password: "p", from: "f", tlsServername: "smtp.test" }, {
    sendMail: async () => { throw Object.assign(new Error("provider secret"), { code: "ETIMEDOUT" }); },
    verify: async () => true,
  });
  await assert.rejects(() => unavailable.send(job), (error: unknown) => {
    assert.ok(error instanceof MailDeliveryError);
    assert.equal(error.code, "smtp_unavailable");
    assert.equal(error.retryable, true);
    assert.equal(error.message.includes("provider"), false);
    return true;
  });
});

test("worker guards before SMTP and fences finish/failure by lease", async () => {
  const calls: string[] = [];
  const store: MailJobStore = {
    async claimMail() { calls.push("claim"); return job; },
    async assertMailLease(id, lease) { calls.push(`guard:${id}:${lease}`); },
    async finishMail(id, lease) { calls.push(`finish:${id}:${lease}`); },
    async failMail(id, lease, code, retryable) { calls.push(`fail:${id}:${lease}:${code}:${retryable}`); },
  };
  const worker = new MailWorker(store, { send: async message => { calls.push(`send:${message.code}`); }, verify: async () => true });
  assert.equal(await worker.processOnce(), true);
  assert.deepEqual(calls, ["claim", "guard:job-1:lease-1", "send:123456", "finish:job-1:lease-1"]);
});

test("worker persists permanent rejection without retry and transient SMTP failure with retry", async () => {
  for (const [failure, expected] of [
    [new MailDeliveryError("rejected", false), "rejected:false"],
    [new MailDeliveryError("smtp_unavailable", true), "smtp_unavailable:true"],
  ] as const) {
    const failures: string[] = [];
    const store: MailJobStore = {
      async claimMail() { return job; },
      async finishMail() {},
      async failMail(_id, _lease, code, retryable) { failures.push(`${code}:${retryable}`); },
    };
    const worker = new MailWorker(store, {
      async send() { throw failure; },
      async verify() { return true; },
    });
    assert.equal(await worker.processOnce(), true);
    assert.deepEqual(failures, [expected]);
  }
});
