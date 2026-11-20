import {isOneTimePrice,type BillingOffer,type BillingPrice} from './billing';

export type BillingInterval=Exclude<BillingPrice['interval'],'once'>;
export const membershipMonths={month:1,quarter:3,year:12};

export const membershipIntervals=(offers:BillingOffer[])=>
  (Object.keys(membershipMonths) as BillingInterval[]).filter(interval=>offers.some(offer=>offer.interval===interval));

export function offerForInterval(offers:BillingOffer[],interval:BillingInterval,selectedPrice=''){
  return offers.find(offer=>offer.interval===interval&&offer.id===selectedPrice)
    ??offers.find(offer=>offer.interval===interval);
}

export function membershipPricing(quotes:BillingOffer[],interval:BillingInterval,selectedPrice=''){
  const display=quotes.filter(offer=>offer.interval!=='once'&&isOneTimePrice(offer));
  const plans=[...new Set(display.map(offer=>offer.plan_id))].map(plan=>
    offerForInterval(display.filter(offer=>offer.plan_id===plan),interval,selectedPrice)).filter((offer):offer is BillingOffer=>!!offer);
  return {display,plans};
}
