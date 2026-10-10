/** Weekday clocks and hiring mutations against explicitly isolated PostgreSQL databases. */
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {test} from 'node:test';
import {Pool} from 'pg';
import {addWorkingDays,workingMillisecondsBetween,type RecruitmentAction,type RecruitmentView} from '@comit/shared';
import {migrate} from '../src/auth/migrate.js';
import {ProfileStore} from '../src/profiles/store.js';
import {RecruitmentStore,validateRejectionFeedback} from '../src/recruitment/store.js';
import {SystemNotificationsStore} from '../src/notifications/system-store.js';
const databaseUrl=process.env.RECRUITMENT_DEADLINES_TEST_DATABASE_URL;
if(databaseUrl){const u=new URL(databaseUrl);if(u.hostname!=='127.0.0.1'||u.port!=='41033'||!/^\/hiring_test_[a-z0-9_]+$/.test(u.pathname))throw new Error('isolated_database_required');}
const feedback='The candidate needs more production experience designing distributed services and debugging API integrations before this technical role would be suitable.';
type Fields<T=RecruitmentAction>=T extends RecruitmentAction?Omit<T,'expected_revision'|'idempotency_key'>:never;
test('working clocks exclude Moscow weekends including weekend starts',()=>{
 assert.equal(addWorkingDays('2026-10-09T09:00:00Z',4).toISOString(),'2026-10-15T09:00:00.000Z');
 assert.equal(addWorkingDays('2026-10-10T09:00:00Z',1).toISOString(),'2026-10-12T21:00:00.000Z');
 assert.equal(workingMillisecondsBetween('2026-10-09T09:00:00Z','2026-10-12T09:00:00Z'),86400000);
 assert.equal(workingMillisecondsBetween('2026-10-10T09:00:00Z','2026-10-11T09:00:00Z'),0);
 assert.throws(()=>addWorkingDays('invalid',4));
});
test('rejection feedback requires meaningful prose and canonical categories',()=>{
 assert.throws(()=>validateRejectionFeedback('Too short',['experience']));
 assert.throws(()=>validateRejectionFeedback('x'.repeat(100),['experience']));
 assert.throws(()=>validateRejectionFeedback(feedback));
 assert.throws(()=>validateRejectionFeedback(feedback,['experience','experience']));
 validateRejectionFeedback(feedback,['experience']);
});
test('durable clock transitions, raced expiry, messages and accepted-offer projection',{skip:!databaseUrl},async t=>{
 const pool=new Pool({connectionString:databaseUrl});const profiles=new ProfileStore(pool),notifications=new SystemNotificationsStore(pool);
 const store=new RecruitmentStore(pool,true,'synthetic-clock-secret',undefined,{initialWorkingDays:4,reminderWorkingDays:3,hireOn:'offer_accept'},notifications);
 const fixture=async()=>{
  const owner=randomUUID(),candidate=randomUUID();
  for(const [id,role]of[[owner,'employer'],[candidate,'candidate']])await pool.query('INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)',[id,`${id}@example.test`,role]);
  await profiles.save(candidate,{expected_revision:0,fields:{experience:[{title:'Prior experience',description:'Preserved work'}]},consent:{processing:true,pool:true,version:'profile-v1'}});
  await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=$1",[candidate]);
  const terms={company_name:'Synthetic company',description:'Position',salary:{from_rub:100000,to_rub:200000},contact_method:'platform' as const,vacancy_title:'Backend engineer',city:'Москва',daily:'Development',work_type:'remote'};
  let view=await store.invite(owner,{candidate_id:candidate,idempotency_key:randomUUID(),description:'Position'},{draft_id:randomUUID(),vacancy_id:randomUUID(),candidate_id:candidate,profile_revision:11,terms});
  const act=async(role:'candidate'|'employer',fields:Fields,given:RecruitmentView=view)=>{view=await store.act(role,role==='candidate'?candidate:owner,given.id,{...fields,expected_revision:given.revision,idempotency_key:randomUUID()});return view;};
  view=await act('candidate',{action:'accept'});return{owner,candidate,view,act};
 };
 try{await migrate(pool);
  await t.test('one reminder and one expiry, including a late competing action',async()=>{
   const f=await fixture();assert.equal(f.view.deadline?.business_days,4);
   await pool.query("UPDATE recruitment_processes SET deadline=jsonb_set(deadline,'{remind_at}',to_jsonb((now()-interval '1 minute')::text)) WHERE id=$1",[f.view.id]);
   await Promise.all([store.processDeadlines(),store.processDeadlines()]);
   assert.equal((await store.events('candidate',f.candidate)).items.filter(e=>e.type==='deadline.reminder').length,1);
   const current=await store.get('candidate',f.candidate,f.view.id);
   await pool.query("UPDATE recruitment_processes SET deadline=jsonb_set(deadline,'{due_at}',to_jsonb((now()-interval '1 minute')::text)) WHERE id=$1",[f.view.id]);
   const late=store.act('employer',f.owner,f.view.id,{action:'reject',reason:feedback,categories:['experience'],expected_revision:current.revision,idempotency_key:randomUUID()});
   const result=await Promise.allSettled([store.processDeadlines(),late]);assert.equal(result[1]?.status,'rejected');
   const expired=await store.get('candidate',f.candidate,f.view.id);assert.equal(expired.state,'expired');assert.equal(expired.closure?.actor,'system');assert.equal(expired.deadline,null);
   assert.equal((await store.events('candidate',f.candidate)).items.filter(e=>e.type==='deadline.expired').length,1);
   assert.equal((await notifications.page('candidate',f.candidate)).items.filter(e=>e.type==='deadline.expired').length,1);
  });
  await t.test('only first successful employer message starts seven days',async()=>{
   const f=await fixture();await assert.rejects(()=>store.withChatGrant('employer',f.owner,f.view.id,true,async()=>{throw new Error('offline');},true));
   await store.withChatGrant('candidate',f.candidate,f.view.id,true,async()=>true,true);
   await store.withChatGrant('employer',f.owner,f.view.id,true,async()=>true,false);
   assert.equal((await store.get('employer',f.owner,f.view.id)).deadline?.stage,'initial');
   await store.withChatGrant('employer',f.owner,f.view.id,true,async()=>true,true);
   const first=await store.get('employer',f.owner,f.view.id);assert.equal(first.deadline?.business_days,7);
   await store.withChatGrant('employer',f.owner,f.view.id,true,async()=>true,true);
   assert.deepEqual((await store.get('employer',f.owner,f.view.id)).deadline,first.deadline);
  });
  await t.test('scheduled interview end starts three days without manual completion',async()=>{
   const f=await fixture();let view=await f.act('employer',{action:'interview_propose',kind:'technical',starts_at:new Date(Date.now()+86400000).toISOString(),time_zone:'Europe/Moscow',duration_minutes:30,format:'remote',location:'https://example.test/meeting'});
   assert.equal(view.deadline?.stage,'conversation');view=await f.act('candidate',{action:'interview_accept',interview_id:view.interviews[0]!.id});assert.equal(view.deadline?.due_at,null);
   await pool.query("UPDATE recruitment_processes SET deadline=jsonb_set(deadline,'{paused_until}',to_jsonb((now()-interval '1 minute')::text)) WHERE id=$1",[view.id]);
   await store.processDeadlines();view=await store.get('candidate',f.candidate,view.id);assert.equal(view.deadline?.stage,'after_interview');assert.equal(view.deadline?.business_days,3);
  });
  await t.test('inactive vacancy suspends expiry and another proposed meeting preserves an agreed pause',async()=>{
   const f=await fixture();let open=false;
   const restricted=new RecruitmentStore(pool,true,'synthetic-clock-secret',async()=>open,undefined,notifications);
   let view=await restricted.get('candidate',f.candidate,f.view.id);assert.equal(view.deadline,null);
   assert.equal((await pool.query<{deadline_suspended:boolean}>('SELECT deadline_suspended FROM recruitment_processes WHERE id=$1',[view.id])).rows[0]!.deadline_suspended,true);
   open=true;view=await restricted.get('candidate',f.candidate,view.id);assert.equal(view.deadline?.business_days,7);
   view=await f.act('employer',{action:'interview_propose',kind:'technical',starts_at:new Date(Date.now()+86400000).toISOString(),time_zone:'Europe/Moscow',duration_minutes:30,format:'remote',location:'https://example.test/meeting'},view);
   view=await f.act('candidate',{action:'interview_accept',interview_id:view.interviews[0]!.id},view);
   const pause=view.deadline?.paused_until;
   view=await f.act('employer',{action:'interview_propose',kind:'general',starts_at:new Date(Date.now()+2*86400000).toISOString(),time_zone:'Europe/Moscow',duration_minutes:30,format:'remote',location:'https://example.test/meeting'},view);
   assert.equal(view.deadline?.paused_until,pause);
   const second=view.interviews.find(i=>i.kind==='general')!;view=await f.act('candidate',{action:'interview_accept',interview_id:second.id},view);
   view=await f.act('employer',{action:'interview_cancel',interview_id:second.id,reason:'Changed availability'},view);
   assert.equal(view.deadline?.paused_until,pause);assert.equal(view.deadline?.stage,'interview_wait');
  });
  await t.test('offer acceptance once appends work and queues exact withdrawal',async()=>{
   const f=await fixture();const optIn=await profiles.get(f.candidate);await profiles.save(f.candidate,{expected_revision:optIn!.revision,fields:{},consent:{processing:true,pool:true,version:'profile-v1',training:true,training_version:'training-v2'}});const sent=await f.act('employer',{action:'offer_send',salary_rub:150000,description:'Synthetic offer',expires_at:new Date(Date.now()+86400000).toISOString()});
   assert.equal((await profiles.get(f.candidate))?.consents.pool,true);
   const command:RecruitmentAction={action:'offer_accept',offer_id:sent.offers[0]!.id,expected_revision:sent.revision,idempotency_key:randomUUID()};
   const [one,two]=await Promise.all([store.act('candidate',f.candidate,sent.id,command),store.act('candidate',f.candidate,sent.id,command)]);assert.deepEqual(one,two);assert.equal(one.state,'hired');
   const profile=await profiles.get(f.candidate);assert.equal(profile?.consents.pool,false);assert.equal(profile?.consents.processing,true);assert.equal(profile?.consents.training,false);assert.equal(profile?.consents.training_version,null);assert.equal(profile?.fields.experience?.length,2);assert.equal(profile?.fields.experience?.[1]?.id,sent.id);assert.equal(profile?.projection.source_revision,profile?.revision);
   const payload=(await pool.query<{payload:Record<string,unknown>}>('SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=$2',[f.candidate,profile!.revision])).rows[0]!.payload;
   assert.equal((payload.consents as {pool:boolean}).pool,false);
   assert.equal((await notifications.page('candidate',f.candidate)).items.filter(e=>e.type==='candidate.hired').length,1);
  });
 }finally{await pool.end();}
});
