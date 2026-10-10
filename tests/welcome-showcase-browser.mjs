/** Public landing, anonymous catalog, cursor pagination and role modal regression. */
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({channel:'chrome',headless:true});
const page = await browser.newPage({viewport:{width:1920,height:1080}});
const origin=process.env.WELCOME_URL || 'http://127.0.0.1:5176/';
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const requests=[];let fail=false,empty=false;
const vacancy={vacancy_id:'10000000-0000-4000-8000-000000000010',title:'Backend-разработчик',company_name:'Тестовая компания',city:'Москва',salary:{from_rub:180000,to_rub:240000},work_types:['remote'],schedule:'5/2',category:{specialization:'backend',grade:'middle'},stacks:['go','postgres'],published_at:'2026-10-10T01:00:00Z'};
const candidate={candidate_id:'a'.repeat(64),display_name:'Специалист',city:'Москва',salary:{from_rub:150000,to_rub:220000},category:{specialization:'frontend',grade:'middle',grade_status:'self_reported'},stacks:['react'],published_at:'2026-10-10T01:00:00Z'};
await page.route('**/api/**',async route=>{
 const url=new URL(route.request().url());requests.push(url);
 if(!/^\/api\/public\/showcase\/(vacancies|candidates)$/.test(url.pathname))return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'unauthenticated'})});
 if(fail)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'showcase_unavailable'})});
 const isVac=url.pathname.endsWith('vacancies'),second=!!url.searchParams.get('cursor');
 const item=isVac?{...vacancy,...second?{vacancy_id:'10000000-0000-4000-8000-000000000011',title:'Go-разработчик'}:{}}:candidate;
 return route.fulfill({contentType:'application/json',body:JSON.stringify({items:empty?[]:[item],limit:24,has_more:!second&&!empty,next_cursor:!second&&!empty?'sealed-test-cursor':null})});
});
await mkdir('/tmp/comit-welcome-browser',{recursive:true});
try {
 await page.goto(origin);await page.getByRole('link',{name:'К витрине COMIT',exact:true}).waitFor();
 const candidateHref=await page.getByRole('link',{name:'К поиску работы',exact:true}).getAttribute('href');const employerHref=await page.getByRole('link',{name:'К панели управления',exact:true}).getAttribute('href');
 assert.ok(new URL(candidateHref).pathname==='/candidate/');assert.ok(new URL(employerHref).pathname==='/employer/');
 assert.equal(requests.length,0);
 await page.getByRole('link',{name:'К витрине COMIT',exact:true}).click();await page.getByRole('button',{name:'Открыть вакансию Backend-разработчик',exact:true}).waitFor();
 assert.equal(await page.locator('.nav-item.selected').getAttribute('aria-label'),'Вакансии');
 await page.getByRole('button',{name:'Открыть вакансию Backend-разработчик',exact:true}).click();
 let dialog=page.getByRole('dialog');await dialog.waitFor();assert.deepEqual(await dialog.locator('a').allTextContents(),['Войти как кандидат','Войти как работодатель']);
 assert.equal(await dialog.locator('a').first().getAttribute('href'),candidateHref);assert.equal(await dialog.locator('a').last().getAttribute('href'),employerHref);
 await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
 assert.equal(requests.filter(url=>/resume.pdf|candidates\/[^?]+$|vacancies\//.test(url.pathname)).length,0);
 await page.getByRole('button',{name:'Показать ещё',exact:true}).click();await page.getByRole('button',{name:'Открыть вакансию Go-разработчик',exact:true}).waitFor();assert.equal(await page.locator('.vacancy-card').count(),2);
 await page.locator('.public-filters .custom-select').first().getByRole('button').click();await page.getByRole('option',{name:'Backend-разработчик',exact:true}).click();
 await page.waitForResponse(r=>r.url().includes('specialization=backend'));await page.getByRole('button',{name:'Открыть вакансию Backend-разработчик',exact:true}).waitFor();
 await page.getByRole('textbox',{name:'Поиск по витрине'}).fill('Go');await page.waitForResponse(r=>r.url().includes('q=Go'));await page.reload();await page.getByRole('button',{name:'Открыть вакансию Backend-разработчик',exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'Поиск по витрине'}).inputValue(),'Go');
 assert.ok(requests.some(url=>url.searchParams.get('specialization')==='backend'&&url.searchParams.get('q')==='Go'));
 await page.getByRole('button',{name:'Соискатели',exact:true}).click();await page.getByRole('button',{name:'Открыть профиль Frontend-разработчик',exact:true}).waitFor();
 assert.equal(await page.locator('.nav-item.selected').getAttribute('aria-label'),'Соискатели');await page.getByRole('button',{name:'Открыть профиль Frontend-разработчик',exact:true}).click();dialog=page.getByRole('dialog');await dialog.waitFor();assert.deepEqual(await dialog.locator('a').allTextContents(),['Войти как работодатель','Войти как кандидат']);await dialog.getByRole('button',{name:'Отмена',exact:true}).click();
 for(const width of [1920,1024,390]){
  await page.setViewportSize({width,height:1080});await page.waitForTimeout(400);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:`/tmp/comit-welcome-browser/candidates-${width}.png`,fullPage:true});
  await page.getByRole('button',{name:'Вакансии',exact:true}).click();await page.getByRole('button',{name:'Открыть вакансию Backend-разработчик',exact:true}).waitFor();await page.waitForTimeout(400);if(width===390) assert.ok(await page.locator('.vacancy-card').first().evaluate(card=>card.getBoundingClientRect().width>=innerWidth-42));assert.ok(await page.locator('.vacancy-card h3').first().evaluate(title=>title.clientHeight>=title.scrollHeight-1));await page.screenshot({path:`/tmp/comit-welcome-browser/vacancies-${width}.png`,fullPage:true});
  await page.getByRole('button',{name:'Соискатели',exact:true}).click();await page.getByRole('button',{name:'Открыть профиль Frontend-разработчик',exact:true}).waitFor();
 }
 fail=true;await page.reload();await page.getByRole('alert').waitFor();fail=false;empty=true;await page.getByRole('button',{name:'Обновить',exact:true}).click();await page.getByRole('heading',{name:'Пока нет опубликованных резюме',exact:true}).waitFor();
 await page.getByRole('link',{name:'Войти',exact:true}).click();await page.getByRole('link',{name:'К витрине COMIT',exact:true}).waitFor();await page.getByRole('link',{name:'К витрине COMIT',exact:true}).click();await page.getByRole('link',{name:'Comit — главная',exact:true}).waitFor();await page.getByRole('link',{name:'Comit — главная',exact:true}).click();await page.getByRole('link',{name:'К поиску работы',exact:true}).waitFor();
 await page.screenshot({path:'/tmp/comit-welcome-browser/landing-390.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS public entry links, role modal order, metadata-only API, cursor paging, server filter/persistence, tabs, errors/empty, root links, 1920/1024/390');
}finally{await browser.close();}
