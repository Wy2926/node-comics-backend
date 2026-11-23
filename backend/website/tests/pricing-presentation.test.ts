import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {load} from 'cheerio';
import {createRequire} from 'node:module';
import * as react from 'react';
import ts from 'typescript';
import QuotaOffers from '../src/components/QuotaOffers';
import {quotaPurchaseCopy} from '../src/i18n/quota-purchase';
import CheckoutButton,{type CheckoutCopy} from '../src/components/CheckoutButton';
import CreemCheckout from '../src/components/CreemCheckout';
import {PlanPrice,PublishedPurchaseAvailability} from '../src/components/PublishedPlanPricing';
import PriceAmount from '../src/components/PriceAmount';
import BillingCycle from '../src/components/BillingCycle';
import FeatureInfo,{tooltipPosition} from '../src/components/FeatureInfo';
import {pricingCopy,publishedPricingCopy} from '../src/lib/pricing';
import {amount,amountParts,annualSavings,billingCopy,type Billing,type BillingCatalog,type BillingOffer,type PendingCheckout} from '../src/lib/billing';
import {comparisonCopy,comparisonRows,extraPagesCopy,paidComparisonValue} from '../src/lib/pricing-comparison';
import {publishedSubscriptions} from '../src/data/published-plans';
import {pricingHighlightsCopy} from '../src/i18n/pricing-highlights';
import PlanComparison from '../src/components/PlanComparison';
import {locales,localPath} from '../src/i18n/locales';
import {prepaidMembershipCopy} from '../src/i18n/prepaid-membership';
import {membershipMonths} from '../src/lib/billing-cycle';

test('published subscription prices remain readable without fabricating a purchasable offer',()=>{
  for(const locale of locales)for(const offer of publishedSubscriptions){
    const interval=offer.interval as keyof typeof membershipMonths;
    const $=load(renderToStaticMarkup(createElement(PlanPrice,{locale,offer,offers:publishedSubscriptions})));
    assert.equal($('.published-plan-pricing').attr('data-billing-interval'),interval,locale);
    assert.equal($('.price .price-value').text(),amount(offer,locale),locale);
    assert.equal($('.price .price-currency').text(),amountParts(offer,locale).find(part=>part.type==='currency')?.value,locale);
    assert.equal($('.monthly-equivalent').length,interval==='month'?0:1,locale);
    if(interval!=='month')assert.ok($('.monthly-equivalent').text().includes(amount({...offer,unit_amount:offer.unit_amount/membershipMonths[interval]},locale)),locale);
    assert.equal($('a,button,[data-purchase-link],[data-billing-catalog="live"]').length,0,locale);
  }
});

test('price hierarchy preserves localized currency order, spaces and minor units',()=>{
  for(const locale of locales)for(const currency of ['usd','eur','jpy','ISK','UGX','bhd']){
    const offer={currency,unit_amount:5999};
    const $=load(renderToStaticMarkup(createElement(PriceAmount,{parts:amountParts(offer,locale)})));
    assert.equal($('.price-value').text(),amount(offer,locale),`${locale} ${currency}`);
    assert.equal($('.price-currency').length,1,`${locale} ${currency}`);
    assert.equal($('.price-value').children(':not(.price-currency)').length,0,'numeric text must not inherit the legacy small-span style');
    assert.ok($('.price-currency').text().length>0);
  }
});

test('only common benefits are marked shared, while paid advantages retain their full copy',()=>{
  for(const locale of locales){
    const rows=comparisonRows(locale,2400);
    assert.deepEqual(rows.map(row=>row.key),['classic','document','model','local','rate','priority','reading','feedback','requests','early'],locale);
    assert.deepEqual(rows.filter(row=>row.shared).map(row=>row.key),['local','reading'],locale);
    for(const row of rows)assert.equal(row.free===row.lite,row.shared,`${locale}: ${row.key}`);
    assert.ok(rows.find(row=>row.key==='rate')?.lite.includes((2400).toLocaleString(locale)),locale);
  }
});

test('cloud, built-in and self-hosted local translation and member support are localized',()=>{
  const english=comparisonCopy('en');
  for(const locale of locales){
    const copy=comparisonCopy(locale);
    assert.match(copy.local.free,/MTU/,locale);
    assert.equal(copy.local.free,copy.local.lite,locale);
    assert.match(copy.local.detail??'',/MTU/,locale);
    for(const key of ['classic','local','feedback','requests'] as const){
      assert.ok(copy[key].label.trim(),`${locale}: ${key}`);
      if(locale!=='en')assert.notEqual(copy[key].label,english[key].label,`${locale}: ${key} must be translated`);
    }
    for(const key of ['feedback','requests'] as const){
      assert.notEqual(copy[key].free,copy[key].lite,`${locale}: ${key} distinguishes ordinary and priority support`);
      if(locale!=='en')for(const tier of ['free','lite'] as const)assert.notEqual(copy[key][tier],english[key][tier],`${locale}: ${key}.${tier} must be translated`);
    }
  }
  assert.equal(comparisonCopy('zh-CN').classic.label,'云端翻译');
  assert.equal(english.classic.label,'Cloud translation');
  assert.match(english.local.detail??'',/built-in browser translation \(Beta\) and self-hosted MTU/);
  assert.match(english.local.detail??'',/Internet requirements depend on the selected channel and configuration/);
  assert.match(english.feedback.detail??'',/does not guarantee a response time/);
  assert.match(english.requests.detail??'',/does not guarantee implementation or a release date/);
});

