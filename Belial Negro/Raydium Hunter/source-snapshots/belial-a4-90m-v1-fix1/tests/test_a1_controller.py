import copy,json,sys,tempfile,unittest
from pathlib import Path
from a1.common import ROOT,LEGACY,DURATION_MS,atomic,digest
sys.path.insert(0,str(LEGACY/'perp/tests'))
from test_books_paper import meta,hl,li,signal,CFG,chosen,HOUR
from a1.paper_controller import Controller
from a1.catalog import initial,freeze,source_ready,summarize
from a1.journal import Journal,read_verified,OrderedDedup
from a1.replay import replay

class Harness:
 def __init__(self,t=1_000_000,two=False,emit=None):
  self.t=t;self.seq=0;self.c=Controller(copy.deepcopy(CFG),emit);self.events=[]
  self.event('markets',{'venue':'hyperliquid','markets':[meta()]},t)
  if two:self.event('markets',{'venue':'lighter','markets':[meta('lighter')]},t)
  caps=initial();caps['FARTCOIN'].update(qualified=True,state='QUALIFIED',identity=True,price=True,actor=True)
  caps['PUMP'].update(state='RAW_ONLY',qualified=False,reason='TOKEN2022_EXTENSION_UNSUPPORTED:TransferHook')
  self.event('cohort',{'cohort':freeze(caps,t),'started_ms':t,'duration_ms':DURATION_MS},t)
  self.healthy(t);self.event('book',hl(t),t)
  if two:self.event('book',li(t),t)
 def event(self,kind,body,t=None,source='fixture'):
  self.seq+=1;event={'kind':kind,'body':body,'at_ms':self.t if t is None else t,'source':source,'epoch':0,'sequence':self.seq};self.events.append(copy.deepcopy(event));self.c.apply(event);return event
 def healthy(self,t):return self.event('source_status',{'symbol':'FARTCOIN','status':dict(state='ACTIVE',at_ms=t,generation=0,integrity=True)},t)
 def open(self):
  self.event('signal',signal(self.t),self.t);self.event('book',hl(self.t+150),self.t+150);return chosen(self.c.engine)

class Capabilities(unittest.TestCase):
 def test_requested_not_qualified(self):
  h=Harness();r=summarize(h.c.cohort,h.c.statuses,[b.view(h.t) for b in h.c.books.books.values()],h.t)
  self.assertEqual((r['requested'],r['qualified_at_start'],r['live_signal_sources'],r['usable_books'],r['paper_prerequisites_ready']),(12,1,1,1,1))
 def test_no_cohort_is_not_eligible(self):self.assertFalse(source_ready('PUMP',None,{},0)[0])
 def test_missing_source_has_reason(self):self.assertFalse(source_ready('FARTCOIN',Harness().c.cohort,{},1_000_000)[0])
 def test_heartbeat_stale(self):
  h=Harness();self.assertFalse(source_ready('FARTCOIN',h.c.cohort,h.c.statuses['FARTCOIN'],h.t+5001)[0])
 def test_heartbeat_future(self):
  h=Harness();h.c.statuses['FARTCOIN']['at_ms']=h.t+501;self.assertFalse(h.c.eligible('FARTCOIN',h.t)[0])
 def test_restart_cannot_reenter(self):
  h=Harness();h.c.statuses['FARTCOIN']['generation']=1;self.assertFalse(h.c.eligible('FARTCOIN',h.t)[0])
 def test_late_qualification_not_added(self):
  h=Harness();h.event('source_status',{'symbol':'PUMP','status':dict(state='ACTIVE',at_ms=h.t,generation=0,integrity=True)})
  self.assertFalse(h.c.eligible('PUMP',h.t)[0]);self.assertFalse(h.c.cohort['integral_mp12_at_start'])
 def test_refreeze_rejected(self):
  h=Harness();self.assertRaisesRegex(ValueError,'FROZEN',h.event,'cohort',dict(cohort=h.c.cohort,started_ms=h.t,duration_ms=DURATION_MS))

