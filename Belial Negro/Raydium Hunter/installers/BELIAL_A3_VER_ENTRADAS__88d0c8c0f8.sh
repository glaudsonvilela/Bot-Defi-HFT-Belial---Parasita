#!/usr/bin/env bash
# Diagnóstico somente leitura. Não instala, reinicia, altera parâmetros ou consulta rede.
set -eu
command -v python3 >/dev/null || { echo 'Python 3 não encontrado.' >&2; exit 1; }
exec python3 -I -B - "$@" <<'PY'
import argparse, collections, json, re, time
from pathlib import Path

ap=argparse.ArgumentParser(description='Leitura de contadores da sessão Belial A3; nenhuma alteração.')
ap.add_argument('--session',type=Path,help='Pasta de uma sessão específica. Por padrão, a A3 mais recente.')
a=ap.parse_args()
base=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter/evidence')
if a.session:
    session=a.session.expanduser()
else:
    candidates=[p for p in base.glob('m2-a3-*') if p.is_dir()]
    if not candidates: raise SystemExit('Nenhuma pasta m2-a3-* encontrada. Nenhum arquivo foi alterado.')
    session=max(candidates,key=lambda p:p.name)
if not session.is_dir(): raise SystemExit('Pasta da sessão não encontrada.')
print('SOMENTE_LEITURA=true; REDE=false; PROCESSOS_REINICIADOS=false')
print('SESSAO='+str(session),flush=True)

def clean(value):
    text=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value)
    text=re.sub(r'(?:https?|wss?)://[^\s"\']+','<ENDPOINT>',text)
    return ''.join(c if ord(c)>=32 else ' ' for c in text)[:1600]

def read(rel):
    p=session/rel
    try:
        if p.stat().st_size>8*1024*1024: raise ValueError('Arquivo de estado acima de 8 MiB; não lido.')
        value=json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(value,dict): raise ValueError('Objeto JSON esperado.')
        return value
    except (OSError,ValueError) as exc:
        print('INDISPONIVEL '+rel+': '+clean(exc))
        return None

def age(obj):
    stamp=(obj or {}).get('at_ms')
    return round((time.time_ns()/1e6-stamp)/1000,2) if isinstance(stamp,(int,float)) else 'n/d'

def show_counts(name,obj,keys):
    counts=obj.get('counts') if isinstance(obj,dict) else None
    if not isinstance(counts,dict): print(name+': contadores indisponíveis'); return
    print(name+': '+ ' | '.join(k+'='+str(counts.get(k,0)) for k in keys))

for i in range(2):
    print('\n=== LEITURA '+str(i+1)+' DE 2 ===',flush=True)
    st=read('SESSION.json') or {};platform=read('PLATFORM.json') or {}
    print('FASE='+clean(st.get('phase','n/d'))+' | etapa='+clean(platform.get('stage','n/d'))+' | idade_painel_s='+str(age(platform)))
    start=st.get('started_ms')
    if isinstance(start,(int,float)):
        end=st.get('ended_ms') or time.time_ns()/1e6
        elapsed=max(0,min(end,st.get('deadline_ms',end))-start)/60000
        print('TEMPO_DESDE_INICIO_MIN='+str(round(elapsed,2)))
    cov=platform.get('coverage') or {}
    print('COBERTURA: '+' | '.join(k+'='+str(cov.get(k,'n/d')) for k in ('requested','qualified_at_start','live_signal_sources','usable_books','paper_prerequisites_ready')))
    if not i:
        rows=cov.get('rows',[])
        blocked=[r for r in rows if not r.get('signal_source_ready')]
        reasons=collections.Counter(r.get('signal_source_reason','sem motivo') for r in blocked)
        print('FONTES_NAO_APTAS='+clean(dict(reasons)))
        for r in blocked:
            s=r.get('source_status') or {}
            print('  '+clean(r.get('symbol'))+' | '+clean(r.get('signal_source_reason'))+' | estado='+clean(s.get('state'))+' | recuperacao_ms='+clean(s.get('warmup_remaining_ms')))
    for label,folder in [('PRINCIPAL_REFERENCIA','m2_tail'),('SHADOW_CORE','shadow_compare'),('SHADOW_EXPANSAO','shadow_expansion')]:
        show_counts(label,read('aggregate/'+folder+'/state.json'),('marks','wallet_exits','arms','confirm_evaluated','confirm_pass','confirm_rejected','support','episodes','entry_unavailable','trades'))
    agg=read('aggregate/status.json') or {}
    print('AGREGADOR: estado='+clean(agg.get('state'))+' | idade_s='+str(age(agg))+' | contadores='+clean(agg.get('counts'))+' | bloqueios='+clean(agg.get('blocked')))
    paper=read('paper/state.json')
    if isinstance(paper,dict):
        print('PAPER: idade_s='+str(age(paper))+' | bloqueios_dependencia='+clean(paper.get('a1_blocked_signals'))+' | integridade='+clean(paper.get('a1_integrity'))+' | erro='+clean(paper.get('a1_error')))
        for group,g in (paper.get('groups') or {}).items():
            show_counts('PAPER_'+group,g,('signals','entries','closed','capital_rejections','dependency_cancelled_entries','connection_cancelled_entries','stale_signals','expired_orders'))
            print('  GATE='+clean(g.get('gate',{})))
            for row in g.get('scenarios',[]):
                if row.get('scenario')=='ROTA':
                    print('  ROTA '+clean(row.get('policy'))+': entradas='+clean(row.get('entries'))+' | abertas='+clean(row.get('open'))+' | fechadas='+clean(row.get('closed'))+' | pendentes='+clean(row.get('pending')))
    for name in ('ws_relay','n2'):
        obj=read(name+'/status.json') or {}
        print(name.upper()+': estado='+clean(obj.get('state'))+' | idade_s='+str(age(obj))+' | contadores='+clean(obj.get('counters',obj.get('stats',{})))+' | erro='+clean(obj.get('error',obj.get('reason'))))
    if i==0:
        print('Aguardando 3 segundos apenas para repetir a leitura...',flush=True);time.sleep(3)
print('\nFIM. Nenhuma alteração. Cenários/políticas são paralelos: não somar contagens como operações independentes.')
PY
