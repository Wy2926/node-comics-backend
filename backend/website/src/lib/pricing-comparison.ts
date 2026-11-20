import {subscriptionQuotaCopy} from '../i18n/subscription-quota';
import { commerceCopy, formatCopy } from '../i18n/commerce';
import {pricingHighlightsCopy} from '../i18n/pricing-highlights';
import type {Locale} from '../i18n/types';
import {hourlyLimitCopy,offerBenefits,type BillingOffer} from './billing';

interface ComparisonRow {label:string;free:string;lite:string;detail?:string}
export interface PricingComparisonCopy {
  feature:string;highlights:string;
  reading:ComparisonRow;classic:ComparisonRow;document:ComparisonRow;local:ComparisonRow;model:ComparisonRow;
  rate:Omit<ComparisonRow,'lite'>&{lite:(hourly:number)=>string};
  priority:ComparisonRow;feedback:ComparisonRow;requests:ComparisonRow;early:ComparisonRow;note:string;
}
const labels:Record<string,Omit<PricingComparisonCopy,'model'|'document'>&{model:{label:string}}>={
  'zh-CN':{
    feature:'功能与权益',highlights:'PLUS / Pro 翻译权益',
    model:{label:'高级翻译模型'},
    reading:{label:'阅读功能',free:'全部基础阅读功能',lite:'全部基础阅读功能'},
    classic:{label:'云端翻译',free:'30 页 / 天',lite:'不设日／月累计上限',detail:'页数按实际成功翻译结算，复用本人已有结果不重复扣减。无限仅指不设日／月累计页数上限，仍受受理限额与服务容量约束。'},
    local:{label:'本地翻译',free:'浏览器内置与自建 MTU',lite:'浏览器内置与自建 MTU',detail:'插件支持浏览器内置翻译（Beta）和自建 MTU 两种渠道。是否需要联网取决于所选渠道与配置。'},
    rate:{label:'新翻译受理限额',free:'10 页 / 滚动分钟',lite:n=>`100 页 / 滚动分钟；${n.toLocaleString('zh-CN')} 页 / 滚动小时`,detail:'限额按同一账户跨设备、模式与语言共享的滚动窗口计算；重复请求与已完成结果复用不重复计数。受理限额和优先调度不承诺完成速度或固定份额。'},
    priority:{label:'翻译任务响应',free:'标准调度',lite:'优先响应'},
    feedback:{label:'用户反馈',free:'正常响应',lite:'优先响应',detail:'会员反馈优先处理，不保证响应时限。'},
    requests:{label:'功能需求',free:'常规评估',lite:'优先评估',detail:'会员功能需求优先评估，不保证实现或上线时间。'},
    early:{label:'新功能体验',free:'随正式版本开放',lite:'高级功能优先体验'},
    note:'新功能以发布说明为准。',
  },
  'zh-TW':{
    feature:'功能與權益',highlights:'PLUS / Pro 翻譯權益',
    model:{label:'進階翻譯模型'},
    reading:{label:'閱讀功能',free:'全部基本閱讀功能',lite:'全部基本閱讀功能'},
    classic:{label:'雲端翻譯',free:'30 頁 / 天',lite:'不設日／月累計上限',detail:'頁數按實際成功翻譯結算，重用本人已有結果不重複扣減。無限僅指不設日／月累計頁數上限，仍受受理上限與服務容量限制。'},
    local:{label:'本機翻譯',free:'瀏覽器內建與自架 MTU',lite:'瀏覽器內建與自架 MTU',detail:'擴充功能支援瀏覽器內建翻譯（Beta）與自架 MTU 兩種管道。是否需要連網取決於所選管道與設定。'},
    rate:{label:'新翻譯受理上限',free:'10 頁 / 滾動分鐘',lite:n=>`100 頁 / 滾動分鐘；${n.toLocaleString('zh-TW')} 頁 / 滾動小時`,detail:'上限按同一帳戶跨裝置、模式與語言共用的滾動窗口計算；重複請求與已完成結果重用不重複計數。受理上限和優先排程不保證完成速度或固定份額。'},
    priority:{label:'翻譯任務回應',free:'標準排程',lite:'優先回應'},
    feedback:{label:'使用者回饋',free:'一般回應',lite:'優先回應',detail:'會員回饋優先處理，不保證回應時限。'},
    requests:{label:'功能需求',free:'一般評估',lite:'優先評估',detail:'會員功能需求優先評估，不保證實作或上線時間。'},
    early:{label:'新功能體驗',free:'隨正式版本開放',lite:'進階功能優先體驗'},
    note:'新功能以版本說明為準。',
  },
  en:{
    feature:'Features and benefits',highlights:'TRANSLATE WITH PLUS / Pro',
    model:{label:'Advanced translation models'},
    reading:{label:'Reading',free:'All core reading features',lite:'All core reading features'},
    classic:{label:'Cloud translation',free:'30 pages / day',lite:'No daily or monthly total cap',detail:'Pages are deducted for successful translations; reusing your completed results does not deduct them again. Unlimited means no daily or monthly total page cap. Request limits and service capacity still apply.'},
    local:{label:'Local translation',free:'Built-in browser translation and self-hosted MTU',lite:'Built-in browser translation and self-hosted MTU',detail:'The extension supports built-in browser translation (Beta) and self-hosted MTU. Internet requirements depend on the selected channel and configuration.'},
    rate:{label:'New translation request limit',free:'10 pages / rolling minute',lite:n=>`100 pages / rolling minute; ${n.toLocaleString('en')} pages / rolling hour`,detail:'Rolling windows are shared across devices, modes and languages on one account. Duplicate requests and reuse of completed results are not counted again. Request limits and priority scheduling do not guarantee completion speed or a fixed share of capacity.'},
    priority:{label:'Translation task response',free:'Standard scheduling',lite:'Priority response'},
    feedback:{label:'User feedback',free:'Standard response',lite:'Priority response',detail:'Member feedback receives priority handling but does not guarantee a response time.'},
    requests:{label:'Feature requests',free:'Standard review',lite:'Priority review',detail:'Member feature requests receive priority review. This does not guarantee implementation or a release date.'},
    early:{label:'New features',free:'At general release',lite:'Early access to advanced features'},
    note:'See release notes for new features.',
  },
  ja:{
    feature:'機能と特典',highlights:'PLUS / Pro の翻訳特典',
    model:{label:'高度な翻訳モデル'},
    reading:{label:'読書機能',free:'すべての基本読書機能',lite:'すべての基本読書機能'},
    classic:{label:'クラウド翻訳',free:'30 ページ / 日',lite:'日・月の累計上限なし',detail:'翻訳に成功したページ数を消費し、自分の完了結果を再利用しても再消費しません。無制限とは日・月の累計ページ数に上限がないことです。受付上限とサービス全体の処理容量は引き続き適用されます。'},
    local:{label:'ローカル翻訳',free:'ブラウザー内蔵翻訳と自前の MTU',lite:'ブラウザー内蔵翻訳と自前の MTU',detail:'拡張機能はブラウザー内蔵翻訳（ベータ）と自前の MTU に対応します。ネット接続の要否は選んだチャネルと設定によって異なります。'},
    rate:{label:'新規翻訳の受付上限',free:'直近1分で10ページ',lite:n=>`直近1分で100ページ、直近1時間で${n.toLocaleString('ja')}ページ`,detail:'直近の受付数は同一アカウントの全端末・モード・言語で共有されます。同じリクエストや完了結果の再利用は重複集計しません。受付上限と優先処理は完了速度や固定の処理枠を保証しません。'},
    priority:{label:'翻訳タスク応答',free:'通常のスケジューリング',lite:'優先応答'},
    feedback:{label:'ユーザーフィードバック',free:'通常対応',lite:'優先対応',detail:'会員からのフィードバックを優先して対応しますが、返答までの時間は保証しません。'},
    requests:{label:'機能リクエスト',free:'通常検討',lite:'優先検討',detail:'会員の機能リクエストを優先して検討しますが、実装や公開時期は保証しません。'},
    early:{label:'新機能の利用',free:'正式公開時に利用可能',lite:'高度な機能を先行体験'},
    note:'新機能はリリースノートをご確認ください。',
  },
  ko:{
    feature:'기능과 혜택',highlights:'PLUS / Pro 번역 혜택',
    model:{label:'고급 번역 모델'},
    reading:{label:'읽기 기능',free:'모든 기본 읽기 기능',lite:'모든 기본 읽기 기능'},
    classic:{label:'클라우드 번역',free:'30페이지 / 일',lite:'일일·월간 총 페이지 제한 없음',detail:'번역에 성공한 페이지를 차감하며, 본인의 완료된 결과를 재사용하면 다시 차감하지 않습니다. 무제한은 일일·월간 총 페이지 수에 상한이 없다는 뜻입니다. 요청 한도와 서비스 처리 용량은 계속 적용됩니다.'},
    local:{label:'로컬 번역',free:'브라우저 내장 번역 및 직접 호스팅한 MTU',lite:'브라우저 내장 번역 및 직접 호스팅한 MTU',detail:'확장 프로그램은 브라우저 내장 번역(베타)과 직접 호스팅한 MTU를 지원합니다. 인터넷 연결이 필요한지는 선택한 채널과 설정에 따라 다릅니다.'},
    rate:{label:'새 번역 요청 한도',free:'최근 1분 동안 10페이지',lite:n=>`최근 1분 동안 100페이지, 최근 1시간 동안 ${n.toLocaleString('ko')}페이지`,detail:'한도는 같은 계정의 모든 기기·모드·언어에서 최근 시간 기준으로 공유됩니다. 중복 요청과 완료된 결과의 재사용은 다시 계산하지 않습니다. 요청 한도와 우선 배정은 완료 속도나 고정 처리 비율을 보장하지 않습니다.'},
    priority:{label:'번역 작업 응답',free:'일반 작업 배정',lite:'우선 응답'},
    feedback:{label:'사용자 의견',free:'일반 응답',lite:'우선 응답',detail:'회원 의견을 우선 처리하지만 응답 기한을 보장하지는 않습니다.'},
    requests:{label:'기능 요청',free:'일반 검토',lite:'우선 검토',detail:'회원의 기능 요청을 우선 검토하지만 구현이나 출시 시점을 보장하지는 않습니다.'},
    early:{label:'새 기능 체험',free:'정식 출시 후 이용',lite:'고급 기능 우선 체험'},
    note:'새 기능은 릴리스 노트를 확인하세요.',
  },
};
const documentCopy:Record<Locale,[label:string,detail:string]>={
  'zh-CN':['整本翻译','支持 PDF、ZIP 和 CBZ。有可用余额的页数包也可解锁。'],
  'zh-TW':['整本翻譯','支援 PDF、ZIP 和 CBZ。仍有可用餘額的頁數包也能解鎖。'],
  en:['Whole-file translation','Supports PDF, ZIP and CBZ. A page pack with available credits also unlocks this feature.'],
  ja:['ファイル全体の翻訳','PDF、ZIP、CBZ に対応。ページパックに残高があれば、この機能を利用できます。'],
  ko:['파일 전체 번역','PDF, ZIP, CBZ를 지원합니다. 잔여 페이지가 있는 페이지 팩으로도 이용할 수 있습니다.'],
  fr:['Traduction de fichiers entiers','Prend en charge les fichiers PDF, ZIP et CBZ. Un pack de pages avec un solde disponible donne aussi accès à cette fonctionnalité.'],
  es:['Traducción de archivos completos','Admite PDF, ZIP y CBZ. Un paquete de páginas con saldo disponible también da acceso a esta función.'],
  'pt-BR':['Tradução de arquivos inteiros','Compatível com PDF, ZIP e CBZ. Um pacote de páginas com saldo disponível também libera esse recurso.'],
  de:['Übersetzung ganzer Dateien','Unterstützt PDF, ZIP und CBZ. Ein Seitenpaket mit verfügbarem Guthaben schaltet diese Funktion ebenfalls frei.'],
  it:['Traduzione di file interi','Supporta PDF, ZIP e CBZ. Anche un pacchetto di pagine con saldo disponibile dà accesso alla funzione.'],
  ru:['Перевод целых файлов','Поддерживаются PDF, ZIP и CBZ. Пакет с оставшимися страницами также даёт доступ к этой функции.'],
  pl:['Tłumaczenie całych plików','Obsługuje PDF, ZIP i CBZ. Pakiet z dostępnymi stronami również odblokowuje tę funkcję.'],
  uk:['Переклад цілих файлів','Підтримуються PDF, ZIP і CBZ. Пакет із доступним залишком сторінок також відкриває доступ до цієї функції.'],
  tr:['Tüm dosyayı çevirme','PDF, ZIP ve CBZ desteklenir. Kullanılabilir bakiyesi olan bir sayfa paketi de bu özelliği açar.'],
  vi:['Dịch toàn bộ tệp','Hỗ trợ PDF, ZIP và CBZ. Gói trang còn số dư khả dụng cũng mở khóa tính năng này.'],
  id:['Terjemahan seluruh file','Mendukung PDF, ZIP, dan CBZ. Paket halaman yang masih memiliki saldo juga membuka fitur ini.'],
  ar:['ترجمة الملفات كاملةً','تدعم ملفات PDF وZIP وCBZ. تتيح باقة صفحات ذات رصيد متاح استخدام هذه الميزة أيضًا.'],
};
export function comparisonCopy(locale:string):PricingComparisonCopy {
 const copy=commerceCopy(locale)?.comparison;
 const value=copy ? {...copy,rate:{...copy.rate,lite:(n:number)=>formatCopy(copy.rate.lite,{n:n.toLocaleString(locale)})}} : labels[locale]??labels.en;
  const {included,notIncluded}=pricingHighlightsCopy(locale);
  const [label,detail]=documentCopy[locale as Locale]??documentCopy.en;
  return {...value,model:{...value.model,free:notIncluded,lite:included},
   document:{label,detail,free:notIncluded,lite:included},
   classic:{...value.classic,lite:subscriptionQuotaCopy(locale).monthly.replace('{0}',(2500).toLocaleString(locale)),detail:subscriptionQuotaCopy(locale).rule}};
}

