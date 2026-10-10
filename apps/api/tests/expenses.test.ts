/** Real PostgreSQL accounting: source retries, daily allowance, ownership, and manual support decisions. */
import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {readFile,readdir} from "node:fs/promises";
import {test} from "node:test";
import {Pool} from "pg";
import {migrate} from "../src/auth/migrate.js";
import {EmployerStatisticsStore} from "../src/statistics/store.js";
const url=process.env.EXPENSE_TEST_DATABASE_URL;
test("durable source accounting is idempotent, transactional, owner-scoped and respects Moscow daily allowance",{skip:!url},async()=>{
 const pool=new Pool({connectionString:url});
 try {
  await migrate(pool);
  for(const folder of ["../../../services/matching-engine/src/matching_engine/migrations/","../../../services/agents-service/src/agents_service/migrations/"]){
   const dir=new URL(folder,import.meta.url);
   for(const name of (await readdir(dir)).filter(n=>n.endsWith(".sql") && n!=="011_employer_expense_hooks.sql").sort()) await pool.query(await readFile(new URL(name,dir),"utf8"));
  }
  const owner=randomUUID(),other=randomUUID(),candidate=randomUUID(),draft=randomUUID(),vacancy=randomUUID(),process=randomUUID();
  await pool.query("INSERT INTO accounts(id,email,role) VALUES($1,$2,'employer'),($3,$4,'candidate'),($5,$6,'employer')",[owner,`${owner}@example.test`,candidate,`${candidate}@example.test`,other,`${other}@example.test`]);
  await pool.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,published_at,business_status) VALUES($1,$2,$3,'2026-10-07T12:00Z','open')",[draft,vacancy,owner]);
  await pool.query("UPDATE agents_service.drafts SET published_at='2026-10-08T12:00Z' WHERE draft_id=$1",[draft]);
  await pool.query("INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,terms) VALUES($1,$2,$3,$4,$5,1,'sent',now(),'{}')",[process,owner,candidate,vacancy,draft]);
  for(const [revision,type] of [[1,'invitation.sent'],[2,'invitation.sent'],[3,'deadline.expired']] as const){
   await pool.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state,created_at) VALUES($1,$2,$3,$4,'sent','2026-10-07T12:00Z')",[randomUUID(),process,revision,type]);
  }
  await pool.query("INSERT INTO agents_service.organization_chats(owner_id,chat_id) VALUES($1,$2)",[owner,randomUUID()]);
  // Reservations and exact provider settlement are actual service source rows.
  for(const [at,cost] of [['2026-10-07T20:59Z',60000],['2026-10-07T20:59:30Z',60000],['2026-10-07T21:00Z',60000]] as const){
   const reservation=randomUUID();
   await pool.query(`INSERT INTO agents_service.organization_usage_reservations(reservation_id,owner_id,upper_bound_micro_usd,input_upper_bound_micro_usd,output_upper_bound_micro_usd,status)
     VALUES($1,$2,200000,200000,0,'captured')`,[reservation,owner]);
   await pool.query(`INSERT INTO agents_service.organization_usage_ledger(ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config,created_at)
    VALUES($1,$2,$3,1,0,$4,0,$4,'{}',$5)`,[randomUUID(),reservation,owner,cost,at]);
  }
  // API migrated before source schemas: settlements already exist when agents 011 installs hooks.
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM employer_expenses WHERE category='ai'")).rows[0].n,0);
  await pool.query(await readFile(new URL("../../../services/agents-service/src/agents_service/migrations/011_employer_expense_hooks.sql",import.meta.url),"utf8"));
  assert.equal((await pool.query("SELECT sum(amount_kopeks)::int AS n FROM employer_expenses WHERE category='ai'")).rows[0].n,200);
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM pg_trigger WHERE tgname='ai_expense_backfill'")).rows[0].n,0);
  // Future settlements still use the permanent INSERT hook, extending the same daily allowance.
  const liveReservation=randomUUID();
  await pool.query("INSERT INTO agents_service.organization_usage_reservations(reservation_id,owner_id,upper_bound_micro_usd,status) VALUES($1,$2,200000,'captured')",[liveReservation,owner]);
  await pool.query("INSERT INTO agents_service.organization_usage_ledger(ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config,created_at) VALUES($1,$2,$3,1,0,60000,0,60000,'{}','2026-10-07T21:01Z')",[randomUUID(),liveReservation,owner]);
  const store=new EmployerStatisticsStore(pool,()=>new Date('2026-10-09T12:00Z'));
  const query={from:'2026-10-07',to:'2026-10-09'};
  const result=await store.get(owner,query);
  assert.equal(result.expenses.total_kopeks,45100);
  assert.deepEqual(result.expenses.categories.map(x=>x.amount_kopeks),[19900,4900,400,19900]);
  assert.equal((await store.get(other,query)).expenses.total_kopeks,0);
  assert.equal((await store.get(owner,{...query,vacancy_id:vacancy})).expenses.total_kopeks,44700);
  const before=(await pool.query('SELECT count(*)::int AS n FROM employer_expenses')).rows[0].n;
  await store.get(owner,query);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM employer_expenses')).rows[0].n,before);
  await pool.query('SELECT install_employer_expense_sources()');
  assert.equal((await store.get(owner,query)).expenses.total_kopeks,45100);
  const decision=randomUUID();
  await pool.query('SELECT record_support_feedback_expense($1,$2,$3,$4)',[owner,vacancy,decision,'Manual support adjudication of feedback']);
  await pool.query('SELECT record_support_feedback_expense($1,$2,$3,$4)',[owner,vacancy,decision,'Manual support adjudication of feedback']);
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM employer_expenses WHERE source_key=$1",[`support:${decision}`])).rows[0].n,1);
  const client=await pool.connect();
  try {
   await client.query('BEGIN');
   await client.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,published_at,business_status) VALUES($1,$2,$3,now(),'open')",[randomUUID(),randomUUID(),owner]);
   await client.query('ROLLBACK');
  }finally{client.release();}
  assert.equal((await store.get(owner,query)).expenses.categories[0]?.events,1);
 }finally{await pool.end();}
});