class FaultContainment(unittest.TestCase):
 def test_pump_failure_does_not_stop_fart_book_or_signal(self):
  h=Harness();h.event('source_status',{'symbol':'PUMP','status':dict(state='QUARANTINED',reason='TransferHook')});h.open();self.assertEqual(len(h.c.engine.positions),6)
 def test_pump_blocked_does_not_fake_orders(self):
  h=Harness();s=signal(h.t);s['symbol']='PUMP';h.event('signal',s);self.assertEqual(h.c.blocked_signals,1);self.assertFalse(h.c.engine.orders)
 def test_raw_log_not_signal(self):
  h=Harness();h.event('signal',dict(symbol='FARTCOIN',kind='RAW_LOG'));self.assertFalse(h.c.engine.orders)
 def test_drop_spot_cancels_only_entry(self):
  h=Harness();h.event('signal',signal(h.t));self.assertTrue(h.c.engine.orders)
  h.event('source_status',{'symbol':'FARTCOIN','status':dict(state='QUARANTINED',reason='RPC_DISCONNECTED')},h.t+10);self.assertFalse(h.c.engine.orders)
  h.event('book',hl(h.t+150),h.t+150);self.assertFalse(h.c.engine.positions);self.assertTrue(h.c.books.books[('hyperliquid','FARTCOIN')].valid)
 def test_source_loss_does_not_delete_position(self):
  h=Harness();p=h.open();n=len(h.c.engine.positions);h.event('source_status',{'symbol':'FARTCOIN','status':dict(state='QUARANTINED')},h.t+200)
  self.assertEqual(len(h.c.engine.positions),n);self.assertIsNone(p.closed_ms)
 def test_source_loss_keeps_exit_evaluation(self):
  h=Harness();p=h.open();h.event('source_status',{'symbol':'FARTCOIN','status':dict(state='QUARANTINED')},h.t+200)
  h.event('book',hl(h.t+210,101.1,101.11),h.t+210);self.assertEqual(p.exit_reason,'STOP');h.event('book',hl(h.t+320,101.2,101.21),h.t+320);self.assertIsNotNone(p.closed_ms)
 def test_evidence_failure_blocks_entry_preserves_exit(self):
  h=Harness();p=h.open();h.event('integrity',{'ok':False,'reason':'DISK'},h.t+200);h.event('signal',signal(h.t+201,'new'),h.t+201)
  self.assertFalse(any(o['kind']=='ENTRY' for o in h.c.engine.orders));h.event('book',hl(h.t+210,102,102.01),h.t+210);h.event('book',hl(h.t+320,102,102.01),h.t+320);self.assertIsNotNone(p.closed_ms)
 def test_stale_source_stops_fill_before_it_happens(self):
  h=Harness();h.event('signal',signal(h.t));h.c.statuses['FARTCOIN']['at_ms']=h.t-5000;h.event('book',hl(h.t+150),h.t+150);self.assertFalse(h.c.engine.positions)
 def test_delta_gap_only_one_venue(self):
  h=Harness(two=True);h.event('book',li(h.t+10,nonce=3,begin=2,snapshot=False),h.t+10)
  self.assertFalse(h.c.books.books[('lighter','FARTCOIN')].valid);self.assertTrue(h.c.books.books[('hyperliquid','FARTCOIN')].valid)
 def test_bad_external_payload_isolates_venue(self):
  h=Harness(two=True);h.event('book',{'venue':'hyperliquid','generation':1,'received_ns':(h.t+10)*1000000,'data':[]},h.t+10)
  self.assertFalse(h.c.books.books[('hyperliquid','FARTCOIN')].valid);self.assertTrue(h.c.books.books[('lighter','FARTCOIN')].valid)
 def test_bad_nested_external_payload_isolates_venue(self):
  h=Harness(two=True);h.event('book',{'venue':'hyperliquid','generation':1,'received_ns':(h.t+10)*1000000,'data':{'data':[]}},h.t+10)
  self.assertFalse(h.c.books.books[('hyperliquid','FARTCOIN')].valid);self.assertTrue(h.c.books.books[('lighter','FARTCOIN')].valid)
 def test_duplicate_delta_no_freshness_refresh(self):
  h=Harness(two=True);b=h.c.books.books[('lighter','FARTCOIN')];n=b.updates;h.event('book',li(h.t+5000,nonce=1,snapshot=False,begin=1),h.t+5000)
  self.assertEqual(b.updates,n);self.assertFalse(b.ready(h.t+5000)[0])
 def test_old_generation_does_not_reset_new(self):
  h=Harness();h.event('book',hl(h.t+10,gen=3),h.t+10);h.event('book',hl(h.t+20,gen=2),h.t+20)
  self.assertEqual(h.c.books.books[('hyperliquid','FARTCOIN')].generation,3)
 def test_disconnect_clears_context(self):
  h=Harness();b=h.c.books.books[('hyperliquid','FARTCOIN')];b.context={'funding':.001};b.context_ms=h.t
  h.event('book',{'venue':'hyperliquid','generation':2,'received_ns':(h.t+10)*1000000,'transport':'DISCONNECTED'},h.t+10);self.assertFalse(b.context)
 def test_stale_close_not_fake_current_profit(self):
  h=Harness();p=h.open();self.assertIsNone(h.c.engine.position_view(p,h.t+5000)['net_usd'])
 def test_duplicate_signal_no_duplicate_positions(self):
  h=Harness();h.open();h.event('signal',signal(h.t),h.t+200);self.assertFalse(any(o['kind']=='ENTRY' for o in h.c.engine.orders));self.assertEqual(len(h.c.engine.positions),6)
 def test_funding_idempotent(self):
  h=Harness(HOUR-1000);p=h.open();h.c.engine.index_history[('hyperliquid','FARTCOIN')].append((HOUR-100,100.))
  h.event('book',hl(HOUR+10),HOUR+10)
  value=dict(key=['hyperliquid','FARTCOIN'],at=HOUR,rate=.0001,known_ms=HOUR+20,origin='FIXTURE')
  h.event('funding',value,HOUR+20);h.event('funding',value,HOUR+21);self.assertEqual(len(p.funding),1)
 def test_unknown_funding_stays_unknown(self):
  h=Harness(HOUR-1000);p=h.open();h.event('book',hl(HOUR+10),HOUR+10);self.assertIsNone(h.c.engine.position_view(p,HOUR+10)['net_usd'])
 def test_after_deadline_does_not_fill(self):
  h=Harness();p=h.open();h.event('book',hl(h.t+DURATION_MS+1,102,102.1),h.t+DURATION_MS+1);self.assertTrue(h.c.finalized);self.assertIsNone(p.closed_ms)
 def test_finalization_is_not_sell(self):
  h=Harness();p=h.open();h.event('finalize',{},h.t+200);self.assertIsNone(p.closed_ms);self.assertFalse(h.c.engine.orders)
 def test_market_list_frozen(self):
  h=Harness();self.assertRaisesRegex(ValueError,'FROZEN',h.event,'markets',dict(venue='lighter',markets=[meta('lighter')]))
 def test_processing_time_regression_refused(self):
  h=Harness();self.assertRaisesRegex(ValueError,'CLOCK_REGRESSION',h.event,'tick',{},h.t-1)
 def test_wrong_duration_refused(self):
  c=Controller(CFG);self.assertRaisesRegex(ValueError,'90_MINUTES',c.apply,dict(kind='cohort',body=dict(duration_ms=540),at_ms=1,source='x',epoch=0,sequence=1))

