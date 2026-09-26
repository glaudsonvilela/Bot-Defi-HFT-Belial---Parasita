"""Synthetic account bytes, complete per-pool dependencies in a single RPC context."""
import base64,copy,json,math,sys,unittest
from pathlib import Path
from unittest.mock import patch
from a2.common import ROOT,LEGACY
from a2.price_samples import quote_snapshot,batches,dependencies,rpc_snapshot,MAX_RPC_AGE_MS
from a2.spot_validation import pf,tc
sys.path.insert(0,str(LEGACY/'runner/tests'))
import protocol_fixtures as f
from test_token_capabilities_fix4 import mint22

def mint(dec=6):
 b=bytearray(82);b[44]=dec;b[45]=1;return f.account(pf.TOKEN,b)
def modern(kind='CPMM',meme0=True):
 cfg,account,_=f.pool_config(kind,meme0);row=pf.validate_pool(cfg['symbol'],cfg['pool'],cfg['meme_mint'],account)
 row.update(coin_token_program=pf.TOKEN,pc_token_program=pf.TOKEN)
 accounts={row['pool']:account,row['coin_mint']:mint(row['coin_decimals']),row['pc_mint']:mint(row['pc_decimals'])}
 for side in ('coin','pc'):accounts[row[side+'_vault']]=f.token_vault(row[side+'_mint'],10**12)
 return row,accounts

def amm():
 meme=pf.CANONICAL_MINTS['BONK'];pool=f.key(150);v0=f.key(151);v1=f.key(152);oo=f.key(153);market=f.key(154);program=f.key(155)
 b=bytearray(752);f.put(b,0,1);f.put(b,32,6);f.put(b,40,9)
 for at,key in [(336,v0),(368,v1),(400,meme),(432,pf.WSOL),(496,oo),(528,market),(560,program)]:b[at:at+32]=f.bkey(key)
 f.put(b,192,100*10**6);f.put(b,200,300*10**9)
 account=f.account(pf.AMM,b);row=pf.validate_pool('BONK',pool,meme,account);row.update(coin_token_program=pf.TOKEN,pc_token_program=pf.TOKEN)
 ob=bytearray(3228);ob[:5]=b'serum';ob[-7:]=b'padding';f.put(ob,5,5);ob[13:45]=f.bkey(market);f.put(ob,85,10*10**6);f.put(ob,101,20*10**9)
 return row,{pool:account,meme:mint(6),pf.WSOL:mint(9),v0:f.token_vault(meme,1000*10**6),v1:f.token_vault(pf.WSOL,2000*10**9),oo:f.account(program,ob)}
def change_account(v,at,value,n=8):
 b=bytearray(base64.b64decode(v['data'][0]));f.put(b,at,value,n);v['data'][0]=base64.b64encode(b).decode()
