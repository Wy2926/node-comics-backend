import {isOneTimePrice,type BillingOffer,type BillingPrice} from './billing';

export type BillingInterval=Exclude<BillingPrice['interval'],'once'>;
export const membershipMonths={month:1,quarter:3,year:12};

export const membershipIntervals=(offers:BillingOffer[])=>
  (Object.keys(membershipMonths) as BillingInterval[]).filter(interval=>offers.some(offer=>offer.interval===interval));

export function membershipPricing(quotes:BillingOffer[],interval:BillingInterval,selectedPrice=''){
  const display=quotes.filter(offer=>offer.interval!=='once'&&isOneTimePrice(offer));
  const plans=[...new Set(display.map(offer=>offer.plan_id))].flatMap(plan=>{
    const matches=display.filter(offer=>offer.plan_id===plan&&offer.interval===interval);
    const offer=matches.find(offer=>offer.id===selectedPrice)??matches[0];
    return offer?[offer]:[];
  });
  return {display,plans};
}
