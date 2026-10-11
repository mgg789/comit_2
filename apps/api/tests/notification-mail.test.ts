/** Rendering, trusted links and provider fencing are checked without SMTP or account side effects. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import { notificationContent, notificationSiteUrl, type NotificationMail } from "../src/notifications/template.js";
import { NotificationWorker } from "../src/notifications/worker.js";
import type { NotificationJob, NotificationJobStore } from "../src/notifications/store.js";
import { createSmtpMailer, MailDeliveryError, type MailTransport } from "../src/auth/mail.js";
import { runMailWorker } from "../src/auth/mail-worker.js";

const base: NotificationMail = { job_id: randomUUID(), email: "synthetic@example.test", kind: "resume_liked", destination: "https://comit.example.test/candidate/" };

test("four Figma event types render accessible HTML/plain text with bundled MIME assets", () => {
  const events: NotificationMail[] = [
    { ...base, kind: "monthly_task", task: "Алгоритмы" },
    { ...base, kind: "employer_interview", candidate: "Синтетический кандидат", starts_at: "2030-10-08T09:45:00Z", time_zone: "Europe/Moscow" },
    { ...base, kind: "candidate_interview", company: "Синтетическая компания", starts_at: "2030-10-08T09:45:00Z", time_zone: "Europe/Moscow" },
    { ...base, company: "Синтетическая компания" },
  ];
  for (const event of events) {
    const rendered = notificationContent(event);
    assert.match(rendered.html, /<table role="presentation"/);
    assert.match(rendered.html, /Это не реклама/);
    assert.match(rendered.text, /Это не реклама/);
    assert.equal(rendered.html.includes(event.email), false);
    assert.equal(rendered.html.includes(event.job_id), false);
    assert.equal(rendered.attachments.length, event.kind.endsWith("interview") ? 7 : 2);
    assert.equal(/<script|<iframe|src="https?:/i.test(rendered.html), false);
    for (const asset of rendered.attachments) assert.ok(rendered.html.includes(`cid:${asset.cid}`));
  }
  assert.match(notificationContent(events[0]!).html, /Пройти задание/);
  assert.match(notificationContent(events[1]!).html, /12:45/);
  assert.match(notificationContent(events[1]!).text, /Europe\/Moscow/);
  assert.match(notificationContent(events[2]!).text, /Вам назначили собеседование/);
  for (const event of events.slice(1, 3)) {
    assert.match(notificationContent(event).html, /Открыть собеседование<\/a>/);
    assert.ok(notificationContent(event).html.includes(`href="${event.destination}"`));
  }
  assert.ok(notificationContent({ ...events[1]!, destination: "https://comit.example.test/employer/?a=1&b=2" }).html.includes('href="https://comit.example.test/employer/?a=1&amp;b=2"'));
  assert.match(notificationContent(events[3]!).html, /В личный кабинет/);
});

test("names and URLs cannot inject HTML; malformed destinations and dates fail closed", () => {
  assert.match(notificationContent({ ...base, company: '<img src=x onerror="bad"> & test' }).html, /&lt;img src=x onerror=&quot;bad&quot;&gt; &amp; test/);
  assert.throws(() => notificationContent({ ...base, destination: "javascript:alert(1)" }));
  assert.throws(() => notificationContent({ ...base, destination: "https://user:pass@example.test/" }));
  assert.throws(() => notificationContent({ ...base, destination: "http://example.test/" }));
  assert.throws(() => notificationContent({ ...base, kind: "candidate_interview", starts_at: "not a date", time_zone: "UTC" }));
  assert.throws(() => notificationContent({ ...base, company: "bad\nname" }));
  assert.match(notificationContent(base).text, /одной из компаний/);
});

test("site origin is trusted configuration only, production requires HTTPS", () => {
  assert.throws(() => notificationSiteUrl(undefined));
  assert.throws(() => notificationSiteUrl("https://example.test/path"));
  assert.throws(() => notificationSiteUrl("http://127.0.0.1:5177"));
  assert.equal(notificationSiteUrl("http://127.0.0.1:5177", false).origin, "http://127.0.0.1:5177");
  assert.throws(() => notificationSiteUrl("https://example.test/?redirect=bad"));
  assert.equal(notificationSiteUrl("https://example.test").href, "https://example.test/");
});

test("notification mail reuses SMTP transport with stable retry message id", async () => {
  const sent: Parameters<MailTransport["sendMail"]>[0][] = [];
  const mailer = createSmtpMailer({ SMTP_HOST: "smtp.test", SMTP_USER: "u", SMTP_PASSWORD: "p" }, {
    sendMail: async options => { sent.push(options); }, verify: async () => true,
  });
  await mailer.sendNotification(base);
  await mailer.sendNotification(base);
  assert.equal(sent[0]?.messageId, sent[1]?.messageId);
  assert.match(sent[0]?.messageId ?? "", /comit-notification-/);
  assert.equal(sent[0]?.to, base.email);
});

test("worker refreshes source before send, cancelled consent skips transport and retries are fenced", async () => {
  for (const cancelled of [false, true]) {
    const calls: string[] = [];
    const job: NotificationJob = { ...base, lease_token: randomUUID() };
    const store: NotificationJobStore = {
      reconcileLikes: async () => { calls.push("reconcile"); },
      claim: async () => { calls.push("claim"); return job; },
      refresh: async () => { calls.push("refresh"); return cancelled ? null : job; },
      finish: async (_id, token) => { calls.push(`finish:${token}`); },
      fail: async () => { assert.fail("should not fail"); },
    };
    await new NotificationWorker(store, { sendNotification: async () => { calls.push("send"); } }).processOnce();
    assert.deepEqual(calls, cancelled ? ["reconcile", "claim", "refresh"] : ["reconcile", "claim", "refresh", "send", `finish:${job.lease_token}`]);
    store.fail = async (id, token, code, retryable) => {
      assert.equal(id, job.job_id); assert.equal(token, job.lease_token);
      assert.equal(code, "rejected"); assert.equal(retryable, false);
    };
    if (!cancelled) await new NotificationWorker(store, { sendNotification: async () => { throw new MailDeliveryError("rejected", false); } }).processOnce();
  }
});

test("event-source outage cannot stop the existing OTP polling worker", async () => {
  const stop = new AbortController();
  let otpClaims = 0;
  const store: NotificationJobStore = {
    reconcileLikes: async () => { throw new Error("private database detail"); }, claim: async () => null,
    refresh: async () => null, finish: async () => {}, fail: async () => {},
  };
  await runMailWorker({ claimMail: async () => { otpClaims++; if (otpClaims === 2) stop.abort(); return null; }, finishMail: async () => {}, failMail: async () => {} },
    { send: async () => {}, verify: async () => true }, { pollMs: 100, stop: stop.signal, notifications: new NotificationWorker(store, { sendNotification: async () => {} }) });
  assert.equal(otpClaims, 2);
});
