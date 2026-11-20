import {useEffect,useState} from 'react';
import {amount,offerBenefits,offerLabel,renewalCopy,findCheckoutOffer,isOneTimePrice,membershipCheckoutBlocked,manageLabel,type BillingCatalog,type Billing} from '../lib/billing';
import {publishedPricingCopy} from '../lib/pricing';
import {comparisonCopy,comparisonRows,extraPagesCopy,paidComparisonValue} from '../lib/pricing-comparison';
import {pricingHighlightsCopy} from '../i18n/pricing-highlights';
import {checkoutStatusCopy} from '../i18n/commerce';
import {publishedSubscriptions,publishedPacks,publishedCurrency} from '../data/published-plans';
import BillingCycle,{type BillingInterval} from './BillingCycle';
import {membershipIntervals,membershipPricing} from '../lib/billing-cycle';
import {PlanPrice,PublishedPurchaseAvailability} from './PublishedPlanPricing';
import PriceAmount from './PriceAmount';
import CheckoutButton, { type CheckoutCopy } from './CheckoutButton';
import QuotaOffers from './QuotaOffers';
import PlanComparison from './PlanComparison';
import {quotaPurchaseCopy} from '../i18n/quota-purchase';
import {subscriptionQuotaCopy} from '../i18n/subscription-quota';
import {prepaidMembershipCopy} from '../i18n/prepaid-membership';
import '../styles/pricing.css';
import '../styles/quota-offers.css';

interface Props {
  locale:string;
  accountHref:string;
  downloadHref:string;
  checkoutCopy:CheckoutCopy;
  free:{name:string;description:string;action:string;note:string;priceLabel:string};
}

