/** Browser acceptance on an explicitly isolated fixture with synthetic devices only.
 * Requires a running testing UI, testing-browser-fixture and private synthetic storage state.
 * PLAYWRIGHT_MODULE can point to a separately installed Playwright runtime.
 */
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const meta=JSON.parse(await readFile(process.env.TESTING_FIXTURE_METADATA,'utf8'));
assert.equal(meta.email,'selftest@example.invalid','Only the isolated synthetic fixture is allowed');
const ui = process.env.TESTING_UI_URL ?? 'http://127.0.0.1:5185';
assert.ok(['127.0.0.1','localhost'].includes(new URL(ui).hostname));
const browser=await chromium.launch({headless:true,channel:'chrome',args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--auto-select-desktop-capture-source=Entire screen']});
try {
const c=await browser.newContext({storageState:process.env.TESTING_STORAGE_STATE,viewport:{width:1440,height:1000},permissions:['camera']});
const p=await c.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));p.on('dialog',dialog=>dialog.accept());
await p.goto(ui+'/testing/?subject=python&ready=1');
async function setup(){await p.getByRole('button',{name:'Включить камеру',exact:true}).click();await p.getByRole('button',{name:'Поделиться всем экраном'}).click();await p.getByRole('checkbox').nth(0).check();await p.getByRole('checkbox').nth(1).check();if(!await p.evaluate(()=>!!document.fullscreenElement)) await p.getByRole('button',{name:'Включить полный экран'}).click();await p.waitForTimeout(250);}
for (const width of [1440,390]) {
  await p.setViewportSize({width,height:1000});
  await p.getByRole('checkbox').first().waitFor();
  const boxes=await p.getByRole('checkbox').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {w:r.width,h:r.height}}));
  assert.ok(boxes.every(r=>Math.abs(r.w-r.h)<1 && r.w>=20),'Checkboxes must remain square and visible');
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow');
}
await p.setViewportSize({width:1440,height:1000});
await setup(); await p.getByRole('button',{name:/^(Начать|Продолжить попытку)$/}).click();await p.waitForTimeout(1500);
if (!await p.locator('.testing-indicators').count()) throw new Error('Test did not start: '+(await p.locator('body').innerText()).slice(-1200));
// The synthetic camera pattern may contain a face-like shape. Real model output may be green or yellow.
assert.equal(await p.locator('.testing-indicators [aria-label^="Камера:"].signal:not(.red)').first().count(),1);
const preflight=async()=> (await c.request.get(meta.apiUrl+'/candidate/testing/preflight?specialization=python')).json();
let pre=await preflight(); assert.equal(typeof pre.next_sequence,'number');const id=pre.active_session_id;assert.ok(id);
await p.waitForTimeout(10500);
const report=await preflight();assert.ok(report.next_sequence>0);assert.ok(report.active_supervision.total_seconds>0);
await p.reload();await setup();await p.getByRole('button',{name:'Продолжить попытку'}).click();await p.waitForTimeout(600);
pre=await preflight();assert.equal(pre.active_session_id,id);
const before=pre.next_sequence;
await p.waitForTimeout(5500);assert.ok((await preflight()).next_sequence>before);
// Lose a response after the real server has persisted the answer; retry must not count twice.
const bank=JSON.parse(await readFile(meta.mediaDir+'/items.json','utf8'));
const q=await (await c.request.get(meta.apiUrl+`/candidate/testing/sessions/${id}/next`)).json();
const item=bank.find(v=>v.item_id===q.task.item_id);
if(q.task.type==='mcq'){await p.getByRole('radio').nth(item.payload.answer_index).check();assert.equal(await p.locator('.testing-answers label.selected').count(),1);}else await p.getByRole('textbox').fill(item.payload.expected_answers[0]);
await p.route('**/answer',async route=>{await route.fetch();await route.abort('failed');await p.unroute('**/answer');});
await p.getByRole('button',{name:'Продолжить',exact:true}).click();await p.waitForTimeout(500);
assert.ok((await p.locator('.testing-errors').innerText()).includes('Не удалось'));
await p.getByRole('button',{name:'Продолжить',exact:true}).click();await p.waitForTimeout(500);
const state=await (await c.request.get(meta.apiUrl+`/candidate/testing/sessions/${id}/result`)).json();assert.equal(state.answered,1);
await p.getByRole('button',{name:'Закончить',exact:true}).click();await p.getByRole('button',{name:'Завершить',exact:true}).click();await p.getByRole('dialog').filter({hasText:'Попытка завершена'}).waitFor();
const final=await (await c.request.get(meta.apiUrl+`/candidate/testing/sessions/${id}/result`)).json();assert.equal(final.status,'abandoned');assert.equal(final.decided_grade,null);assert.ok(final.supervision);
console.log(JSON.stringify({resumeSameAttempt:true,sequenceResumed:true,supervisionPreserved:true,lostAnswerResponseIdempotent:true,abandon:true,errors}));
} finally { await browser.close(); }
