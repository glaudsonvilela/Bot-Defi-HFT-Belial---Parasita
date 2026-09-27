import io,json,tempfile,threading,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from a8.net import RPC,Denied,fetch,audit_writer,HttpError,RpcError
from a8.chains import Sensors
class Network(unittest.TestCase):
 def test_denial_method_and_code_persist_without_endpoint_credentials(self):
  events=[];rpc=RPC('https://provider/?key=SECRET',audit=events.append,chain='bsc')
  with patch('a8.net.fetch',side_effect=Denied(403)) as f:
   with self.assertRaises(Denied) as e:rpc.call('eth_getLogs',[dict(address=['x','y'],fromBlock='0x1',toBlock='0x3')])
   self.assertIn('403_eth_getLogs',str(e.exception));self.assertTrue(rpc.disabled)
   with self.assertRaises(Denied):rpc.call('eth_chainId')
   self.assertEqual(f.call_count,1)
  self.assertEqual(events[0]['filter_address_count'],2);self.assertEqual(events[0]['block_span'],3);self.assertNotIn('SECRET',json.dumps(events));self.assertNotIn('https',json.dumps(events))
 def test_http_deny_classification(self):
  for code in (401,403,451):
   with patch('a8.net.open_request') as p:
    p.side_effect=urllib.error.HTTPError('https://x',code,'secret',{},io.BytesIO())
    with self.assertRaises(Denied) as e:fetch('https://x')
    self.assertEqual(e.exception.http_status,code);self.assertNotIn('secret',str(e.exception))
 def test_rate_limit_respects_retry_after_and_keeps_http_code(self):
  with patch('a8.net.open_request') as p:
   p.side_effect=urllib.error.HTTPError('https://x',429,'secret',{'Retry-After':'30'},io.BytesIO())
   with self.assertRaises(HttpError) as e:fetch('https://x')
   self.assertEqual(e.exception.retry_after,30)
 def test_rpc_error_is_redacted(self):
  events=[]
  with patch('a8.net.fetch',return_value={'error':{'code':-32005,'message':'disabled https://x?key=SECRET'}}):
   with self.assertRaises(RpcError):RPC('https://x',audit=events.append).call('eth_getLogs',[])
  self.assertNotIn('SECRET',json.dumps(events));self.assertEqual(events[0]['rpc_code'],-32005)
 def test_worker_preserves_denial_details_and_stops(self):
  stop=threading.Event();out=[]
  class R:
   def call(self,*args):raise Denied(403,'eth_getBlockByNumber')
  Sensors([dict(chain='bsc',pool='pool')],None,R(),None,out.append,stop).bnb_worker()
  self.assertEqual(len(out),1);self.assertEqual(out[0]['state'],'FAILED');self.assertIn('403_eth_getBlockByNumber',out[0]['reason'])
 def test_no_transaction_submission_method(self):
  with self.assertRaises(ValueError):RPC('https://x').call('eth_sendRawTransaction',[])

class ConnectionReuse(unittest.TestCase):
 def test_keepalive_is_used_without_configured_proxy(self):
  from a8.net import open_request
  import urllib.request
  with patch('a8.net.urllib.request.getproxies',return_value={}),patch('a8.net._DIRECT_POOL') as pool:
   open_request(urllib.request.Request('https://public.invalid'),12);self.assertEqual(pool.open.call_count,1)
 def test_proxy_is_honored_without_direct_fallback(self):
  from a8.net import open_request
  import urllib.request
  with patch('a8.net.urllib.request.getproxies',return_value={'https':'http://configured.invalid'}),patch('a8.net._DIRECT_POOL') as pool,patch('a8.net.urllib.request.build_opener') as opener:
   opener.return_value.open.side_effect=urllib.error.URLError('blocked')
   with self.assertRaises(urllib.error.URLError):open_request(urllib.request.Request('https://public.invalid'),12)
   self.assertEqual(pool.open.call_count,0);self.assertEqual(opener.return_value.open.call_count,1)

if __name__=='__main__':unittest.main()
