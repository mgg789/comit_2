/** Explicit local demo source seeder. Never creates login challenges, SMTP jobs, test grades or hiring events. */
import { readFileSync } from "node:fs";
import { Pool } from "pg";
import type { CandidateProfileFields } from "@comit/shared";
import { ProfileStore } from "../src/profiles/store.js";
import { EmployerProfileStore } from "../src/employers/store.js";
import { ProfileWorker } from "../src/profiles/worker.js";

type RecordIdentity = { id: string; key: string; email: string };
type Catalog = { version: string; synthetic: true; candidates: Array<RecordIdentity & { fields: CandidateProfileFields }>; employers: Array<RecordIdentity & { company_name: string }> };
const marker = "comit-demo-2026-v1";
const databaseUrl = process.env.DATABASE_URL || "";
const address = new URL(databaseUrl);
const target = process.env.COMIT_DEMO_TARGET || "";
if (!["localhost", "127.0.0.1", "host.docker.internal"].includes(address.hostname) || !/^comit_demo_[a-zA-Z0-9_]+$/.test(target) || address.pathname !== `/${target}`) throw new Error("explicit local COMIT_DEMO_TARGET is required");
const pool = new Pool({ connectionString: databaseUrl });
const profiles = new ProfileStore(pool);
try {
  const foreign = await pool.query<{ count: number }>("SELECT count(*)::int AS count FROM accounts WHERE processing_consent_version IS DISTINCT FROM $1", [marker]);
  if (foreign.rows[0]?.count !== 0) throw new Error("demo seeding refuses a database with non-demo accounts");
  if (process.argv[2] === "--project") {
    const count = Number(process.argv[3]);
    const url = new URL(process.env.MATCHING_ENGINE_URL || "");
    if (!["localhost", "127.0.0.1", "host.docker.internal"].includes(url.hostname) || !Number.isSafeInteger(count) || count < 1 || count > 1000) throw new Error("bounded local matching projection required");
    const worker = new ProfileWorker(profiles, { engineUrl: url.toString(), token: process.env.CANDIDATE_PIPELINE_TOKEN || "" });
    let processed = 0;
    while (processed < count && await worker.processOnce()) processed += 1;
    process.stdout.write(JSON.stringify({ projection_passes: processed }) + "\n");
  } else {
    const catalog: Catalog = JSON.parse(readFileSync(0, "utf8"));
    if (catalog.version !== marker || catalog.synthetic !== true || !Array.isArray(catalog.candidates) || !Array.isArray(catalog.employers) || catalog.candidates.length > 240 || catalog.employers.length > 16) throw new Error("invalid demo catalog");
    const known = [...catalog.candidates, ...catalog.employers];
    if (known.some(item => !/^[a-z0-9-]+@comit-demo\.invalid$/.test(item.email) || !/^[0-9a-f-]{36}$/.test(item.id))) throw new Error("invalid synthetic identity");
    async function account(item: RecordIdentity, role: "candidate" | "employer"): Promise<void> {
      await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
        VALUES($1,$2,$3,true,$4,now()) ON CONFLICT(id) DO NOTHING`, [item.id, item.email, role, marker]);
      const row = (await pool.query<{ email: string; role: string; processing_consent_version: string }>("SELECT email,role,processing_consent_version FROM accounts WHERE id=$1", [item.id])).rows[0];
      if (!row || row.email !== item.email || row.role !== role || row.processing_consent_version !== marker) throw new Error("demo identity collision");
    }
    let created = 0;
    for (const item of catalog.candidates) {
      await account(item, "candidate");
      if (await profiles.get(item.id)) continue;
      await profiles.save(item.id, { expected_revision: 0, fields: item.fields, consent: { processing: true, pool: true, training: false, version: "profile-v1" } });
      created += 1;
    }
    const employers = new EmployerProfileStore(pool);
    for (const item of catalog.employers) {
      await account(item, "employer");
      if ((await employers.get(item.id)).revision) continue;
      await employers.update(item.id, { expected_revision: 0, company_name: item.company_name });
    }
    process.stdout.write(JSON.stringify({ source_candidates_created: created, synthetic_accounts: known.length, mail_jobs_created: 0 }) + "\n");
  }
} finally { await pool.end(); }
