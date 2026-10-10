import fs from "node:fs";
import pg from "../apps/api/node_modules/pg/lib/index.js";
import { AuthStore } from "../apps/api/dist/auth/store.js";

const { Pool } = pg;

// This helper is an IPC-only mailbox. It never writes to stdout/stderr: the
// configured descriptor is owned by the Python acceptance process and carries
// one claimed job privately.
const input = JSON.parse(fs.readFileSync(0, "utf8"));
if (typeof input.email !== "string" || !input.email.endsWith("@example.test")
  || !/^postgres(?:ql)?:\/\/[^@]+@(?:127\.0\.0\.1|localhost):25433\/comit_profile_acceptance(?:_[a-z0-9]+)*$/.test(String(process.env.DATABASE_URL))) {
  throw new Error("mailbox_target_rejected");
}
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
try {
  const store = new AuthStore(pool, process.env.AUTH_SECRET);
  const deadline = Date.now() + 10_000;
  let job = null;
  while (Date.now() < deadline) {
    const candidate = await store.claimMail();
    if (candidate && candidate.email === input.email && candidate.challenge_id === input.challenge_id) {
      job = candidate;
      break;
    }
    if (!candidate) await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (!job) throw new Error("mailbox_timeout");
  fs.writeSync(Number(process.env.PROFILE_MAILBOX_FD), JSON.stringify(job));
} finally {
  await pool.end();
}
