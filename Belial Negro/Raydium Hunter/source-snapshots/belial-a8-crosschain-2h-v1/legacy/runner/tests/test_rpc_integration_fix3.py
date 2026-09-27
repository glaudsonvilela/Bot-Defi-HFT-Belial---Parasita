"""End-to-end wiring against synthetic accounts; protocol IO fixtures, not market."""
import contextlib,hashlib,io,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'runner/tools'))
import rpc_transport as t
import rpc_selection as s
import rpc_ws_probe as w
import preflight_pools as p
import install_and_run as installer
import test_preflight_fix1 as f
from test_rpc_fix3 import Response, Clock, SocketStub, envelope, http_error, good_ws, server_frame

class FullPreflightIntegration(unittest.TestCase):
 def test_403_on_actual_12_account_batch_uses_second_provider_without_rediscovery(self):
  targets,accounts,metadata=f.fixture();calls=[];clients=[];clock=Clock()
  def factory(url,log,**_):
   def opener(req,timeout):
    body=json.loads(req.data);method=body['method'];calls.append((url,method))
    if method=='getMultipleAccounts' and url=='https://api.mainnet.solana.com':raise http_error(403)
    if method=='getGenesisHash':result=t.MAINNET_GENESIS
    elif method=='getMultipleAccounts':result={'context':{'slot':1000},'value':[accounts[k] for k in body['params'][0]]}
    elif method=='getAccountInfo':result={'context':{'slot':1000},'value':accounts[body['params'][0]]}
    elif method=='getSignaturesForAddress':result=[{'signature':'fixture','slot':1000,'err':None}]
    elif method=='getTransaction':result={'slot':1000,'meta':{'err':None,'logMessages':[]},'transaction':{'signatures':['fixture']}}
    else:raise AssertionError(method)
    return Response({'jsonrpc':'2.0','id':body['id'],'result':result})
   client=t.Client(url,log,open_url=opener,clock=clock,sleep=clock.sleep);clients.append(client);return client
  # Force all validation code through the actual transport, mocked only at network IO.
  def selector(*args,**kwargs):return s.select(*args,client_factory=factory,ws_probe=good_ws,**kwargs)
  with tempfile.TemporaryDirectory() as td,patch.object(p,'http',side_effect=metadata) as meta,patch.object(p,'select_rpc',side_effect=selector):
   report=p.check_auto(td)
   self.assertTrue(report['ok']);self.assertEqual(len(report['pools']),12)
   self.assertEqual(meta.call_count,6) # Metadata was not repeated on fallback.
   self.assertEqual(report['rpc'],'https://solana-rpc.publicnode.com')
   self.assertEqual(report['ws'],'wss://solana-rpc.publicnode.com')
   self.assertEqual([m for url,m in calls if url=='https://api.mainnet.solana.com'],['getGenesisHash','getMultipleAccounts'])
   self.assertEqual(sum(m=='getMultipleAccounts' for _,m in calls),3)
   self.assertEqual(s.connection_for_session(report),(report['rpc'],report['ws']))
   diag=json.loads((Path(td)/'RESOLUTION_SUMMARY.json').read_text());self.assertEqual(diag['onchain_verified_pools'],12)
   rejected=list((Path(td)/'rpc_fix3/candidate_1').glob('*.jsonl'));self.assertEqual(len(rejected),1)
   self.assertIn('403',rejected[0].read_text());self.assertNotIn('METADATA_HTTP_FAILED',rejected[0].read_text())
 def test_streamed_child_log_and_exit_status(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()) as output:
   path=Path(td)/'stream.log';installer.run([sys.executable,'-c','print("RPC_TEST_PROGRESS")'],path,timeout=5,stream=True)
   self.assertIn('RPC_TEST_PROGRESS',output.getvalue());self.assertEqual(path.read_text().strip(),'RPC_TEST_PROGRESS')
 def test_streamed_child_failure_is_not_reported_as_success(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
   path=Path(td)/'stream.log'
   self.assertRaisesRegex(RuntimeError,'COMMAND_FAILED',installer.run,[sys.executable,'-c','print("RPC_FAIL");raise SystemExit(3)'],path,None,5,True)
   self.assertIn('RPC_FAIL',path.read_text())
 def test_log_stream_redacts_user_key_without_editing_original_log(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()) as output:
   path=Path(td)/'stream.log';url='https://custom.invalid/?api-key=PRIVATE'
   installer.run([sys.executable,'-c',f'print({url!r})'],path,{'BELIAL_SOLANA_RPC_URL':url},5,True)
   self.assertNotIn('PRIVATE',output.getvalue());self.assertIn('PRIVATE',path.read_text())

class UpgradeSocket(SocketStub):
 def __init__(self,status=101,bad_accept=False):super().__init__();self.status=status;self.bad_accept=bad_accept
 def sendall(self,data):
  super().sendall(data)
  if data.startswith(b'GET '):
   key=next(l.split(b':',1)[1].strip() for l in data.split(b'\r\n') if l.startswith(b'Sec-WebSocket-Key:'))
   import base64
   accept=base64.b64encode(hashlib.sha1(key+w.GUID.encode()).digest()).decode() if not self.bad_accept else 'wrong'
   self.data=(f'HTTP/1.1 {self.status} Status\r\nUpgrade: websocket\r\nConnection: keep-alive, Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n').encode()+server_frame(1,'{"jsonrpc":"2.0","id":1,"result":99}')

class UpgradeTests(unittest.TestCase):
 def connect(self,sock):
  context=Mock();context.wrap_socket.return_value=sock
  conn=w.Connection('wss://fixture.invalid',time.monotonic()+5,socket_factory=lambda *a,**kw:sock,context_factory=lambda:context)
  return conn,context
 def test_tls_server_hostname_and_upgrade_accept_validated(self):
  sock=UpgradeSocket();c,context=self.connect(sock)
  with patch.object(w.urllib.request,'getproxies',return_value={}):
   with c as session:
    self.assertEqual(session.recv()['result'],99)
   self.assertTrue(sock.closed);context.wrap_socket.assert_called_once_with(sock,server_hostname='fixture.invalid')
 def test_ws_http403_is_not_an_empty_book_or_success(self):
  sock=UpgradeSocket(status=403);c,_=self.connect(sock)
  with patch.object(w.urllib.request,'getproxies',return_value={}):
   with self.assertRaises(t.RpcFailure) as e:c.__enter__()
  self.assertEqual(e.exception.status,403);self.assertTrue(sock.closed);self.assertEqual(len(sock.sent),1)
 def test_invalid_websocket_accept_closes_socket(self):
  sock=UpgradeSocket(bad_accept=True);c,_=self.connect(sock)
  with patch.object(w.urllib.request,'getproxies',return_value={}):self.assertRaisesRegex(t.RpcFailure,'HANDSHAKE_INVALID',c.__enter__)
  self.assertTrue(sock.closed)

if __name__=='__main__':unittest.main()
