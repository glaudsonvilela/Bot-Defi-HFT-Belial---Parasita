"""Existing behavioral regressions ported to A6 Controller/RPC; old universe contracts kept in historical tests."""
import copy,json,sys,tempfile,threading,time,unittest,urllib.error
from pathlib import Path
from a51.common import ROOT,LEGACY,SYMBOLS,CORE12,FOCUS4,cohort_of,atomic,digest,DURATION_MS
sys.path.insert(0,str(LEGACY/'perp/tests'))
from test_books_paper import meta,hl,li,signal,CFG,chosen,HOUR
from a51.paper_controller import Controller
from a51.economics import COST_KEYS,validate_costs
from a51.catalog import initial,freeze,source_ready
from a51.sensor_worker import Monitor,WARMUP_MS
from a51.rpc_relay import Budget,SharedReads
from a51.journal import Journal
from a51.replay import replay

def renamed(row,symbol):return json.loads(json.dumps(row).replace('FARTCOIN',symbol))
class Harness:
 def __init__(self,t=1_000_000,emit=None,cfg=None):
  self.t=t;self.seq=0;self.c=Controller(copy.deepcopy(cfg or CFG),emit);self.events=[]
  self.event('markets',{'venue':'hyperliquid','markets':[meta(),renamed(meta(),'RENDER')]},t)
  caps=initial()
  for s in ('FARTCOIN','RENDER'):caps[s].update(qualified=True,state='QUALIFIED',identity=True,price=True,actor=True)
  caps['PUMP'].update(state='RAW_ONLY',qualified=False,reason='TransferHook')
  self.event('cohort',{'cohort':freeze(caps,t),'started_ms':t,'duration_ms':DURATION_MS},t)
  for s in ('FARTCOIN','RENDER'):
   self.healthy(s,t);self.event('book',renamed(hl(t),s),t)
 def event(self,kind,body,t=None):
  self.seq+=1;e=dict(kind=kind,body=body,at_ms=t if t is not None else self.t,source='fixture',epoch=0,sequence=self.seq);self.events.append(copy.deepcopy(e));self.c.apply(e);return e
 def healthy(self,s,t,generation=0):
  return self.event('source_status',dict(symbol=s,status=dict(state='ACTIVE',at_ms=t,generation=generation,actor_ready=True,ready_since_ms=t,integrity=True)),t)
 def open(self,s='FARTCOIN',t=None):
  t=t or self.t;self.healthy(s,t);self.event('book',renamed(hl(t),s),t);self.event('signal',renamed(signal(t,s+'-EP'),s),t);self.event('book',renamed(hl(t+150),s),t+150);return chosen(self.c.engines[cohort_of(s)])



class FundingTests(unittest.TestCase):
 def setup_funding(self):
  h=Harness(HOUR-1000);p=h.open();e=h.c.engine;b=h.c.books.books[('hyperliquid','FARTCOIN')];b.context={'index_price':100.};b.context_ms=HOUR-100;e.record_context(('hyperliquid','FARTCOIN'),b,HOUR-100);return h,p,e
 def rate(self,e,t=HOUR+76,rate=.0001,known=HOUR+50000):e.funding_rate(('hyperliquid','FARTCOIN'),t,rate,known,'HYPERLIQUID_FUNDING_HISTORY')
 def test_official_plus76ms_accepted(self):h,p,e=self.setup_funding();self.rate(e);self.assertIn(HOUR,p.funding)
 def test_preserves_original_timestamp(self):h,p,e=self.setup_funding();self.rate(e);self.assertEqual(e.settled_rates[(('hyperliquid','FARTCOIN'),HOUR)]['source_timestamp_ms'],HOUR+76)
 def test_retained_after_deque_evicted(self):
  h,p,e=self.setup_funding();e.index_history[('hyperliquid','FARTCOIN')].clear();self.rate(e);self.assertIn(HOUR,p.funding)
 def test_no_index_means_unknown(self):
  h=Harness(HOUR-1000);p=h.open();self.rate(h.c.engine);self.assertIsNone(p.funding_total(HOUR+100)[0])
 def test_future_rate_not_accepted(self):h,p,e=self.setup_funding();self.rate(e,known=HOUR);self.assertFalse(p.funding)
 def test_predicted_rate_never_settled(self):
  h,p,e=self.setup_funding();e.funding_rate(('hyperliquid','FARTCOIN'),HOUR,.0001,HOUR+100,'PREDICTED');self.assertFalse(p.funding)
 def test_unbounded_time_rounding_refused(self):h,p,e=self.setup_funding();self.rate(e,t=HOUR+15000);self.assertFalse(p.funding)
 def test_idempotence(self):h,p,e=self.setup_funding();self.rate(e);first=dict(p.funding);self.rate(e);self.assertEqual(p.funding,first)
 def test_conflict_becomes_unknown(self):h,p,e=self.setup_funding();self.rate(e);self.rate(e,rate=.0002);self.assertFalse(p.funding)
 def test_short_sign_correct(self):h,p,e=self.setup_funding();self.rate(e,rate=-.0001);self.assertLess(p.funding[HOUR],0)
 def test_no_post_boundary_index(self):
  h=Harness(HOUR-1000);p=h.open();e=h.c.engine;b=h.c.books.books[('hyperliquid','FARTCOIN')];b.context={'index_price':100};b.context_ms=HOUR+100;e.record_context(('hyperliquid','FARTCOIN'),b,HOUR+100);self.rate(e);self.assertFalse(p.funding)

