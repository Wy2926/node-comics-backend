import { commerceCopy, formatCopy } from '../i18n/commerce';
const labels={
  'zh-CN':{monthly:'折合每月',save:(p:number)=>`年付约省 ${p}%`},
  'zh-TW':{monthly:'平均每月',save:(p:number)=>`年付約省 ${p}%`},
  en:{monthly:'Monthly equivalent',save:(p:number)=>`Save ≈${p}% yearly`},
  ja:{monthly:'月額換算',save:(p:number)=>`年払いで約 ${p}% お得`},
  ko:{monthly:'월 환산 요금',save:(p:number)=>`연간 약 ${p}% 절약`},
};
export function pricingCopy(locale:string) {
  const copy=commerceCopy(locale)?.pricing;
  return copy ? {monthly:copy.monthly,save:(n:number)=>formatCopy(copy.save,{n})} : labels[locale as keyof typeof labels]??labels.en;
}
const publishedLabels={
  'zh-CN':{loading:'正在确认购买状态…',unavailable:'在线订阅当前不可用',error:'暂时无法确认购买状态，请刷新重试。已公布价格仍可查看。',tax:'税费及最终应付金额以结账页为准。'},
  'zh-TW':{loading:'正在確認購買狀態…',unavailable:'線上訂閱目前無法使用',error:'暫時無法確認購買狀態，請重新整理。仍可查看已公布價格。',tax:'稅費及最終應付金額以結帳頁為準。'},
  en:{loading:'Checking purchase availability…',unavailable:'Online subscriptions are currently unavailable',error:'Unable to confirm purchase availability. Please refresh. Published prices remain visible.',tax:'Taxes and the final amount are shown at checkout.'},
  ja:{loading:'購入受付状況を確認中…',unavailable:'オンライン購読は現在利用できません',error:'購入受付状況を確認できません。再読み込みしてください。公開価格は引き続きご覧いただけます。',tax:'税金と最終支払額は決済画面に表示されます。'},
  ko:{loading:'구매 가능 여부 확인 중…',unavailable:'현재 온라인 구독을 이용할 수 없습니다',error:'구매 가능 여부를 확인할 수 없습니다. 새로고침해 주세요. 공개 요금은 계속 확인할 수 있습니다.',tax:'세금 및 최종 결제 금액은 결제 화면에 표시됩니다.'},
};
export const publishedPricingCopy=(locale:string)=>commerceCopy(locale)?.publishedPricing??publishedLabels[locale as keyof typeof publishedLabels]??publishedLabels.en;
