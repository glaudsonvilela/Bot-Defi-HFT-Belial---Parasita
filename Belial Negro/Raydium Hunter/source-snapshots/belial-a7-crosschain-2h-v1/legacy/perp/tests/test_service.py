import argparse,gzip,http.client,json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import serve
from test_books_paper import meta

class ServiceTests(unittest.TestCase):
 def setUp(self):serve.STOP.clear()
 def tearDown(self):serve.STOP.set()
 def test_journal_roundtrip_and_compressed_raw(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);j=serve.Journal(root)
   for i in range(50):j.write('orders',{'i':i});j.write('raw_ws',{'i':i})
   j.close();self.assertEqual(len((root/'orders.jsonl').read_text().splitlines()),50)
   with gzip.open(root/'raw_ws.jsonl.gz','rt') as f:self.assertEqual(len(f.readlines()),50)
 def test_tail_partial_record_then_finish(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x';p.write_text('{"i":1');t=serve.Tail(p);self.assertEqual(t.read(),[])
   with p.open('a') as f:f.write('}\n')
   self.assertEqual(t.read(),[{'i':1}]);t.close()
 def test_tail_truncation_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x';p.write_text('{"i":1}\n');t=serve.Tail(p);t.read();p.write_text('')
   self.assertRaisesRegex(RuntimeError,'TRUNCATED',t.read);t.close()
 def test_http_loopback_get_only(self):
  bus=serve.Bus();server=serve.make_server(bus,ROOT/'web/dashboard.html')
  try:
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2);c.request('GET','/health');r=c.getresponse();self.assertEqual(r.status,200);self.assertFalse(json.loads(r.read())['orders'])
   c.request('POST','/orders',body='{}');r=c.getresponse();self.assertEqual(r.status,501);r.read();c.close()
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2);c.request('GET','/health',headers={'Host':'attacker.invalid'});r=c.getresponse();self.assertEqual(r.status,403);r.read();c.close()
  finally:server.shutdown();server.server_close()
 def test_sse_preserves_event_type_and_json(self):
  bus=serve.Bus();server=serve.make_server(bus,ROOT/'web/dashboard.html');c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
  try:
   bus.publish('books',{'fixture':True});c.request('GET','/events');r=c.getresponse();self.assertEqual(r.status,200)
   lines=[r.readline().decode().strip() for _ in range(4)];self.assertIn('event: books',lines);self.assertTrue(any('"fixture":true' in s for s in lines));r.close();c.close()
  finally:serve.STOP.set();server.shutdown();server.server_close()
 def test_service_end_to_end_fixture_no_exchange_connection(self):
  """Real subprocess + pipe + parsing + PAPER + audit + final HTML; synthetic data only."""
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);ev=root/'evidence';ev.mkdir();n2=root/'n2';n2.mkdir();(ev/'shadow_compare').mkdir()
   mp=root/'markets.json';mp.write_text(json.dumps({'ok':True,'markets':[meta()],'coverage':{'FARTCOIN':{'hyperliquid':True,'lighter':False}}}))
   config=ROOT.parent/'n2_mp12/CONFIG_N2.json';fake=root/'fixture_feed.py'
   fake.write_text('''#!/usr/bin/env python3
import json,sys,time,threading
stop=threading.Event()
def control():
 for line in sys.stdin:
  if json.loads(line).get("stop"):stop.set();return
threading.Thread(target=control,daemon=True).start()
while not stop.is_set():
 t=time.time_ns();ms=t//1000000
 print(json.dumps({"venue":"hyperliquid","generation":1,"received_ns":t,"writer_ns":t,"data":{"channel":"l2Book","data":{"coin":"FARTCOIN","time":ms,"levels":[[{"px":"100","sz":"1000"}],[{"px":"100.01","sz":"1000"}]]}}}),flush=True)
 time.sleep(.02)
''');fake.chmod(0o700)
   def make_signal():
    limit=time.monotonic()+3
    while not (ev/'perp_t3/raw_ws.jsonl.gz').exists() and time.monotonic()<limit:time.sleep(.02)
    time.sleep(.05);t=serve.now();(ev/'shadow_compare/events.jsonl').write_text(json.dumps({'kind':'EPISODE_OPEN','episode_id':'fixture-only','symbol':'FARTCOIN','at_unix_ms':t,'paper_only':True,'orders':False})+'\n')
   thread=threading.Thread(target=make_signal);thread.start()
   a=argparse.Namespace(evidence=ev,markets=mp,binary=fake,n2_runtime=n2,n2_config=config,deadline_ms=serve.now()+2200,duration_ms=5400000)
   with patch.object(serve.markets,'http',return_value=[]):self.assertEqual(serve.run(a),0)
   thread.join();status=json.loads((ev/'perp_t3/SERVICE_COMPLETE.json').read_text());state=json.loads((ev/'perp_t3/state.json').read_text())
   self.assertEqual(state['duration_ms'],5400000);self.assertEqual(state['deadline_ms']-state['started_ms'],5400000);self.assertTrue(status['ok']);self.assertGreater(status['frames_received'],10);self.assertTrue(state['complete']);self.assertTrue(state['positions'],json.dumps({'transport':status,'counts':state['counts'],'gate':state['gate'],'recent':state['recent']}));self.assertFalse(status['orders'])
   html=(ev/'perp_t3/DASHBOARD_FINAL.html').read_text();self.assertIn('window.BELIAL_FINAL_STATE=',html);self.assertIn('fixture-only',html)
   # Session end censors open positions rather than inventing a final market fill.
   self.assertTrue(all(p['status']=='OPEN' for p in state['positions']))

if __name__=='__main__':unittest.main()