class CohortPaperTests(unittest.TestCase):
 def test_two_independent_portfolios(self):
  h=Harness();h.open();h.open('RENDER',h.t+200);self.assertEqual(len(h.c.engine.positions),6);self.assertEqual(len(h.c.engines['FOCUS4'].positions),6)
 def test_outputs_cohort_annotated(self):
  out=[];h=Harness(emit=lambda k,r:out.append((k,r)));h.open('RENDER');self.assertTrue(out);self.assertTrue(all(r.get('cohort')=='FOCUS4' for k,r in out if k in ('signals','orders')))
 def test_source_failure_only_one_cohort(self):
  h=Harness();h.open();h.event('source_status',dict(symbol='FARTCOIN',status={'state':'DEGRADED'}),h.t+180);h.open('RENDER',h.t+200);self.assertEqual(len(h.c.engines['FOCUS4'].positions),6)
 def test_no_new_trade_from_pump(self):h=Harness();h.event('signal',renamed(signal(h.t),'PUMP'));self.assertFalse(h.c.engine.orders);self.assertFalse(h.c.engines['FOCUS4'].orders)
 def test_open_position_remains_after_source_loss(self):
  h=Harness();p=h.open();h.event('source_status',dict(symbol='FARTCOIN',status={'state':'DEGRADED'}),h.t+200);h.event('book',hl(h.t+210,102,102.01),h.t+210);h.event('book',hl(h.t+350,102,102.01),h.t+350);self.assertIsNotNone(p.closed_ms)
 def test_return_snapshots_separate(self):h=Harness();self.assertEqual(set(h.c.snapshot(h.t)['groups']),{'CORE12','FOCUS4'})
 def test_no_all_in_without_external_costs(self):h=Harness();p=h.open();self.assertIsNone(h.c.engine.position_view(p,h.t+150)['all_in_model_usd'])
 def test_external_costs_exactly_once(self):
  cfg=copy.deepcopy(CFG);profile=json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text());profile['basis']='USER_ASSUMPTIONS'
  for v in ('hyperliquid','lighter'):profile[v]={k:.01 for k in COST_KEYS}
  cfg['external_costs']=profile;h=Harness(cfg=cfg);p=h.open();r=h.c.engine.position_view(p,h.t+150);self.assertAlmostEqual(r['all_in_model_usd'],r['net_usd']-.04)
 def test_network_unknown_does_not_zero(self):
  profile=json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text());validate_costs(profile);self.assertIsNone(profile['hyperliquid']['network_entry_usd'])
 def test_cost_negative_refused(self):
  profile=json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text());profile['lighter']['network_entry_usd']=-1;self.assertRaises(ValueError,validate_costs,profile)
 def test_cost_nan_refused(self):
  profile=json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text());profile['lighter']['network_entry_usd']=float('nan');self.assertRaises(ValueError,validate_costs,profile)
 def test_quote_stale_no_fake_allin(self):h=Harness();p=h.open();self.assertIsNone(h.c.engine.position_view(p,h.t+10000)['all_in_model_usd'])
 def test_replay_two_groups_exact_hash(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);out=Journal(root/'outputs.jsonl');h=Harness(emit=lambda k,r:out.append({'kind':k,'body':r}));h.open();h.open('RENDER',h.t+200);h.event('book',renamed(hl(h.t+410,102,102.01),'RENDER'),h.t+410);h.event('book',renamed(hl(h.t+550,102,102.01),'RENDER'),h.t+550);h.event('finalize',{},h.t+600);out.close()
   j=Journal(root/'inputs.jsonl')
   for e in h.events:j.append(e)
   j.close();atomic(root/'CONFIG_EFFECTIVE.json',CFG);r=replay(root/'inputs.jsonl',root/'replay');self.assertEqual(r['output_sha256'],digest(root/'outputs.jsonl'))

