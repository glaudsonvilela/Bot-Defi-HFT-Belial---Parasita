import json,tempfile,threading,time,unittest,urllib.request,urllib.error
from pathlib import Path
from a1.rpc_relay import make_relay,ALLOWED
from a1.common import ROOT,atomic,read_json,JsonTail
from a1.rpc_relay import RpcFailure

class Fake:
 calls=[];error=None
 def __init__(self,*args,**kwargs):pass
 def call(self,m,p):
  type(self).calls.append((m,p))
  if self.error:raise self.error
  return {'fixture':True}

class RelayTests(unittest.TestCase):
 def setUp(self):
  Fake.calls=[];Fake.error=None;self.temp=tempfile.TemporaryDirectory();self.server,self.budget,self.url=make_relay('https://fixture.invalid',Path(self.temp.name)/'log',token='test-token',client_factory=Fake)
  threading.Thread(target=self.server.serve_forever,daemon=True).start()
 def tearDown(self):self.server.shutdown();self.server.server_close();self.temp.cleanup()
 def request(self,method='getAccountInfo',url=None,host=None):
  req=urllib.request.Request(url or self.url,data=json.dumps(dict(jsonrpc='2.0',id=44,method=method,params=[])).encode(),headers={'Content-Type':'application/json'})
  if host:req.add_header('Host',host)
  try:
   with urllib.request.urlopen(req,timeout=3) as f:return f.status,json.load(f)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def test_read_only_method(self):status,body=self.request();self.assertEqual(status,200);self.assertEqual(body['id'],44);self.assertTrue(body['result']['fixture'])
 def test_sendtransaction_refused(self):self.assertEqual(self.request('sendTransaction')[0],403);self.assertFalse(Fake.calls)
 def test_unknown_method_refused(self):self.assertEqual(self.request('requestAirdrop')[0],403)
 def test_host_refused(self):self.assertEqual(self.request(host='evil.example')[0],403)
 def test_path_token_required(self):self.assertEqual(self.request(url=self.url+'wrong')[0],403)
 def test_queue_capacity_explicit(self):
  for _ in range(16):self.assertTrue(self.budget.slots.acquire(False))
  self.assertEqual(self.request()[0],503);self.assertFalse(Fake.calls)
 def test_access_denied_stops_other_sensors_retrying(self):
  Fake.error=RpcFailure('RPC_HTTP_FORBIDDEN','https://fixture.invalid','getAccountInfo',status=403)
  self.assertEqual(self.request()[0],503);self.assertTrue(self.budget.blocked);self.assertEqual(self.request()[0],503);self.assertEqual(len(Fake.calls),1)
 def test_shared_budget_paces_source_launches(self):
  before=time.monotonic();self.request();self.request();self.request();self.assertGreaterEqual(time.monotonic()-before,.6);self.assertEqual(self.budget.calls,3)
 def test_read_allowlist_contains_no_mutation(self):self.assertEqual(ALLOWED,{'getAccountInfo','getMultipleAccounts','getTransaction','getSignaturesForAddress','getSlot','getGenesisHash'})

class FileConsumer(unittest.TestCase):
 def test_partial_line_not_committed(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'input';p.write_bytes(b'{"a":1}');r=JsonTail(p);self.assertEqual(r.read(),[])
   with p.open('ab') as f:f.write(b'\n')
   self.assertEqual(r.read(),[(0,{'a':1})]);r.close()
 def test_truncation_not_silent_replay(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'input';p.write_text('{"a":1}\n');r=JsonTail(p);r.read();p.write_text('');self.assertRaisesRegex(RuntimeError,'TRUNCATED',r.read);r.close()
 def test_symlink_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'link';p.symlink_to(Path(td)/'target');self.assertRaisesRegex(RuntimeError,'SYMLINK',atomic,p,{'a':1})
 def test_ui_contains_per_source_states_and_not_profit_claim(self):
  h=(ROOT/'a1/web/dashboard.html').read_text();self.assertIn('a1-qualified',h);self.assertIn('a1-books',h);self.assertIn('não PnL da estratégia principal',h);self.assertIn("stream.addEventListener('platform'",h)
if __name__=='__main__':unittest.main()
