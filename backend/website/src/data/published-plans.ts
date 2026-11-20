import catalog from '../../../app/catalog_defaults.json';
import type {BillingOffer} from '../lib/billing';

export const publishedCurrency=catalog.currency;

// Display only: only the billing API can supply active bindings and checkout IDs.
export const publishedOffers:BillingOffer[]=catalog.products.flatMap(product=>
  product.prices.map(price=>({
    id:`published-${price.id}`,name:product.name,plan_id:product.id,
    plan_revision_id:`${product.id}-v1`,currency:catalog.currency,unit_amount:price.unit_amount,
    interval:price.interval as BillingOffer['interval'],monthly_classic_pages:product.monthly_classic_pages,
    billing_type:price.billing_type as BillingOffer['billing_type'],
    service_plan_id:product.service_plan_id,service_plan_name:catalog.products.find(plan=>plan.id===product.service_plan_id)?.name??product.service_plan_id,quota_pages:product.quota_pages,quota_validity_days:null,
    hourly_image_limit:null,trial_days:0,trial_classic_pages:0,channels:[],
  })));
export const publishedSubscriptions=publishedOffers.filter(offer=>offer.interval!=='once');
export const publishedPacks=publishedOffers.filter(offer=>offer.interval==='once');
