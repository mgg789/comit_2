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
  const owner=randomUUID(),other=randomUUID(),candidate=randomUUID(),legacyCandidate=randomUUID(),draft=randomUUID(),vacancy=randomUUID(),process=randomUUID();
  await pool.query("INSERT INTO accounts(id,email,role) VALUES($1,$2,'employer'),($3,$4,'candidate'),($5,$6,'employer'),($7,$8,'candidate')",
   [owner,`${owner}@example.test`,candidate,`${candidate}@example.test`,other,`${other}@example.test`,legacyCandidate,`${legacyCandidate}@example.test`]);
  await pool.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,published_at,business_status) VALUES($1,$2,$3,NULL,'open')",[draft,vacancy,owner]);
  await pool.query("INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,terms) VALUES($1,$2,$3,$4,$5,1,'sent',now(),'{}')",[process,owner,candidate,vacancy,draft]);
  for(const [revision,type] of [[1,'invitation.sent'],[2,'invitation.sent'],[3,'deadline.expired'],[4,'accept'],[5,'accept']] as const){
   await pool.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state,created_at) VALUES($1,$2,$3,$4,'sent','2026-10-07T12:00Z')",[randomUUID(),process,revision,type]);
  }
  const legacyProcess=randomUUID();
  await pool.query("INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,terms) VALUES($1,$2,$3,$4,$5,1,'sent',now(),'{}')",[legacyProcess,owner,legacyCandidate,vacancy,draft]);
  await pool.query("INSERT INTO employer_expenses(source_key,owner_id,vacancy_id,category,amount_kopeks,occurred_at,policy,reason) VALUES($1,$2,$3,'invitation',4900,'2026-10-07T12:00Z',$4,'invitation.sent')",
   [`invitation:${legacyProcess}`,owner,vacancy,JSON.stringify({version:'expense-v1',amount_kopeks:4900})]);
  await pool.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state,created_at) VALUES($1,$2,1,'accept','accepted','2026-10-07T12:00Z')",[randomUUID(),legacyProcess]);
  assert.equal((await pool.query("SELECT sum(amount_kopeks)::int AS n FROM employer_expenses WHERE source_key IN ($1,$2)",[`invitation:${process}`,`invitation-accept:${process}`])).rows[0].n,19600);
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM employer_expenses WHERE source_key=$1",[`invitation-accept:${process}`])).rows[0].n,1,
   "acceptance retries with distinct source events are charged once");
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM employer_expenses WHERE source_key=$1",[`invitation-accept:${legacyProcess}`])).rows[0].n,0,
   "accepting a v1 invitation does not apply the new tariff retroactively");
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
  await pool.query("UPDATE agents_service.drafts SET published_at='2026-10-08T12:00Z' WHERE draft_id=$1",[draft]);
  // Future settlements still use the permanent INSERT hook, extending the same daily allowance.
  const liveReservation=randomUUID();
  await pool.query("INSERT INTO agents_service.organization_usage_reservations(reservation_id,owner_id,upper_bound_micro_usd,status) VALUES($1,$2,200000,'captured')",[liveReservation,owner]);
  await pool.query("INSERT INTO agents_service.organization_usage_ledger(ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config,created_at) VALUES($1,$2,$3,1,0,60000,0,60000,'{}','2026-10-07T21:01Z')",[randomUUID(),liveReservation,owner]);
  const store=new EmployerStatisticsStore(pool,()=>new Date('2026-10-09T12:00Z'));
  const query={from:'2026-10-07',to:'2026-10-09'};
  const result=await store.get(owner,query);
  assert.equal(result.expenses.total_kopeks,104500);
  assert.deepEqual(result.expenses.categories.map(x=>x.amount_kopeks),[39800,24500,400,39800]);
  assert.deepEqual(result.expenses.tariffs,{publication_kopeks:39800,invitation_kopeks:19600,invitation_send_kopeks:4900,
   invitation_accept_kopeks:14700,deadline_penalty_kopeks:39800,ai_daily_allowance_kopeks:1000,ai_usd_rub:100});
  assert.equal((await store.get(other,query)).expenses.total_kopeks,0);
  assert.equal((await store.get(owner,{...query,vacancy_id:vacancy})).expenses.total_kopeks,104100);
  const before=(await pool.query('SELECT count(*)::int AS n FROM employer_expenses')).rows[0].n;
  await store.get(owner,query);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM employer_expenses')).rows[0].n,before);
  await pool.query('SELECT install_employer_expense_sources()');
  assert.equal((await store.get(owner,query)).expenses.total_kopeks,104500);
  const decision=randomUUID();
  await pool.query('SELECT record_support_feedback_expense($1,$2,$3,$4)',[owner,vacancy,decision,'Manual support adjudication of feedback']);
  await pool.query('SELECT record_support_feedback_expense($1,$2,$3,$4)',[owner,vacancy,decision,'Manual support adjudication of feedback']);
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM employer_expenses WHERE source_key=$1",[`support:${decision}`])).rows[0].n,1);
  assert.equal((await pool.query("SELECT amount_kopeks::int AS amount FROM employer_expenses WHERE source_key=$1",[`support:${decision}`])).rows[0].amount,39800);
  const client=await pool.connect();
  try {
   await client.query('BEGIN');
   await client.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,published_at,business_status) VALUES($1,$2,$3,now(),'open')",[randomUUID(),randomUUID(),owner]);
   await client.query('ROLLBACK');
  }finally{client.release();}
  assert.equal((await store.get(owner,query)).expenses.categories[0]?.events,1);
 }finally{await pool.end();}
});
