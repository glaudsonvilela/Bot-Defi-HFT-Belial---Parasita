#!/usr/bin/env python3
"""M2 causal, reference-only Tail sidecar. No order or wallet API.
Uses the frozen structure/policy values, with M1's explicit 30s signal validity.
The collector and N1 do not accept any feedback from this module.
"""
from __future__ import annotations
import collections, dataclasses, heapq, math, json, os
from pathlib import Path
from typing import Any
from mp12_contract import Mark, accounting, serial, POLICIES, ROLES, ACTIVE_RULES

MAX_AGE=30000
POOLS={'FARTCOIN':'Bzc9NZfMqkXR6fz1DBph7BDf9BroyEf6pnzESP7v5iiw',
       'BOME':'DSUvc5qf5LJHHV5e2tD184ixotSnCnwj7i4jJa4Xsrmt',
       'WIF':'EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx',
       'POPCAT':'FRhB8L7Y9Qq41qZXYLtC2nw8An1RJfLLxRF2x9RwLLMo',
       'RAY':'AVs9TA4nWDzfPJE9gGVNJMVhcQy3V9PGazuz33BfG2RA',
       'JUP':'EYErUp5muPYEEkeaUCY22JibeZX7E9UuMcJFZkmNAN7c'}
def configure_pool_manifest():
    path=os.environ.get('BELIAL_T3_POOL_MANIFEST')
    if path:
        data=json.loads(Path(path).read_text())
        if data.get('ok') is not True or len(data.get('pools',[]))!=12:raise RuntimeError('T3_POOL_MANIFEST_NOT_VALIDATED')
        resolved={r['symbol']:r['pool'] for r in data['pools']}
        if set(resolved)!=set(ROLES):raise RuntimeError('T3_POOL_SYMBOL_MISMATCH')
        POOLS.clear();POOLS.update(resolved)
configure_pool_manifest()
RULES={
'SAME_WALLET_10S_GE_5_SOL':('same',None,1,5),
'SAME_WALLET_10S_GE_3_SELLS':('same',None,3,0),
'SAME_WALLET_10S_GE_2_SELLS_GE_2_SOL':('same',None,2,2),
'MULTI_WALLET_10S_GE_5_SOL':('multi',None,2,5)}

def num(x):
    if x is None or isinstance(x,bool): raise ValueError('NUMBER_MISSING')
    v=float(x)
    if not math.isfinite(v):raise ValueError('NONFINITE')
    return v

def ret(a,b):return 10000*(num(a)-num(b))/num(a)

@dataclasses.dataclass
class Position:
    episode:dict
    policy:str
    price:float
    mark:Mark
    opened:int
    mfe:float=0.0
    mae:float=0.0
    request:tuple[int,str,int]|None=None

