"""Post-capture public settlement history; no predicted funding presented as paid."""
from collections import defaultdict
from a7.net import fetch,Denied
from a51.journal import read_verified
from a51.common import atomic

def reconcile(session,ended_ms):
    positions=defaultdict(list)
    for e in read_verified(session/'decisions.jsonl'):
        if e['kind'] in ('EXIT_FILLED','OPEN_CENSORED'):positions[e['position']['symbol']].append((e['arm'],e['position']))
    result={};histories={};denied=False
    for symbol,items in positions.items():
        begin=min(p['opened_ms'] for _,p in items)
        if denied:histories[symbol]=dict(error='PROVIDER_DISABLED_ACCESS_DENIED');continue
        try:
            rows=fetch('https://fapi.binance.com/fapi/v1/fundingRate?symbol='+symbol+'&startTime='+str(begin)+'&endTime='+str(ended_ms)+'&limit=1000')
            if not isinstance(rows,list) or len(rows)>=1000:raise ValueError('FUNDING_HISTORY_INCOMPLETE')
            histories[symbol]=rows
        except Denied:denied=True;histories[symbol]=dict(error='ACCESS_DENIED');continue
        except Exception as e:histories[symbol]=dict(error=type(e).__name__);continue
    atomic(session/'FUNDING_HISTORY.json',histories)
    for symbol,items in positions.items():
        rows=histories[symbol]
        for arm,p in items:
            end=p.get('closed_ms',ended_ms);value=0.;complete=isinstance(rows,list);settlements=[]
            if complete:
                for r in rows:
                    at=int(r['fundingTime'])
                    if r['symbol']!=symbol:complete=False;break
                    if p['opened_ms']<=at<end:
                        try:
                            mark=float(r['markPrice']);rate=float(r['fundingRate'])
                            import math
                            if not math.isfinite(mark) or mark<=0 or not math.isfinite(rate):raise ValueError()
                            cash=p['qty']*mark*rate # short receives positive funding
                            value+=cash;settlements.append(dict(at_ms=at,cash_usdt=cash))
                        except (ValueError,KeyError,TypeError):complete=False
            net=p.get('net_ex_funding');key=arm+':'+symbol+':'+str(p['opened_ms'])
            result[key]=dict(arm=arm,symbol=symbol,opened_ms=p['opened_ms'],closed_ms=p.get('closed_ms'),
                funding_status='HISTORY_RECONCILED_MODEL' if complete else 'UNKNOWN',funding_cash_usdt=value if complete else None,
                modeled_net_including_funding=(net+value) if complete and net is not None else None,
                settlements=settlements,path_censored=p['path_censored'],right_censored=p.get('right_censored',False),
                fee_basis='5_BPS_EACH_SIDE_ASSUMED',real_account_pnl=False)
    atomic(session/'FUNDING_RECONCILIATION.json',result,True)
    return result
