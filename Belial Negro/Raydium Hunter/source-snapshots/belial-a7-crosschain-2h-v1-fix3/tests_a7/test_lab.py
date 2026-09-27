import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from a7.lab import ChainSignals,Lab,Arm,POLICY
from a7.chains import token_delta,solana_events,bnb_events,V2,V3,TRANSFER
from a7.selection import book_metrics,pool_candidates,candidates_ranked
from a7.net import RPC,Denied
from a51.common import atomic
from a51.journal import Journal
from a7.replay import replay
from tests_a6.test_core import engine,META,T,book
REG=[dict(symbol='WIFUSDT',base='WIF',multiplier=1,sources=[dict(chain='solana',pool='pool',mint='mint',quote='quote',program='program')])]
def trade(tx='tx1',at=T,**kw):return dict(kind='chain_trade',chain='solana',symbol='WIFUSDT',pool='pool',mint='mint',wallet='wallet',tx=tx,available_ms=at,chain_time_ms=at-1000,quantity=300.,qualified_sell=True,**kw)
def ready_chain():
 c=ChainSignals(REG);c.ingest(dict(kind='chain_status',chain='solana',state='CONNECTED'),T-20000);c.ingest(dict(kind='chain_status',chain='solana',state='HEARTBEAT'),T);return c

def warm_arm(entry=False,exit=False):
 chain=ready_chain();a=Arm('TEST',chain,lambda e:None,entry,exit);old,_=engine()
 for k in ('catalog','metadata_ms','history','flows','books','start','deadline'):setattr(a,k,copy.deepcopy(getattr(old,k)))
 return a
class CausalTests(unittest.TestCase):
 def test_single_sale_insufficient(self):
  c=ready_chain();c.ingest(trade(),T);self.assertEqual(c.signal('WIFUSDT',T,1),[])
 def test_recurrent_verified_sale_qualifies(self):
  c=ready_chain();c.ingest(trade(),T);c.ingest(trade('tx2',T+1),T+1);self.assertEqual(c.signal('WIFUSDT',T+1,1)[0]['count'],2)
 def test_duplicate_tx_never_recurrence(self):
  c=ready_chain();c.ingest(trade(),T);c.ingest(trade(),T+1);self.assertEqual(c.signal('WIFUSDT',T+1,1),[])
 def test_unavailable_future_rejected(self):
  c=ready_chain();c.ingest(trade('x',T+1),T);self.assertFalse(c.history)
 def test_old_chain_tx_rejected(self):
  c=ready_chain();e=trade();e['chain_time_ms']=T-16000;c.ingest(e,T);self.assertFalse(c.history)
 def test_unknown_time_rejected(self):
  c=ready_chain();e=trade();e['chain_time_ms']=None;c.ingest(e,T);self.assertFalse(c.history)
 def test_unknown_mapping_rejected(self):
  c=ready_chain();e=trade();e['pool']='spoof';c.ingest(e,T);self.assertFalse(c.history)
 def test_missing_net_evidence_rejected(self):
  c=ready_chain();e=trade();e['qualified_sell']=False;c.ingest(e,T);self.assertFalse(c.history)
 def test_contract_multiplier(self):
  c=ready_chain();c.registry['WIFUSDT']['multiplier']=1000;c.ingest(trade(),T);c.ingest(trade('tx2'),T);self.assertFalse(c.signal('WIFUSDT',T,1));c.registry['WIFUSDT']['multiplier']=1
 def test_gap_requires_new_observations(self):
  c=ready_chain();c.ingest(trade(),T);c.ingest(trade('tx2'),T)
  c.ingest(dict(kind='chain_status',chain='solana',state='GAP'),T+1);self.assertFalse(c.history)
  c.ingest(dict(kind='chain_status',chain='solana',state='HEARTBEAT'),T+2);self.assertEqual(c.statuses['solana']['since'],T+2);self.assertFalse(c.signal('WIFUSDT',T+2,1))
 def test_failed_provider_not_revived_by_heartbeat(self):
  c=ready_chain();c.ingest(dict(kind='chain_status',chain='solana',state='FAILED'),T);c.ingest(dict(kind='chain_status',chain='solana',state='HEARTBEAT'),T+1);self.assertEqual(c.statuses['solana']['state'],'FAILED')
 def test_entry_needs_both_chain_and_binance(self):
  a=warm_arm(True);self.assertEqual(a.reason('WIFUSDT',T)[0],'NO_FRESH_RECURRENT_NET_SELL')
  a.chain.ingest(trade(),T);a.chain.ingest(trade('tx2'),T);self.assertIsNone(a.reason('WIFUSDT',T)[0])
  a.flows['WIFUSDT'].clear();self.assertNotEqual(a.reason('WIFUSDT',T)[0],None)
 def test_entry_revalidates_after_latency(self):
  a=warm_arm(True);a.chain.ingest(trade(),T);a.chain.ingest(trade('tx2'),T);a.tick(T,['WIFUSDT']);self.assertTrue(a.pending)
  a.chain.ingest(dict(kind='chain_status',chain='solana',state='GAP'),T+100);a.ingest(book(T+300),T+300);self.assertFalse(a.positions);self.assertFalse(a.pending)
 def test_risk_budget_notional_with_costs(self):
  a=warm_arm();q=a.quote('WIFUSDT',T);self.assertLessEqual(q['entry']['quote']*(75+10+3)/10000,1.001)
 def test_new_exit_persists_and_needs_later_fill(self):
  a=warm_arm(exit=True);a.tick(T,['WIFUSDT']);a.ingest(book(T+300),T+300);self.assertTrue(a.positions)
  for t in range(T+31000,T+38001,1000):
   a.flows['WIFUSDT'].append((t,200.,10.,3));a.ingest(book(t,1.002,1.0022,seq=t),t)
  self.assertIsNone(a.positions['WIFUSDT']['exit_request'])
  for t in range(T+39000,T+43001,1000):
   a.flows['WIFUSDT'].append((t,200.,10.,3));a.ingest(book(t,1.002,1.0022,seq=t),t)
  self.assertEqual(a.positions['WIFUSDT']['exit_reason'],'THESIS_FAILED_BUY_ABSORPTION')
  a.ingest(book(T+43300,1.002,1.0022,seq=T+43300),T+43300);self.assertTrue(a.closed)
 def test_control_without_chain_can_signal(self):
  a=warm_arm();self.assertIsNone(a.reason('WIFUSDT',T)[0])
 def test_ordered_time_enforced(self):
  l=Lab(REG,lambda e:None);l.ingest(dict(kind='tick'),T)
  with self.assertRaises(ValueError):l.ingest(dict(kind='tick'),T-1)

