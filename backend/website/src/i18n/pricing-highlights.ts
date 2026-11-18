import type {Locale} from './types';

const labels:Record<Locale,[string,string,string,string,string,string]>={
  'zh-CN':['轻量翻译','日常追漫','更多翻译用量','已包含','未包含','比 {plan} 每月多 {pages} 页'],
  'zh-TW':['輕量翻譯','日常追漫','更多翻譯用量','已包含','未包含','比 {plan} 每月多 {pages} 頁'],
  en:['Light translation','Everyday reading','More translation pages','Included','Not included','{pages} more pages per month than {plan}'],
  ja:['気軽に翻訳','日々の漫画に','より多くの翻訳に','含まれます','含まれません','{plan} より毎月 {pages} ページ多く翻訳'],
  ko:['가벼운 번역','매일 만화 읽기','더 많은 번역','포함','미포함','{plan}보다 매월 {pages}페이지 더 제공'],
  fr:['Traduction occasionnelle','Lecture quotidienne','Plus de pages à traduire','Inclus','Non inclus','{pages} pages de plus par mois que {plan}'],
  es:['Traducción ocasional','Lectura diaria','Más páginas para traducir','Incluido','No incluido','{pages} páginas más al mes que {plan}'],
  'pt-BR':['Tradução ocasional','Leitura diária','Mais páginas para traduzir','Incluído','Não incluído','{pages} páginas a mais por mês que {plan}'],
  de:['Gelegentlich übersetzen','Täglich lesen','Mehr Seiten übersetzen','Enthalten','Nicht enthalten','{pages} Seiten mehr pro Monat als {plan}'],
  it:['Traduzione occasionale','Lettura quotidiana','Più pagine da tradurre','Incluso','Non incluso','{pages} pagine in più al mese rispetto a {plan}'],
  ru:['Редкие переводы','Ежедневное чтение','Больше страниц перевода','Включено','Не включено','На {pages} страниц в месяц больше, чем в {plan}'],
  pl:['Okazjonalne tłumaczenia','Codzienne czytanie','Więcej stron do tłumaczenia','W cenie','Brak','O {pages} stron miesięcznie więcej niż {plan}'],
  uk:['Нечасті переклади','Щоденне читання','Більше сторінок перекладу','Включено','Не включено','На {pages} сторінок на місяць більше, ніж у {plan}'],
  tr:['Ara sıra çeviri','Günlük okuma','Daha fazla çeviri','Dahil','Dahil değil','{plan} planından ayda {pages} sayfa daha fazla'],
  vi:['Dịch nhẹ nhàng','Đọc truyện mỗi ngày','Dịch nhiều trang hơn','Có','Không có','Nhiều hơn {plan} {pages} trang mỗi tháng'],
  id:['Terjemahan ringan','Membaca setiap hari','Lebih banyak terjemahan','Termasuk','Tidak termasuk','{pages} halaman lebih banyak per bulan dari {plan}'],
  ar:['ترجمة خفيفة','قراءة يومية','صفحات ترجمة أكثر','مشمول','غير مشمول','{pages} صفحة إضافية شهريًا مقارنةً بـ {plan}'],
};

export function pricingHighlightsCopy(locale:string){
  const [free,plus,pro,included,notIncluded,morePages]=labels[locale as Locale]??labels.en;
  return {free,plus,pro,included,notIncluded,morePages};
}
