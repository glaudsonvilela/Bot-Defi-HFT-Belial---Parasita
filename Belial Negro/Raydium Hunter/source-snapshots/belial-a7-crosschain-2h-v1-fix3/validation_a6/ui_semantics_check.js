const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('a6_release/a51/web/dashboard.html','utf8');
const ingest=src.match(/function ingest\(kind,value\)\{.*?\}\n/)[0];
const clock=src.match(/function renderClock\(\)\{.*?\}\n/)[0];
const seen={};const elements={progress:{},health:{},clock:{},phase:{},connection:{}};
const ctx={D:{state:{at_ms:Date.now()-99999},platform:{at_ms:Date.now()-99999,phase:'CAPTURING'},ui:{},books:{}},U:{received:0,messages:0,dirty:false,connected:true,render:[]},Date,Math,performance:{now:()=>0},now:()=>Date.now(),duration:x=>String(x),num:()=>'',text:(k,v)=>seen[k]=v,$:k=>elements[k]||(elements[k]={}),schedule:()=>{}};
vm.createContext(ctx);vm.runInContext(ingest+clock,ctx);vm.runInContext("ingest('ui',{at_ms:Date.now()});renderClock()",ctx);
assert.strictEqual(seen.linkState,'Estado sem atualização');
console.log('UI_STATE_FRESHNESS_IGNORES_UI_HEARTBEAT=PASS');