class FakeClock:
 def __init__(self):self.t=100.
 def __call__(self):return self.t
 def sleep(self,n):self.t+=n
class RpcTests(unittest.TestCase):
 def test_budget_spacing(self):
  c=FakeClock();b=Budget(clock=c,sleep=c.sleep);b.pace(110);first=c();b.pace(110);self.assertGreaterEqual(c()-first,1/3-.00001)
 def test_429_global_cooldown(self):
  c=FakeClock();b=Budget(clock=c,sleep=c.sleep);b.failure(429,'4');b.pace(110);self.assertGreaterEqual(c(),104)
 def test_403_access_does_not_retry(self):
  b=Budget();b.failure(403);self.assertRaises(RuntimeError,b.pace,time.monotonic()+20)
 def test_deadline_refuses(self):
  c=FakeClock();b=Budget(clock=c,sleep=c.sleep);b.failure(429,'20');self.assertRaises(TimeoutError,b.pace,110)
 def test_budget_adapts_not_more_load(self):b=Budget();b.failure(429,'1');self.assertLess(b.view()['current_target_rps'],3)
 def reader(self,result=None):
  calls=[]
  class Client:
   def __init__(self,*a,**k):pass
   def call(self,m,p):calls.append((m,p));return result
  c=FakeClock();b=Budget(clock=c,sleep=c.sleep);return SharedReads('x',None,b,Client,clock=c),calls,b,c
 def test_success_tx_cache(self):
  r,c,b,t=self.reader({'slot':5});self.assertEqual(r.call('getTransaction',['sig']),r.call('getTransaction',['sig']));self.assertEqual(len(c),1)
 def test_null_tx_not_long_cached(self):
  r,c,b,t=self.reader(None);r.call('getTransaction',['sig']);t.t+=.4;r.call('getTransaction',['sig']);self.assertEqual(len(c),2)
 def test_context_batches_never_cached(self):
  r,c,b,t=self.reader({'context':{'slot':1}});r.call('getMultipleAccounts',['p']);r.call('getMultipleAccounts',['p']);self.assertEqual(len(c),2)
 def test_inflight_shared(self):
  calls=[];entered=threading.Event();release=threading.Event()
  class Client:
   def __init__(self,*a,**k):pass
   def call(self,m,p):calls.append(1);entered.set();release.wait(2);return {'slot':7}
  b=Budget(rps=100);r=SharedReads('x',None,b,Client);answers=[];workers=[threading.Thread(target=lambda:answers.append(r.call('getTransaction',['s']))) for _ in range(4)]
  for t in workers:t.start()
  entered.wait(1);time.sleep(.05);release.set()
  for t in workers:t.join(2)
  self.assertEqual(len(calls),1);self.assertEqual(len(answers),4)
 def test_concurrency_no_burst_after_cooldown(self):
  b=Budget(rps=20);b.cooldown=time.monotonic()+.1;times=[]
  def work():b.pace(time.monotonic()+2);times.append(time.monotonic())
  threads=[threading.Thread(target=work) for _ in range(4)]
  for t in threads:t.start()
  for t in threads:t.join()
  times.sort();self.assertTrue(all(y-x>=.045 for x,y in zip(times,times[1:])),times)
if __name__=='__main__':unittest.main()
