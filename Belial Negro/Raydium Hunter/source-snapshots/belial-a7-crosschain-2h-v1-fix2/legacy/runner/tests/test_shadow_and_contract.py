import importlib,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import shadow_compare as s
class J:
 def __init__(self):self.rows=[]
 def emit(self,k,o):self.rows.append((k,o))
class T(unittest.TestCase):
 def test_six_pools(self):
  self.assertEqual(set(s.POOLS),{'FARTCOIN','BOME','WIF','POPCAT','RAY','JUP'})
 def test_popcat_mark(self):
  j=J();t=s.Tail(j.emit,'x');row={'symbol':'POPCAT','pool':s.POOLS['POPCAT'],'signature':'sig1','slot':1,'received_unix_ms':1000,'log_type':3,'side':'SELL','price_sol_per_meme':.001,'meme_qty':100.0,'quote_sol':.1,'pool_meme_reserve':1_000_000.0,'pool_quote_reserve_sol':1000.0,'reference_post_price':(1000-.1)/(1_000_000+100)}
  t.feed('swap',row,1000);self.assertEqual(t.counts['marks'],1);self.assertEqual(t.counts['invalid_marks'],0)
 def test_shadow_state_is_not_strategy(self):
  j=J();t=s.Tail(j.emit,'x');st=t.state();self.assertTrue(st['counterfactual_shadow_compare']);self.assertTrue(st['not_strategy_pnl']);self.assertEqual(st['shadow_focus_policy'],'TAIL_STOP100_TRAIL75')
if __name__=='__main__':unittest.main()