test('only detailed feature headings have keyboard-addressable localized tooltip buttons',()=>{
  for(const locale of locales){
    const rows=comparisonRows(locale,1200);
    assert.deepEqual(rows.filter(row=>row.detail).map(row=>row.key),['classic','document','local','rate','feedback','requests'],locale);
    const $=load(renderToStaticMarkup(createElement('div',null,rows.map(row=>createElement('div',{'data-feature':row.key,key:row.key},row.detail?createElement(FeatureInfo,{label:row.label,detail:row.detail}):null)))));
    assert.equal($('.feature-info-trigger').length,6,locale);
    const ids=new Set<string>();
    for(const row of rows){
      const heading=$(`[data-feature="${row.key}"]`),button=heading.find('.feature-info-trigger'),tip=heading.find('[role="tooltip"]');
      assert.equal(button.length,row.detail?1:0,`${locale}: ${row.key}`);
      if(!row.detail)continue;
      assert.equal(button.attr('type'),'button',locale);
      assert.equal(button.attr('aria-label'),row.label,locale);
      assert.equal(button.attr('aria-expanded'),'false',locale);
      assert.equal(button.attr('aria-describedby'),tip.attr('id'),locale);
      assert.equal(button.attr('aria-controls'),tip.attr('id'),locale);
      assert.equal(button.find('svg[aria-hidden="true"][focusable="false"]').length,1,locale);
      assert.equal(tip.is('[hidden]'),true,locale);
      assert.equal(tip.text(),row.detail,locale);
      ids.add(tip.attr('id')!);
      if(locale!=='en')assert.notEqual(row.detail,comparisonRows('en',1200).find(english=>english.key===row.key)?.detail,`${locale}: ${row.key} detail must be translated`);
    }
    assert.equal(ids.size,6,locale);
  }
});

test('feature tips stay inside narrow LTR and RTL viewports and flip above near the bottom',()=>{
  for(const width of [320,390,1280])for(const left of [20,width-48]){
    const anchor={left,right:left+28,top:120,bottom:148},size={width:Math.min(320,width-24),height:180};
    const position=tooltipPosition(anchor,size,{width,height:800});
    assert.ok(position.left>=12&&position.left+size.width<=width-12);
    assert.equal(position.top,148);
    assert.equal(tooltipPosition({...anchor,top:720,bottom:748},size,{width,height:800}).top,540);
  }
});

test('annual discount has a dedicated stable slot in either public billing cadence',()=>{
  const publishedAnnualDiscount=Math.min(...publishedSubscriptions.filter(offer=>offer.interval==='year').map(offer=>annualSavings(offer,publishedSubscriptions)!.percent));
  assert.equal(publishedAnnualDiscount,24.8);
  for(const locale of locales)for(const value of ['month','quarter','year'] as const)for(const annualDiscount of [0,publishedAnnualDiscount]){
    const offers=annualDiscount?publishedSubscriptions:publishedSubscriptions.filter(offer=>offer.interval===value);
    const $=load(renderToStaticMarkup(createElement(BillingCycle,{offers,value,onChange:()=>{},locale})));
    assert.equal($('.billing-cycle-picker > .billing-cycle-saving').length,1,locale);
    assert.equal($('.billing-cycle .annual-badge').length,0,locale);
    assert.equal($('.annual-badge').text(),annualDiscount?pricingCopy(locale).save(annualDiscount):'',locale);
    assert.equal($('input:checked').attr('value'),value,locale);
    assert.equal($('input:disabled').length,0,locale);
  }
});

test('live annual quotes show the full charge before the monthly equivalent',()=>{
  const offer={...publishedSubscriptions[0],unit_amount:7199,currency:'USD',interval:'year',channels:[{provider:'creem'}]} as BillingOffer;
  for(const locale of locales){
    const $=load(renderToStaticMarkup(createElement(PlanPrice,{locale,offer})));
    assert.equal($('.price .price-value').text(),amount(offer,locale),locale);
    assert.equal($('.billing-total').length,0,'The actual charge is already the main figure');
    assert.ok($('.monthly-equivalent').text().includes(amount({...offer,unit_amount:7199/12},locale)),locale);
    assert.equal($('[data-billing-catalog="published"]').length,0,locale);
  }
});

test('unavailable purchase states disclose nonrenewal and disable checkout',()=>{
  for(const locale of locales)for(const state of ['loading','unavailable','error'] as const){
    const $=load(renderToStaticMarkup(createElement(PublishedPurchaseAvailability,{locale,state,name:'Studio'})));
    const copy=publishedPricingCopy(locale);
    assert.equal($('.billing-availability').attr('data-state'),state,`${locale}: ${state}`);
    assert.equal($('.billing-status').text(),copy[state],locale);
    assert.equal($('.billing-status').attr('role'),state==='error'?'alert':'status',locale);
    assert.equal($('button[disabled]').length,1,locale);
    assert.equal($('a,[data-purchase-link]').length,0,locale);
    assert.ok($('.trial-note').text().includes(prepaidMembershipCopy(locale).notice),locale);
    assert.ok($.text().includes(copy.tax),locale);
  }
});

