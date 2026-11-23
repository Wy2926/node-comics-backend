import {test} from 'node:test';
import assert from 'node:assert/strict';
import {appBillingRequest,appBillingSession} from '../src/lib/app-billing';
import {checkoutStatus,directCheckout} from '../src/lib/direct-checkout';

test('app pricing redeems once, binds the purchase to the app account, and never falls back to site login',async t=>{
  let code='a'.repeat(43), token='b'.repeat(43), owner='app-alice';
  const location=new URL('https://comics.example/pricing/?app=1#app-billing='+code);
  const tab=new Map<string,string>(),local=new Map<string,string>();
  const storage=(m:Map<string,string>)=>({getItem:(k:string)=>m.get(k)??null,setItem:(k:string,v:string)=>m.set(k,v),removeItem:(k:string)=>m.delete(k)});
  const globals={location,history:{state:null,replaceState:(_:unknown,_title:string,path:string)=>{location.href=new URL(path,location).href;}},
    sessionStorage:storage(tab),localStorage:storage(local)};
  for(const [k,v] of Object.entries(globals)){
    const old=Object.getOwnPropertyDescriptor(globalThis,k);
    Object.defineProperty(globalThis,k,{configurable:true,value:v});
    t.after(()=>old?Object.defineProperty(globalThis,k,old):Reflect.deleteProperty(globalThis,k));
  }
  let redemptions=0,orders=0, hold:Promise<void>|undefined;
  const started=Promise.withResolvers<void>();
  const billing={enabled:true,offers:[],quota_offers:[{id:'pack',name:'100 pages',interval:'once',channels:[{provider:'stripe'}]}]};
  t.mock.method(globalThis,'fetch',async(path:string,options:RequestInit)=>{
    assert.equal(location.hash,'');
    assert.equal(options.credentials,'omit'); assert.equal(options.redirect,'error');
    if(path==='/v1/billing/app-handoff/redeem'){
      redemptions++; assert.equal(options.method,'POST'); assert.deepEqual(JSON.parse(String(options.body)),{code});
      return Response.json({account_id:owner,session:token});
    }
    const headers=new Headers(options.headers);assert.equal(headers.get('authorization'),'Billing '+token);
    if(path.startsWith('/v1/billing/status')){
      if(hold){started.resolve();await hold;}
      return Response.json(billing);
    }
    assert.equal(path,'/v1/billing/checkouts'); assert.equal(options.method,'POST'); orders++;
    assert.deepEqual(JSON.parse(String(options.body)),{price_id:'pack',provider:'stripe'});
    assert.ok(headers.get('idempotency-key'));
    return Response.json({provider:'stripe',checkout_url:'https://checkout.stripe.com/test'});
  });
  const [status,bound]=await Promise.all([checkoutStatus(),appBillingSession()]);
  assert.deepEqual(status,{...billing,appAccountId:'app-alice'}); assert.equal(redemptions,1); assert.equal(orders,0);
  await assert.rejects(appBillingRequest(bound)('/v1/me'));
  assert.equal(await directCheckout('pack'),'https://checkout.stripe.com/test');
  assert.equal(orders,1);
  assert.ok(local.has('nc-quota-purchase:app-alice:pack'));
  assert.ok(!JSON.stringify([...local]).includes(token));
  const released=Promise.withResolvers<void>();hold=released.promise;
  const late=assert.rejects(directCheckout('pack'));
  await started.promise;
  code='c'.repeat(43);token='d'.repeat(43);owner='app-bob';
  location.hash='app-billing='+code;
  assert.equal((await appBillingSession()).account_id,owner);
  released.resolve();await late;
  assert.equal(orders,1);
  await assert.rejects(appBillingRequest(bound)('/v1/billing/status'));
  tab.clear(); await assert.rejects(checkoutStatus()); await assert.rejects(directCheckout('pack'));
  assert.equal(orders,1);
});
