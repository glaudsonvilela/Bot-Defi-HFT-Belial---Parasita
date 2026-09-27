import base64,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import preflight_pools as p

def decode(s):
 n=0
 for x in s:n=n*58+p.ALPH.index(x)
 return b'\0'*(len(s)-len(s.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8,'big')
def account(reverse=False):
 sym,pool,mint=p.ORIGINAL[0];raw=bytearray(752);raw[32:40]=(9 if reverse else 6).to_bytes(8,'little');raw[40:48]=(6 if reverse else 9).to_bytes(8,'little');raw[400:432]=decode(p.WSOL if reverse else mint);raw[432:464]=decode(mint if reverse else p.WSOL)
 return sym,pool,mint,{'owner':p.AMM,'data':[base64.b64encode(raw).decode(),'base64'],'executable':False}
class PreflightTests(unittest.TestCase):
 def test_12_symbols_unique(self):self.assertEqual(len(set(p.SYMBOLS)),12)
 def test_b58_preserves_zero_bytes(self):self.assertEqual(p.b58(bytes(32)),'1'*32)
 def test_token_sol_valid(self):self.assertEqual(p.validate_pool(*account())['pc_mint'],p.WSOL)
 def test_sol_token_orientation_valid(self):self.assertEqual(p.validate_pool(*account(True))['coin_mint'],p.WSOL)
 def test_bad_owner_rejected(self):args=list(account());args[3]['owner']='CPMM';self.assertRaisesRegex(RuntimeError,'OWNER',p.validate_pool,*args)
 def test_bad_layout_rejected(self):args=list(account());args[3]['data'][0]=base64.b64encode(bytes(753)).decode();self.assertRaisesRegex(RuntimeError,'LAYOUT',p.validate_pool,*args)
 def test_bad_pair_rejected(self):args=list(account());args[2]='Unknown';self.assertRaisesRegex(RuntimeError,'PAIR',p.validate_pool,*args)
 def test_pinned_identity_independent_of_registry(self):self.assertEqual(set(p.canonical_mints()),set(p.SYMBOLS[6:]))
 def test_exact_bonk_address(self):self.assertEqual(p.canonical_mints()['BONK'],'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263')
 def test_cpmm_does_not_become_legacy(self):self.assertRaisesRegex(RuntimeError,'NO_COMPATIBLE',p.choose_legacy,{'success':True,'data':{'data':[{'id':'x','programId':'CPMM','mintA':{'address':'mint'},'mintB':{'address':p.WSOL}}]}},'mint')
 def test_select_highest_compatible_liquidity(self):
  rows=[{'id':str(n),'tvl':n,'programId':p.AMM,'mintA':{'address':'mint'},'mintB':{'address':p.WSOL}} for n in [1,3,2]]
  self.assertEqual(p.choose_legacy({'success':True,'data':{'data':rows}},'mint')['id'],'3')
if __name__=='__main__':unittest.main()