class Tail:
    def __init__(self,emit,session:str):
        self.emit=emit;self.session=session;self.clock=0;self.seq=0
        self.marks={s:collections.deque(maxlen=4096) for s in ROLES}
        self.exits={s:collections.deque(maxlen=4096) for s in ROLES}
        self.pending=[];self.last_rule={};self.last_episode={};self.positions={}
        self.seen=collections.OrderedDict();self.last_rows=collections.deque(maxlen=8)
        self.counts=collections.Counter()
        self.agg={p:{'n':0,'gross_bps':0.0} for p in POLICIES}
        self.pool_agg={sym:{p:{'n':0,'gross_bps':0.0} for p in POLICIES} for sym in ROLES}
        self.pool_episodes=collections.Counter()
    def record(self,kind,row):
        self.emit(kind,serial({'schema':'belial_mp12_shadow_compare_'+kind+'_v1','at_unix_ms':self.clock,
          'session':self.session,'paper_only':True,'orders':False,'transactions_submitted':False,
          'signal_engine_feedback':False,'executable_net_pnl':None,**row}))
    def dedup(self,key):
        if key in self.seen:return False
        self.seen[key]=True
        if len(self.seen)>32768:self.seen.popitem(last=False)
        return True
    def mark_at(self,s,target,available=None):
        available=self.clock if available is None else available
        candidates=[(m.ts,m.seq,m) for m,a in self.marks[s] if m.ts<=target and a<=available and target-m.ts<=MAX_AGE]
        return max(candidates,key=lambda x:x[:2])[2] if candidates else None
    def trigger_mark(self,row):
        sig=str(row['event_id']).split('|')[0]
        return next((m for m,a in reversed(self.marks[row['symbol']]) if m.signature==sig and m.ts==int(row['event_unix_ms']) and a<=self.clock),None)
    def schedule(self,when,kind,obj):
        if len(self.pending)>=2048:raise RuntimeError('TAIL_PENDING_CAPACITY')
        self.seq+=1;heapq.heappush(self.pending,(when,self.seq,kind,obj))
    def feed(self,kind,row,at):
        if at<self.clock:raise ValueError('CLOCK_REGRESSION')
        self.clock=at
        if kind=='swap':
            try:m=Mark.from_row(row,self.seq)
            except (ValueError,KeyError,TypeError):self.counts['invalid_marks']+=1;return
            if m.pool!=POOLS[m.symbol]:self.counts['invalid_pool']+=1;return
            if m.ts>at or not self.dedup(('swap',m.pool,m.signature)):return
            if 'reference_post_price' in row and abs(num(row['reference_post_price'])-float(m.post))>max(1e-15,abs(float(m.post))*1e-10):
                raise RuntimeError('RUST_PYTHON_REFERENCE_MISMATCH')
            self.marks[m.symbol].append((m,at));self.seq+=1;self.counts['marks']+=1
            for key,p in list(self.positions.items()):
                if p.episode['symbol']!=m.symbol or m.ts<p.opened or at-m.ts>MAX_AGE:continue
                g=ret(p.price,m.post);p.mfe=max(p.mfe,g);p.mae=max(p.mae,-g)
                stop,activate,give,duration=POLICIES[p.policy]
                if p.request:self.close(key,p,m,at)
                elif g<=-float(stop):self.request(key,p,at,'HARD_STOP',at)
                elif p.mfe>=float(activate) and p.mfe-g>=float(give):self.request(key,p,at,'TRAIL_GIVEBACK',at)
        elif kind=='exit':
            try:
                s=str(row['symbol']);event=int(row['event_unix_ms']);av=int(row['signal_available_unix_ms'])
                q=num(row['sell_quote_sol']);absorption=num(row['absorption_score'])
                if s not in ROLES or event<=0 or not event<=av<=at or q<0:raise ValueError()
            except (KeyError,ValueError,TypeError):self.counts['invalid_exits']+=1;return
            if not self.dedup(('exit',row['event_id'])):return
            self.counts['wallet_exits']+=1
            # Absorption is a gate, not a model output. Its observed availability is explicit.
            if absorption>=60 and at-event<=MAX_AGE:
                for key,p in list(self.positions.items()):
                    if p.episode['symbol']==s and event>=p.opened:self.request(key,p,at,'ABSORPTION_RETURN',at)
            previous=[x for x in self.exits[s] if 0<=event-int(x['event_unix_ms'])<=10000]
            self.exits[s].append(dict(row))
            if at-event>MAX_AGE:self.counts['signal_expired_before_arm']+=1;return
            window=previous+[row];same=[x for x in window if x['wallet']==row['wallet']]
            count=len(same);total=sum(num(x['sell_quote_sol']) for x in same)
            wallets=len({x['wallet'] for x in window});cluster=sum(num(x['sell_quote_sol']) for x in window)
            fired=[]
            for rid,(mode,symbol,n,qmin) in RULES.items():
                if mode=='same':ok=count>=n and total>=qmin;key=(rid,s,row['wallet'])
                else:
                    if symbol is not None and symbol!=s:continue
                    ok=wallets>=n and cluster>=qmin;key=(rid,s)
                if ok and event-self.last_rule.get(key,-10**18)>30000:
                    self.last_rule[key]=event;fired.append(rid)
            if len(self.last_rule)>16384:self.last_rule={k:v for k,v in self.last_rule.items() if at-v<=120000}
            if fired:
                arm={**row,'rule_ids':fired,'arm_observed_ms':at,'active_for_entry':bool(ACTIVE_RULES.intersection(fired))}
                self.counts['arms']+=1;self.record('events',{'kind':'ARM',**arm})
                self.schedule(max(event+5000,av,at),'confirm',arm)
    def confirm(self,a):
        event=int(a['event_unix_ms']);s=a['symbol'];due=event+5000
        anchor=self.trigger_mark(a);mark=self.mark_at(s,due)
        value=-ret(anchor.post,mark.post) if anchor and mark else None
        reason=('OBSERVE_ONLY' if not a['active_for_entry'] else
          'SIGNAL_EXPIRED_30S' if self.clock-event>MAX_AGE else
          'REFERENCE_MISSING_OR_STALE' if value is None else
          'ABSORPTION_GATE' if num(a['absorption_score'])>=60 else
          'NO_NEGATIVE_CONTINUATION' if value>=0 else None)
        result={'kind':'CONFIRM','event_id':a['event_id'],'symbol':s,'due_ms':due,'actor_available_ms':a['signal_available_unix_ms'],
          'publication_delay_ms':self.clock-max(due,int(a['signal_available_unix_ms'])),
          'signal_age_ms':self.clock-event,'reference_return_5s_bps':value,'rule_ids':a['rule_ids']}
        self.counts['confirm_evaluated']+=1
        if reason:self.counts['confirm_rejected']+=1;self.record('events',{**result,'reason':reason,'confirmed':False});return
        self.counts['confirm_pass']+=1
        prior=self.last_episode.get(s)
        if prior and self.clock-prior['confirm_observed_ms']<=30000:
            self.counts['support']+=1
            self.record('events',{**result,'kind':'EPISODE_SUPPORT','episode_id':prior['episode_id'],'confirmed':True})
            return
        ep={'episode_id':f'MP12_SHADOW|{s}|{a["event_id"]}|{self.clock}','symbol':s,'wallet':a['wallet'],
          'arm_event_id':a['event_id'],'event_ms':event,'rule_ids':a['rule_ids'],'confirm_observed_ms':self.clock,
          'entry_target_ms':self.clock+1000}
        self.last_episode[s]=ep
        self.record('events',{**result,'confirmed':True,'episode_id':ep['episode_id']})
        self.schedule(self.clock+1000,'entry',ep)
    def entry(self,ep):
        mark=self.mark_at(ep['symbol'],ep['entry_target_ms'])
        if mark is None or self.clock-ep['event_ms']>MAX_AGE:
            self.counts['entry_unavailable']+=1;self.record('events',{'kind':'ENTRY_UNAVAILABLE',**ep});return
        # Actual publication time, never a paper entry retrodated to a past timer.
        ep={**ep,'entry_observed_ms':self.clock,'entry_timer_delay_ms':self.clock-ep['entry_target_ms'],
          'entry_reference':str(mark.post),'mark_age_ms':self.clock-mark.ts}
        if ep['mark_age_ms']>MAX_AGE:self.counts['entry_unavailable']+=1;return
        if len(self.positions)+len(POLICIES)>1024:raise RuntimeError('POSITION_CAPACITY')
        self.counts['episodes']+=1;self.pool_episodes[ep['symbol']]+=1;self.record('events',{'kind':'EPISODE_OPEN',**ep})
        for policy in POLICIES:
            key=(ep['episode_id'],policy);self.positions[key]=Position(ep,policy,float(mark.post),mark,self.clock)
            self.schedule(self.clock+POLICIES[policy][3],'timeout',key)
    def request(self,key,p,at,reason,due):
        if p.request is None:
            p.request=(at,reason,due)
            self.record('events',{'kind':'EXIT_REQUEST','episode_id':key[0],'policy_id':key[1],
              'request_ms':at,'trigger_due_ms':due,'timer_delay_ms':at-due,'reason':reason})
        mark=self.mark_at(p.episode['symbol'],at)
        if mark and mark.ts>=p.opened:self.close(key,p,mark,at)
        else:self.counts['exit_requests_waiting_mark']+=1
    def close(self,key,p,mark,at):
        if key not in self.positions:return
        assert p.request is not None
        request,reason,due=p.request;acc=accounting(p.price,mark.post)
        g=float(acc['reference_gross_short_bps']);p.mfe=max(p.mfe,g);p.mae=max(p.mae,-g)
        row={'episode_id':key[0],'policy_id':key[1],'symbol':p.episode['symbol'],'wallet':p.episode['wallet'],
          'entry_observed_ms':p.opened,'exit_requested_ms':request,'trigger_due_ms':due,'exit_observed_ms':at,
          'exit_wait_ms':at-request,'hold_ms':at-p.opened,'exit_reason':reason,
          'entry_reference':p.price,'exit_reference':str(mark.post),'exit_mark_age_ms':at-mark.ts,
          'exit_signature':mark.signature,'mfe_reference_bps':p.mfe,'mae_reference_bps':p.mae,'accounting':acc,
          'status':'REFERENCE_CLOSED_NOT_EXECUTED'}
        self.record('trades',row);self.last_rows.append(serial(row));self.positions.pop(key)
        agg=self.agg[key[1]];agg['n']+=1;agg['gross_bps']+=g
        pagg=self.pool_agg[p.episode['symbol']][key[1]];pagg['n']+=1;pagg['gross_bps']+=g
        self.counts['trades']+=1
    def advance(self,now,inclusive=True):
        if now<self.clock:raise ValueError('CLOCK_REGRESSION')
        self.clock=now
        while self.pending and (self.pending[0][0]<=now if inclusive else self.pending[0][0]<now):
            due,_,kind,obj=heapq.heappop(self.pending)
            if kind=='confirm':self.confirm(obj)
            elif kind=='entry':self.entry(obj)
            elif obj in self.positions:self.request(obj,self.positions[obj],now,'MAX_HOLD',due)
    def state(self):
        policies={}
        for key,a in self.agg.items():
            open_rows=[]
            for p in self.positions.values():
                if p.policy!=key:continue
                m=self.mark_at(p.episode['symbol'],self.clock)
                open_rows.append({'episode_id':p.episode['episode_id'],'symbol':p.episode['symbol'],
                  'entry_observed_ms':p.opened,'exit_pending':p.request is not None,
                  'unrealized_reference_bps':ret(p.price,m.post) if m else None})
            policies[key]={'n':a['n'],'open':len(open_rows),'gross_reference_bps':a['gross_bps'],
              'scenarios':{str(c):{'normalized_pnl_per_100_per_entry':(a['gross_bps']-a['n']*c)/100,'all_in_assumed_bps':c} for c in (20,50,75)},
              'open_rows':open_rows[-8:]}
        by_pool={};focus='TAIL_STOP100_TRAIL75'
        for sym in ROLES:
            a=self.pool_agg[sym][focus];n=a['n'];g=a['gross_bps']
            by_pool[sym]={'episodes':self.pool_episodes[sym],'closed':n,'closed_policy_trades':n,'policy_id':focus,
              'pnl20':round((g-n*20)/100,6) if n else None,'pnl50':round((g-n*50)/100,6) if n else None,
              'pnl75':round((g-n*75)/100,6) if n else None}
        return {'schema':'belial_mp12_shadow_compare_state_v1','clock_ms':self.clock,'counts':dict(self.counts),'policies':policies,
          'by_pool':by_pool,'recent_trades':list(self.last_rows),'open_positions':len(self.positions),'pool_roles':ROLES,
          'price_basis':'PER_POOL_PROTOCOL_SPECIFIC_NOT_EXECUTION','counterfactual_shadow_compare':True,'not_strategy_pnl':True,'not_account_balance':True,
          'shadow_focus_policy':focus,'executable_net_pnl':None,'paper_only':True,'orders':False,'transactions_submitted':False,'signal_engine_feedback':False}
