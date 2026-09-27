"""Four causal PAPER arms. Identical market stream, fee assumptions and risk limits."""
from collections import defaultdict,deque,Counter,OrderedDict
from decimal import Decimal,ROUND_DOWN
import math


POLICY=dict(risk_usdt=1.,max_notional=100.,min_wallet_sells=2,wallet_window_ms=60000,
    min_wallet_sells_value_usdt=500.,max_chain_age_ms=15000,max_signal_age_ms=15000,
    min_chain_warmup_ms=10000,short_confirmation_bps=-3.,max_drop120_bps=-250.,
    failure_min_hold_ms=30000,failure_persist_ms=5000,failure_loss_bps=10.,failure_sell_fraction=.45,
    fee_bps=5.,stress_bps=3.,orders=False)

class ChainSignals:
    def __init__(self,registry):
        self.registry={c['symbol']:c for c in registry};self.history=defaultdict(lambda:deque(maxlen=256))
        self.statuses={};self.seen=OrderedDict();self.counts=Counter()
    def ingest(self,event,now):
        chain=event.get('chain');kind=event['kind']
        if kind=='chain_status':
            state=event['state'];old=self.statuses.get(chain,{})
            if state=='CONNECTED':self.statuses[chain]=dict(state=state,since=now,last=now)
            elif state=='HEARTBEAT':
                if old.get('state')=='CONNECTED':old['last']=now
                elif old.get('state')=='GAP':self.statuses[chain]=dict(state='CONNECTED',since=now,last=now)
            elif state in ('GAP','FAILED'):
                self.statuses[chain]=dict(state=state,since=now,last=now,reason=event.get('reason'))
                for key in list(self.history):
                    if key[0]==chain:del self.history[key]
            self.counts[chain+'_'+state]+=1;return
        if kind!='chain_trade':return
        self.counts[chain+'_attributions']+=1
        c=self.registry.get(event['symbol'])
        if not c:return
        if not any(p['chain']==chain and p['pool']==event['pool'] and p['mint']==event['mint'] for p in c['sources']):return
        key=(chain,event['tx'],event['wallet'],event['mint'])
        if key in self.seen:self.counts['duplicates']+=1;return
        self.seen[key]=True
        while len(self.seen)>16384:self.seen.popitem(last=False)
        available=event['available_ms'];chain_time=event.get('chain_time_ms')
        if available>now or now-available>2000 or chain_time is None or not 0<=now-chain_time<=POLICY['max_chain_age_ms']:
            self.counts['late_or_unknown_timestamp']+=1;return
        if event.get('qualified_sell') is not True:self.counts['not_verified_net_sell']+=1;return
        quantity=event.get('quantity',0)
        if not isinstance(quantity,(int,float)) or not math.isfinite(quantity) or quantity<=0:return
        if len(self.history)>=8192 and (chain,event['symbol'],event['wallet']) not in self.history:
            del self.history[next(iter(self.history))];self.counts['wallet_history_evicted']+=1
        self.history[(chain,event['symbol'],event['wallet'])].append(dict(event,decision_available_ms=now))
        self.counts[chain+'_qualified_sells']+=1
    def signal(self,s,now,contract_price):
        c=self.registry[s];matches=[]
        for (chain,symbol,wallet),events in self.history.items():
            if symbol!=s:continue
            status=self.statuses.get(chain,{})
            if status.get('state')!='CONNECTED' or now-status['since']<POLICY['min_chain_warmup_ms'] or now-status['last']>20000:continue
            recent=[r for r in events if 0<=now-r['decision_available_ms']<=POLICY['wallet_window_ms']]
            if len(recent)<POLICY['min_wallet_sells'] or now-recent[-1]['decision_available_ms']>POLICY['max_signal_age_ms']:continue
            value=sum(r['quantity'] for r in recent)*contract_price/c['multiplier']
            if value<POLICY['min_wallet_sells_value_usdt']:continue
            matches.append(dict(chain=chain,wallet=wallet,count=len(recent),estimated_value_usdt=value,
                last_available_ms=recent[-1]['decision_available_ms'],transactions=[r['tx'] for r in recent],
                value_basis='CURRENT_BINANCE_REFERENCE_NOT_ONCHAIN_EXECUTION_PRICE'))
        return sorted(matches,key=lambda r:(-r['last_available_ms'],r['wallet']))