const pack:BillingOffer={id:'synthetic-pack',name:'Lite',plan_id:'pages',plan_revision_id:'pack-v1',currency:'usd',unit_amount:700,interval:'once',monthly_classic_pages:0,trial_days:0,trial_classic_pages:0,service_plan_id:'lite',service_plan_name:'Lite',quota_pages:100,quota_validity_days:null,hourly_image_limit:1200,channels:[{provider:'creem',binding_id:'synthetic',trial_days:0,trial_classic_pages:0}]};
const checkoutCopy:CheckoutCopy={busy:'busy',error:'error',before:'before',refund:'refund',resume:'resume original'};
const subscription={...pack,id:'subscription-month',name:'Lite',plan_id:'lite',quota_pages:0,monthly_classic_pages:500,trial_days:0,trial_classic_pages:0,interval:'month' as const,billing_type:'onetime' as const};
const original=(price:BillingOffer):PendingCheckout=>({id:'checkout-'+price.id,provider:'creem',price,idempotency_key:price.interval==='once'||price.billing_type==='onetime'?'purchase-key':null,error:null});
const emptyBilling:Billing={enabled:true,providers:[],provider:null,environment:'test',trial_eligible:true,
  subscription_checkout:null,offers:[subscription],quota_offers:[pack,{...pack,id:'second-pack'}],
  entitlement_expires_at:null,gift:null,subscription:null};
