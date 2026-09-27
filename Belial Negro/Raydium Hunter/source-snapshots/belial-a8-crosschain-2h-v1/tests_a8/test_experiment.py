import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from a8.lab import Lab,Arm
from a8.signals import ChainSignals
from a8.readiness import capture_readiness
from tests_a7.test_lab import REG,trade
from tests_a6.test_core import T,META,book
from a51.common import atomic
from a51.journal import Journal
from a8.replay import replay

def flow(t,p=1.,ident=None):return dict(kind='flow',received_ms=t,symbol='WIFUSDT',event_ms=t,buy=10,sell=200,trades=3,price=p,price_trade_id=ident or t,price_source='AGGTRADE_LAST_EXECUTED',generation=1)
def warm(entry=False,exit=False,source='AGGTRADE_LAST_EXECUTED'):
 c=ChainSignals(REG)
 for name in ('solana','bsc'):c.ingest(dict(kind='chain_status',chain=name,state='CONNECTED'),T-180000);c.ingest(dict(kind='chain_status',chain=name,state='HEARTBEAT'),T)
 a=Arm('TEST',c,lambda e:None,entry,exit,source);a.start=T-180000;a.deadline=T+1000000
 a.ingest(dict(kind='metadata',received_ms=T-180000,data=META),T-180000)
 for t in range(T-180000,T+1,1000):
  p=1+(T-t)/10000000
  a.ingest(flow(t,p),t);a.ingest(dict(kind='wide',received_ms=t,data=dict(e='24hrMiniTicker',s='WIFUSDT',E=t,c=str(p))),t)
 a.ingest(book(T,seq=1),T);return a
class Prices(unittest.TestCase):
 def test_new_source_ignores_miniticker(self):
  a=warm();before=list(a.history['WIFUSDT']);a.ingest(dict(kind='wide',received_ms=T+1,data=dict(e='24hrMiniTicker',s='WIFUSDT',E=T+1,c='99')),T+1);self.assertEqual(list(a.history['WIFUSDT']),before)
 def test_old_control_ignores_trade_prices(self):
  a=warm(source='MINITICKER');before=list(a.history['WIFUSDT']);a.ingest(flow(T+1000,99),T+1000);self.assertEqual(list(a.history['WIFUSDT']),before)
 def test_new_price_is_last_executed_and_time_is_not_refreshed(self):
  a=warm();a.ingest(flow(T+1000,.99),T+1050);self.assertEqual(a.history['WIFUSDT'][-1],(T+1000,.99));self.assertEqual(a.price_reason('WIFUSDT',T+12000),'PRICE_STALE')
 def test_stale_and_duplicate_flow_cannot_refresh_price(self):
  a=warm();before=list(a.history['WIFUSDT']);a.ingest(flow(T,99),T+3000);self.assertEqual(list(a.history['WIFUSDT']),before)
  a.ingest(flow(T+1000,99,ident=T),T+1000);self.assertEqual(list(a.history['WIFUSDT']),before)
 def test_missing_invalid_price_not_invented(self):
  for p in (None,float('nan'),0,-1,True):
   a=warm();before=list(a.history['WIFUSDT']);a.ingest(flow(T+1000,p),T+1000);self.assertEqual(list(a.history['WIFUSDT']),before)
 def test_recurrent_sell_can_be_evaluated_with_trade_history(self):
  a=warm(True);self.assertEqual(a.reason('WIFUSDT',T)[0],'NO_FRESH_RECURRENT_NET_SELL')
  a.chain.ingest(trade(),T);a.chain.ingest(trade('two'),T);self.assertIsNone(a.reason('WIFUSDT',T)[0])
 def test_chain_failure_not_mislabeled_as_no_sells(self):
  a=warm(True);a.chain.ingest(dict(kind='chain_status',chain='solana',state='FAILED',reason='ACCESS_DENIED'),T);self.assertEqual(a.reason('WIFUSDT',T)[0],'CHAIN_UNAVAILABLE')
 def test_missing_pool_explicit(self):
  a=warm(True);a.chain.registry=copy.deepcopy(a.chain.registry);a.chain.registry['WIFUSDT']['sources']=[];self.assertEqual(a.reason('WIFUSDT',T)[0],'POOL_UNAVAILABLE')
 def test_price_warmup_diagnostics(self):
  a=warm();a.history['WIFUSDT'].clear();self.assertEqual(a.reason('WIFUSDT',T)[0],'PRICE_NOT_OBSERVED')
  a.ingest(flow(T+1000),T+1000);self.assertEqual(a.reason('WIFUSDT',T+1000)[0],'PRICE_REFERENCE_GAP')
 def test_baseline_and_price_variant_equal_given_same_price_observations(self):
  a=warm();b=warm(source='MINITICKER');self.assertEqual(a.features('WIFUSDT',T),b.features('WIFUSDT',T));self.assertEqual(a.reason('WIFUSDT',T),b.reason('WIFUSDT',T))
