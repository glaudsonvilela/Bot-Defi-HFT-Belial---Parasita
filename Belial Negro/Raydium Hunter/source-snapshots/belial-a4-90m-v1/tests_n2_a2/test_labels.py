"""Synthetic clock and prices, not a replayed profitability sample."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'n2_a2/tools'))
import observer as o,common as c
from features import Mark,build_base
from opencl_core import MAX_POOLS,MAX_DIM,MAX_HEADS
class FakeCore:
 def __init__(self):self.calls=[]
 def predict_batch(self,pool,bases,dim,heads):return [[.5]*heads for _ in bases]
 def train(self,pool,head,base,dim,label,lr,l2):self.calls.append((pool,head,label));return .5
class Journal:
 def __init__(self):self.rows=[]
 def write(self,k,r,fsync=False):self.rows.append((k,r))
def swap(t,p=1.,s='FARTCOIN'):
 return dict(symbol=s,received_unix_ms=t,reference_post_price=p,quote_sol=1.,pool_quote_reserve_sol=1000.,side='SELL',signature='fixture',a2_available_ms=t)
def ex(t,s='FARTCOIN'):
 return dict(symbol=s,event_id='fixture|wallet|x',event_unix_ms=t-100,signal_available_unix_ms=t,sell_quote_sol=2.5,cluster_sell_quote_sol_10s=5.5,cluster_size=2,cluster_unique_wallets_10s=2,actor_resolution_latency_ms=100,impact_score=30,absorption_score=0,inventory_exit_pct=100,observed_inventory_pool_pct=.01,leader_quality_120s_at_entry=0,leader_samples_120s_at_entry=0,pre_return_30s_bps=0,pre_sell_pressure_30s_pct=50,regime='MIXED',observed_entry_price=1.,entry_price=1.)
def observation(t,slot,p=1.,s='FARTCOIN'):
 return dict(symbol=s,valid=True,slot=slot,available_ms=t,price=p,not_a_swap=True,quote='WSOL',wallet=None,volume=None)
class Labels(unittest.TestCase):
 def make(self,s='FARTCOIN'):
  j=Journal();core=FakeCore();model=o.Model(core,8192,32,j);ob=o.Observer(model,j,'SYNTHETIC');t=1_000_000
  for n in range(10):ob.feed_swap(swap(t-10000+n*1000,s=s),t)
  ob.feed_exit(ex(t,s),t);self.assertEqual(ob.counts['predictions'],1);return ob,j,core,t
 def fill(self,ob,t,end=1800000,s='FARTCOIN',skip=()):
  for n in range(1,end//10000+1):
   if n in skip:continue
   at=t+n*10000;ob.feed_price_sample(observation(at,n,.99 if n>150 else 1.,s),at)
 def labels(self,j,h):return [r for k,r in j.rows if k=='labels' and r.get('horizon_ms')==h]
 def test_dimensions_8192_32_24(self):self.assertEqual((MAX_DIM,MAX_HEADS,MAX_POOLS),(8192,32,24))
 def test_old_32_heads_unchanged(self):self.assertEqual(o.HEADS,json.loads((ROOT/'legacy/n2_mp12/CONFIG_N2.json').read_text())['heads'])
 def test_weights_schema_new(self):self.assertIn('A2_OBSERVED_PATH',o.CONFIG['label_revision'])
 def test_300s_samples_valid(self):
  ob,j,c,t=self.make();self.fill(ob,t,300000);ob.advance(t+300000);self.assertTrue(all(r['valid'] for r in self.labels(j,300000)))
 def test_600s_samples_valid(self):
  ob,j,c,t=self.make();self.fill(ob,t,600000);ob.advance(t+600000);self.assertTrue(all(r['valid'] for r in self.labels(j,600000)))
 def test_900s_samples_valid(self):
  ob,j,c,t=self.make();self.fill(ob,t,900000);ob.advance(t+900000);self.assertTrue(all(r['valid'] for r in self.labels(j,900000)))
 def test_1800s_delayed_tail_is_positive(self):
  ob,j,c,t=self.make();self.fill(ob,t);ob.advance(t+1800000);rows=self.labels(j,1800000);self.assertEqual(len(rows),3);self.assertTrue(all(r['valid'] for r in rows));self.assertEqual([r['label'] for r in rows if r['direction']=='favorable'],[1,1])
 def test_true_gap_stays_censored(self):
  ob,j,c,t=self.make();self.fill(ob,t,300000,skip=range(10,15));ob.advance(t+300000);self.assertTrue(all(not r['valid'] and r['reason']=='OBSERVATION_GAP_GT_30S' for r in self.labels(j,300000)))
 def test_empty_sample_stream_never_carry_forward(self):
  ob,j,c,t=self.make();ob.advance(t+300000);self.assertTrue(all(not r['valid'] for r in self.labels(j,300000)))
 def test_samples_do_not_become_features(self):
  ob,j,c,t=self.make();before=list(ob.history['FARTCOIN']);self.fill(ob,t);self.assertEqual(before,list(ob.history['FARTCOIN']));self.assertEqual(ob.counts['swaps'],10)
 def test_feature_values_unchanged_by_samples(self):
  ob,j,c,t=self.make();base=build_base(ex(t),list(ob.history['FARTCOIN']),t)[0];ob.feed_price_sample(observation(t,1,.1),t);self.assertEqual(base,build_base(ex(t),list(ob.history['FARTCOIN']),t)[0])
 def test_repeated_slot_not_new_observation(self):
  ob,j,c,t=self.make();self.assertTrue(ob.feed_price_sample(observation(t+10000,1),t+10000));self.assertFalse(ob.feed_price_sample(observation(t+20000,1),t+20000))
 def test_future_timestamp_refused(self):ob,j,c,t=self.make();self.assertFalse(ob.feed_price_sample(observation(t+10000,1),t))
 def test_invalid_sample_refused(self):ob,j,c,t=self.make();r=observation(t+10000,1);r['valid']=False;self.assertFalse(ob.feed_price_sample(r,t+10000))
 def test_missing_not_a_swap_refused(self):ob,j,c,t=self.make();r=observation(t+10000,1);r.pop('not_a_swap');self.assertFalse(ob.feed_price_sample(r,t+10000))
 def test_wrong_quote_refused(self):ob,j,c,t=self.make();r=observation(t+10000,1);r['quote']='USD';self.assertFalse(ob.feed_price_sample(r,t+10000))
 def test_no_training_before_maturity(self):ob,j,c,t=self.make();self.fill(ob,t,30000);self.assertFalse(c.calls)
 def test_incomplete_long_window_not_negative(self):
  ob,j,c,t=self.make();self.fill(ob,t,30000);ob.finalize(t+30000);rows=[r for k,r in j.rows if k=='labels' and r.get('head_id')=='SHORT_50BP_1800S'];self.assertTrue(rows);self.assertIsNone(rows[0]['label'])
 def test_last_expansion_asset_has_own_model(self):ob,j,c,t=self.make('KMNO');self.fill(ob,t,300000,'KMNO');ob.advance(t+300000);self.assertTrue(any(pool==23 for pool,_,_ in c.calls))
 def test_repeated_advance_not_duplicate_updates(self):
  ob,j,c,t=self.make();self.fill(ob,t,300000);ob.advance(t+300000);n=len(c.calls);ob.advance(t+300000);self.assertEqual(len(c.calls),n)
 def test_declares_sampled_not_continuous_target(self):
  ob,j,c,t=self.make();self.fill(ob,t,300000);ob.advance(t+300000);self.assertTrue(all(r['target_is_observed_path_not_continuous'] for r in self.labels(j,300000)))
if __name__=='__main__':unittest.main()
