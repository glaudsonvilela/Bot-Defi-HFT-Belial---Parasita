"""Loopback read-only dashboard. No external scripts, accounts or trade controls."""
import json,secrets,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from a51.common import canonical,atomic
HTML='''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Belial A7 · PAPER</title>
<style>body{margin:0;background:#0d1521;color:#e2e9f3;font:15px system-ui;padding:30px;max-width:1500px;margin:auto}h1{font-size:29px}small,.muted{color:#a8b8cd}#arms{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}.card{background:#172334;border:1px solid #2b3d56;border-radius:12px;padding:18px}b{color:#63dec1}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:25px}td,th{padding:10px;text-align:left;border-bottom:1px solid #2b3d56}#table{overflow:auto}canvas{width:130px;height:35px}#error{color:#ffad82}a{color:#63dec1}</style>
<h1>Belial A7 <b>· PAPER</b></h1><p id="status">Carregando…</p><p id="error"></p><p id="chains" class="muted"></p><div id="arms"></div><div id="table"><table><thead><tr><th>Contrato</th><th>Redes confirmadas</th><th>Profundidade ±10 bps¹</th><th>Preço recente</th><th>Variação 2 min</th><th>Nova entrada</th></tr></thead><tbody id="coins"></tbody></table></div><p class="muted">¹ Menor lado do livro na seleção. Sem ordens reais. Taxa modelada: 0,05% por lado. Resultado abaixo exclui funding; conciliação no relatório final. Carteiras independentes, USDT simulados.</p><p class="muted">Wallet recorrente com redução líquida observada não identifica um HFT da Binance. Amostragem on-chain pode perder eventos. Posições abertas ao fim permanecem censuradas.</p>
<script>
const fmt=(x,n=2)=>Number.isFinite(x)?x.toLocaleString('pt-BR',{maximumFractionDigits:n}):'—';
function cell(tr,text){let d=document.createElement('td');d.textContent=text;tr.append(d);return d;}
function draw(c,points){c.width=260;c.height=70;let g=c.getContext('2d');if(points.length<2)return;let values=points.map(x=>x[1]),lo=Math.min(...values),hi=Math.max(...values);g.strokeStyle='#63dec1';g.lineWidth=2;g.beginPath();values.forEach((v,i)=>{let x=i/(values.length-1)*256+2,y=65-(v-lo)/(hi-lo||1)*60;i?g.lineTo(x,y):g.moveTo(x,y)});g.stroke();}
async function refresh(){try{let r=await fetch(location.pathname+'state',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);let s=await r.json(),v=s.lab||{},arms=v.arms||{};document.querySelector('#status').textContent=s.phase+' · '+fmt((s.elapsed_ms||0)/60000,1)+' / 120 minutos · '+(s.message||'');let age=Date.now()-(s.updated_ms||0);document.querySelector('#error').textContent=age>6000&&s.phase==='CAPTURING'?'Painel sem atualização há '+fmt(age/1000)+' s. Dados não confirmados como atuais.':'';document.querySelector('#chains').textContent=Object.entries(v.chains||{}).map(([c,x])=>c+': '+x.state).join(' · ')+' | Livros atuais: '+(arms.CONTROLE?.fresh_books||0)+'/20 | Amostras descartadas: '+(s.sampling||0);let cards=document.querySelector('#arms');cards.replaceChildren();for(let [name,a] of Object.entries(arms)){let d=document.createElement('div');d.className='card';let h=document.createElement('h3');h.textContent=name;let p=document.createElement('p');p.textContent=fmt(a.realized_net_ex_funding)+' USDT realizados';let q=document.createElement('small');q.textContent=(a.counts.entries||0)+' entradas · '+(a.counts.closed||0)+' fechadas · '+a.positions.length+' abertas';d.append(h,p,q);cards.append(d)}let rows=document.querySelector('#coins');rows.replaceChildren();for(let c of s.universe||[]){let tr=document.createElement('tr'),l=(arms.ENTRADA?.leaders||[]).find(x=>x.symbol===c.symbol);cell(tr,c.symbol);cell(tr,c.sources.map(p=>p.chain).join(', ')||'Sem pool: entrada bloqueada');cell(tr,fmt(c.depth_10bps_usdt)+' USDT');let cv=document.createElement('canvas');cell(tr,'').append(cv);draw(cv,l?.chart||[]);cell(tr,l?fmt(l.r120)+' bps':'Aquecendo');cell(tr,l?.gate||'Aquecendo');rows.append(tr)}}catch(e){document.querySelector('#error').textContent='Painel desconectado; não trate os dados exibidos como atuais.'}}
refresh();setInterval(refresh,2000);
</script></html>'''
class UI:
    def __init__(self,session):
        self.session=Path(session);self.state=dict(phase='PREPARING');self.token=secrets.token_urlsafe(24);owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/'+owner.token+'/':body=HTML.encode();typ='text/html; charset=utf-8'
                elif self.path=='/'+owner.token+'/state':body=canonical(owner.state).encode();typ='application/json'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',typ);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.url='http://127.0.0.1:'+str(self.server.server_port)+'/'+self.token+'/'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def update(self,state):self.state=state;atomic(self.session/'STATE.json',state)
    def close(self):self.server.shutdown();self.server.server_close()

def final_html(session,state):
    # Data is JSON, not markup; escape script delimiters even for future symbols.
    payload=canonical(state).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=HTML.replace("let r=await fetch(location.pathname+'state',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);let s=await r.json()",'let s='+payload).replace("refresh();setInterval(refresh,2000);","refresh();").replace("age>6000&&s.phase==='CAPTURING'?","false?")
    (Path(session)/'RELATORIO.html').write_text(html)
