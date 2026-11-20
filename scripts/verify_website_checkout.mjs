// Run against the built website preview. Identity, checkout and provider content stay synthetic.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const site=new URL(process.env.WEBSITE_PREVIEW_URL||'http://127.0.0.1:4321');
assert.ok(['127.0.0.1','localhost'].includes(site.hostname),'Use a local website preview');
const origin=site.origin,issuer=origin+'/identity',hosted='https://www.creem.io/test/checkout/prod_fixture/ch_fixture';
const pass={id:'fixture-month',name:'PLUS',plan_id:'plus',plan_revision_id:'v1',interval:'month',unit_amount:299,currency:'usd',billing_type:'onetime',
  monthly_classic_pages:2500,trial_days:0,trial_classic_pages:0,channels:[{provider:'creem',binding_id:'fixture',trial_days:0,trial_classic_pages:0}]};
const year={...pass,id:'fixture-year',interval:'year',unit_amount:2399};
const catalog={offers:[pass,year],quota_offers:[]};
const reads={
  '/v1/auth/config':{mode:'oidc',dev_auth:false,issuer,client_id:'fixture',audience:origin+'/api',authorization_endpoint:issuer+'/auth',token_endpoint:issuer+'/token',scopes:'openid profile'},
  '/v1/me':{user:{id:'buyer'}},'/v1/billing/catalog':catalog,
  '/v1/billing/status':{...catalog,enabled:true,subscription:null,subscription_checkout:null,gift:null},
};
const user={profile:{sub:'buyer'},access_token:'fixture',refresh_token:'fixture',token_type:'Bearer',expires_at:Math.floor(Date.now()/1000)+3600};
const storageState={cookies:[],origins:[{origin,localStorage:[{name:`nc-site-session:${issuer}:fixture`,value:JSON.stringify({id:'fixture',user:JSON.stringify(user)})}]}]};
const browser=await chromium.launch({channel:'chrome'});
try{
  for(const [width,height,colorScheme,prefix] of [[1280,900,'light','/'],[390,844,'dark','/en/'],[320,568,'light','/ar/']]){
    const ctx=await browser.newContext({viewport:{width,height},colorScheme,reducedMotion:'reduce',storageState,serviceWorkers:'block'});
    const writes=[],errors=[];
    await ctx.route('**/*',route=>{
      const req=route.request(),url=new URL(req.url()),method=req.method();
      if(method==='GET'&&url.origin+url.pathname===hosted)return route.fulfill({contentType:'text/html',body:'<button>Complete</button>'});
      if(url.origin===origin){
        if(method==='GET'&&Object.hasOwn(reads,url.pathname))return route.fulfill({json:reads[url.pathname]});
        if(method==='GET'&&!url.pathname.startsWith('/v1/')&&!url.pathname.startsWith('/identity/'))return route.continue();
        if(method==='POST'&&url.pathname==='/v1/billing/checkouts'){
          writes.push({body:req.postDataJSON(),key:req.headers()['idempotency-key']});
          return route.fulfill({json:{provider:'creem',checkout_url:hosted}});
        }
      }
      errors.push(`${method} ${url.origin}${url.pathname}`);return route.abort();
    });
    const page=await ctx.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    const pricing=origin+prefix+'pricing/';
    await page.goto(pricing);
    await page.locator('.subscription-card.paid .checkout-methods strong').waitFor();
    const info=page.locator('tr[data-feature="local"] .feature-info-trigger');
    await info.scrollIntoViewIfNeeded();await info.press('Enter');await page.locator('tr[data-feature="local"] [role="tooltip"]').waitFor();
    await info.press('Escape');await page.locator('tr[data-feature="local"] [role="tooltip"]').waitFor({state:'hidden'});
    await page.locator('label:has(input[value="year"])').click();
    const card=page.locator('.subscription-card.paid'),buy=card.locator('[data-purchase-link]');
    await card.locator('.checkout-methods strong').waitFor();
    assert.match(await card.locator('.checkout-methods strong').innerText(),/Alipay|支付宝/);
    assert.equal(await buy.getAttribute('href'),prefix+'pricing/?price='+year.id);
    const dialog=page.getByRole('dialog'),frame=page.frameLocator('.payment-frame iframe');
    await buy.click();await frame.getByRole('button').waitFor();
    assert.equal(await dialog.locator('.payment-price').innerText(),await card.locator('.price .price-value').innerText());
    assert.equal(await dialog.locator('.payment-help a').getAttribute('href'),hosted);
    assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth&&document.documentElement.scrollWidth<=innerWidth));
    const methods=await dialog.locator('.checkout-methods strong').boundingBox();
    assert.ok(methods&&methods.y>=0&&methods.y+methods.height<=height);
    await page.evaluate(()=>{
      const data={source:'creem-embed',type:'completed'},provider=document.querySelector('dialog iframe').contentWindow;
      for(const [source,origin] of [[window,'https://www.creem.io'],[provider,'https://example.invalid']])
        window.dispatchEvent(new MessageEvent('message',{data,source,origin}));
    });
    await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
    assert.equal(page.url(),pricing);assert.ok(await buy.evaluate(el=>document.activeElement===el));
    await buy.click();await frame.getByRole('button').waitFor();
    if(width===1280){
      await dialog.locator('.payment-help a').click();await page.waitForURL(hosted);
    }else{
      await frame.getByRole('button').evaluate((_,origin)=>parent.postMessage({source:'creem-embed',type:'completed'},origin),origin);
      await page.waitForURL(origin+prefix+'payment/success/');
      await page.locator(`.receipt a[href="${prefix}account/"]`).waitFor();
    }
    assert.equal(writes.length,2);assert.ok(writes[0].key);
    assert.deepEqual(writes[0].body,{price_id:year.id,provider:'creem'});assert.deepEqual(writes[1],writes[0]);
    assert.deepEqual(errors,[]);await ctx.close();
  }
  console.log('PASS: local pricing, keyboard controls, same-order fallback and localized receipts across desktop, mobile and RTL.');
}finally{await browser.close();}