class ReplayTests(unittest.TestCase):
 def test_replayed_inputs_same_output_hash(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);out=Journal(root/'outputs.jsonl');h=Harness(emit=lambda k,r:out.append({'kind':k,'body':r}));h.open();h.event('book',hl(h.t+210,102,102.01),h.t+210);h.event('book',hl(h.t+320,102,102.01),h.t+320);h.event('finalize',{},h.t+400);out.close()
   inputs=Journal(root/'inputs.jsonl')
   for e in h.events:inputs.append(e)
   inputs.close();atomic(root/'CONFIG_EFFECTIVE.json',CFG);result=replay(root/'inputs.jsonl',root/'replay')
   self.assertEqual(result['output_sha256'],digest(root/'outputs.jsonl'));self.assertFalse(result['n2_training'])
 def test_duplicate_input_does_not_refresh_or_reexecute(self):
  h=Harness();event=h.events[-1];updates=h.c.books.books[('hyperliquid','FARTCOIN')].updates;h.c.apply(event);self.assertEqual(h.c.books.books[('hyperliquid','FARTCOIN')].updates,updates)
 def test_altered_journal_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'i';j=Journal(p);j.append({'x':1});j.close();p.write_text(p.read_text().replace('"x":1','"x":2'))
   self.assertRaisesRegex(ValueError,'HASH',list,read_verified(p))
 def test_truncated_journal_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'i';j=Journal(p);j.append({'x':1});j.close();p.write_bytes(p.read_bytes()[:-1]);self.assertRaisesRegex(ValueError,'INCOMPLETE',list,read_verified(p))
 def test_hash_chain_reordering_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'i';j=Journal(p);j.append({'x':1});j.append({'x':2});j.close();p.write_text(''.join(reversed(p.read_text().splitlines(True))));self.assertRaisesRegex(ValueError,'SEQUENCE',list,read_verified(p))
 def test_dedup_memory_is_per_source(self):
  d=OrderedDedup()
  for i in range(1,10001):self.assertTrue(d.accept('x',0,i))
  self.assertEqual(len(d.last),1);self.assertFalse(d.accept('x',0,5));self.assertTrue(d.accept('x',1,1))
if __name__=='__main__':unittest.main()
