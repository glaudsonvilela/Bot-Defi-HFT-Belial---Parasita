const fs=require('fs'),vm=require('vm');
function element(tag='div'){return {tag,textContent:'',children:[],append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},getContext(){return {beginPath(){},lineTo(){},moveTo(){},stroke(){}}}}}
async function check(path,mode){let elements={};let doc={querySelector(sel){return elements[sel]??=element()},createElement:element};let html=fs.readFileSync(path,'utf8'),script=html.split('<script>')[1].split('</script>')[0];let context={document:doc,Date,Number,Math,Object,Array,setInterval(){},console};vm.runInNewContext(script,context);await new Promise(setImmediate);let error=elements['#error'].textContent;
 if(mode==='failed'&&!error.includes('ACCESS_DENIED_HTTP_403_eth_getLogs'))throw Error('Failure diagnostic not displayed: '+error);
 if(mode==='trades'){let rows=elements['#trades'].children;if(rows.length!==2)throw Error('Individual trade rows missing');if(rows[0].children[6].textContent!=='—')throw Error('Stale open PnL should be unknown');if(rows[1].children[6].textContent!=='1,25')throw Error('Closed PnL not rendered');}
}
(async()=>{await check('a8_ui_failure/RELATORIO.html','failed');await check('a8_ui_trades/RELATORIO.html','trades');console.log('UI_DIAGNOSTIC_AND_TRADE_RENDER=PASS')})().catch(e=>{console.error(e);process.exit(1)});