class ChainDecoders(unittest.TestCase):
 def balances(self,pre=1000,post=500):
  def row(i,m,a):return dict(accountIndex=i,owner='wallet',mint=m,uiTokenAmount=dict(amount=str(a),decimals=0))
  return dict(preTokenBalances=[row(1,'mint',pre),row(2,'quote',0)],postTokenBalances=[row(1,'mint',post),row(2,'quote',500)],err=None,logMessages=['Program program invoke [1]'])
 def sol(self):return dict(meta=self.balances(),slot=1,blockTime=T//1000,transaction=dict(signatures=['sig'],message=dict(accountKeys=['wallet','pool','program'],header=dict(numRequiredSignatures=1))))
 def test_net_zero_not_sell(self):
  t=self.sol();t['meta']=self.balances(1000,1000);self.assertFalse(solana_events(t,'sig',REG[0]['sources'][0]|{'symbol':'WIFUSDT'},T,T)[0]['qualified_sell'])
 def test_real_balance_reduction_and_quote_increase(self):
  self.assertTrue(solana_events(self.sol(),'sig',REG[0]['sources'][0]|{'symbol':'WIFUSDT'},T,T)[0]['qualified_sell'])
 def test_missing_pre_balance_is_unknown(self):
  m=self.balances();m['preTokenBalances']=[];self.assertIsNone(token_delta(m,'wallet','mint'))
 def test_closed_account_is_unknown(self):
  m=self.balances();m['postTokenBalances']=[];self.assertIsNone(token_delta(m,'wallet','mint'))
 def test_signature_mismatch_fails(self):
  with self.assertRaises(ValueError):solana_events(self.sol(),'spoof',REG[0]['sources'][0],T,T)
 def evm(self,v3=False,roundtrip=False):
  wallet='0x'+'1'*40;pool='0x'+'2'*40;mint='0x'+'3'*40;quote='0x'+'4'*40
  enc=lambda vals:'0x'+''.join(format(v%(1<<256),'064x') for v in vals)
  log=dict(address=pool,transactionHash='0xabc',blockHash='0xblock',blockNumber='0x1',logIndex='0x1',topics=[V3 if v3 else V2,'0x'+wallet[2:].zfill(64),'0x'+pool[2:].zfill(64)],data=enc([500,-2,1,1,1,1,1] if v3 else [500,0,0,2]))
  transfer=dict(address=mint,topics=[TRANSFER,'0x'+wallet[2:].zfill(64),'0x'+pool[2:].zfill(64)],data=enc([500]))
  receipt=dict(transactionHash='0xabc',blockHash='0xblock',status='0x1',logs=[log,transfer])
  if roundtrip:receipt['logs'].append(dict(transfer,topics=[TRANSFER,transfer['topics'][2],transfer['topics'][1]]))
  p=dict(chain='bsc',symbol='WIFUSDT',pool=pool,mint=mint,quote=quote,token0=mint,token1=quote,decimals=0,protocol='PANCAKE_V3' if v3 else 'PANCAKE_V2')
  return log,receipt,dict(hash='0xabc',**{'from':wallet}),p,T,T
 def test_pancake_v2_sell(self):self.assertTrue(bnb_events(*self.evm())[0]['qualified_sell'])
 def test_pancake_v3_seven_fields_sell(self):self.assertTrue(bnb_events(*self.evm(True))[0]['qualified_sell'])
 def test_evm_net_zero_not_sell(self):self.assertFalse(bnb_events(*self.evm(roundtrip=True))[0]['qualified_sell'])
 def test_five_word_uniswap_abi_not_pancake(self):
  args=self.evm(True);args[0]['data']=args[0]['data'][:-128]
  with self.assertRaises(ValueError):bnb_events(*args)
 def test_removed_event_rejected(self):
  args=self.evm();args[0]['removed']=True;self.assertEqual(bnb_events(*args),[])
 def test_wrong_receipt_rejected(self):
  args=self.evm();args[1]['transactionHash']='bad';self.assertEqual(bnb_events(*args),[])

class ReplayAndNet(unittest.TestCase):
 def test_complete_replay_with_entry_and_censored_position(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);atomic(p/'selection/UNIVERSE.json',dict(selected=REG));o=Journal(p/'decisions.jsonl');j=Journal(p/'inputs.jsonl');lab=Lab(REG,o.append)
   def row(r,t):j.append(dict(row=r,at_ms=t));lab.ingest(r,t)
   row(dict(kind='session',started_ms=T-180000,deadline_ms=T+10000),T-180000)
   row(dict(kind='metadata',received_ms=T-180000,data=META),T-180000)
   for t in range(T-180000,T+1,1000):
    row(dict(kind='wide',received_ms=t,data=dict(e='24hrMiniTicker',s='WIFUSDT',E=t,c=str(1+(T-t)/10000000))),t)
    row(dict(kind='flow',received_ms=t,symbol='WIFUSDT',event_ms=t,buy=10,sell=200,trades=3),t)
   row(book(T,seq=1),T);row(dict(kind='tick'),T);row(book(T+300),T+300);row(dict(kind='finish'),T+500)
   atomic(p/'FINAL_STATE.json',lab.view(lab.last_ms));j.close();o.close();self.assertTrue(lab.arms[0].positions);self.assertTrue(replay(p)['ok'])
   with (p/'inputs.jsonl').open('ab') as f:f.write(b'broken\n')
   with self.assertRaises((ValueError,KeyError)):replay(p)
 def test_rpc_rejects_write_methods(self):
  with self.assertRaises(ValueError):RPC('https://example.com').call('eth_sendRawTransaction',['deadbeef'])
 def test_denial_disables_provider(self):
  r=RPC('https://example.com')
  with patch('a7.net.fetch',side_effect=Denied('HTTP_ACCESS_DENIED_403')) as f:
   with self.assertRaises(Denied):r.call('eth_chainId')
   with self.assertRaises(Denied):r.call('eth_chainId')
   self.assertEqual(f.call_count,1)
 def test_bad_book_and_real_walk(self):
  with self.assertRaises(ValueError):book_metrics(dict(bids=[['1','100']],asks=[['.9','100']]))
  b=book_metrics(dict(bids=[['.9999','1000']],asks=[['1.0001','1000']]))
  self.assertAlmostEqual(b['spread_bps'],2);self.assertGreater(b['roundtrip_100usdt_bps'],15)
 def test_pool_symbol_does_not_substitute_address(self):
  row=dict(chainId='solana',dexId='raydium',pairAddress='pool',baseToken=dict(address='spoof'),quoteToken=dict(address='So11111111111111111111111111111111111111112'),liquidity=dict(usd=1e6))
  self.assertEqual(pool_candidates([row],'solana','mint'),[])
if __name__=='__main__':unittest.main()
