import json,tempfile,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from a7.ui import UI,final_html
from a7.funding import reconcile
from a51.journal import Journal
from a7.chains import Sensors
from a7.run import Inbox
import threading
class OtherTests(unittest.TestCase):
 def test_local_dashboard_http_and_static_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=UI(p);state=dict(phase='FINISHED',updated_ms=0,orders=False,universe=[],lab={});u.update(state)
   try:
    with urllib.request.urlopen(u.url+'state') as r:self.assertEqual(json.load(r),state)
    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(u.url.replace(u.token,'wrong'))
    final_html(p,state);html=(p/'RELATORIO.html').read_text();self.assertNotIn('await fetch',html);self.assertNotIn('setInterval',html)
   finally:u.close()
 def test_funding_sign_short_and_timestamp_boundary(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);j=Journal(p/'decisions.jsonl');position=dict(symbol='XUSDT',opened_ms=1000,closed_ms=4000,qty=2,net_ex_funding=1.,path_censored=False)
   j.append(dict(kind='EXIT_FILLED',arm='CONTROLE',position=position));j.close()
   rows=[dict(symbol='XUSDT',fundingTime=2000,markPrice='100',fundingRate='.001'),dict(symbol='XUSDT',fundingTime=4000,markPrice='100',fundingRate='.1')]
   with patch('a7.funding.fetch',return_value=rows):out=reconcile(p,5000)
   r=next(iter(out.values()));self.assertAlmostEqual(r['funding_cash_usdt'],.2);self.assertAlmostEqual(r['modeled_net_including_funding'],1.2)
 def test_unavailable_funding_not_zero(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);j=Journal(p/'decisions.jsonl');j.append(dict(kind='EXIT_FILLED',arm='CONTROLE',position=dict(symbol='XUSDT',opened_ms=1000,closed_ms=4000,qty=2,net_ex_funding=1.,path_censored=False)));j.close()
   with patch('a7.funding.fetch',side_effect=RuntimeError('HTTP_429')):out=reconcile(p,5000)
   self.assertIsNone(next(iter(out.values()))['modeled_net_including_funding'])
 def test_sampling_does_not_flood_input_queue(self):
  box=Inbox(threading.Event())
  for _ in range(10000):box.put(dict(kind='sampling'))
  self.assertEqual(box.q.qsize(),0);self.assertEqual(box.losses(),(0,10000))
 def test_overflow_is_counted(self):
  box=Inbox(threading.Event())
  for _ in range(4098):box.put(dict(kind='tick'))
  self.assertEqual(box.losses()[0],2)
if __name__=='__main__':unittest.main()
