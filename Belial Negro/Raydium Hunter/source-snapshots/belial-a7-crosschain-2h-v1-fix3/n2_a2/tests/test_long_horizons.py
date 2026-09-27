import sys,unittest,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import observer as o,live
import test_observer as fixtures

class LongHorizons(unittest.TestCase):
 def make(self):
  j=fixtures.J();fc=fixtures.FakeCore();model=o.Model(fc,8192,32,j);return j,fc,o.Observer(model,j,'T3_FIXTURE')
 def start(self,ob,start,symbol='FARTCOIN'):
  for k in range(10):
   r=fixtures.swap(start-9000+k*900,1.0,sig='x' if k==9 else str(k));r['symbol']=symbol;ob.feed_swap(r,start)
  row=fixtures.ex(start);row['symbol']=symbol;ob.feed_exit(row,start,1)
 def test_31_favorable_one_adverse_and_history(self):
  self.assertEqual(sum(h['direction']=='favorable' for h in o.HEADS),31);self.assertEqual(sum(h['direction']=='adverse' for h in o.HEADS),1)
  self.assertEqual(o.UNIQUE_HORIZONS,[10000,30000,60000,120000,300000,600000,900000,1800000]);self.assertGreater(o.HISTORY_MS,1800000+o.MAX_GAP)
 def test_late_drop_negative_at_300_positive_at_1800(self):
  j,fc,ob=self.make();start=10000000;self.start(ob,start)
  for sec in range(10,1801,10):
   ob.feed_swap(fixtures.swap(start+sec*1000,1.0 if sec<1200 else .9935,sig=str(sec)),start+sec*1000);ob.advance(start+sec*1000)
  labels=[r for n,r,_ in j.rows if n=='labels'];short=next(x for x in labels if x['head_id']=='SHORT_50BP_300S');long=next(x for x in labels if x['head_id']=='SHORT_50BP_1800S')
  self.assertEqual(short['label'],0);self.assertEqual(long['label'],1);self.assertTrue(long['valid']);self.assertTrue(long['trained'])
 def test_late_sample_is_censored_not_negative(self):
  j,fc,ob=self.make();start=10000000;self.start(ob,start)
  for sec in range(10,601,10):
   ob.feed_swap(fixtures.swap(start+sec*1000,.999,sig=str(sec)),start+sec*1000);ob.advance(start+sec*1000)
  ob.finalize(start+600000)
  labels=[r for n,r,_ in j.rows if n=='labels' and r.get('head_id')=='SHORT_50BP_1800S'];self.assertEqual(len(labels),1);self.assertIsNone(labels[0]['label']);self.assertFalse(labels[0]['trained'])
 def test_every_new_pool_can_forecast(self):
  j,fc,ob=self.make()
  for symbol in o.c.POOLS:self.start(ob,10000000,symbol)
  self.assertEqual(ob.counts['predictions'],24);self.assertEqual({r['symbol'] for n,r,_ in j.rows if n=='predictions'},set(o.c.POOLS))
 def test_backlog_reader_not_caught_up_until_drained(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'a';p.write_text('{}\n{}\n{}\n');r=live.TailReader(p,False);self.assertFalse(r.caught_up());r.read(1);self.assertFalse(r.caught_up());r.read(10);self.assertTrue(r.caught_up());r.close()
 def test_live_advances_only_after_backlog_drained(self):
  src=(ROOT/'tools/live.py').read_text();self.assertIn('if caught_up:obs.advance',src);self.assertNotIn('now=c.now_ms();obs.advance(now)',src)
 def test_final_status_preserved_for_only_session_runner(self):
  src=(ROOT/'tools/live.py').read_text();self.assertIn('if only_evidence is not None and str(only_evidence.resolve()) in blocked:',src)
if __name__=='__main__':unittest.main()
