import {quarterlyCopy} from '../i18n/billing-cadence';
import {commerceCopy} from '../i18n/commerce';
import {quotaPurchaseCopy} from '../i18n/quota-purchase';
import {subscriptionQuotaCopy} from '../i18n/subscription-quota';
import {prepaidMembershipCopy} from '../i18n/prepaid-membership';
import {checkoutMethodCopy} from '../i18n/checkout-methods';
export type BillingProvider='stripe'|'creem';
export type BillingType='recurring'|'onetime';
export interface BillingCatalog {offers:BillingOffer[];quota_offers:BillingOffer[]}
export interface BillingChannel {provider:BillingProvider;binding_id:string;trial_days:number;trial_classic_pages:number|null}
export interface BillingPrice {id:string;name:string;currency:string;unit_amount:number;interval:'month'|'quarter'|'year'|'once';billing_type?:BillingType;service_plan_id?:string;service_plan_name?:string;quota_pages?:number;quota_validity_days?:number|null;monthly_classic_pages:number|null;trial_days:number;trial_classic_pages:number|null;plan_id?:string;hourly_image_limit?:number|null}
export interface BillingOffer extends BillingPrice {plan_id:string;plan_revision_id:string;channels:BillingChannel[]}
export interface MembershipGift {starts_at:string|null;ends_at:string|null;days:number;state:'pending'|'scheduled'|'active'|'expired'}
export interface PurchaseQuota {granted:number;used:number;reserved:number;available:number;next_expiry_at:string|null}
export interface PendingCheckout {id:string;provider:BillingProvider;price:BillingOffer;idempotency_key:string|null;error:string|null}
export interface Billing {
  enabled:boolean;providers:{id:BillingProvider;label:string;environment:'test'|'live'}[];
  provider:BillingProvider|null;environment:'test'|'live';trial_eligible:boolean;
  subscription_checkout:PendingCheckout|null;
  pending_checkout?:PendingCheckout|null;
  offers:BillingOffer[];quota_offers?:BillingOffer[];entitlement_expires_at:string|null;gift:MembershipGift|null;
  prepaid_membership?:{price:BillingPrice;starts_at:string;ends_at:string;paid_through?:string}|null;
  subscription:{provider:BillingProvider;price:BillingPrice;status:string;next_billed_at:string|null;cancel_at:string|null;paid_ends_at:string|null;trial_ends_at:string|null;auto_renew:boolean;can_cancel:boolean;renewal_state:'normal'|'deferring'|'deferred'|'resuming'|'canceling'|'canceled'|'attention';resume_at:string|null}|null;
}
export const manageLabel=(locale:string)=>commerceCopy(locale)?.manage??({'zh-CN':'管理订阅','zh-TW':'管理訂閱',en:'Manage subscription',ja:'購読を管理',ko:'구독 관리'}[locale]??'Manage subscription');
export function findPendingCheckout(billing:Billing,priceId:string){
  return [billing.pending_checkout,billing.subscription_checkout].find(checkout=>checkout?.price.id===priceId)??null;
}
export function findCheckoutOffer(billing:Billing,priceId:string){
  return findPendingCheckout(billing,priceId)?.price??billing.offers.find(offer=>offer.id===priceId)
    ??billing.quota_offers?.find(offer=>offer.id===priceId);
}
export function selectedChannel(offer:BillingOffer|undefined,provider?:BillingProvider){
  return provider?offer?.channels.find(channel=>channel.provider===provider):offer?.channels[0];
}
export function hasManagedSubscription(status:string|undefined,entitlementExpiresAt:string|null|undefined,now=Date.now()){
  return !!status&&(['active','trialing','past_due','unpaid','paused','incomplete','scheduled_cancel'].includes(status)||!!entitlementExpiresAt&&Date.parse(entitlementExpiresAt)>now);
}
export const isOneTimePrice=(price:Pick<BillingPrice,'interval'|'billing_type'>)=>price.billing_type==='onetime'||price.interval==='once';
export function checkoutMethods(offer:BillingOffer,locale:string,billing?:Billing|null){
  const pending=billing&&findPendingCheckout(billing,offer.id);
  const price=pending?.price??offer,provider=selectedChannel(price,pending?.provider)?.provider;
  const [alipayAndCard,card,hint]=checkoutMethodCopy(locale);
  // Creem offers Alipay for one-time payments; our Stripe sessions request cards.
  if(provider==='creem')return {label:isOneTimePrice(price)?alipayAndCard:card,hint};
  if(provider==='stripe')return {label:card};
}
export const hasPrepaidMembership=(billing:Billing,now=Date.now())=>!!billing.prepaid_membership&&Date.parse(billing.prepaid_membership.paid_through??billing.prepaid_membership.ends_at)>now;
export function hasCurrentSubscription(billing:Billing){
  const subscription=billing.subscription;
  // Aggregate entitlement may belong to prepaid time, not a historical subscription.
  const subscriptionEnd=billing.prepaid_membership?subscription?.paid_ends_at??subscription?.trial_ends_at:billing.entitlement_expires_at;
  return hasManagedSubscription(subscription?.status,subscriptionEnd);
}
export function membershipCheckoutBlocked(billing:Billing,price:BillingPrice){
  return price.interval!=='once'&&(hasCurrentSubscription(billing)||(!!billing.gift&&billing.gift.state!=='expired')||(!isOneTimePrice(price)&&hasPrepaidMembership(billing)));
}
export function subscriptionStatusLabel(status:string,locale:string){
  const index:Record<string,number>={active:0,trialing:1,past_due:2,unpaid:2,incomplete:2,paused:3,scheduled_cancel:4,canceled:5,expired:6,incomplete_expired:6};
  const text:Record<string,string[]>={
    'zh-CN':['订阅生效中','试用中','账单待处理','订阅已暂停','已安排取消续费','已取消续费','订阅已到期','正在核实订阅'],
    'zh-TW':['訂閱生效中','試用中','帳單待處理','訂閱已暫停','已安排取消續訂','已取消續訂','訂閱已到期','正在確認訂閱'],
    en:['Active','Trialing','Payment pending','Subscription paused','Cancellation scheduled','Renewal canceled','Subscription ended','Checking subscription'],
    ja:['有効','体験期間中','支払い確認待ち','購読一時停止中','更新停止予定','自動更新停止済み','購読終了','購読を確認中'],
    ko:['활성','체험 중','결제 대기','구독 일시 중지','갱신 취소 예정','갱신 취소됨','구독 종료','구독 확인 중'],
  };
  return (commerceCopy(locale)?.statuses??text[locale]??text.en)[index[status]??7];
}
const labels:Record<string,{month:string;year:string;loading:string;classic:string;renew:(annual:boolean)=>string}>={
  'zh-CN':{month:'月',year:'年',loading:'正在读取套餐…',classic:'翻译不限页数',renew:a=>a?'按年自动续费。':'按月自动续费。'},
  'zh-TW':{month:'月',year:'年',loading:'正在讀取方案…',classic:'翻譯不限頁數',renew:a=>a?'按年自動續訂。':'按月自動續訂。'},
  en:{month:'month',year:'year',loading:'Loading plans…',classic:'Unlimited translation',renew:a=>a?'Renews annually.':'Renews monthly.'},
  ja:{month:'月',year:'年',loading:'プランを読み込み中…',classic:'翻訳はページ数無制限',renew:a=>a?'毎年自動更新。':'毎月自動更新。'},
  ko:{month:'월',year:'년',loading:'요금제 불러오는 중…',classic:'번역 페이지 무제한',renew:a=>a?'매년 자동 갱신됩니다.':'매월 자동 갱신됩니다.'},
};
export function billingCopy(locale:string) {
 const copy=commerceCopy(locale)?.billing;
 return copy ? {...copy,renew:(annual:boolean)=>annual?copy.renewAnnual:copy.renewMonthly} : labels[locale]??labels.en;
}
const hourlyLabels:Record<string,string>={
  'zh-CN':'每滚动小时最多新增 {n} 页翻译','zh-TW':'每滾動小時最多新增 {n} 頁翻譯',
  en:'Up to {n} new translation pages per rolling hour',ja:'直近1時間の新規翻訳受付は最大 {n} ページ',ko:'최근 1시간 동안 새 번역 요청 최대 {n}페이지',
};
export function hourlyLimitCopy(limit:number,locale:string){
  return (commerceCopy(locale)?.hourly??hourlyLabels[locale]??hourlyLabels.en).replace('{n}',limit.toLocaleString(locale));
}
export function offerBenefits(offer:Pick<BillingPrice,'hourly_image_limit'> & Partial<Pick<BillingPrice,'interval'|'quota_pages'|'monthly_classic_pages'>>,locale:string){
  if(offer.interval==='once')return quotaPurchaseCopy(locale).pages.replace('{0}',(offer.quota_pages??0).toLocaleString(locale));
  return offer.monthly_classic_pages!=null?subscriptionQuotaCopy(locale).monthly.replace('{0}',offer.monthly_classic_pages.toLocaleString(locale)):billingCopy(locale).classic;
}
export function renewalCopy(offer:Pick<BillingPrice,'interval'|'billing_type'>,locale:string){
  if(offer.interval==='once')return quotaPurchaseCopy(locale).once;
  if(isOneTimePrice(offer))return prepaidMembershipCopy(locale).notice;
  return offer.interval==='quarter'?quarterlyCopy(locale).renewal:billingCopy(locale).renew(offer.interval==='year');
}
export function amountParts(offer:Pick<BillingPrice,'currency'|'unit_amount'>,locale:string){
  const formatter=new Intl.NumberFormat(locale,{style:'currency',currency:offer.currency});
  const digits=['isk','ugx'].includes(offer.currency.toLowerCase())?2:formatter.resolvedOptions().maximumFractionDigits??2;
  return formatter.formatToParts(offer.unit_amount/10**digits);
}
export function amount(offer:Pick<BillingPrice,'currency'|'unit_amount'>,locale:string){return amountParts(offer,locale).map(part=>part.value).join('');}
export const offerLabel=(offer:BillingPrice,locale:string)=>`${offer.name} · ${amount(offer,locale)}${offer.interval==='once'?'':` / ${offer.interval==='quarter'?quarterlyCopy(locale).unit:offer.interval==='year'?billingCopy(locale).year:billingCopy(locale).month}`}`;

// Compare published quotes only when they buy the same benefits in the same currency.
export function annualSavings(offer:BillingOffer,offers:BillingOffer[]){
  if(offer.interval!=='year')return null;
  const comparable=offers.filter(p=>isOneTimePrice(p)===isOneTimePrice(offer)&&p.plan_id===offer.plan_id&&p.plan_revision_id===offer.plan_revision_id&&p.currency===offer.currency&&p.unit_amount>0);
  const monthly=comparable.filter(p=>p.interval==='month');
  const baseline=monthly.length?monthly:comparable.filter(p=>p.interval==='quarter');
  if(!baseline.length)return null;
  const regular=Math.min(...baseline.map(p=>p.unit_amount*(p.interval==='quarter'?4:12)));
  const saved=regular-offer.unit_amount;
  return offer.unit_amount>=0&&saved>0?{regular,saved,percent:Math.round(saved/regular*1000)/10}:null;
}
