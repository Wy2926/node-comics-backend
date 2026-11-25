import type {Locale} from './types';

const labels:Record<Locale,[alipayAndCard:string,card:string,afterDetails:string]>={
  'zh-CN':['支持支付宝、银行卡','银行卡支付','先在收银台填写邮箱、姓名并选择账单国家或地区，下一步会显示本单可用的支付方式。'],
  'zh-TW':['支援支付寶、銀行卡','銀行卡付款','先在結帳頁填寫電子郵件、姓名並選擇帳單國家或地區，下一步會顯示這筆訂單可用的付款方式。'],
  en:['Alipay or card','Credit or debit card','Enter your email, name and billing country or region at checkout to see the payment methods available for your order.'],
  ja:['Alipay・カード払い','カード払い','決済画面でメールアドレス、氏名、請求先の国・地域を入力すると、この注文で利用できる支払い方法が表示されます。'],
  ko:['Alipay 또는 카드 결제','카드 결제','결제 화면에서 이메일, 이름, 청구 국가 또는 지역을 입력하면 이 주문에 사용할 수 있는 결제 수단이 표시됩니다.'],
  fr:['Alipay ou carte bancaire','Carte bancaire','Indiquez votre e-mail, votre nom et votre pays ou région de facturation pour voir les moyens de paiement disponibles pour votre commande.'],
  es:['Alipay o tarjeta','Tarjeta de crédito o débito','Al pagar, introduce tu correo, tu nombre y el país o región de facturación para ver los métodos disponibles para tu pedido.'],
  'pt-BR':['Alipay ou cartão','Cartão de crédito ou débito','Informe seu e-mail, nome e país ou região de cobrança para ver as formas de pagamento disponíveis para o pedido.'],
  de:['Alipay oder Karte','Kredit- oder Debitkarte','Gib an der Kasse deine E-Mail-Adresse, deinen Namen und dein Rechnungsland bzw. deine Region an. Danach siehst du die verfügbaren Zahlungsarten.'],
  it:['Alipay o carta','Carta di credito o debito','Al pagamento, inserisci e-mail, nome e paese o regione di fatturazione per vedere i metodi disponibili per il tuo ordine.'],
  ru:['Alipay или банковская карта','Банковская карта','Укажите электронную почту, имя и страну или регион выставления счёта. Затем появятся доступные способы оплаты заказа.'],
  pl:['Alipay lub karta','Karta płatnicza','Podaj adres e-mail, imię i nazwisko oraz kraj lub region rozliczenia, aby zobaczyć dostępne metody płatności za zamówienie.'],
  uk:['Alipay або банківська картка','Банківська картка','Вкажіть електронну пошту, ім’я та країну або регіон виставлення рахунку. Далі з’являться доступні способи оплати замовлення.'],
  tr:['Alipay veya kart','Kredi veya banka kartı','Ödeme sayfasında e-posta adresinizi, adınızı ve fatura ülkenizi veya bölgenizi girerek siparişiniz için kullanılabilen ödeme yöntemlerini görebilirsiniz.'],
  vi:['Alipay hoặc thẻ','Thẻ tín dụng hoặc thẻ ghi nợ','Nhập email, họ tên và quốc gia hoặc khu vực thanh toán để xem các phương thức thanh toán có thể dùng cho đơn hàng.'],
  id:['Alipay atau kartu','Kartu kredit atau debit','Masukkan email, nama, dan negara atau wilayah penagihan untuk melihat metode pembayaran yang tersedia bagi pesanan Anda.'],
  ar:['Alipay أو بطاقة مصرفية','بطاقة ائتمان أو خصم','أدخل بريدك الإلكتروني واسمك وبلد الفوترة أو منطقتها عند الدفع لتظهر طرق الدفع المتاحة لطلبك.'],
};

export const checkoutMethodCopy=(locale:string)=>labels[locale as Locale]??labels.en;

const dialog:Record<Locale,[title:string,hosted:string,close:string,loading:string]>={
  'zh-CN':['订单付款','在 Creem 付款页继续','关闭','正在打开收银台…'],
  'zh-TW':['訂單付款','前往 Creem 付款頁繼續','關閉','正在開啟付款表單…'],
  en:['Checkout','Continue on Creem','Close','Opening checkout…'],
  ja:['お支払い','Creem の決済ページで続ける','閉じる','決済画面を開いています…'],
  ko:['주문 결제','Creem 결제 페이지에서 계속','닫기','결제 화면을 여는 중…'],
  fr:['Paiement','Continuer sur Creem','Fermer','Ouverture du formulaire de paiement…'],
  es:['Pago','Continuar en Creem','Cerrar','Abriendo el formulario de pago…'],
  'pt-BR':['Pagamento','Continuar no Creem','Fechar','Abrindo o formulário de pagamento…'],
  de:['Zur Kasse','Auf Creem fortfahren','Schließen','Zahlungsformular wird geöffnet…'],
  it:['Pagamento','Continua su Creem','Chiudi','Apertura del modulo di pagamento…'],
  ru:['Оплата заказа','Продолжить на Creem','Закрыть','Открываем форму оплаты…'],
  pl:['Płatność','Kontynuuj w Creem','Zamknij','Otwieranie formularza płatności…'],
  uk:['Оплата замовлення','Продовжити на Creem','Закрити','Відкриваємо форму оплати…'],
  tr:['Ödeme','Creem’de devam et','Kapat','Ödeme formu açılıyor…'],
  vi:['Thanh toán','Tiếp tục trên Creem','Đóng','Đang mở biểu mẫu thanh toán…'],
  id:['Pembayaran','Lanjutkan di Creem','Tutup','Membuka formulir pembayaran…'],
  ar:['الدفع','متابعة الدفع على Creem','إغلاق','جارٍ فتح نموذج الدفع…'],
};
export const checkoutDialogCopy=(locale:string)=>dialog[locale as Locale]??dialog.en;
