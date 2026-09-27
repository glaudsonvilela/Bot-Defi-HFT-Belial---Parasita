"""Offline regression + real local HTTP fixture. No market calls/real orders."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import socket
import struct
import sys
import tempfile
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'runner/tools'))
import rpc_transport as t
import rpc_selection as s
import rpc_ws_probe as w
import preflight_pools as p
import run90m
import qualify_protocols as q
import test_preflight_fix1 as f

class Clock:
 def __init__(self):self.now=100.;self.waits=[]
 def __call__(self):return self.now
 def sleep(self,n):self.waits.append(n);self.now+=n

class Response(io.BytesIO):
 status=200
 def __init__(self,value):super().__init__(value if isinstance(value,bytes) else json.dumps(value).encode())

def envelope(result):return {'jsonrpc':'2.0','id':1,'result':result}
def http_error(code,headers=None):
 return urllib.error.HTTPError('https://fixture.invalid/private-key',code,'remote',headers or {},io.BytesIO(b'fixture denial'))

def transport(side_effect,log=None):
 clock=Clock();op=Mock(side_effect=side_effect)
 return t.Client('https://fixture.invalid',log,open_url=op,clock=clock,sleep=clock.sleep),op,clock

class TransportTests(unittest.TestCase):
 def test_public_evidence_urls_stay_byte_identical(self):
  value='{"metadata":"https://api-v3.raydium.io/pools/info/list-v2?mint1=token","rpc":"https://api.mainnet.solana.com"}'
  self.assertEqual(t.redact_text(value,['https://api.mainnet.solana.com','wss://api.mainnet.solana.com']),value)
 def test_boolean_id_does_not_impersonate_integer_id(self):
  c,_,_=transport([Response({'jsonrpc':'2.0','id':True,'result':9})]);self.assertRaises(t.RpcFailure,c.call,'getSlot')

 def test_success_valid_envelope(self):
  c,op,_=transport([Response(envelope(3))]);self.assertEqual(c.call('getSlot'),3);self.assertEqual(op.call_count,1)
 def test_403_is_not_retried(self):
  c,op,clock=transport([http_error(403)])
  with self.assertRaises(t.RpcFailure) as e:c.call('getMultipleAccounts',[])
  self.assertEqual(e.exception.status,403);self.assertEqual(e.exception.kind,'RPC_HTTP_FORBIDDEN')
  self.assertEqual(op.call_count,1);self.assertEqual(clock.waits,[])
 def test_403_circuits_off_followup_requests_to_same_provider(self):
  c,op,_=transport([http_error(403)])
  for method in ('getGenesisHash','getSlot'):
   self.assertRaises(t.RpcFailure,c.call,method)
  self.assertEqual(op.call_count,1)
 def test_401_does_not_retry_or_guess_credentials(self):
  c,op,_=transport([http_error(401)]);self.assertRaises(t.RpcFailure,c.call,'getSlot');self.assertEqual(op.call_count,1)
 def test_429_honors_retry_after(self):
  c,op,clock=transport([http_error(429,{'Retry-After':'7'}),Response(envelope(42))])
  self.assertEqual(c.call('getSlot'),42);self.assertIn(7.,clock.waits);self.assertEqual(op.call_count,2)
 def test_429_long_retry_after_aborts_without_hot_retry(self):
  c,op,clock=transport([http_error(429,{'Retry-After':'120'})])
  self.assertRaises(t.RpcFailure,c.call,'getSlot');self.assertEqual(op.call_count,1);self.assertEqual(clock.waits,[])
 def test_http_date_retry_after(self):
  self.assertEqual(t.retry_after_seconds('Thu, 01 Jan 1970 00:02:00 GMT',100),20)
 def test_invalid_retry_after_is_bounded(self):
  for value in ('garbage','nan','inf',None):self.assertEqual(t.retry_after_seconds(value),1.)
 def test_network_retries_are_bounded(self):
  c,op,clock=transport([urllib.error.URLError('temporary')]*3)
  self.assertRaises(t.RpcFailure,c.call,'getSlot');self.assertEqual(op.call_count,3);self.assertIn(1.,clock.waits);self.assertIn(2.,clock.waits)
 def test_5xx_retries_then_success(self):
  c,op,_=transport([http_error(503),Response(envelope(7))]);self.assertEqual(c.call('getSlot'),7);self.assertEqual(op.call_count,2)
 def test_json_rpc_error_is_not_success(self):
  c,op,_=transport([Response({'jsonrpc':'2.0','id':1,'error':{'code':-32601,'message':'method unavailable'}})])
  with self.assertRaises(t.RpcFailure) as e:c.call('getSlot')
  self.assertEqual(e.exception.code,-32601);self.assertEqual(op.call_count,1)
 def test_non_json_html_rejected(self):
  c,op,_=transport([Response(b'<html>blocked</html>')]);self.assertRaisesRegex(t.RpcFailure,'NON_JSON',c.call,'getSlot');self.assertEqual(op.call_count,1)
 def test_nonfinite_json_rejected(self):
  c,_,_=transport([Response(b'{"jsonrpc":"2.0","id":1,"result":NaN}')]);self.assertRaises(t.RpcFailure,c.call,'getSlot')
 def test_missing_result_rejected(self):
  c,_,_=transport([Response({'jsonrpc':'2.0','id':1})]);self.assertRaisesRegex(t.RpcFailure,'RESULT_MISSING',c.call,'getSlot')
 def test_response_id_mismatch_rejected(self):
  c,_,_=transport([Response({'jsonrpc':'2.0','id':999,'result':7})]);self.assertRaisesRegex(t.RpcFailure,'ENVELOPE',c.call,'getSlot')
 def test_empty_array_envelope_rejected(self):
  c,_,_=transport([Response([])]);self.assertRaisesRegex(t.RpcFailure,'ENVELOPE',c.call,'getSlot')
 def test_no_transaction_submission_method(self):
  c,op,_=transport([])
  for method in ('sendTransaction','requestAirdrop','sendBundle','simulateTransaction'):
   self.assertRaisesRegex(t.RpcFailure,'READ_ONLY',c.call,method)
  op.assert_not_called()
 def test_deadline_prevents_request(self):
  c,op,clock=transport([]);c.deadline=clock.now-1
  self.assertRaisesRegex(t.RpcFailure,'DEADLINE',c.call,'getSlot');op.assert_not_called()
 def test_rate_spacing_applied_between_methods(self):
  c,_,clock=transport([Response(envelope(2)),Response({'jsonrpc':'2.0','id':2,'result':3})])
  c.call('getSlot');c.call('getSlot');self.assertAlmostEqual(sum(clock.waits),.36)
 def test_redirect_is_not_followed(self):
  self.assertIsNone(t.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid'))
  c,op,_=transport([http_error(302)]);self.assertRaises(t.RpcFailure,c.call,'getSlot');self.assertEqual(op.call_count,1)
 def test_diagnostics_include_method_and_status_without_error_body(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'rpc.jsonl';c,_,_=transport([http_error(403)],path)
   self.assertRaises(t.RpcFailure,c.call,'getMultipleAccounts',[])
   row=json.loads(path.read_text());self.assertEqual(row['method'],'getMultipleAccounts');self.assertEqual(row['http_status'],403)
   self.assertNotIn('fixture denial',path.read_text());self.assertFalse(row['error_body_recorded'])
 def test_url_credentials_redacted(self):
  for url in ('https://host.invalid/?api-key=SECRET','https://host.invalid/path/SECRET','wss://host.invalid/SECRET'):
   text=t.redact_text('RPC='+url,[url]);self.assertNotIn('SECRET',text);self.assertIn('host.invalid',text)
 def test_auth_and_insecure_urls_rejected(self):
  for url in ('http://host.invalid','https://u:p@host.invalid','https://host.invalid/#fragment','https://host.invalid/\r\nX:1','file:///tmp/data'):
   self.assertRaises(ValueError,t.validate_url,url,'https')
 def test_default_profile_order_official_public_before_old_endpoint(self):
  got=t.profiles();self.assertEqual(len(got),2);self.assertEqual(got[0]['rpc'],'https://api.mainnet.solana.com')
  self.assertEqual(got[1]['rpc'],'https://solana-rpc.publicnode.com')
 def test_explicit_rpc_never_silently_falls_back(self):
  got=t.profiles('https://user.invalid/?api-key=SECRET','wss://user.invalid/?api-key=SECRET');self.assertEqual(len(got),1)
 def test_custom_ws_required_if_not_known(self):
  self.assertRaisesRegex(ValueError,'CUSTOM_RPC',t.profiles,'https://user.invalid')
 def test_known_explicit_rpc_derives_only_documented_ws(self):
  self.assertEqual(t.profiles('https://api.mainnet.solana.com')[0]['ws'],'wss://api.mainnet.solana.com')
 def test_custom_ws_without_rpc_refused(self):self.assertRaises(ValueError,t.profiles,None,'wss://user.invalid')
 def test_redacted_archive_preserves_local_evidence(self):
  with tempfile.TemporaryDirectory() as td:
   td=Path(td);p=td/'source.json';url='https://user.invalid/?api-key=SECRET';p.write_text(json.dumps({'rpc':url}))
   with zipfile.ZipFile(td/'out.zip','w') as z:t.archive_file(z,p,'source.json',[url])
   with zipfile.ZipFile(td/'out.zip') as z:self.assertNotIn(b'SECRET',z.read('source.json'))
   self.assertIn('SECRET',p.read_text())
 def test_private_connection_json_mode_600(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'state.json';t.save_json(path,{'value':1});self.assertEqual(path.stat().st_mode&0o777,0o600)

class FakeClient:
 def __init__(self,url,*_,**__):self.url=url;self.calls=[]
 def call(self,method,params=None):
  self.calls.append((method,params))
  if method=='getGenesisHash':return t.MAINNET_GENESIS
  if method=='getAccountInfo':return {'value':{'owner':p.AMM}}
  if method=='getSignaturesForAddress':return [{'err':None,'signature':'fixture-signature','slot':100}]
  if method=='getTransaction':return {'slot':100,'meta':{'err':None,'logMessages':[]},'transaction':{'signatures':['fixture-signature']}}
  raise AssertionError(method)

def fake_validate(url,folder,client):
 return {'ok':True,'pools':[{'pool':'pool'+str(i),'owner':p.AMM} for i in range(12)]}

def good_ws(*_):return {'ok':True,'subscriptions_acknowledged':12,'subscriptions_cancelled':12}
TARGETS=[('S'+str(i),'pool'+str(i),'mint'+str(i)) for i in range(12)]

class SelectionTests(unittest.TestCase):
 def select(self,td,**kwargs):
  return s.select(TARGETS,kwargs.pop('validate',fake_validate),td,client_factory=kwargs.pop('client',FakeClient),ws_probe=kwargs.pop('ws_probe',good_ws),**kwargs)
 def test_success_selected_pair_frozen_into_manifest(self):
  with tempfile.TemporaryDirectory() as td:
   m=self.select(td);self.assertEqual(s.connection_for_session(m),('https://api.mainnet.solana.com','wss://api.mainnet.solana.com'))
   report=json.loads((Path(td)/'RPC_SELECTION.json').read_text());self.assertTrue(report['ok']);self.assertFalse(report['scan_started']);self.assertFalse(report['orders'])
 def test_first_provider_403_second_can_pass(self):
  clients=[]
  class DenyFirst(FakeClient):
   def call(self,method,params=None):
    if self.url=='https://api.mainnet.solana.com':raise t.RpcFailure('RPC_HTTP_FORBIDDEN',self.url,method,status=403)
    return super().call(method,params)
  with tempfile.TemporaryDirectory() as td:
   m=self.select(td,client=DenyFirst);self.assertEqual(m['rpc'],'https://solana-rpc.publicnode.com')
   report=json.loads((Path(td)/'RPC_SELECTION.json').read_text());self.assertEqual(len(report['attempts']),2);self.assertIn('403',report['attempts'][0]['error'])
 def test_both_forbidden_abort_not_success(self):
  class Deny(FakeClient):
   def call(self,method,params=None):raise t.RpcFailure('RPC_HTTP_FORBIDDEN',self.url,method,status=403)
  with tempfile.TemporaryDirectory() as td:
   self.assertRaisesRegex(RuntimeError,'ALL_CANDIDATES',self.select,td,client=Deny)
   report=json.loads((Path(td)/'RPC_SELECTION.json').read_text());self.assertFalse(report['ok']);self.assertEqual(len(report['attempts']),2)
 def test_real_account_validation_error_does_not_switch_provider(self):
  check=Mock(side_effect=RuntimeError('PUMP:PAIR_IDENTITY_MISMATCH'))
  with tempfile.TemporaryDirectory() as td:
   self.assertRaisesRegex(RuntimeError,'IDENTITY_MISMATCH',self.select,td,validate=check);self.assertEqual(check.call_count,1)
 def test_wrong_genesis_not_approved(self):
  class Devnet(FakeClient):
   def call(self,method,params=None):return 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG'
  with tempfile.TemporaryDirectory() as td:self.assertRaisesRegex(RuntimeError,'ALL_CANDIDATES',self.select,td,client=Devnet)
 def test_ws_rejection_prevents_selection(self):
  ws=Mock(side_effect=t.RpcFailure('WS_HTTP_REJECTED','wss://fixture.invalid','logsSubscribe',status=403))
  with tempfile.TemporaryDirectory() as td:self.assertRaisesRegex(RuntimeError,'ALL_CANDIDATES',self.select,td,ws_probe=ws)
 def test_11_ws_acks_cannot_approve_12_pools(self):
  with tempfile.TemporaryDirectory() as td:self.assertRaises(RuntimeError,self.select,td,ws_probe=lambda *_:{'ok':True,'subscriptions_acknowledged':11,'subscriptions_cancelled':11})
 def test_11_pools_cannot_pass(self):
  with tempfile.TemporaryDirectory() as td:self.assertRaises(RuntimeError,s.select,TARGETS[:11],fake_validate,td)
 def test_changed_rpc_is_rejected_by_runner(self):
  with tempfile.TemporaryDirectory() as td:
   m=self.select(td);m['rpc']='https://different.invalid';self.assertRaisesRegex(RuntimeError,'CHANGED',s.connection_for_session,m)
 def test_changed_ws_is_rejected_by_runner(self):
  with tempfile.TemporaryDirectory() as td:
   m=self.select(td);m['ws']='wss://different.invalid';self.assertRaisesRegex(RuntimeError,'CHANGED',s.connection_for_session,m)
 def test_missing_manifest_connection_has_no_implicit_default(self):
  self.assertRaises((RuntimeError,ValueError),s.connection_for_session,{'ok':True})
 def test_runner_passes_exact_selected_endpoints_and_duration(self):
  with tempfile.TemporaryDirectory() as td:
   m=self.select(td,rpc='https://user.invalid/private',ws='wss://user.invalid/private')
   cmd=run90m.collector_command(Path(td),m)
   self.assertEqual(cmd[cmd.index('--rpc')+1],m['rpc']);self.assertEqual(cmd[cmd.index('--ws')+1],m['ws'])
   self.assertEqual(cmd[cmd.index('--minutes')+1],'90');self.assertNotIn('https://solana-rpc.publicnode.com',cmd)
 def test_null_history_is_not_treated_as_good_connection(self):
  c=FakeClient('https://fixture.invalid');base=c.call
  c.call=lambda m,p=None:None if m=='getTransaction' else base(m,p)
  self.assertRaisesRegex(t.RpcFailure,'HISTORY_NOT_AVAILABLE',s.probe_history,c,'pool')
 def test_history_probe_uses_supported_transaction_version(self):
  c=FakeClient('https://fixture.invalid');s.probe_history(c,'pool')
  self.assertEqual(c.calls[1][1][1]['maxSupportedTransactionVersion'],0)
 def test_preflight_discovers_metadata_only_once(self):
  targets,accounts,fake_http=f.fixture()
  with tempfile.TemporaryDirectory() as td,patch.object(p,'http',side_effect=fake_http),patch.object(p,'select_rpc',return_value={'ok':True,'rpc_connection':{'profile':'fixture'},'slot':1,'mint_slot':1}) as select:
   p.check_auto(td);self.assertEqual(select.call_count,1);self.assertEqual(len(select.call_args.args[0]),12)
 def test_explicit_failed_endpoint_does_not_contact_defaults(self):
  calls=[]
  class Deny(FakeClient):
   def call(self,method,params=None):calls.append(self.url);raise t.RpcFailure('RPC_HTTP_UNAUTHORIZED',self.url,method,status=401)
  with tempfile.TemporaryDirectory() as td:
   self.assertRaises(RuntimeError,self.select,td,client=Deny,rpc='https://explicit.invalid',ws='wss://explicit.invalid')
   self.assertEqual(calls,['https://explicit.invalid'])
 def test_qualification_rpc_403_stops_instead_of_trying_12_signatures(self):
  import protocol_fixtures as pf
  case,_,_=pf.clmm();manifest={'ok':True,'rpc':'https://fixture.invalid','pools':[case['cfg']]+[{'owner':'legacy'}]*11};calls=[]
  def deny(url,method,params):
   calls.append(method)
   if method=='getSignaturesForAddress':return [{'signature':case['signature'],'slot':case['slot'],'err':None}]*12
   raise t.RpcFailure('RPC_HTTP_FORBIDDEN',url,method,status=403)
  with tempfile.TemporaryDirectory() as td,patch.object(q.time,'sleep'),patch.object(q.subprocess,'run') as binary:
   self.assertRaises(t.RpcFailure,q.qualify,manifest,'fixture',Path(td)/'out',deny)
   self.assertEqual(calls,['getSignaturesForAddress','getTransaction']);binary.assert_not_called()

class SocketStub:
 def __init__(self,data=b''):self.data=data;self.sent=[];self.closed=False
 def settimeout(self,*_):pass
 def recv(self,n):part,self.data=self.data[:n],self.data[n:];return part
 def sendall(self,data):self.sent.append(data)
 def close(self):self.closed=True

def server_frame(op,data,fin=True):
 if isinstance(data,str):data=data.encode()
 n=len(data);h=bytes(((0x80 if fin else 0)|op,n if n<126 else 126))
 if n>=126:h+=struct.pack('!H',n)
 return h+data

class WSTests(unittest.TestCase):
 def conn(self,data):
  c=w.Connection('wss://fixture.invalid',time.monotonic()+5);c.sock=SocketStub(data);return c
 def test_client_frames_are_masked_and_roundtrip_payload(self):
  payload=b'hello';frame=w.masked_frame(1,payload);self.assertEqual(frame[1]&0x80,0x80)
  mask=frame[2:6];self.assertEqual(bytes(x^mask[i%4] for i,x in enumerate(frame[6:])),payload)
 def test_extended_payload_length(self):
  data=b'x'*300;frame=w.masked_frame(1,data);self.assertEqual(frame[1]&127,126);self.assertEqual(struct.unpack('!H',frame[2:4])[0],300)
 def test_fragmented_json_with_interleaved_ping(self):
  c=self.conn(server_frame(1,'{"value":',False)+server_frame(9,'hi')+server_frame(0,'1}',True))
  self.assertEqual(c.recv(),{'value':1});self.assertEqual(c.sock.sent[0][0]&15,10)
 def test_masked_server_frame_rejected(self):self.assertRaisesRegex(t.RpcFailure,'UNSUPPORTED_FRAME',self.conn(w.masked_frame(1,'{}')).recv)
 def test_orphan_continuation_rejected(self):self.assertRaises(t.RpcFailure,self.conn(server_frame(0,'{}')).recv)
 def test_close_frame_rejected(self):self.assertRaisesRegex(t.RpcFailure,'REMOTE_CLOSE',self.conn(server_frame(8,b'')).recv)
 def test_binary_frame_rejected(self):self.assertRaisesRegex(t.RpcFailure,'OPCODE',self.conn(server_frame(2,'{}')).recv)
 def test_nonfinite_json_rejected(self):self.assertRaises(t.RpcFailure,self.conn(server_frame(1,'{"n":NaN}')).recv)
 def test_bad_control_frame_rejected(self):self.assertRaises(t.RpcFailure,self.conn(server_frame(9,'x',False)).recv)
 def test_oversized_frame_rejected_before_allocation(self):
  self.assertRaisesRegex(t.RpcFailure,'TOO_LARGE',self.conn(b'\x81\x7f'+struct.pack('!Q',1<<35)).recv)
 def test_probe_sends_one_address_per_subscription_and_unsubscribes_all(self):
  class FakeWS:
   instance=None
   def __init__(self,*_):self.sent=[];self.row=None;FakeWS.instance=self
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def send(self,row):self.row=row;self.sent.append(row)
   def recv(self):return {'jsonrpc':'2.0','id':self.row['id'],'result':self.row['id'] if self.row['method']=='logsSubscribe' else True}
  report=w.probe('wss://fixture.invalid',[x[1] for x in TARGETS],connection_factory=FakeWS)
  self.assertTrue(report['ok']);self.assertEqual(len(FakeWS.instance.sent),24)
  for row in FakeWS.instance.sent[:12]:self.assertEqual(len(row['params'][0]['mentions']),1)
  self.assertFalse(report['data_used_for_training'])
 def test_probe_bad_ack_is_failure(self):
  class Bad:
   def __init__(self,*_):pass
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def send(self,*_):pass
   def recv(self):return {'jsonrpc':'2.0','id':1,'result':True}
  self.assertRaisesRegex(t.RpcFailure,'SUBSCRIPTION',w.probe,'wss://fixture.invalid',[x[1] for x in TARGETS],connection_factory=Bad)
 def test_proxy_is_not_silently_bypassed(self):
  with patch.object(w.urllib.request,'getproxies',return_value={'https':'http://proxy.invalid'}),patch.object(w.urllib.request,'proxy_bypass',return_value=False):
   c=w.Connection('wss://fixture.invalid',time.monotonic()+3);self.assertRaisesRegex(t.RpcFailure,'PROXY',c.__enter__)

if __name__=='__main__':unittest.main()
