import type {BillingOffer} from '../lib/billing';
import {comparisonCopy,comparisonRows,extraPagesCopy,paidComparisonValue} from '../lib/pricing-comparison';
import FeatureInfo from './FeatureInfo';

export default function PlanComparison({locale,plans,freeName}:{locale:string;plans:BillingOffer[];freeName:string}){
  const copy=comparisonCopy(locale),rows=comparisonRows(locale,0);
  return <div className="comparison-scroll" role="region" aria-label={copy.feature} tabIndex={0}>
    <table className="plan-comparison">
      <caption>{copy.feature}</caption>
      <colgroup><col className="feature-column"/>{[freeName,...plans.map(plan=>plan.id)].map((key,index)=><col key={`${key}-${index}`}/>)}</colgroup>
      <thead><tr><th scope="col">{copy.feature}</th><th scope="col">{freeName}</th>{plans.map(plan=><th scope="col" key={plan.id}>{plan.name}</th>)}</tr></thead>
      <tbody>
        {rows.map(row=><tr key={row.key} data-feature={row.key}>
          <th scope="row">{row.label}{row.detail&&<FeatureInfo label={row.label} detail={row.detail}/>}</th>
          <td><strong>{row.free}</strong></td>
          {plans.map(plan=>{const value=paidComparisonValue(row,plan,locale),extra=row.key==='classic'?extraPagesCopy(plan,plans,locale):'';return <td key={plan.id} data-advantage={value!==row.free} data-more-pages={!!extra}>
            <strong>{value}</strong>{extra&&<small className="comparison-delta">{extra}</small>}
          </td>;})}
        </tr>)}
      </tbody>
    </table>
  </div>;
}
