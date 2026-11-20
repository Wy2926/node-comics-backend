import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';

const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const origin='http://127.0.0.1:5193',out='artifacts/checkout-experience';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  for(const [width,height,theme] of [[1280,900,'light'],[390,844,'dark'],[320,568,'light']]){
    const page=await browser.newPage({viewport:{width,height}}),unexpected=[];
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin===origin&&!url.pathname.startsWith('/v1/')&&route.request().method()==='GET')return route.continue();
      unexpected.push(url.pathname);return route.abort();
    });
    await page.goto(origin+'/?theme='+theme);
    const dialog=page.getByRole('dialog'),frame=page.frameLocator('.payment-frame iframe');
    const open=frame.getByRole('button',{name:'继续选择支付方式'});
    await open.waitFor();
    assert.equal(await page.locator('html').getAttribute('data-site-theme'),theme==='dark'?'midnight':'paper');
    assert.equal(await dialog.locator('.payment-price').innerText(),'US$2.99');
    assert.ok(await dialog.evaluate(d=>d.scrollWidth<=d.clientWidth));
    const methods=await dialog.locator('.checkout-methods strong').boundingBox();
    assert.ok(methods&&methods.y>=0&&methods.y+methods.height<height);
    await page.screenshot({path:`${out}/${width}-${theme}.png`});
    await frame.getByLabel('姓名').fill('测试读者');
    await open.click();
    await frame.getByRole('button',{name:'修改账单信息'}).click();
    assert.equal(await frame.getByLabel('姓名').inputValue(),'测试读者');
    await open.click();
    const rail=theme==='dark'?'银行卡':'支付宝';
    await frame.getByRole('radio',{name:new RegExp(rail)}).check();
    await frame.getByRole('link',{name:'模拟支付'}).click();
    await page.getByRole('heading',{name:'模拟付款已完成'}).waitFor();
    assert.match(await page.locator('.demo-card p').innerText(),new RegExp(rail));
    assert.equal(new URL(page.url()).searchParams.get('method'),rail==='银行卡'?'card':'alipay');
    const pack=page.getByRole('button',{name:/额度包/});
    await pack.click();
    assert.match(await dialog.locator('.ticket-main').innerText(),/3,500 页/);
    await page.keyboard.press('Escape');
    await dialog.waitFor({state:'detached'});
    assert.ok(await pack.evaluate(el=>el===document.activeElement));
    await pack.click();
    const fallback=dialog.getByRole('link',{name:/Creem/}),url=await fallback.getAttribute('href');
    await fallback.click();await page.waitForURL(url);
    await page.getByRole('heading',{name:'账单信息'}).waitFor();
    assert.equal(new URL(url).origin,origin);assert.deepEqual(unexpected,[]);
    await page.close();
  }
  console.log('PASS: interactive local checkout in light/dark, desktop and narrow mobile; both payment methods, back, receipt, keyboard close, and same-order fallback.');
}finally{await browser.close();}