class Snapshots(unittest.TestCase):
 def test_cpmm_subtract_all_fee_reserves(self):
  row,a=modern();state=a[row['pool']]
  for at,amount in ((341,10**6),(357,2*10**6),(397,3*10**6),(349,10**9),(365,2*10**9),(405,3*10**9)):change_account(state,at,amount)
  q=quote_snapshot(row,a);self.assertAlmostEqual(q['price'],(10**12-6*10**9)/(10**12-6*10**6)*.001)
 def test_cpmm_reverse_orientation(self):row,a=modern(meme0=False);self.assertAlmostEqual(quote_snapshot(row,a)['price'],.001)
 def test_clmm_uses_sqrt_not_vault_ratio(self):
  row,a=modern('CLMM');change_account(a[row['pool']],253,2*(1<<64),16);self.assertAlmostEqual(quote_snapshot(row,a)['price'],.004)
 def test_clmm_reverse_orientation(self):row,a=modern('CLMM',False);change_account(a[row['pool']],253,2*(1<<64),16);self.assertAlmostEqual(quote_snapshot(row,a)['price'],.00025)
 def test_amm_openorders_and_pnl(self):row,a=amm();self.assertAlmostEqual(quote_snapshot(row,a)['price'],1720/910)
 def test_amm_not_vault_only(self):row,a=amm();self.assertNotEqual(quote_snapshot(row,a)['price'],2.)
 def test_missing_openorders_refused(self):row,a=amm();a.pop(dependencies(row)[-1]);self.assertRaises((KeyError,ValueError),quote_snapshot,row,a)
 def test_openorders_wrong_market(self):row,a=amm();v=a[dependencies(row)[-1]];change_account(v,13,0,32);self.assertRaises(ValueError,quote_snapshot,row,a)
 def test_openorders_wrong_owner(self):row,a=amm();a[dependencies(row)[-1]]['owner']=f.key(199);self.assertRaises(ValueError,quote_snapshot,row,a)
 def test_amm_dependency_changed(self):row,a=amm();change_account(a[row['pool']],496,1,32);self.assertRaises(ValueError,quote_snapshot,row,a)
 def test_negative_reserve_refused(self):row,a=modern();change_account(a[row['pool']],341,10**13);self.assertRaises(ValueError,quote_snapshot,row,a)
 def test_wrong_mint_owner_refused(self):row,a=modern();a[row['coin_mint']]['owner']=f.key(202);self.assertRaises(RuntimeError,quote_snapshot,row,a)
 def test_transferhook_not_bypassed(self):
  row,a=modern();row['coin_token_program']=tc.TOKEN_2022;a[row['coin_mint']]=mint22(row['coin_mint'],[(14,bytes(64))]);self.assertRaises(RuntimeError,quote_snapshot,row,a)
 def test_price_samples_never_a_swap(self):
  row,a=modern();q=quote_snapshot(row,a);self.assertTrue(q['not_a_swap']);self.assertIsNone(q['wallet']);self.assertIsNone(q['volume'])
 def test_cpmm_status_disabled(self):row,a=modern();change_account(a[row['pool']],329,4,1);self.assertRaises(RuntimeError,quote_snapshot,row,a)
 def test_clmm_disabled(self):row,a=modern('CLMM');change_account(a[row['pool']],389,16,1);self.assertRaises(RuntimeError,quote_snapshot,row,a)
 def test_batches_keep_entire_pool(self):
  row,_=amm();rows={str(i):dict(row,pool=f.key(i+1)) for i in range(24)};result=list(batches(rows,8));self.assertGreater(len(result),1)
  for names,keys in result:
   self.assertLessEqual(len(keys),8)
   for n in names:self.assertTrue(set(dependencies(rows[n]))<=set(keys))
 def test_rpc_slot_regression_refused(self):
  class R:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def read(self,*_):return json.dumps({'id':1,'jsonrpc':'2.0','result':{'context':{'slot':9},'value':[{}]}}).encode()
  with patch('a2.price_samples.urllib.request.urlopen',return_value=R()):self.assertRaises(ValueError,rpc_snapshot,'http://127.0.0.1',['key'],10)
 def test_rpc_same_response_context_returned(self):
  class R:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def read(self,*_):return json.dumps({'id':1,'jsonrpc':'2.0','result':{'context':{'slot':12},'value':[{},{}]}}).encode()
  with patch('a2.price_samples.urllib.request.urlopen',return_value=R()):vals,slot,begin,end=rpc_snapshot('http://127.0.0.1',['x','y'],10)
  self.assertEqual(slot,12);self.assertEqual(len(vals),2)
 def test_slow_rpc_censored_not_backdated(self):
  class R:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def read(self,*_):return json.dumps({'id':1,'jsonrpc':'2.0','result':{'context':{'slot':12},'value':[{}]}}).encode()
  with patch('a2.price_samples.urllib.request.urlopen',return_value=R()),patch('a2.price_samples.now_ms',side_effect=[1000,11001]):self.assertRaisesRegex(ValueError,'LATENCY',rpc_snapshot,'http://127.0.0.1',['x'])
if __name__=='__main__':unittest.main()