class Lifecycle(unittest.TestCase):
 def test_pending_cannot_fill_after_entry_deadline(self):
  a=warm();a.deadline=T+200;a.tick(T,['WIFUSDT']);self.assertTrue(a.pending);a.ingest(book(T+300),T+300);self.assertFalse(a.positions);self.assertFalse(a.pending)
 def test_followup_preserves_positions_and_allows_exit(self):
  a=warm();a.tick(T,['WIFUSDT']);a.ingest(book(T+300),T+300);self.assertTrue(a.positions)
  l=Lab(REG,lambda e:None);l.arms=[a];l.ingest(dict(kind='end_entries'),T+1000);self.assertTrue(a.positions)
  # Stop executes on a later observed book, after entry window closes.
  a.deadline=T+1000;a.ingest(book(T+2000,1.02,1.0202,seq=T+2000),T+2000);self.assertIsNotNone(a.positions['WIFUSDT']['exit_request'])
  a.ingest(book(T+2400,1.02,1.0202,seq=T+2400),T+2400);self.assertTrue(a.closed);self.assertFalse(a.positions)
 def test_readiness_requires_stability_and_price_observation(self):
  l=Lab(REG,lambda e:None);a=warm();l.arms[0]=a;l.price_arm=a;l.chain=a.chain
  a.channel_state={c:'CONNECTED' for c in ('wide','flow','depth')};l.price_seen={'WIFUSDT'}
  self.assertTrue(capture_readiness(l,T,120000)['ready'])
  l.chain.statuses['bsc']['since']=T-10000;self.assertFalse(capture_readiness(l,T,120000)['ready'])
  l.chain.statuses['bsc']['since']=T-180000;l.price_seen.clear();self.assertFalse(capture_readiness(l,T,120000)['ready'])
 def test_six_arms_complete_replay_and_final_state(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);atomic(p/'selection/UNIVERSE.json',dict(selected=REG));o=Journal(p/'decisions.jsonl');j=Journal(p/'inputs.jsonl');lab=Lab(REG,o.append)
   def row(r,t):j.append(dict(row=r,at_ms=t));lab.ingest(r,t)
   row(dict(kind='metadata',received_ms=T-180000,data=META),T-180000)
   for t in range(T-180000,T+1,1000):
    row(flow(t,1+(T-t)/10000000),t);row(dict(kind='wide',received_ms=t,data=dict(e='24hrMiniTicker',s='WIFUSDT',E=t,c=str(1+(T-t)/10000000))),t)
   row(dict(kind='session',started_ms=T,deadline_ms=T+1000),T);row(book(T,seq=1),T);row(dict(kind='tick'),T);row(book(T+300),T+300)
   row(dict(kind='end_entries'),T+1000);row(dict(kind='finish'),T+1500)
   atomic(p/'FINAL_STATE.json',lab.view(lab.last_ms));j.close();o.close()
   self.assertEqual(len(lab.arms),6);self.assertTrue(lab.arms[1].positions);self.assertTrue(replay(p,save=False)['ok'])

class BookPrice(unittest.TestCase):
 def test_midpoint_is_observation_not_fill_price(self):
  a=warm(source='BOOK_MID');a.history['WIFUSDT'].clear();a.ingest(book(T+1000,.99,1.01,seq=500),T+1000)
  self.assertEqual(a.history['WIFUSDT'][-1],(T+1000,1.0));self.assertEqual(a.price_source,'BOOK_MID')
  self.assertNotEqual(a.history['WIFUSDT'][-1][1],a.quote('WIFUSDT',T+1000,100)['entry']['vwap'])
 def test_stale_crossed_and_repeated_books_do_not_make_price(self):
  a=warm(source='BOOK_MID');before=list(a.history['WIFUSDT'])
  a.ingest(book(T+1000,1.02,1.01,seq=500),T+1000);self.assertEqual(list(a.history['WIFUSDT']),before)
  a.ingest(book(T+1000,.99,1.01,seq=500),T+4000);self.assertEqual(list(a.history['WIFUSDT']),before)
 def test_book_variant_not_overwritten_by_executed_trade_price(self):
  a=warm(source='BOOK_MID');before=list(a.history['WIFUSDT']);a.ingest(flow(T+1000,99),T+1000);self.assertEqual(list(a.history['WIFUSDT']),before)

class SourceGaps(unittest.TestCase):
 def test_source_disconnect_clears_price_history(self):
  for source,channel in [('BOOK_MID','depth'),('AGGTRADE_LAST_EXECUTED','flow')]:
   a=warm(source=source);self.assertTrue(a.history);a.gap(channel,T+1,'DISCONNECTED');self.assertFalse(a.history)
 def test_stability_reset_after_chain_gap(self):
  l=Lab(REG,lambda e:None);a=warm();l.arms[0]=a;l.price_arm=a;l.chain=a.chain;l.price_seen={'WIFUSDT'};a.channel_state={c:'CONNECTED' for c in ('wide','flow','depth')}
  l.chain.ingest(dict(kind='chain_status',chain='bsc',state='GAP'),T);l.chain.ingest(dict(kind='chain_status',chain='bsc',state='HEARTBEAT'),T+1)
  r=capture_readiness(l,T+2,120000);self.assertFalse(r['ready']);self.assertIn('BSC_STABILITY_WARMUP',r['blocked'])

class FeatureCache(unittest.TestCase):
 def test_cached_features_equal_uncached_across_updates_and_gap(self):
  from a6.tails import Tails
  a=warm()
  for t in range(T+1000,T+10001,1000):
   a.ingest(flow(t,1+(t-T)/1000000),t)
   self.assertEqual(a.features('WIFUSDT',t),Tails.features(a,'WIFUSDT',t))
   a.features('WIFUSDT',t)['price']=999
   self.assertEqual(a.features('WIFUSDT',t),Tails.features(a,'WIFUSDT',t))
  a.gap('flow',T+10001,'DISCONNECTED');self.assertIsNone(a.features('WIFUSDT',T+10001))
 def test_cached_features_invalidate_when_flow_changes_at_same_decision_time(self):
  from a6.tails import Tails
  a=warm(source='MINITICKER');old=a.features('WIFUSDT',T+1000)
  a.ingest(flow(T+1000),T+1000);new=a.features('WIFUSDT',T+1000)
  self.assertEqual(new,Tails.features(a,'WIFUSDT',T+1000));self.assertNotEqual(old['flow'],new['flow'])

if __name__=='__main__':unittest.main()
