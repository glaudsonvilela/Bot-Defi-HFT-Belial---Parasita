import json,math,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import common as c,observer as o

class FakeCore:
 def __init__(self):self.w={};self.train_calls=0
 def predict_batch(self,pool,bases,dim,heads):
  out=[]
  for x in bases:
   ps=[]
   for h in range(heads):
    z=self.w.get((pool,h),0.0)*x[0];ps.append(1/(1+math.exp(-z)))
   out.append(ps)
  return out
 def train(self,pool,head,base,dim,label,lr,l2):
  p=self.predict_batch(pool,[base],dim,head+1)[0][head];self.w[(pool,head)]=self.w.get((pool,head),0)+(label-p)*lr*base[0];self.train_calls+=1;return p
class J:
 def __init__(self):self.rows=[]
 def write(self,n,obj,fsync=False):self.rows.append((n,obj,fsync))

def swap(t,p,side='SELL',sig='x'):
 return {'schema':'belial_m2_swap_v1','received_unix_ms':t,'reference_post_price':p,'quote_sol':1.0,'pool_quote_reserve_sol':1000.0,'side':side,'signature':sig,'symbol':'FARTCOIN'}
def ex(t):
 return {'symbol':'FARTCOIN','event_id':'x|w|z','event_unix_ms':t-100,'signal_available_unix_ms':t,'sell_quote_sol':2.5,'cluster_sell_quote_sol_10s':5.5,'cluster_size':2,'cluster_unique_wallets_10s':2,'actor_resolution_latency_ms':100,'impact_score':30,'absorption_score':0,'inventory_exit_pct':100,'observed_inventory_pool_pct':.01,'leader_quality_120s_at_entry':0,'leader_samples_120s_at_entry':0,'pre_return_30s_bps':0,'pre_sell_pressure_30s_pct':50,'regime':'MIXED','observed_entry_price':1.0,'entry_price':1.0}
class T(unittest.TestCase):
 def make(self):j=J();fc=FakeCore();m=o.Model(fc,512,32,j);ob=o.Observer(m,j,'t');return j,fc,m,ob
 def test_prediction_and_short_label(self):
  j,fc,m,ob=self.make();start=1_000_000
  for k in range(70):ob.feed_swap(swap(start-60_000+k*900,1.0,'SELL','x' if k==69 else str(k)),start)
  ob.feed_exit(ex(start),start,7);self.assertEqual(ob.counts['predictions'],1)
  # Two post-start marks and >25bp favorable move inside 10s.
  ob.feed_swap(swap(start+1000,.995),start+1000);ob.feed_swap(swap(start+5000,.990),start+5000);ob.feed_swap(swap(start+9000,.989),start+9000);ob.advance(start+10_000)
  labs=[r for n,r,_ in j.rows if n=='labels' and r.get('horizon_ms')==10000];self.assertEqual(len(labs),3);self.assertTrue(all(r['valid'] for r in labs));self.assertGreaterEqual(fc.train_calls,1);self.assertTrue(any(r['label']==1 for r in labs))
 def test_gap_censors_and_does_not_train(self):
  j,fc,m,ob=self.make();start=2_000_000
  for k in range(5):ob.feed_swap(swap(start-4000+k*800,1.0,'SELL','x' if k==4 else str(k)),start)
  ob.feed_exit(ex(start),start,1);ob.advance(start+60_000);labs=[r for n,r,_ in j.rows if n=='labels'];self.assertTrue(labs);self.assertTrue(all(not r['valid'] for r in labs));self.assertEqual(fc.train_calls,0)
 def test_primary_is_per_head_horizon(self):
  j,fc,m,ob=self.make();base=[.2]*64;p,b,prim=m.predict('FARTCOIN',1000,base);self.assertTrue(all(prim));p,b,prim2=m.predict('FARTCOIN',12000,base);self.assertTrue(prim2[0]);self.assertFalse(prim2[4])
 def test_heads_contract(self):self.assertEqual(len(o.HEADS),32);self.assertEqual(len({h['id'] for h in o.HEADS}),32);self.assertEqual(o.HEADS[29]['id'],'SHORT_50BP_1800S');self.assertEqual(o.HEADS[31]['id'],'ADVERSE_100BP_1800S')

if __name__=='__main__':unittest.main()
