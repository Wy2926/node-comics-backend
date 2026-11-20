import {subscriptionQuotaCopy} from '../src/i18n/subscription-quota';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {annualSavings,amount,billingCopy,findCheckoutOffer,selectedChannel,hasManagedSubscription,offerBenefits,offerLabel,renewalCopy,type Billing,type BillingOffer} from '../src/lib/billing';
import {quotaPurchase} from '../src/i18n/quota-purchase';
import {membershipPricing} from '../src/lib/billing-cycle';
import {publishedSubscriptions} from '../src/data/published-plans';
import {prepaidMembershipCopy} from '../src/i18n/prepaid-membership';
import {locales} from '../src/i18n/locales';

test('all purchase locales preserve placeholders and do not describe a page pack as a subscription',()=>{
  const pack={id:'pack',name:'Pages',plan_id:'pack',plan_revision_id:'pack-v1',currency:'usd',unit_amount:123,interval:'once',quota_pages:100,quota_validity_days:null,service_plan_id:'lite',monthly_classic_pages:0,trial_days:0,trial_classic_pages:0,channels:[]} as BillingOffer;
  for(const locale of locales){
    assert.deepEqual(Object.keys(quotaPurchase[locale]),Object.keys(quotaPurchase.en),locale);
    for(const [key,value] of Object.entries(quotaPurchase.en)){
      const translation=quotaPurchase[locale][key as keyof typeof quotaPurchase.en];
      assert.ok(translation.trim(),locale+key);
      assert.deepEqual(translation.match(/\{\d+\}/g)?.sort()??[],value.match(/\{\d+\}/g)?.sort()??[],locale+key);
      // “pages” is the same word in French and English; all prose remains localized.
      if(locale!=='en'&&!(locale==='fr'&&key==='pages'))assert.notEqual(translation,value,locale+key);
    }
    assert.equal(renewalCopy(pack,locale),quotaPurchase[locale].once);
    assert.equal(offerBenefits(pack,locale),quotaPurchase[locale].pages.replace('{0}',(100).toLocaleString(locale)));
    assert.equal(offerLabel(pack,locale),`Pages · ${amount(pack,locale)}`);
    assert.equal(annualSavings(pack,[pack]),null);
  }
});

test('channel selection never changes a pending checkout provider',()=>{
  const offer={channels:[{provider:'stripe',binding_id:'stripe'},{provider:'creem',binding_id:'creem'}]} as BillingOffer;
  assert.equal(selectedChannel(offer,'creem')?.provider,'creem');
  assert.equal(selectedChannel({...offer,channels:[offer.channels[0]]},'creem'),undefined);
  assert.equal(selectedChannel({...offer,channels:[]},'creem'),undefined);
  assert.equal(selectedChannel(offer)?.provider,'stripe');
});

test('checkout quote lookup preserves pending priority and includes prepaid and quota offers',()=>{
  const published=publishedSubscriptions[0];
  const current={...published,id:'same-price',channels:[{provider:'creem' as const,binding_id:'current',trial_days:0,trial_classic_pages:0}]};
  const pending={...current,channels:[{...current.channels[0],provider:'stripe' as const,binding_id:'original'}]};
  const prepaid={...current,id:'prepaid',billing_type:'onetime' as const};
  const pack={...current,id:'pack',interval:'once' as const};
  const billing={subscription_checkout:{price:pending},offers:[current,prepaid],quota_offers:[pack]} as Billing;
  assert.equal(findCheckoutOffer(billing,current.id),pending);
  assert.equal(findCheckoutOffer({...billing,subscription_checkout:null},current.id),current);
  assert.equal(findCheckoutOffer(billing,prepaid.id),prepaid);
  assert.equal(findCheckoutOffer(billing,pack.id),pack);
  assert.equal(findCheckoutOffer(billing,'retired'),undefined);
  assert.equal(findCheckoutOffer({...billing,quota_offers:undefined},pack.id),undefined);
  const original={...prepaid,channels:pending.channels};
  const withPurchase={...billing,pending_checkout:{price:original}} as Billing;
  assert.equal(findCheckoutOffer(withPurchase,prepaid.id),original);
  assert.equal(findCheckoutOffer(withPurchase,current.id),pending);
  assert.equal(findCheckoutOffer({...billing,pending_checkout:{price:pack}} as Billing,pack.id),pack);
});

