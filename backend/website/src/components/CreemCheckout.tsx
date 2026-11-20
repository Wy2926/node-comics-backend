import {useLayoutEffect,useRef,useState} from 'react';
import {checkoutDialogCopy,checkoutMethodCopy} from '../i18n/checkout-methods';
import {prepaidMembershipCopy} from '../i18n/prepaid-membership';
import {quotaPurchaseCopy} from '../i18n/quota-purchase';
import {isCreemOrigin} from '../lib/auth-config';
import {amountParts,offerBenefits} from '../lib/billing';
import type {CreemPurchase} from '../lib/direct-checkout';
import PriceAmount from './PriceAmount';

export default function CreemCheckout({purchase:{url,offer},locale,successHref,onClose}:{
  purchase:CreemPurchase;locale:string;successHref:string;onClose:()=>void;
}){
  const dialog=useRef<HTMLDialogElement>(null),frame=useRef<HTMLIFrameElement>(null);
  const [loaded,setLoaded]=useState(false);
  const [title,hosted,close,loading]=checkoutDialogCopy(locale),[methods,,hint]=checkoutMethodCopy(locale);
  const quota=quotaPurchaseCopy(locale);
  const allowance=offerBenefits(offer,locale);
  const expiry=offer.quota_validity_days==null?quota.noExpiry:quota.validity.replace('{0}',offer.quota_validity_days.toLocaleString(locale));
  const name=offer.interval==='once'?(offer.name===String(offer.quota_pages)?allowance:offer.name):`${offer.name} · ${prepaidMembershipCopy(locale)[offer.interval]}`;
  const detail=offer.interval==='once'?(name===allowance?expiry:`${allowance} · ${expiry}`):allowance;
  const embedded=new URL(url);
  embedded.searchParams.set('locale',locale);
  const theme=typeof document!=='undefined'&&document.documentElement.dataset.siteTheme;
  if(theme&&!embedded.searchParams.has('theme'))embedded.searchParams.set('theme',theme==='midnight'?'dark':'light');
  useLayoutEffect(()=>{
    const element=dialog.current!;
    const trigger=document.activeElement;
    const overflow=document.body.style.overflow;
    function completed(event:MessageEvent){
      if(!frame.current?.contentWindow||event.source!==frame.current.contentWindow||!isCreemOrigin(event.origin))return;
      // Only the verified backend payment grants benefits; the frame may open our receipt.
      if(event.data?.source==='creem-embed'&&event.data.type==='completed')location.assign(successHref);
    }
    window.addEventListener('message',completed);
    element.showModal();
    document.body.style.overflow='hidden';
    return()=>{
      window.removeEventListener('message',completed);
      element.close();
      document.body.style.overflow=overflow;
      if(trigger instanceof HTMLElement&&trigger.isConnected)trigger.focus({preventScroll:true});
    };
  },[successHref]);
  return <dialog ref={dialog} className="payment-dialog" aria-labelledby="payment-title" onCancel={event=>{event.preventDefault();onClose();}}>
    <header className="payment-header">
      <img src="/icon-128.png" width="36" height="36" alt=""/>
      <div><small className="eyebrow">NODELANE COMICS</small><h2 id="payment-title">{title}</h2></div>
      <button type="button" className="button compact secondary payment-close" aria-label={close} title={close} onClick={onClose}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
      </button>
    </header>
    <div className="payment-body">
      <section className="payment-summary">
        <div className="payment-ticket">
          <div className="ticket-main"><div><h3>{name}</h3><p>{detail}</p></div>
            <svg className="ticket-pages" viewBox="0 0 84 80" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <g transform="rotate(12 42 40)"><path d="M15 20v47q0 4 4 4h41M21 15v46q0 4 4 4h41"/><rect x="27" y="8" width="42" height="51" rx="4"/><path d="M48 8v20l6-4 6 4V8" fill="var(--accent-soft)"/><path d="M34 41h22m-22 6h15"/></g>
            </svg>
          </div>
          <div className="ticket-stub"><small>{quota.once}</small><p className="payment-price"><bdi><PriceAmount parts={amountParts(offer,locale)}/></bdi></p></div>
        </div>
        <div className="checkout-methods">
          <strong><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18M7 15h4"/></svg>{methods}</strong>
          <span>{hint}</span>
        </div>
        <div className="payment-help"><small>{quota.tax}</small>
          <a className="text-link" href={url} referrerPolicy="no-referrer">{hosted} <span aria-hidden="true">↗</span></a>
        </div>
      </section>
      <div className="payment-frame">
        {!loaded&&<p className="payment-loading" role="status">{loading}</p>}
        <iframe ref={frame} src={embedded.href} onLoad={()=>setLoaded(true)} title={`Creem · ${title}`} allow="payment *; publickey-credentials-get *" referrerPolicy="no-referrer"/>
      </div>
    </div>
  </dialog>;
}
