/** Synthetic-only shared login regression checks preserve candidate role and consent. */
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({channel:'chrome',headless:true});
const context = await browser.newContext({viewport:{width:1920,height:1080}});
let codeCalls=0,verifyCalls=0,authenticated=false;
const account={id:'10000000-0000-4000-8000-000000000001',email:'shared-ui@example.test',role:'candidate',email_verified:true,created_at:'2026-10-09T00:00:00Z',consent:{processing:true,version:'account-v1',accepted_at:'2026-10-09T00:00:00Z'}};
await context.route('**/api/**',async route=>{
 const req=route.request(),path=new URL(req.url()).pathname;
 const reply=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 if(path==='/api/auth/candidate/me')return reply(authenticated?account:{detail:'unauthorized'},authenticated?200:401);
 if(path==='/api/auth/candidate/code'){codeCalls++;assert.equal(req.postDataJSON().email,account.email);return reply({challenge_id:'synthetic-login-challenge',resend_after:'2099-01-01T00:00:00Z',expires_at:'2099-01-01T00:00:00Z'});}
 if(path==='/api/auth/candidate/verify'){verifyCalls++;assert.deepEqual(req.postDataJSON(),{email:account.email,challenge_id:'synthetic-login-challenge',code:'123456',consent:{processing:true,version:'account-v1'}});authenticated=true;return reply({account,token_type:'Bearer',expires_at:'2099-01-01T00:00:00Z'});}
 if(path==='/api/employer/vacancies')return reply({items:[],next_cursor:null});
 return reply({detail:'synthetic_test_unavailable'},503);
});
const page=await context.newPage(),errors=[];
page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/");
 await page.getByLabel('Email',{exact:true}).fill(account.email);
 await page.getByRole('button',{name:'Получить код',exact:true}).click();
 assert.equal(codeCalls,0);
 await page.getByText('Отметьте согласие на обработку данных, чтобы продолжить.',{exact:true}).waitFor();
 await page.getByRole('checkbox').check();
 await page.getByRole('button',{name:'Войти через Яндекс',exact:true}).click();
 await page.getByText('Вход через Яндекс пока не подключён. Войдите по почте.',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Получить код',exact:true}).click();
 await page.getByLabel('Код подтверждения из письма, 6 цифр').waitFor();
 assert.equal(codeCalls,1);
 assert.equal(await page.locator('.otp-digit').count(),6);
 for(const width of [1920,1024,390]){
   await page.setViewportSize({width,height:1080});
   const dims=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));
   assert.ok(dims.scroll<=dims.width+1,JSON.stringify(dims));
 }
 await page.getByLabel('Код подтверждения из письма, 6 цифр').fill('123456');
 await page.getByRole('button',{name:'Продолжить',exact:true}).click();
 await page.getByRole('navigation',{name:'Навигация кандидата'}).waitFor();
 assert.equal(verifyCalls,1);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'passed',checks:['consent gates real callback','unavailable Yandex explicit','six digit OTP','candidate role callback binding','1920/1024/390 no overflow']}));
}finally{await browser.close();}
