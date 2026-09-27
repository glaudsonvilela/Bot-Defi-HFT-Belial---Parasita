import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from a7.readiness import snapshot,check_bnb_capabilities
from a7.net import RPC,RpcError
from a7.chains import Sensors
from a7.lab import Lab
from tests_a7.test_lab import REG,warm_arm
from tests_a6.test_core import T,book
class ReadyTests(unittest.TestCase):
 def lab(self):
  lab=Lab(REG,lambda e:None);a=warm_arm();lab.arms[0]=a;a.channel_state={k:'CONNECTED' for k in ('wide','depth','flow')};lab.chain.statuses={k:dict(state='CONNECTED',last=T,since=T-20000) for k in ('solana','bsc')};return lab
 def test_all_requirements_are_observed(self):self.assertTrue(snapshot(self.lab(),T)['ready'])
 def test_bnb_failure_names_exact_blocker(self):
  l=self.lab();l.chain.statuses['bsc'].update(state='GAP',reason='RPC_eth_getLogs_LIMIT_OR_METHOD_RESTRICTION_-32005');r=snapshot(l,T)
  self.assertFalse(r['ready']);self.assertIn('RPC_eth_getLogs',r['blocked'][0]);self.assertEqual(r['fresh_books'],1)
 def test_missing_and_stale_books_separate(self):
  l=self.lab();self.assertEqual(snapshot(l,T+2001)['stale_books'],['WIFUSDT']);l.arms[0].books.clear();self.assertEqual(snapshot(l,T)['missing_books'],['WIFUSDT'])
 def test_stale_connected_chain_is_not_ready(self):
  l=self.lab();l.chain.statuses['bsc']['last']=T-21000;r=snapshot(l,T);self.assertFalse(r['ready']);self.assertFalse(r['chains']['bsc']['healthy'])
 def test_transport_connecting_is_not_ready(self):
  l=self.lab();l.arms[0].channel_state['flow']='CONNECTING';self.assertFalse(snapshot(l,T)['ready'])
 def test_open_pnl_disappears_when_book_is_stale(self):
  a=warm_arm();a.tick(T,['WIFUSDT']);a.ingest(book(T+300),T+300)
  self.assertIsNotNone(a.view(T+300,['WIFUSDT'])['positions'][0]['pnl_live_net_ex_funding'])
  row=a.view(T+3001,['WIFUSDT'])['positions'][0];self.assertIsNone(row['pnl_live_net_ex_funding']);self.assertEqual(row['pnl_quote_status'],'STALE_OR_MISSING_BOOK')
 def test_pnl_view_does_not_mutate_simulation(self):
  a=warm_arm();a.tick(T,['WIFUSDT']);a.ingest(book(T+300),T+300);before=copy.deepcopy(a.positions);a.view(T+300,['WIFUSDT']);self.assertEqual(a.positions,before)

class BnbTests(unittest.TestCase):
 def test_getlogs_required_in_preflight_even_if_empty(self):
  r=Mock();r.call.side_effect=['0x38',{'number':'0x1','hash':'0xabc'},[]]
  with tempfile.TemporaryDirectory() as d:
   self.assertTrue(check_bnb_capabilities(r,Path(d))['passed']);self.assertEqual(r.call.call_args_list[-1].args[0],'eth_getLogs')
   self.assertTrue(json.loads((Path(d)/'NETWORK_CHECK_BNB.json').read_text())['passed'])
 def test_disabled_method_preserves_failure_proof(self):
  r=Mock();r.call.side_effect=['0x38',{'number':'0x1','hash':'0xabc'},RpcError('eth_getLogs',-32005,'method disabled')]
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(RpcError):check_bnb_capabilities(r,Path(d))
   proof=json.loads((Path(d)/'NETWORK_CHECK_BNB.json').read_text());self.assertFalse(proof['passed']);self.assertIn('METHOD_UNAVAILABLE',proof['error'])
 def test_rpc_error_keeps_method_but_not_provider_secret(self):
  with patch('a7.net.fetch',return_value={'error':{'code':-32005,'message':'disabled https://host/?key=SECRET'}}):
   with self.assertRaises(RpcError) as c:RPC('https://example.com').call('eth_getLogs',[])
   self.assertIn('eth_getLogs',str(c.exception));self.assertNotIn('SECRET',str(c.exception));self.assertNotIn('https',str(c.exception))
 def worker(self,fail=False):
  stop=threading.Event();events=[];sequence=[]
  class Fake:
   def call(self,method,args):
    sequence.append(method)
    if method=='eth_getBlockByNumber':return dict(number='0x5',hash='0xhead')
    if method=='eth_getLogs':
     stop.set()
     if fail:raise RpcError(method,-32005,'method disabled')
     return []
  def emit(e):sequence.append('STATUS_'+e['state']);events.append(e)
  Sensors([dict(chain='bsc',pool='0xpool')],None,Fake(),None,emit,stop).bnb_worker()
  return events,sequence
 def test_connected_only_after_successful_log_poll(self):
  events,seq=self.worker();self.assertLess(seq.index('eth_getLogs'),seq.index('STATUS_CONNECTED'))
 def test_disabled_logs_never_reports_connected(self):
  events,seq=self.worker(True);self.assertNotIn('STATUS_CONNECTED',seq);self.assertIn('STATUS_FAILED',seq)
if __name__=='__main__':unittest.main()
