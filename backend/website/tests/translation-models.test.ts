import {test} from 'node:test';
import assert from 'node:assert/strict';
import {load} from 'cheerio';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {translationBody} from '../src/lib/translation-api';
import type {RecordMeta} from '../src/lib/translation-store';
import TranslationModelPicker from '../src/components/TranslationModelPicker';
import {modelCopy} from '../src/i18n/translation-models';
import {locales} from '../src/i18n/locales';
import {selectedModelAvailable} from '../../shared/translation-models';

const record:RecordMeta={id:'image',scope:'user:a',name:'sample.png',mode:'classic',language:'en',bytes:4,created:1,updated:1,state:'submitting',sha256:'a'.repeat(64),inputBytes:4,mime:'image/png'};
test('old records omit model; explicit choices survive ordinary/retry/regenerate request serialization',()=>{
  assert.ok(!('model_id' in translationBody(record)));
  assert.equal(translationBody({...record,modelId:'a'}).model_id,'a');
  assert.deepEqual(translationBody({...record,modelId:'a',intent:{retry_of:'source'}}),{retry_of:'source',model_id:'a'});
  assert.deepEqual(translationBody({...record,modelId:'b',intent:{regenerate_of:'source'}}),{regenerate_of:'source',model_id:'b'});
});
test('both availability states and upgrade are accessible in every locale',()=>{
  for(const locale of locales){
    assert.ok(Object.values(modelCopy[locale]).every(value=>typeof value==='string'&&value.length>0));
    const $=load(renderToStaticMarkup(createElement(TranslationModelPicker,{models:[
      {id:'free',name:'Free model',available:true,requires_paid:false,quota_multiplier:0.125},
      {id:'paid',name:'Paid model',available:false,requires_paid:true,unavailable_reason:'not_allowed',quota_multiplier:2.5},
    ],onChange:()=>{},copy:modelCopy[locale],upgradeUrl:'/pricing/'})));
    const free=$('button').filter((_,element)=>$(element).text().includes('Free model'));
    const paid=$('a[href="/pricing/"]');
    assert.ok(free.text().includes(modelCopy[locale].free) && free.text().includes(modelCopy[locale].available));
    assert.ok(paid.text().includes('Paid model') && paid.text().includes(modelCopy[locale].paid));
    assert.ok(paid.text().includes(modelCopy[locale].unavailable) && paid.text().includes(modelCopy[locale].upgrade));
    assert.ok(free.text().includes('0.125×') && paid.text().includes('2.5×'));
  }
});
test('missing capability supports legacy automatic requests but not stale explicit selections',()=>{
  assert.equal(selectedModelAvailable(undefined),true);assert.equal(selectedModelAvailable([]),false);
  assert.equal(selectedModelAvailable(undefined,'saved'),false);
});