const pricingFile=new URL('../src/components/BillingOffers.tsx',import.meta.url);
const pricingRequire=createRequire(pricingFile);
const pricingCompiled=ts.transpileModule(readFileSync(pricingFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
function pricingSnapshot(billing:Billing|null,locale='en',selectedPrice='',interval:string|null='month',catalog:BillingCatalog|null={offers:billing?.offers??[subscription],quota_offers:billing?.quota_offers??[pack]},error=false){
  // Seed only BillingOffers' loaded snapshot. Child controls use real React SSR.
  const states:unknown[]=[catalog??undefined,error,billing,false,interval??undefined,selectedPrice];
  const exports={} as typeof import('../src/components/BillingOffers');
  new Function('require','exports',pricingCompiled)((name:string)=>name==='react'?{...react,useEffect:()=>{},useState:()=>[states.shift(),()=>{}]}:name.endsWith('.css')?{}:pricingRequire(name),exports);
  return load(renderToStaticMarkup(createElement(exports.default,{locale,accountHref:localPath('/account/',locale as typeof locales[number]),downloadHref:'/download/',checkoutCopy,
    free:{name:'Free',description:'Free',action:'Download',note:'Free',priceLabel:'free'}})));
}

test('signed-out buyers see Alipay and the next step beside live Creem purchases before clicking',()=>{
  for(const locale of locales){
    const $=pricingSnapshot(null,locale);
    for(const selector of ['.subscription-card.paid','.quota-offer']){
      const link=$(selector).find('[data-purchase-link]'),note=link.prev('.checkout-methods');
      assert.match(note.find('strong').text(),/Alipay|支付宝|支付寶/,locale);
      assert.equal(link.attr('aria-describedby'),note.attr('id'),locale);
      const hint=note.find('span').text();
      assert.ok(hint.trim(),locale);
      if(locale==='zh-CN')assert.match(hint,/邮箱、姓名.*账单国家或地区.*下一步.*可用的支付方式/);
    }
    assert.equal($('.free .checkout-methods').length,0);
  }
  assert.equal(pricingSnapshot(null,'zh-CN','',null,null)('.checkout-methods').length,0,'static prices have no verified channel');
});

test('payment method hints follow the selected or original channel and exclude Alipay on recurring orders',()=>{
  const stripe={...pack.channels[0],provider:'stripe' as const};
  const stripeFirst={...pack,channels:[stripe,...pack.channels]};
  const recurring={...subscription,id:'legacy',billing_type:'recurring' as const};
  const current={...emptyBilling,offers:[{...subscription,channels:stripeFirst.channels}],quota_offers:[stripeFirst]};
  const cards=pricingSnapshot(current,'zh-CN');
  assert.deepEqual(cards('.checkout-methods strong').map((_,el)=>cards(el).text()).get(),['银行卡支付','银行卡支付']);
  const resumed=pricingSnapshot({...current,pending_checkout:original(stripeFirst),subscription_checkout:original(recurring)},'zh-CN');
  assert.match(resumed('.quota-offer .checkout-methods').text(),/支付宝/,'resume the Creem pack even though Stripe is now first');
  assert.match(resumed('.billing-recovery .checkout-methods strong').text(),/^银行卡支付$/,'Creem recurring is not Alipay eligible');
  assert.equal(resumed('.subscription-card .checkout-methods').length,0,'no payment promise beside a blocked purchase');
  assert.equal(pricingSnapshot(current,'zh-CN','unknown-price')('.billing-recovery .checkout-methods').length,0);
});

test('the checkout sheet shows the exact quote and methods above a localized frame with the same hosted fallback',()=>{
  const url='https://www.creem.io/test/checkout/prod_demo/ch_demo?theme=light';
  for(const offer of [pack,subscription])for(const locale of locales){
    const $=load(renderToStaticMarkup(createElement(CreemCheckout,{locale,purchase:{url,offer},successHref:localPath('/payment/success/',locale),onClose:()=>{}})));
    const summary=$('.payment-summary'),frame=$('iframe'),src=new URL(frame.attr('src')!);
    assert.ok(summary.find('h3').text().includes(offer.name));
    assert.equal(summary.find('.payment-price').text(),amount(offer,locale));
    assert.match(summary.find('.checkout-methods strong').text(),/Alipay|支付宝|支付寶/);
    assert.equal(summary.next('.payment-frame').find('iframe').length,1);
    assert.ok(summary.find('.ticket-stub').text().includes(quotaPurchaseCopy(locale).once));
    assert.ok($('.payment-frame [role="status"]').text().trim());
    assert.equal(src.searchParams.get('locale'),locale);
    src.searchParams.delete('locale');assert.equal(src.href,url);
    assert.equal(summary.find('a').attr('href'),url);
    assert.equal(frame.attr('referrerpolicy'),'no-referrer');
  }
});

test('a numbered page pack has one quantity on the ticket and the catalog expiry',()=>{
  const offer={...pack,name:'3500',quota_pages:3500,quota_validity_days:30};
  const $=load(renderToStaticMarkup(createElement(CreemCheckout,{locale:'zh-CN',purchase:{offer,url:'https://creem.io/test/checkout/demo'},successHref:'/payment/success/',onClose:()=>{}})));
  assert.equal($('.ticket-main h3').text(),'3,500 页');
  assert.equal($('.ticket-main p').text(),'30 天有效');
});

test('page packs show exact page counts and expiry without recurring or unlimited subscription claims in all locales',()=>{
  for(const locale of locales)for(const days of [null,30]){
    const copy=quotaPurchaseCopy(locale),quote={...pack,quota_validity_days:days};
    const $=load(renderToStaticMarkup(createElement(QuotaOffers,{locale,offers:[quote],accountHref:localPath('/account/',locale),checkoutCopy})));
    assert.equal($('.quota-offer').length,1);
    assert.equal($('.quota-offer-quantity strong').text(),(100).toLocaleString(locale),locale);
    assert.equal($('.quota-offer-price .price-value').text(),amount(quote,locale),locale);
    assert.ok($.text().includes(copy.pages.replace('{0}',(100).toLocaleString(locale))),locale);
    assert.ok($.text().includes(days===null?copy.noExpiry:copy.validity.replace('{0}',(30).toLocaleString(locale))),locale);
    assert.ok($.text().includes(copy.oneTime),locale);
    assert.equal($('.quota-offers-heading').text(),copy.title,locale);
    assert.equal($('.quota-offers-heading .quota-purchase-kind').length,0,locale);
    assert.ok(!('noRenewal' in copy),locale);
    assert.ok($.text().includes(copy.service.replace('{0}','Lite')),locale);
    assert.ok($.text().includes(copy.hourly.replace('{0}',(1200).toLocaleString(locale))),locale);
    assert.equal($('.quota-offer-features li').length,3,locale);
    assert.equal($('.quota-offer .quota-icon[aria-hidden="true"][focusable="false"]').length,4,locale);
    assert.ok(!$.text().includes(billingCopy(locale).classic),locale);
    assert.ok(!$.text().includes('subscription renewal'),locale);
    assert.equal($('[data-purchase-link]').attr('href'),localPath('/pricing/?price=synthetic-pack',locale));
    assert.ok($('[data-purchase-link]').text().includes(copy.action),locale);
  }
});

test('pack cards share one complete policy disclosure without repeating paragraphs in each card',()=>{
  for(const locale of locales){
    const copy=quotaPurchaseCopy(locale),accountHref=localPath('/account/',locale);
    const $=load(renderToStaticMarkup(createElement(QuotaOffers,{locale,offers:[pack,{...pack,id:'expiring',quota_pages:50,quota_validity_days:30}],accountHref,checkoutCopy})));
    assert.equal($('.quota-offer p,.quota-offer .trial-note').length,0,locale);
    assert.equal($('.quota-offer [data-purchase-link]').length,2,locale);
    assert.equal($('.quota-offer-legal a').attr('href'),localPath('/refund/',locale));
    assert.equal($(`a[href="${localPath('/refund/',locale)}"]`).length,1,locale);
    for(const rule of [copy.subscription,copy.usage,copy.validityStart,copy.tax,copy.terms])assert.ok($('.quota-offer-policy').text().includes(rule),`${locale}: ${rule}`);
    const pass=load(renderToStaticMarkup(createElement(CheckoutButton,{priceId:'pass',accountHref,label:'buy',copy:checkoutCopy})));
    assert.equal(pass('.trial-note a').attr('href'),localPath('/refund/',locale));
  }
});

test('quota presentation uses shared theme tokens and scopes every rule away from subscription cards',()=>{
  const css=readFileSync(new URL('../src/styles/quota-offers.css',import.meta.url),'utf8');
  for(const match of css.matchAll(/([^{}]+)\{/g)){
    const selector=match[1].trim();
    if(!selector.startsWith('@media'))assert.ok(selector.startsWith('.quota-offers'),selector);
  }
  assert.doesNotMatch(css,/#(?:[a-f\d]{3,8})\b|url\(/i,'No alternate hard-coded palette or bitmap assets');
  for(const token of ['--comic-panel-radius','--comic-panel-padding','--accent','--surface','--ink','--muted'])assert.ok(css.includes(`var(${token})`),token);
});

test('packs without channels can be previewed but only sellable packs have a checkout link',()=>{
  const props={locale:'en',accountHref:'/en/account/',checkoutCopy};
  for(const offers of [[],[{...pack,interval:'month' as const}]]){
    assert.equal(renderToStaticMarkup(createElement(QuotaOffers,{...props,offers})), '');
  }
  const preview=load(renderToStaticMarkup(createElement(QuotaOffers,{...props,offers:[{...pack,channels:[]}]})));
  assert.equal(preview('.quota-offer button:disabled').length,1);
  assert.equal(preview('[data-purchase-link],.checkout-methods').length,0);
  const $=load(renderToStaticMarkup(createElement(QuotaOffers,{...props,offers:[pack,{...pack,id:'other-pack'}]})));
  assert.equal($('.quota-offer').length,2);
  assert.deepEqual($('[data-purchase-link]').toArray().map(link=>$(link).attr('href')),['/en/pricing/?price=synthetic-pack','/en/pricing/?price=other-pack']);
  assert.ok(!$('[data-purchase-link]').text().includes(checkoutCopy.resume!));
  const blocked=load(renderToStaticMarkup(createElement(QuotaOffers,{...props,offers:[pack],blocked:true})));
  assert.equal(blocked('[data-purchase-link]').attr('aria-disabled'),'true');
});

test('subscription recovery does not hide or disable any quota purchase action',()=>{
  for(const subscription_checkout of [null,original(subscription)]){
    const $=pricingSnapshot({...emptyBilling,subscription_checkout});
    assert.equal($('.subscription-card.paid [data-purchase-link]').attr('aria-disabled'),undefined);
    for(const link of $('.quota-offer [data-purchase-link]').toArray())assert.equal($(link).attr('aria-disabled'),undefined);
    assert.equal($('.quota-offer').length,2);
    assert.ok(!$('.quota-offer [data-purchase-link]').text().includes('resume original'));
    if(!subscription_checkout){
      assert.equal($('.billing-cycle[disabled]').length,0);
      assert.ok(!$('.subscription-card.paid [data-purchase-link]').text().includes('resume original'));
    }else assert.ok($('.subscription-card.paid [data-purchase-link]').text().includes('resume original'));
  }
});

test('every locale keeps other subscription prices and cycles available beside pending checkout',()=>{
  const sub={...subscription,id:'retired-year',interval:'year' as const};
  for(const locale of locales){
    const $=pricingSnapshot({...emptyBilling,subscription_checkout:original(sub)},locale,subscription.id);
    const subscriptionButton=$('.subscription-card.paid [data-purchase-link]'),quotaButton=$('.quota-offer [data-purchase-link]');
    assert.equal(subscriptionButton.attr('href'),localPath('/pricing/?price=subscription-month',locale));
    assert.equal(quotaButton.attr('href'),localPath('/pricing/?price=synthetic-pack',locale));
    assert.ok(!subscriptionButton.text().includes('resume original'));assert.ok(!quotaButton.text().includes('resume original'));
    assert.equal(subscriptionButton.attr('aria-disabled'),undefined);assert.equal(quotaButton.attr('aria-disabled'),undefined);
    assert.equal($('.quota-offer').length,2);assert.equal($('.billing-cycle[disabled]').length,0);
    const recovered=pricingSnapshot({...emptyBilling,subscription_checkout:original(sub)},locale,sub.id);
    assert.equal(recovered('.subscription-card.paid [data-purchase-link]').attr('href'),localPath('/pricing/?price=retired-year',locale));
    assert.ok(recovered('.subscription-card.paid [data-purchase-link]').text().includes('resume original'));
  }
});

test('every locale shows only prepaid membership even with an explicit recurring quote',()=>{
  const recurring={...subscription,id:'quarter',interval:'quarter' as const,billing_type:'recurring' as const,trial_days:0};
  const prepaid={...recurring,id:'quarter-prepaid',billing_type:'onetime' as const};
  const annual={...prepaid,id:'year-prepaid',interval:'year' as const};
  const snapshot={...emptyBilling,offers:[recurring,prepaid,annual]};
  for(const locale of locales){
    const $=pricingSnapshot(snapshot,locale,'','quarter');
    const selected=$('.subscription-card.paid [data-purchase-link]');
    assert.equal(selected.attr('href'),localPath(`/pricing/?price=${prepaid.id}`,locale));
    assert.equal($('.subscription-card.paid').attr('data-billing-type'),'onetime');
    assert.equal($('.billing-payment-type').length,0);
    assert.ok($('.billing-cycle').text().includes(prepaidMembershipCopy(locale).quarter));assert.ok($('.billing-cycle').text().includes(prepaidMembershipCopy(locale).year));
    assert.ok($('.subscription-card.paid').text().includes(prepaidMembershipCopy(locale).notice));
    assert.doesNotMatch($('.subscription-card.paid').text(),/subscription renewal|下次续费前取消|每三个月自动续费/);
    for(const offer of [prepaid,annual]){
      const exact=pricingSnapshot(snapshot,locale,offer.id,'quarter');
      assert.equal(exact('.subscription-card.paid [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${offer.id}`,locale));
      assert.equal(exact('.subscription-card.paid').attr('data-billing-type'),offer.billing_type);
    }
    assert.equal(pricingSnapshot(snapshot,locale,recurring.id,'quarter')('.subscription-card.paid [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${prepaid.id}`,locale));
  }
  const unavailable=pricingSnapshot({...snapshot,offers:[recurring]},'zh-CN','','quarter');
  assert.equal(unavailable('.subscription-card.paid [data-purchase-link]').length,0);
  assert.equal(unavailable('.subscription-card.paid').length,0);
  assert.equal(unavailable('.billing-status').text(),publishedPricingCopy('zh-CN').unavailable);
  assert.doesNotMatch(unavailable('.subscription-card.paid').text(),/每三个月自动续费|下次续费前取消/);
});

test('pending prepaid orders retain exact recovery beside other passes and packs',()=>{
  const prepaid={...subscription,id:'prepaid-quarter',interval:'quarter' as const};
  for(const locale of locales){
    const pending={...prepaid,id:'retired-prepaid',interval:'year' as const};
    const status={...emptyBilling,offers:[prepaid],pending_checkout:original(pending)};
    const $=pricingSnapshot(status,locale,'',null);
    assert.equal($('.subscription-card.paid').attr('data-billing-type'),'onetime',locale);
    assert.equal($('.billing-cycle input:checked').attr('value'),'year',locale);
    assert.equal($('.subscription-card.paid [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${pending.id}`,locale));
    assert.ok($('.subscription-card.paid [data-purchase-link]').text().includes('resume original'));
    assert.equal($('.billing-recovery').length,0,'the visible original card is already the recovery action');
    const changed=pricingSnapshot(status,locale,'','quarter');
    assert.equal(changed('.subscription-card.paid [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${prepaid.id}`,locale));
    assert.equal(changed('.subscription-card.paid [data-purchase-link]').attr('aria-disabled'),undefined);
    assert.equal(changed('.billing-recovery [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${pending.id}`,locale));
    assert.equal(changed('.quota-offer [data-purchase-link]').first().attr('aria-disabled'),undefined);
  }
});

test('legacy recurring checkout has a separate recovery action and blocks conflicting new passes',()=>{
  const recurring={...subscription,id:'recurring-quarter',interval:'quarter' as const,billing_type:'recurring' as const,trial_days:0};
  const prepaid={...recurring,id:'prepaid-quarter',billing_type:'onetime' as const};
  for(const locale of locales)for(const selected of ['',prepaid.id,recurring.id]){
    const status={...emptyBilling,offers:[prepaid],pending_checkout:original(recurring)};
    const $=pricingSnapshot(status,locale,selected,'quarter');
    assert.equal($('.subscription-card.paid').attr('data-billing-type'),'onetime',locale);
    const purchase=$('.subscription-card.paid [data-purchase-link]');
    assert.equal(purchase.attr('href'),localPath(`/pricing/?price=${prepaid.id}`,locale));
    assert.equal(purchase.attr('aria-disabled'),'true');
    assert.ok($('.billing-recovery [role="status"]').text().trim(),locale);
    assert.equal($('.billing-recovery [data-purchase-link]').attr('href'),localPath(`/pricing/?price=${recurring.id}`,locale));
    assert.equal($('.billing-recovery [data-purchase-link]').length,1);
    assert.ok($('.billing-recovery [data-purchase-link]').text().includes('resume original'));
    assert.equal($('.quota-offer [data-purchase-link]').first().attr('aria-disabled'),undefined);
  }
});

test('mixed legacy pending orders expose both originals without opening another payable membership',()=>{
  const recurring={...subscription,id:'old-renewal',billing_type:'recurring' as const};
  const unpaid={...subscription,id:'unpaid-pass'};
  const $=pricingSnapshot({...emptyBilling,pending_checkout:original(unpaid),subscription_checkout:original(recurring)},'en');
  const links=$('[data-purchase-link]').toArray().map(el=>({url:$(el).attr('href'),disabled:$(el).attr('aria-disabled')}));
  for(const id of [recurring.id,unpaid.id,pack.id])assert.ok(links.some(link=>link.url?.endsWith('='+id)&&!link.disabled),id);
  assert.ok($('.billing-recovery').text().includes('Renews monthly.'));
  const other=pricingSnapshot({...emptyBilling,pending_checkout:original(unpaid),subscription_checkout:original(recurring)},'en',subscription.id);
  assert.equal(other('.subscription-card.paid [data-purchase-link]').attr('aria-disabled'),'true');
  assert.equal(other('.billing-recovery [data-purchase-link]').length,2);
});

test('prepaid membership keeps manual purchases available',()=>{
  const prepaid={...subscription,id:'quarter-prepaid',interval:'quarter' as const,billing_type:'onetime' as const};
  const status={...emptyBilling,offers:[prepaid,subscription],prepaid_membership:{price:prepaid,starts_at:'2026-01-01T00:00:00Z',ends_at:'2099-01-01T00:00:00Z'}};
  const renewable=pricingSnapshot(status,'zh-CN',prepaid.id,'quarter');
  assert.equal(renewable('.subscription-card.paid [data-purchase-link]').attr('href'),'/pricing/?price=quarter-prepaid');
  assert.equal(pricingSnapshot(status,'en',subscription.id)('.subscription-card.paid [data-purchase-link]').length,1);
});

test('an explicit prepaid price retains manual original-result verification after a subscription becomes active',()=>{
  const prepaid={...subscription,id:'quarter-prepaid',interval:'quarter' as const,billing_type:'onetime' as const};
  const status={...emptyBilling,offers:[prepaid,subscription],subscription:{status:'active'} as Billing['subscription']};
  const selected=pricingSnapshot(status,'zh-CN',prepaid.id,'quarter');
  assert.equal(selected('.subscription-card.paid [data-purchase-link]').length,0);
  assert.equal(selected('.billing-recovery [data-purchase-link]').attr('href'),'/pricing/?price=quarter-prepaid');
  assert.ok(selected('.billing-recovery').text().includes(checkoutCopy.resume!));
  assert.equal(pricingSnapshot(status,'zh-CN','','quarter')('.billing-recovery').length,0);
  assert.equal(pricingSnapshot(status,'en',subscription.id)('.billing-recovery').length,1);
});

test('a retired selected quote gets one manual recovery action only for a signed-in pricing snapshot',()=>{
  for(const locale of locales){
    const $=pricingSnapshot(emptyBilling,locale,'retired-pack');
    assert.equal($('.billing-recovery [data-purchase-link]').length,1);
    assert.equal($('.billing-recovery [data-purchase-link]').attr('href'),localPath('/pricing/?price=retired-pack',locale));
    assert.ok($('.billing-recovery').text().includes(checkoutCopy.resume!));
    assert.equal($('.quota-offer').length,2);
  }
  for(const selected of ['',pack.id,subscription.id,'../invalid'])assert.equal(pricingSnapshot(emptyBilling,'en',selected)('.billing-recovery').length,0);
  assert.equal(pricingSnapshot(null,'en','retired-pack')('.billing-recovery').length,0);
  assert.match(readFileSync(pricingFile,'utf8'),/className="billing-recovery"[^\n]*continueAfterLogin=\{false\}/);
});


test('finite and unlimited subscription cards use the catalog rather than product names',()=>{
  const unlimited={...subscription,id:'pro-month',plan_id:'pro',name:'Pro',monthly_classic_pages:null,trial_classic_pages:null};
  const $=pricingSnapshot({...emptyBilling,offers:[subscription,unlimited]});
  assert.equal($('.subscription-card').length,3);
  assert.match($('.subscription-card.paid').eq(0).find('.subscription-allowance').text(),/500/);
  assert.ok($('.subscription-card.paid').eq(1).find('.subscription-allowance').text().includes(billingCopy('en').classic));
  assert.ok($('.subscription-card.paid').eq(0).find('.trial-note').text().includes(prepaidMembershipCopy('en').notice));
  assert.equal($('.subscription-card.paid [data-purchase-link]').length,2);
});

test('one accessible comparison groups advanced models and shares both local channels across every plan',()=>{
  const plans=publishedSubscriptions.filter(offer=>offer.interval==='quarter');
  for(const locale of locales){
    const $=load(renderToStaticMarkup(createElement(PlanComparison,{locale,plans,freeName:'Free'})));
    const text=pricingHighlightsCopy(locale);
    assert.deepEqual($('thead th').toArray().slice(1).map(el=>$(el).text()),['Free','PLUS','Pro']);
    assert.equal($('[role="region"][tabindex="0"]').length,1);
    assert.equal($('caption').text(),comparisonCopy(locale).feature);
    assert.equal($('.feature-info-trigger').length,6,'one tooltip per detailed feature, not per plan');
    const {model,local,document}=comparisonCopy(locale);
    const advanced=$('tr[data-feature="model"]');
    assert.equal($('tr[data-model]').length,0,locale);
    assert.equal(advanced.length,1,locale);
    assert.equal(advanced.find('th').text(),model.label,locale);
    assert.deepEqual(advanced.find('td').toArray().map(el=>$(el).text()),[text.notIncluded,text.included,text.included],locale);
    const wholeFile=$('tr[data-feature="document"]');
    assert.equal(wholeFile.find('th .feature-info-trigger').attr('aria-label'),document.label,locale);
    assert.deepEqual(wholeFile.find('td').toArray().map(el=>$(el).text()),[text.notIncluded,text.included,text.included],locale);
    assert.match(wholeFile.find('[role="tooltip"]').text(),/PDF.*ZIP.*CBZ/,locale);
    assert.deepEqual($('tr[data-feature="local"] td').toArray().map(el=>$(el).text()),[local.free,local.free,local.free],locale);
    assert.ok($('[data-feature="classic"] td').eq(1).text().includes((2500).toLocaleString(locale)));
    assert.ok($('[data-feature="classic"] td').eq(2).text().includes((4000).toLocaleString(locale)));
  }
});

test('larger paid plans highlight their actual monthly difference, not invented speed or model privileges',()=>{
  const plans=publishedSubscriptions.filter(offer=>offer.interval==='quarter');
  for(const locale of locales){
    assert.equal(extraPagesCopy(plans[0],plans,locale),'');
    assert.ok(extraPagesCopy(plans[1],plans,locale).includes((1500).toLocaleString(locale)));
    assert.ok(extraPagesCopy(plans[1],plans,locale).includes('PLUS'));
    assert.equal(extraPagesCopy({...plans[1],monthly_classic_pages:null},plans,locale),'');
    assert.equal(extraPagesCopy(plans[1],[plans[1]],locale),'');
    assert.equal(extraPagesCopy(plans[1],[{...plans[0],monthly_classic_pages:4000},plans[1]],locale),'');
    const rate=comparisonRows(locale,0).find(row=>row.key==='rate')!;
    const value=paidComparisonValue(rate,{...plans[0],hourly_image_limit:1200},locale);
    assert.ok(value.includes((1200).toLocaleString(locale)));
    assert.ok(value.includes('100'),'hourly limits must not replace the minute limit');
  }
});

test('annual price tags only advertise verified comparable discounts and use decorative SVG',()=>{
  for(const locale of locales)for(const plan of ['plus','pro']){
    const offer=publishedSubscriptions.find(item=>item.plan_id===plan&&item.interval==='year')!;
    const $=load(renderToStaticMarkup(createElement(PlanPrice,{locale,offer,offers:publishedSubscriptions})));
    assert.equal($('.price-discount').text(),pricingCopy(locale).save(annualSavings(offer,publishedSubscriptions)!.percent),locale);
    assert.equal($('.price-discount svg[aria-hidden="true"][focusable="false"]').length,1);
    assert.equal($('.monthly-equivalent svg[aria-hidden="true"]').length,1);
    assert.equal($('.price .price-value').text(),amount(offer,locale));
    for(const offers of [[],[{...offer,interval:'quarter' as const,plan_revision_id:'different'}],[{...offer,interval:'quarter' as const,currency:'eur'}]]){
      const html=load(renderToStaticMarkup(createElement(PlanPrice,{locale,offer,offers})));
      assert.equal(html('.price-discount').length,0,'No comparable quote means no discount claim');
    }
  }
});

test('empty live catalogs remove build-time memberships and packs, including signed-out visitors',()=>{
  const empty={offers:[],quota_offers:[]};
  for(const billing of [null,{...emptyBilling,...empty}]){
    const $=pricingSnapshot(billing,'zh-CN','',null,empty);
    assert.equal($('.subscription-card.paid,.quota-offer,[data-purchase-link]').length,0);
    assert.equal($('.billing-status').text(),publishedPricingCopy('zh-CN').unavailable);
  }
  const loading=pricingSnapshot(null,'zh-CN','',null,null);
  assert.equal(loading('.subscription-card.paid').length,2);
  assert.equal(loading('[data-purchase-link]').length,0);
  const failed=pricingSnapshot(null,'zh-CN','',null,null,true);
  assert.equal(failed('.subscription-card.paid,.quota-offer').length,0);
  assert.equal(failed('[role="alert"]').text(),publishedPricingCopy('zh-CN').error);
});

test('a new prepaid product and currency render without frontend plan mappings',()=>{
  const offer={...subscription,id:'studio-quarter',plan_id:'studio',name:'Studio',currency:'eur',unit_amount:1750,
    interval:'quarter' as const,billing_type:'onetime' as const,trial_days:0,monthly_classic_pages:6000};
  const catalog={offers:[offer],quota_offers:[]};
  const $=pricingSnapshot(null,'en','',null,catalog);
  assert.equal($('.subscription-card.paid').length,1);
  assert.equal($('.subscription-card.paid h2').text(),'Studio');
  assert.equal($('.price .price-value').last().text(),amount(offer,'en'));
  assert.equal($('.billing-payment-type').length,0);
  assert.equal($('.billing-cycle input').length,1);
  assert.equal($('.billing-cycle input:checked').attr('value'),'quarter');
  assert.equal($('[data-purchase-link]').attr('href'),'/en/pricing/?price=studio-quarter');
  for(const currency of ['eur','jpy','bhd'])for(const channels of [[],subscription.channels]){
    const quote={...offer,currency,channels},view=load(renderToStaticMarkup(createElement(PlanPrice,{locale:'en',offer:quote})));
    assert.equal(view('.price-value').first().text(),amount(quote,'en'));
  }
});

test('visual advantages distinguish paid additions and quota differences, not shared benefits',()=>{
  const plans=publishedSubscriptions.filter(offer=>offer.interval==='quarter');
  const $=load(renderToStaticMarkup(createElement(PlanComparison,{locale:'zh-CN',plans,freeName:'免费'})));
  for(const key of ['reading','local'])assert.equal($(`tr[data-feature="${key}"] [data-advantage="true"]`).length,0);
  for(const key of ['classic','document','model','rate','priority','feedback','requests','early'])assert.equal($(`tr[data-feature="${key}"] [data-advantage="true"]`).length,2);
  assert.equal($('[data-more-pages="true"]').length,1);
  assert.match($('.comparison-delta').text(),/1,500/);
});

test('monthly passes keep exact prices and recoverable pending checkouts in every language',()=>{
  const quotes=publishedSubscriptions.filter(offer=>offer.interval==='month').map(offer=>({
    ...offer,id:offer.id.replace('published-',''),channels:subscription.channels}));
  for(const locale of locales){
    const status={...emptyBilling,offers:quotes};
    const $=pricingSnapshot(status,locale,'','month');
    assert.equal($('.billing-payment-type').length,0);
    assert.equal($('.billing-cycle input:checked').attr('value'),'month');
    assert.deepEqual($('.billing-cycle input').toArray().map(el=>$(el).attr('value')),['month']);
    assert.equal($('.subscription-card.paid').length,2);
    for(const [index,plan] of ['plus','pro'].entries()){
      const offer=quotes.find(offer=>offer.plan_id===plan)!;
      const card=$('.subscription-card.paid').eq(index);
      assert.equal(card.find('[data-purchase-link]').attr('href'),localPath(`/pricing/?price=${offer.id}`,locale));
      assert.equal(card.find('.price-value').text(),amount(offer,locale));
      assert.equal(card.find('.monthly-equivalent').length,0);
    }
    assert.ok($('.billing-cycle').text().includes(prepaidMembershipCopy(locale).month));
    const pending=quotes[0];
    const recovered=pricingSnapshot({...status,pending_checkout:original(pending)},locale,'',null);
    assert.equal(recovered('.billing-payment-type').length,0);
    assert.equal(recovered(`.subscription-card.paid[data-plan="${pending.plan_id}"] [data-purchase-link]`).attr('href'),localPath(`/pricing/?price=${pending.id}`,locale));
    assert.ok(recovered('.subscription-card.paid [data-purchase-link]').text().includes('resume original'));
  }
});
