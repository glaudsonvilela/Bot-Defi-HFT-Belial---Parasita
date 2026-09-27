"""Descriptive comparison with exposure and data-quality limits."""
from collections import Counter
from a51.journal import read_verified
from a51.common import atomic,read_json

def compare(session,state):
    closed={name:[] for name in state['arms']};signals=Counter()
    for e in read_verified(session/'decisions.jsonl'):
        if e['kind']=='EXIT_FILLED':closed[e['arm']].append(e['position'])
        if e['kind']=='SIGNAL':signals[e['arm']]+=1
    funding=read_json(session/'FUNDING_RECONCILIATION.json',{}) or {};out={}
    for name,view in state['arms'].items():
        rows=closed[name];wins=sum(r['net_ex_funding']>0 for r in rows);positive=sum(max(0,r['net_ex_funding']) for r in rows);negative=-sum(min(0,r['net_ex_funding']) for r in rows)
        f=[r for r in funding.values() if r['arm']==name and r['closed_ms'] is not None]
        complete=len(f)==len(rows) and all(r['modeled_net_including_funding'] is not None for r in f)
        out[name]=dict(signals=signals[name],entries=view['counts'].get('entries',0),closed=len(rows),open=len(view['positions']),
            realized_net_ex_funding=view['realized_net_ex_funding'],modeled_closed_net_with_funding=sum(r['modeled_net_including_funding'] for r in f) if complete else None,
            win_rate=wins/len(rows) if rows else None,profit_factor=positive/negative if negative else None,
            expectancy_ex_funding=view['realized_net_ex_funding']/len(rows) if rows else None,
            stop_count=sum(r.get('exit_reason')=='STOP' for r in rows),closed_mfe_at_least_100bps=sum(r['mfe_bps']>=100 for r in rows),
            path_censored_closed=sum(r['path_censored'] for r in rows),exit_reasons=dict(Counter(r.get('exit_reason') for r in rows)),
            closed_notional_usdt=sum(r['notional'] for r in rows),closed_position_minutes=sum((r['closed_ms']-r['opened_ms'])/60000 for r in rows),
            rejections=view['rejections'])
    result=dict(arms=out,chains=state['chain_counts'],replay=read_json(session/'REPLAY.json',{}),
        conclusion='EXPLORATORY_ONLY_NO_PROFITABILITY_CLAIM',
        comparison='Same 20 contracts and feed; common new sizing and causal ranking. Control preserves A6 signal/exit rules, not an exact reproduction of A6 portfolio.',
        limitations=['Two-hour rare-event sample; zero entries is inconclusive.','Unequal entries/exposure; compare per trade, closed notional, open/censored paths and time in positions.','On-chain is a sampled net-disposal proxy, not Binance HFT identity.','Missing pool coverage blocks the entry arm for that coin.','Public funding history models cash flows; fees are assumed and fills are simulated.'])
    atomic(session/'COMPARISON.json',result,True)
    lines=['# Belial A7 — comparação PAPER','',result['conclusion'],'', '| Braço | Entradas | Fechadas | Abertas | Líquido sem funding | Stops | MFE ≥100bps |', '|---|---:|---:|---:|---:|---:|---:|']
    for name,r in out.items():lines.append(f"| {name} | {r['entries']} | {r['closed']} | {r['open']} | {r['realized_net_ex_funding']:.6f} | {r['stop_count']} | {r['closed_mfe_at_least_100bps']} |")
    lines+=['','Consulte COMPARISON.json para exposição, conciliação de funding, censura e qualidade. Posições abertas não são lucro realizado. O controle usa as regras de sinal/saída A6 com dimensionamento e ordenação comuns aos quatro braços. Duas horas servem para testar funcionamento e observar ocorrências; não estabelecem vantagem estatística.']
    (session/'COMPARACAO.md').write_text('\n'.join(lines)+'\n')
    return result
