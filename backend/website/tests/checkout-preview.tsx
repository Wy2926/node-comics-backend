import {useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import CreemCheckout from '../src/components/CreemCheckout';
import {publishedSubscriptions,publishedPacks} from '../src/data/published-plans';
import {amount} from '../src/lib/billing';
import '../src/styles/global.css';
import '../src/styles/controls.css';
import '../src/styles/marketing.css';
import '../src/styles/pricing.css';
import './checkout-preview.css';

if(!['127.0.0.1','localhost'].includes(location.hostname))throw Error('仅限本机预览');
const locale='zh-CN',params=new URLSearchParams(location.search);
const samples=[publishedSubscriptions.find(item=>item.plan_id==='plus'&&item.interval==='month')!,publishedPacks[0]];
const quote=samples.find(item=>item.id===params.get('offer'))??samples[0];
const appearance=params.get('theme');
const theme=appearance==='dark'||appearance!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
document.documentElement.dataset.siteTheme=theme==='dark'?'midnight':'paper';
const sampleName=(price:typeof quote)=>price.interval==='once'?`${price.quota_pages?.toLocaleString(locale)} 页额度包`:`${price.name} 月卡`;
const localUrl=(view:string,offer=quote.id,appearance=theme)=>new URL(`/?${new URLSearchParams({view,offer,theme:appearance})}`,location.origin).href;

function DemoForm(){
  const [step,setStep]=useState(1),[method,setMethod]=useState('支付宝');
  const email=useRef<HTMLInputElement>(null),next=useRef<HTMLHeadingElement>(null);
  return <main className="demo-form">
    <div className="demo-form-top"><span className="demo-stamp">本地模拟 · 不会扣款</span><span>0{step} / 02</span></div>
    <form hidden={step!==1} onSubmit={event=>{event.preventDefault();setStep(2);requestAnimationFrame(()=>next.current?.focus());}}>
      <h2>账单信息</h2><p>示例资料已填好，可直接继续。</p>
      <label>邮箱<input ref={email} type="email" required defaultValue="reader@example.test" autoComplete="off"/></label>
      <div className="demo-fields"><label>姓名<input required defaultValue="示例读者" autoComplete="off"/></label>
        <label>账单国家或地区<select defaultValue="中国大陆"><option>中国大陆</option><option>中国香港</option><option>其他地区</option></select></label></div>
      <button type="submit" className="button">继续选择支付方式 <span aria-hidden="true">→</span></button>
    </form>
    {step===2&&<section><h2 ref={next} tabIndex={-1}>支付方式</h2><p>选择一种方式，体验订单确认。</p>
      <fieldset className="demo-methods"><legend>选择支付方式</legend>{['支付宝','银行卡'].map((name,index)=><label key={name}>
        <input type="radio" name="method" checked={method===name} onChange={()=>setMethod(name)}/>
        <span><b>{name}</b><small>{index?'本次无需输入卡号':'本次无需打开支付宝'}</small></span>
      </label>)}</fieldset>
      <a className="button" target="_top" href={localUrl('receipt')+`&method=${method==='支付宝'?'alipay':'card'}`}>模拟支付 {amount(quote,locale)}</a>
      <button className="demo-back" onClick={()=>{setStep(1);requestAnimationFrame(()=>email.current?.focus());}}>← 修改账单信息</button>
    </section>}
  </main>;
}

function Preview(){
  const receipt=params.get('view')==='receipt';
  const [active,setActive]=useState<typeof quote|null>(receipt||params.get('view')==='offers'?null:quote);
  return <main className="demo-stage pricing-comparison">
    <div className="demo-card"><span className="demo-stamp">NODELANE COMICS · 本地体验</span>
      <h1>{receipt?'模拟付款已完成':'选一张阅读票'}</h1>
      <p>{receipt?`${sampleName(quote)} · ${amount(quote,locale)} · ${params.get('method')==='card'?'银行卡':'支付宝'}。没有扣款，也没有变更账户权益。`:'试试购买会员卡或额度包。这里使用示例资料，不需要登录。'}</p>
      <div className="demo-picks">{samples.map(item=><button className="button secondary" key={item.id} onClick={()=>setActive(item)}>{sampleName(item)} · {amount(item,locale)} <span aria-hidden="true">→</span></button>)}</div>
      <a className="text-link" href={localUrl('offers',quote.id,theme==='dark'?'light':'dark')}>{theme==='dark'?'切换为浅色':'切换为深色'}</a>
    </div>
    {active&&<CreemCheckout locale={locale} purchase={{offer:active,url:localUrl('form',active.id)}} successHref={localUrl('receipt',active.id)} onClose={()=>setActive(null)}/>}
  </main>;
}

createRoot(document.getElementById('root')!).render(params.get('view')==='form'?<DemoForm/>:<Preview/>);