export default function BillingOffers({locale,accountHref,downloadHref,free,checkoutCopy}:Props){
  const [catalog,setCatalog]=useState<BillingCatalog>(),[error,setError]=useState(false);
  const [billing,setBilling]=useState<Billing|null>();
  const [preferred,setPreferred]=useState<BillingInterval>();
  const [selectedPrice,setSelectedPrice]=useState('');
  const comparison=comparisonCopy(locale);
  const quota=subscriptionQuotaCopy(locale);
  const prepaid=prepaidMembershipCopy(locale);
  const highlights=pricingHighlightsCopy(locale);
  const {retry,retryHint}=quotaPurchaseCopy(locale);
  const buttonCopy={...checkoutCopy,...checkoutStatusCopy(locale),retry,retryHint};
  useEffect(()=>{
    setSelectedPrice(new URLSearchParams(location.search).get('price')??'');
    const controller=new AbortController();
    let disposed=false;
    let unsubscribe:(()=>void)|undefined,epoch=0;
    const timeout=setTimeout(()=>{setError(true);controller.abort();},10000);
    fetch('/v1/billing/catalog',{signal:controller.signal,cache:'no-store'}).then(async r=>{
      if(!r.ok)throw Error();
      const catalog:BillingCatalog=await r.json();
      if(!Array.isArray(catalog.offers)||!Array.isArray(catalog.quota_offers))throw Error();
      setCatalog({...catalog,offers:catalog.offers.filter(offer=>offer.interval!=='once'&&offer?.channels?.length>0),
        quota_offers:catalog.quota_offers.filter(offer=>offer.interval==='once'&&offer?.channels?.length>0)});
    }).catch(()=>{if(!controller.signal.aborted)setError(true);}).finally(()=>clearTimeout(timeout));
    // Account-aware checkout belongs only to the pricing page. Public home
    // previews do not load identity or billing status.
    void import('../lib/direct-checkout').then(({checkoutStatus,subscribeAuth})=>{
      if(disposed)return;
      const load=async()=>{
        const current=++epoch;
        setBilling(undefined);
        try{const value=await checkoutStatus();if(!disposed&&current===epoch)setBilling(value);}
        catch{if(!disposed&&current===epoch)setBilling(null);}
      };
      unsubscribe=subscribeAuth(()=>void load());
      void load();
    }).catch(()=>{if(!disposed)setBilling(null);});
    return()=>{disposed=true;unsubscribe?.();clearTimeout(timeout);controller.abort();};
  },[]);
  const outstanding=[billing?.pending_checkout?.price,billing?.subscription_checkout?.price];
  const pending=outstanding.find(price=>price&&price.interval!=='once'&&isOneTimePrice(price));
  const recurring=outstanding.find(price=>price&&!isOneTimePrice(price));
  // A loaded catalog, including an empty one, replaces the build-time preview.
  const quotes=(billing?.offers??catalog?.offers??(error?[]:publishedSubscriptions)).filter(offer=>offer.interval!=='once'&&isOneTimePrice(offer)&&offer.id!==pending?.id);
  if(pending)quotes.push(pending);
  const requested=quotes.find(offer=>offer.id===selectedPrice);
  const interval=requested?.interval??preferred??pending?.interval;
  const cadence=interval&&interval!=='once'?interval:membershipIntervals(quotes)[0]??'month';
  const {display,plans}=membershipPricing(quotes,cadence,selectedPrice||(preferred===undefined?pending?.id:undefined));
  const availability=error?'error':catalog||billing?'unavailable':'loading';
  const recoveries=[recurring,pending&&!plans.some(offer=>offer.id===pending.id&&(!billing||!membershipCheckoutBlocked(billing,offer)))?pending:undefined].filter(price=>!!price);
  const recoverSelected=!!billing&&/^[\w-]{1,36}$/.test(selectedPrice)&&
    (!findCheckoutOffer(billing,selectedPrice)||
      !!requested&&isOneTimePrice(requested)&&membershipCheckoutBlocked(billing,requested));
  const rows=comparisonRows(locale,0).filter(row=>row.key==='model'||row.key==='rate'||row.key==='priority');
  return <div className="pricing-comparison">
    <BillingCycle offers={display} value={cadence} onChange={value=>{setPreferred(value);setSelectedPrice('');}} locale={locale}/>
    {!plans.length&&<p className="billing-status" role={error?'alert':'status'}>{publishedPricingCopy(locale)[availability]}</p>}
    {recoveries.map(price=><div className="billing-recovery" key={price.id}>
      {price===recurring&&<p className="billing-status" role="status">{buttonCopy.pending}</p>}
      <p>{offerLabel(price,locale)} · {renewalCopy(price,locale)}</p>
      <CheckoutButton priceId={price.id} accountHref={accountHref} label={checkoutCopy.resume??prepaid.buy.replace('{0}',price.name)} copy={buttonCopy} showDisclosure={false} continueAfterLogin={false}/>
    </div>)}
    <div className="subscription-grid">
      <article className="subscription-card free">
        <header><p className="membership-heading">{highlights.free}</p><h2>{free.name}</h2></header>
        <div><p className="price"><PriceAmount parts={new Intl.NumberFormat(locale,{style:'currency',currency:plans[0]?.currency??publishedCurrency,maximumFractionDigits:0}).formatToParts(0)}/><small className="price-unit">/ {free.priceLabel}</small></p><p className="price-terms">{free.description}</p></div>
        <div className="plan-allowance"><span>{quota.free}</span><strong className="subscription-allowance">{comparison.classic.free}</strong></div>
        <div className="subscription-purchase"><a className="button secondary" data-install-extension href={downloadHref}>{free.action}<span className="ui-icon icon-arrow" aria-hidden="true"/></a><p className="trial-note">{free.note}</p></div>
        <ul className="subscription-features">{rows.map(row=><li key={row.key} data-feature={row.key}><span>{row.label}</span><strong>{row.free}</strong></li>)}</ul>
      </article>
      {plans.map(offer=>{
        const extra=extraPagesCopy(offer,plans,locale),managed=!!billing&&membershipCheckoutBlocked(billing,offer);
        const yearly=display.find(p=>p.plan_id===offer.plan_id&&p.plan_revision_id===offer.plan_revision_id&&p.interval==='year'&&p.currency===offer.currency);
        return <article className="subscription-card paid" data-plan={offer.plan_id} data-billing-type="onetime" data-more-pages={!!extra} key={offer.id}>
        <header><p className="membership-heading">{extra?highlights.pro:highlights.plus}</p><h2>{offer.name}</h2></header>
        <div aria-live="polite"><PlanPrice locale={locale} offer={offer} offers={display}/>
          {yearly&&<noscript><p className="billing-total">{prepaid.year}: {amount(yearly,locale)}</p></noscript>}
        </div>
        <div className="plan-allowance"><span>{prepaid.allowance}</span><strong className="subscription-allowance">{offerBenefits(offer,locale)}</strong>{extra&&<p className="plan-difference">{extra}</p>}</div>
        <div className="subscription-purchase">
        {offer.channels.length>0?<>
        {managed?<a className="button" href={accountHref}>{manageLabel(locale)} ↗</a>:<CheckoutButton priceId={offer.id} accountHref={accountHref} label={pending?.id===offer.id?checkoutCopy.resume??prepaid.buy.replace('{0}',offer.name):prepaid.buy.replace('{0}',offer.name)} copy={buttonCopy} disabled={billing===undefined||billing?.enabled===false||!!recurring&&pending?.id!==offer.id}/>}
        <p className="trial-note">{prepaid.notice} {publishedPricingCopy(locale).tax}</p>
        </>:<PublishedPurchaseAvailability locale={locale} name={offer.name} state={availability}/>}
        </div>
        <ul className="subscription-features">{rows.map(row=><li key={row.key} data-feature={row.key}><span>{row.label}</span><strong>{paidComparisonValue(row,offer,locale)}</strong></li>)}</ul>
      </article>;})}
    </div>
    <PlanComparison locale={locale} plans={plans} freeName={free.name}/>
    <p className="comparison-note">{quota.rule}</p>
    <p className="comparison-note">{prepaid.quotaRule}</p>
    <p className="comparison-note">{comparison.note}</p>
    {recoverSelected&&!recoveries.some(price=>price.id===selectedPrice)&&<div className="billing-recovery"><CheckoutButton priceId={selectedPrice} accountHref={accountHref} label={checkoutCopy.resume??retry} copy={buttonCopy} showDisclosure={false} continueAfterLogin={false}/></div>}
    <QuotaOffers preview locale={locale} offers={billing?.quota_offers??catalog?.quota_offers??(error?[]:publishedPacks)} blocked={billing===undefined||billing?.enabled===false} accountHref={accountHref} checkoutCopy={buttonCopy}/>
  </div>;
}
