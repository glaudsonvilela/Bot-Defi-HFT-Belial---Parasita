import sys,unittest,tempfile,json,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from tail import Tail,POLICIES,POOLS
from m1_contract import scenario,Mark

def swap(t,p=1.0,s='FARTCOIN',side='SELL',sig=None):
    x=1000.;q=1.;qty=1.;y=p*(x+qty)+q if side=='SELL' else p*(x-qty)-q
    return {'symbol':s,'pool':POOLS[s],'signature':sig or f'sig-{t}-{side}', 'slot':t,
      'received_unix_ms':t,'side':side,'log_type':3,'direction':2 if side=='SELL' else 1,
      'price_sol_per_meme':q/qty,'meme_qty':qty,'quote_sol':q,'pool_meme_reserve':x,'pool_quote_reserve_sol':y}
def ex(t,s='FARTCOIN',wallet='W',q=1.1):
    return {'symbol':s,'wallet':wallet,'event_id':f'sig-{t}-SELL|{wallet}|{t}',
     'event_unix_ms':t,'signal_available_unix_ms':t+100,'sell_quote_sol':q,'absorption_score':0.0}

class TailTest(unittest.TestCase):
    def setUp(self):self.rows=[];self.t=Tail(lambda kind,row:self.rows.append((kind,row)),'test')
    def setup_arm(self,third=False,symbol='FARTCOIN'):
        for i in ([1000,2000,3000] if third else [1000,2000]):
            self.t.feed('swap',swap(i,s=symbol),i);self.t.feed('exit',ex(i,s=symbol),i+100)
        self.t.feed('swap',swap(6900,.99,s=symbol),6900)
    def opened(self):self.setup_arm();self.t.advance(7000);self.t.advance(8000);self.assertEqual(len(self.t.positions),3)
    def test_no_entry_before_confirmation(self):self.setup_arm();self.t.advance(6999);self.assertEqual(len(self.t.positions),0)
    def test_no_entry_before_latency(self):self.setup_arm();self.t.advance(7000);self.t.advance(7999);self.assertEqual(len(self.t.positions),0)
    def test_entry_three_alternatives(self):self.opened();self.assertEqual(self.t.counts['episodes'],1)
    def test_support_does_not_double_positions(self):
        self.setup_arm(True);self.t.advance(7000);self.t.advance(8000)
        self.assertEqual(len(self.t.positions),3);self.assertEqual(self.t.counts['support'],1)
    def test_bome_not_opened(self):self.setup_arm(symbol='BOME');self.t.advance(8000);self.t.advance(9000);self.assertEqual(len(self.t.positions),0)
    def test_wif_not_opened(self):self.setup_arm(symbol='WIF');self.t.advance(8000);self.t.advance(9000);self.assertEqual(len(self.t.positions),0)
    def test_expired_input_not_new_arm(self):
        self.t.feed('swap',swap(1000),1000);self.t.feed('exit',ex(1000),32000);self.assertEqual(self.t.counts['arms'],0)
    def test_expired_confirmation(self):self.setup_arm();self.t.advance(40000);self.assertEqual(self.t.counts['confirm_pass'],0)
    def test_no_backdating_when_poll_late(self):
        self.setup_arm();self.t.advance(7200);self.t.advance(8200)
        p=next(iter(self.t.positions.values()));self.assertEqual(p.opened,8200)
    def test_hard_stop_reference_not_swap_side(self):
        self.opened();self.t.feed('swap',swap(9000,.99,side='BUY'),9000);self.assertEqual(len(self.t.positions),3)
    def test_actual_reference_stop(self):self.opened();self.t.feed('swap',swap(9000,1.01),9000);self.assertEqual(len(self.t.positions),0)
    def test_trail(self):
        self.opened();self.t.feed('swap',swap(9000,.97),9000);self.t.feed('swap',swap(10000,.976),10000)
        self.assertEqual(len(self.t.positions),1);self.assertEqual(self.t.counts['trades'],2)
    def test_cost_separated_once(self):
        self.opened();self.t.feed('swap',swap(9000,1.01),9000)
        row=next(r for k,r in self.rows if k=='trades');acc=row['accounting']
        self.assertAlmostEqual(float(acc['reference_gross_short_bps'])-float(acc['all_in_scenarios']['20']['net_bps']),20)
        self.assertIsNone(row['executable_net_pnl'])
    def test_unknown_execution_not_zero(self):self.assertIsNone(self.t.state()['executable_net_pnl'])
    def test_timeout_no_new_swap(self):
        self.opened();self.t.advance(908000)
        self.assertEqual(sum(p.request is not None for p in self.t.positions.values()),2);self.assertEqual(len(self.t.positions),3)
    def test_timeout_wait_not_retroactive(self):
        self.opened();self.t.advance(908000);self.t.feed('swap',swap(909000,.99),909000)
        rows=[r for k,r in self.rows if k=='trades'];self.assertEqual(len(rows),2)
        self.assertTrue(all(r['exit_wait_ms']==1000 and r['exit_observed_ms']==909000 for r in rows))
    def test_timeout_fresh_reference(self):
        self.opened();self.t.feed('swap',swap(907999,.99),907999);self.t.advance(908000);self.assertEqual(len(self.t.positions),1)
    def test_invalid_mark_censored(self):
        r=swap(1000);r['pool_meme_reserve']=0;self.t.feed('swap',r,1000);self.assertEqual(self.t.counts['marks'],0)
    def test_duplicate_mark(self):self.t.feed('swap',swap(1000),1000);self.t.feed('swap',swap(1000),1000);self.assertEqual(self.t.counts['marks'],1)
    def test_duplicate_exit(self):self.t.feed('exit',ex(1000),1100);self.t.feed('exit',ex(1000),1100);self.assertEqual(self.t.counts['wallet_exits'],1)
    def test_wrong_pool_rejected(self):
        r=swap(1000);r['pool']='WRONG';self.t.feed('swap',r,1000);self.assertEqual(self.t.counts['invalid_pool'],1);self.assertEqual(self.t.counts['marks'],0)
    def test_future_mark_not_used(self):self.t.feed('swap',swap(10000),1000);self.assertEqual(len(self.t.marks['FARTCOIN']),0)
    def test_clock_regression_refused(self):self.t.advance(1000);self.assertRaises(ValueError,self.t.advance,999)
    def test_reference_mismatch_fails(self):
        r=swap(1000);r['reference_post_price']=9;self.assertRaises(RuntimeError,self.t.feed,'swap',r,1000)
    def test_missing_reference_does_not_confirm(self):
        self.t.feed('exit',ex(1000),1100);self.t.feed('exit',ex(2000),2100);self.t.advance(7000);self.assertEqual(self.t.counts['confirm_pass'],0)
    def test_no_negative_move_rejects(self):
        self.t.feed('swap',swap(1000),1000);self.t.feed('exit',ex(1000),1100)
        self.t.feed('swap',swap(2000),2000);self.t.feed('exit',ex(2000),2100)
        self.t.feed('swap',swap(6900,1.01),6900);self.t.advance(7000);self.assertEqual(self.t.counts['confirm_pass'],0)
    def test_absorption_blocks(self):
        self.t.feed('swap',swap(1000),1000);self.t.feed('exit',ex(1000),1100)
        self.t.feed('swap',swap(2000),2000);e=ex(2000);e['absorption_score']=60;self.t.feed('exit',e,2100)
        self.t.feed('swap',swap(6900,.99),6900);self.t.advance(7000);self.assertEqual(self.t.counts['confirm_pass'],0)
    def test_missing_absorption_not_zero(self):
        e=ex(1000);e.pop('absorption_score');self.t.feed('exit',e,1100);self.assertEqual(self.t.counts['invalid_exits'],1)
    def test_finite_no_json_nan(self):self.assertEqual(json.loads(json.dumps(self.t.state(),allow_nan=False))['open_positions'],0)
    def test_no_force_close_at_snapshot(self):self.opened();self.assertEqual(self.t.state()['open_positions'],3)
    def test_duplicate_fee_configuration_refused(self):self.assertRaises(ValueError,scenario,10,all_in_bps=20,entry_fee_bps=25)
    def test_policies_exact(self):self.assertEqual([x[3] for x in POLICIES.values()],[900000,900000,1800000])

if __name__=='__main__':unittest.main()
