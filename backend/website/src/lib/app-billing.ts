import {ApiError} from './auth-session';
import type {api as websiteApi} from './auth';
import {basePath} from '../i18n/locales';

export function isAppPricing() {
  return typeof location !== 'undefined' && basePath(location.pathname) === '/pricing/' &&
    new URLSearchParams(location.search).get('app') === '1';
}

const slot = 'nc-app-billing';
const secret = /^[A-Za-z0-9_-]{43}$/;
const expired = () => new ApiError('BILLING_LINK_EXPIRED', 401, 'BILLING_LINK_EXPIRED');
type Session = {account_id:string; session:string};
let opening:Promise<Session>|undefined;
let generation=0;

function checked(value: Partial<Session>|null): Session {
  if (typeof value?.account_id!=='string' || !value.account_id ||
      typeof value.session!=='string' || !secret.test(value.session)) throw expired();
  return {account_id:value.account_id,session:value.session};
}

export async function appBillingSession(): Promise<Session> {
  if (!isAppPricing()) throw expired();
  const fragment = location.hash;
  if (fragment.startsWith('#app-billing=')) {
    const current=++generation;
    opening=undefined;
    history.replaceState(history.state, '', location.pathname + location.search);
    sessionStorage.removeItem(slot);
    const code = fragment.slice('#app-billing='.length);
    if (!secret.test(code)) throw expired();
    const request = (async () => {
      const response = await fetch('/v1/billing/app-handoff/redeem', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({code}),
        credentials:'omit', redirect:'error', cache:'no-store', signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw expired();
      const value = checked(await response.json());
      if (current!==generation) throw expired();
      sessionStorage.setItem(slot, JSON.stringify(value));
      return value;
    })();
    opening=request;
    try {return await request;} finally {if(opening===request) opening=undefined;}
  }
  if (opening) return opening;
  try { return checked(JSON.parse(sessionStorage.getItem(slot)??'null')); }
  catch { throw expired(); }
}

export function appBillingRequest(bound:Session): typeof websiteApi {
  return async <T>(path:string, method:'GET'|'POST'='GET', body?:unknown, headers?:Record<string,string>) => {
    if (!(method==='GET' && /^\/v1\/billing\/status(?:\?price_id=[\w-]{1,36})?$/.test(path) ||
          method==='POST' && path==='/v1/billing/checkouts')) throw expired();
    if ((await appBillingSession()).session!==bound.session) throw expired();
    const response = await fetch(path,{method,credentials:'omit',redirect:'error',cache:'no-store',
      signal:AbortSignal.timeout(20000),headers:{...headers,Authorization:'Billing '+bound.session,
        ...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
    const value=await response.json();
    if ((await appBillingSession()).session!==bound.session) throw expired();
    if (!response.ok) throw new ApiError('Checkout unavailable',response.status,value?.error?.code);
    return value as T;
  };
}
