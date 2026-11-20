import {useId} from 'react';
import {annualSavings,type BillingOffer} from '../lib/billing';
import {prepaidMembershipCopy} from '../i18n/prepaid-membership';
import {pricingCopy} from '../lib/pricing';
import {membershipIntervals,type BillingInterval} from '../lib/billing-cycle';

export default function BillingCycle({offers,value,onChange,locale}:{offers:BillingOffer[];value:BillingInterval;onChange:(value:BillingInterval)=>void;locale:string}){
  const id=useId(),prepaid=prepaidMembershipCopy(locale);
  const annualOffers=offers.filter(p=>p.interval==='year');
  const discount=annualOffers.length?Math.min(...annualOffers.map(p=>annualSavings(p,offers)?.percent??0)):0;
  return <div className="billing-cycle-picker"><fieldset className="billing-cycle billing-cycle-segmented"><legend>{prepaid.duration}</legend>{membershipIntervals(offers).map(cycle=>
    <label className="billing-cycle-card" key={cycle} data-selected={value===cycle}>
      <input type="radio" name={id} value={cycle} checked={value===cycle} onChange={()=>onChange(cycle)}/>
      <strong>{prepaid[cycle]}</strong>
    </label>)}</fieldset><p className="billing-cycle-saving">{discount>0&&<span className="annual-badge">{pricingCopy(locale).save(discount)}</span>}</p></div>;
}
