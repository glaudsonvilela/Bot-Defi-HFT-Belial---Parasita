import math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import features as f

class T(unittest.TestCase):
 def rows(self):
  out=[]
  for i in range(80):
   out.append(f.Mark(1_000_000+i*900,1e-5*(1-i*0.0001),.1+(i%5)*.02,1000.0,'SELL' if i%3 else 'BUY',f's{i}'))
  return out
 def exit(self):
  return {'event_id':'s79|wallet|x','event_unix_ms':1_070_000,'sell_quote_sol':3.0,'cluster_sell_quote_sol_10s':7.0,'cluster_size':3,'cluster_unique_wallets_10s':2,'actor_resolution_latency_ms':120,'impact_score':44,'absorption_score':12,'inventory_exit_pct':80,'observed_inventory_pool_pct':.02,'leader_quality_120s_at_entry':.3,'leader_samples_120s_at_entry':8,'pre_return_30s_bps':-12,'pre_sell_pressure_30s_pct':65,'regime':'MIXED','observed_entry_price':9.9e-6,'entry_price':1e-5}
 def test_dimension_and_bounds(self):
  x,d=f.build_base(self.exit(),self.rows(),1_071_200);self.assertEqual(len(x),64);self.assertTrue(all(-1<=v<=1 and math.isfinite(v) for v in x));self.assertGreater(d['reference_price'],0)
 def test_expand_deterministic(self):
  x,_=f.build_base(self.exit(),self.rows(),1_071_200);a=f.expand_cpu(x,512);b=f.expand_cpu(x,512);self.assertEqual(a,b);self.assertEqual(a[0],1);self.assertEqual(a[1:65],x);self.assertTrue(all(-1<=v<=1 for v in a))
 def test_old_reference_rejected(self):
  with self.assertRaisesRegex(ValueError,'REFERENCE_TOO_OLD'):f.build_base(self.exit(),self.rows()[:2],2_000_000)
 def test_base_names(self):self.assertEqual(len(f.BASE_NAMES),64);self.assertEqual(len(set(f.BASE_NAMES)),64)

if __name__=='__main__':unittest.main()