export function paidComparisonValue(row:ReturnType<typeof comparisonRows>[number],offer:BillingOffer,locale:string){
 if(row.key==='classic')return offerBenefits(offer,locale);
 if(row.key==='rate')return offer.hourly_image_limit
   ?`${subscriptionQuotaCopy(locale).paidRate} · ${hourlyLimitCopy(offer.hourly_image_limit,locale)}`
   :subscriptionQuotaCopy(locale).paidRate;
 return row.lite;
}

export function extraPagesCopy(offer:BillingOffer,plans:BillingOffer[],locale:string){
 if(offer.monthly_classic_pages==null||offer.monthly_classic_pages<=0)return '';
 const smaller=plans.filter(plan=>plan.monthly_classic_pages!=null&&plan.monthly_classic_pages>0&&plan.monthly_classic_pages<offer.monthly_classic_pages!)
   .sort((a,b)=>b.monthly_classic_pages!-a.monthly_classic_pages!)[0];
 return smaller?pricingHighlightsCopy(locale).morePages.replace('{plan}',smaller.name)
   .replace('{pages}',(offer.monthly_classic_pages-smaller.monthly_classic_pages!).toLocaleString(locale)):'';
}
export function comparisonRows(locale:string,hourly:number){
 const copy=comparisonCopy(locale);
  return (['classic','document','model','local','rate','priority','reading','feedback','requests','early'] as const).map(key=>{
   const row=copy[key],lite=typeof row.lite==='function'?(hourly>0?row.lite(hourly):subscriptionQuotaCopy(locale).paidRate):row.lite;
   return {key,label:row.label,free:row.free,lite,detail:row.detail,shared:row.free===lite};
 });
}