test('membership pricing derives exact quotes and never revives unpublished prices',()=>{
  const base=publishedSubscriptions.find(offer=>offer.plan_id==='plus'&&offer.interval==='quarter')!;
  const recurring={...base,id:'live-quarter',billing_type:'recurring' as const,channels:[{provider:'creem' as const,binding_id:'live',trial_days:0,trial_classic_pages:0}]};
  const prepaid={...recurring,id:'prepaid-quarter',billing_type:'onetime' as const};
  const alternate={...prepaid,id:'prepaid-exact'};
  const pro={...prepaid,id:'prepaid-pro',plan_id:'pro'};
  const year={...prepaid,id:'prepaid-year',interval:'year' as const};
  const quotes=[recurring,prepaid,alternate,pro,year],original=structuredClone(quotes);
  const selected=membershipPricing(quotes,'quarter',alternate.id);
  assert.deepEqual(selected.display,[prepaid,alternate,pro,year]);
  assert.deepEqual(selected.plans,[alternate,pro]);
  assert.deepEqual(membershipPricing(quotes,'quarter',recurring.id).plans,[prepaid,pro]);
  assert.deepEqual(membershipPricing(quotes,'year',alternate.id).plans,[year]);

  for(const available of [[],[recurring],[year]]){
    const missing=membershipPricing(available,'quarter');
    assert.deepEqual(missing.plans,[]);
    assert.deepEqual(missing.display,available[0]===year?[year]:[]);
  }
  assert.deepEqual(quotes,original);
  const pack={...prepaid,interval:'once' as const};
  assert.deepEqual(membershipPricing([pack],'month').display,[]);
});

test('expired and revoked terminal subscriptions allow another purchase',()=>{
  const now=Date.parse('2026-09-20T00:00:00Z');
  for(const status of ['active','trialing','paused','past_due','unpaid','incomplete','scheduled_cancel'])assert.equal(hasManagedSubscription(status,null,now),true);
  for(const status of ['canceled','expired','incomplete_expired']){
    assert.equal(hasManagedSubscription(status,null,now),false);
    assert.equal(hasManagedSubscription(status,'2026-09-19T00:00:00Z',now),false);
    assert.equal(hasManagedSubscription(status,'2026-09-21T00:00:00Z',now),true);
  }
  assert.equal(hasManagedSubscription(undefined,null,now),false);
});

test('Stripe minor units render correctly in public and account prices',()=>{
  const offer:BillingOffer={id:'test',name:'Sample',plan_id:'plus',plan_revision_id:'plus-v1',currency:'usd',unit_amount:999,interval:'year',monthly_classic_pages:300,trial_days:0,trial_classic_pages:0,channels:[{provider:'stripe',binding_id:'stripe-fixture',trial_days:7,trial_classic_pages:30},{provider:'creem',binding_id:'creem-fixture',trial_days:7,trial_classic_pages:30}]};
  for(const [currency,unit_amount,value] of [['usd',999,9.99],['jpy',500,500],['isk',500,5],['ugx',500,5],['ISK',500,5],['UGX',500,5]] as const)
    assert.equal(amount({...offer,currency,unit_amount},'en'),new Intl.NumberFormat('en',{style:'currency',currency}).format(value));
});

test('prepaid membership keeps the existing monthly allowance and has localized nonrenewing terms',()=>{
  const quarter:BillingOffer={id:'quarter-prepaid',plan_id:'plus',plan_revision_id:'plus-v1',name:'PLUS',currency:'usd',unit_amount:666,interval:'quarter',billing_type:'onetime',monthly_classic_pages:2500,trial_days:0,trial_classic_pages:0,channels:[]};
  const year={...quarter,id:'year-prepaid',interval:'year' as const,unit_amount:2399};
  for(const locale of locales){
    const copy=prepaidMembershipCopy(locale);
    assert.equal(renewalCopy(quarter,locale),copy.notice,locale);
    assert.equal(offerBenefits(quarter,locale),subscriptionQuotaCopy(locale).monthly.replace('{0}',(2500).toLocaleString(locale)),locale);
    for(const [key,value] of Object.entries(copy)){
      assert.ok(value.trim(),locale+key);
      assert.deepEqual(value.match(/\{\d+\}/g)?.sort()??[],prepaidMembershipCopy('en')[key as keyof typeof copy].match(/\{\d+\}/g)?.sort()??[],locale+key);
    }
  }
  assert.equal(annualSavings(year,[{...quarter,billing_type:'recurring'}]),null);
  assert.deepEqual(annualSavings(year,[quarter]),{regular:2664,saved:265,percent:9.9});
  assert.equal(renewalCopy({...quarter,billing_type:'recurring'},'zh-CN'),'每三个月自动续费。');
});

test('the public preview contains only the six prepaid memberships',()=>{
  assert.equal(publishedSubscriptions.length,6);
  assert.ok(publishedSubscriptions.every(offer=>offer.billing_type==='onetime'));
  for(const interval of ['month','quarter','year'] as const)
    assert.equal(membershipPricing(publishedSubscriptions,interval).plans.length,2);
});

test('annual savings compare the same published benefits and currency',()=>{
  const month:BillingOffer={id:'month',plan_id:'plus',plan_revision_id:'plus-v1',name:'PLUS',currency:'usd',unit_amount:999,interval:'month',monthly_classic_pages:300,trial_days:7,trial_classic_pages:30,channels:[]};
  const year:BillingOffer={...month,id:'year',interval:'year',unit_amount:9999};
  assert.deepEqual(annualSavings(year,[month,year]),{regular:11988,saved:1989,percent:16.6});
  assert.equal(annualSavings(month,[month,year]),null);
  assert.equal(annualSavings(year,[year]),null);
  for(const mismatch of [{currency:'eur'},{plan_id:'other'},{plan_revision_id:'plus-v2'},{unit_amount:0}])
    assert.equal(annualSavings(year,[{...month,...mismatch},year]),null);
  for(const unit_amount of [-1,11988,13000])assert.equal(annualSavings({...year,unit_amount},[month]),null);
  assert.deepEqual(annualSavings(year,[month,{...month,unit_amount:1099}]),annualSavings(year,[month]));
  assert.equal(amount({...year,unit_amount:year.unit_amount/12},'en'),'$8.33');
});

test('annual savings prefer actual monthly quotes over quarterly equivalents and stay within the payment type',()=>{
  for(const type of ['onetime','recurring'] as const){
    const offers=publishedSubscriptions.map(offer=>({...offer,billing_type:type}));
    for(const [plan,regular,saved,percent] of [['plus',3588,1189,33.1],['pro',4788,1189,24.8]] as const){
      const yearly=offers.find(offer=>offer.plan_id===plan&&offer.interval==='year')!;
      assert.deepEqual(annualSavings(yearly,offers),{regular,saved,percent});
      assert.deepEqual(annualSavings(yearly,offers.filter(offer=>offer.interval!=='month'))?.percent,9.9);
    }
  }
});

test('legacy subscriptions keep finite or unlimited allowances and automatic renewal',()=>{
  const lite={interval:'year' as const,hourly_image_limit:1200,monthly_classic_pages:null};
  const plus={...lite,hourly_image_limit:null,monthly_classic_pages:300};
  for(const locale of locales){
    const copy=billingCopy(locale);
    assert.equal(offerBenefits(lite,locale),copy.classic,locale);
    assert.equal(offerBenefits(plus,locale),subscriptionQuotaCopy(locale).monthly.replace('{0}',(300).toLocaleString(locale)),locale);
    assert.equal(renewalCopy(lite,locale),renewalCopy(plus,locale),locale);
    assert.equal(renewalCopy(lite,locale),copy.renew(true),locale);
  }
});
