import copy,json,sys,unittest,math,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from books import Book,Books,step_down
from paper import Engine,POLICIES,HOUR
import markets
CFG=json.loads((ROOT/'CONFIG_PAPER.json').read_text())

def meta(venue='hyperliquid',mult=1):return {'venue':venue,'symbol':'FARTCOIN','market':'FARTCOIN' if venue=='hyperliquid' else '42','multiplier':mult,'size_step':.001,'min_size':.001,'min_notional':10.,'status':'ACTIVE'}
def hl(t,bid=100.,ask=100.01,qty=10000.,gen=1):
 return {'venue':'hyperliquid','generation':gen,'received_ns':t*1000000,'data':{'channel':'l2Book','data':{'coin':'FARTCOIN','time':t,'levels':[[{'px':str(bid),'sz':str(qty)}],[{'px':str(ask),'sz':str(qty)}]]}}}
def li(t,nonce=1,begin=None,snapshot=True,bids=None,asks=None,gen=1):
 return {'venue':'lighter','generation':gen,'received_ns':t*1000000,'data':{'channel':'order_book:42','type':'subscribed/order_book' if snapshot else 'update/order_book','timestamp':t,'order_book':{'nonce':nonce,'begin_nonce':begin,'last_updated_at':t*1000,'bids':bids if bids is not None else [{'price':'100','size':'10000'}],'asks':asks if asks is not None else [{'price':'100.01','size':'10000'}]}}}
def signal(t,ident='EP1'):return {'kind':'EPISODE_OPEN','episode_id':ident,'symbol':'FARTCOIN','at_unix_ms':t,'paper_only':True,'orders':False}
def setup(two=False,start=1000000):
 bs=Books({'markets':[meta()]+([meta('lighter')] if two else [])});bs.ingest(hl(start))
 if two:bs.ingest(li(start))
 rows=[];en=Engine(bs,copy.deepcopy(CFG),lambda k,r:rows.append((k,r)));return bs,en,rows

def tick_book(bs,en,t,bid=100,ask=100.01,gen=1):
 for key in bs.ingest(hl(t,bid,ask,gen=gen)):en.on_book(key,t)

def chosen(en):return next(p for p in en.positions.values() if p.scenario=='ROTA' and p.policy=='STOP100_TRAIL75')

class BookTests(unittest.TestCase):
 def test_snapshot_replaces(self):
  bs,en,_=setup();bs.ingest(hl(1000010,101,101.01));b=bs.books[('hyperliquid','FARTCOIN')];self.assertEqual(len(b.bids),1);self.assertEqual(float(max(b.bids)),101)
 def test_duplicate_snapshot_does_not_refresh(self):
  bs,en,_=setup();x=hl(1000000);x['received_ns']=1004000*1000000;self.assertEqual(bs.ingest(x),[]);b=bs.books[('hyperliquid','FARTCOIN')];self.assertFalse(b.ready(1004000)[0])
 def test_li_delta_and_zero_remove(self):
  bs=Books({'markets':[meta('lighter')]});bs.ingest(li(1000));x=li(1010,2,1,False,bids=[{'price':'100','size':'0'},{'price':'99','size':'5'}],asks=[]);self.assertTrue(bs.ingest(x));b=bs.books[('lighter','FARTCOIN')];self.assertEqual(float(max(b.bids)),99);self.assertEqual(float(min(b.asks)),100.01)
 def test_gap_quarantines(self):
  resets=[];bs=Books({'markets':[meta('lighter')]},resets.append);bs.ingest(li(1000));self.assertEqual(bs.ingest(li(1010,3,2,False)),[]);b=bs.books[('lighter','FARTCOIN')];self.assertFalse(b.valid);self.assertEqual(b.reason,'NONCE_GAP');self.assertEqual(resets,['lighter'])
 def test_snapshot_after_reconnect(self):
  bs=Books({'markets':[meta('lighter')]});bs.ingest(li(1000));bs.ingest({'venue':'lighter','generation':2,'received_ns':1010000000,'transport':'CONNECTED'});self.assertEqual(bs.ingest(li(1020,2,1,False,gen=2)),[]);self.assertTrue(bs.ingest(li(1030,100,snapshot=True,gen=2)))
 def test_invalid_size(self):
  bs=Books({'markets':[meta()]});x=hl(1000,qty=-1);bs.ingest(x);self.assertFalse(bs.books[('hyperliquid','FARTCOIN')].valid)
 def test_nonfinite_price(self):
  bs=Books({'markets':[meta()]});bs.ingest(hl(1000,bid=float('nan')));self.assertFalse(bs.books[('hyperliquid','FARTCOIN')].valid)
 def test_crossed_book(self):
  bs=Books({'markets':[meta()]});bs.ingest(hl(1000,bid=101,ask=100));self.assertFalse(bs.books[('hyperliquid','FARTCOIN')].valid)
 def test_timestamp_future(self):
  bs,en,_=setup();self.assertFalse(bs.books[('hyperliquid','FARTCOIN')].ready(999000)[0])
 def test_old_source_new_receive_still_stale(self):
  bs,en,_=setup();x=hl(1000010);x['received_ns']=1008000*1000000;bs.ingest(x);self.assertFalse(bs.books[('hyperliquid','FARTCOIN')].ready(1008000)[0])
 def test_multiplier_normalizes_quantity_and_price(self):
  bs=Books({'markets':[meta(mult=1000)]});bs.ingest(hl(1000,bid=100,ask=101,qty=2));b=bs.books[('hyperliquid','FARTCOIN')];self.assertEqual(float(max(b.bids)),.1);self.assertEqual(next(iter(b.bids.values())),2000)
 def test_depth_weighted_vwap(self):
  bs=Books({'markets':[meta()]});x=hl(1000);x['data']['data']['levels'][1]=[{'px':'101','sz':'1'},{'px':'102','sz':'2'}];bs.ingest(x);f=bs.books[('hyperliquid','FARTCOIN')].fill('BUY',3,1000);self.assertAlmostEqual(f['vwap'],305/3)
 def test_insufficient_depth_never_fabricates(self):
  bs=Books({'markets':[meta()]});bs.ingest(hl(1000,qty=1));self.assertRaisesRegex(ValueError,'INSUFFICIENT',bs.books[('hyperliquid','FARTCOIN')].fill,'SELL',2,1000)
 def test_step_floor_not_round_up(self):self.assertEqual(step_down(.999999,.001),.999)
 def test_min_step_invalid(self):self.assertRaises(ValueError,step_down,1,0)
 def test_ticker_does_not_execute_or_refresh_depth(self):
  bs=Books({'markets':[meta('lighter')]});bs.ingest(li(1000));env={'venue':'lighter','generation':1,'received_ns':9000000000,'data':{'channel':'ticker:42','ticker':{'a':{'price':'101'},'b':{'price':'100'}},'nonce':2}}
  self.assertEqual(bs.ingest(env),[]);self.assertFalse(bs.books[('lighter','FARTCOIN')].ready(9000)[0])

class PaperTests(unittest.TestCase):
 def test_no_trade_from_non_episode(self):
  bs,e,r=setup();e.signal({'kind':'ARM'},1000000);self.assertFalse(e.orders)
 def test_no_entry_before_latency(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000050);self.assertFalse(e.positions);tick_book(bs,e,1000101);self.assertEqual(len(e.positions),6)
 def test_missing_new_book_cannot_fill(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);e.on_book(('hyperliquid','FARTCOIN'),1000150);self.assertFalse(e.positions)
 def test_stale_signal_rejected(self):
  bs,e,r=setup();e.signal(signal(900000),1000000);self.assertFalse(e.orders)
 def test_future_signal_rejected(self):
  bs,e,r=setup();e.signal(signal(1000001),1000000);self.assertFalse(e.orders)
 def test_episode_dedup(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);n=len(e.orders);e.signal(signal(1000000),1000000);self.assertEqual(len(e.orders),n)
 def test_roundtrip_fees_on_each_actual_notional(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);p=chosen(e);e.request_exit(p,1000200,'TEST');tick_book(bs,e,1000310,98.99,99)
  self.assertIsNotNone(p.closed_ms);self.assertAlmostEqual(p.fee_entry,p.qty*100*.00045);self.assertAlmostEqual(p.fee_exit,p.qty*99*.00045)
  row=e.position_view(p,1000310);self.assertAlmostEqual(row['net_usd'],p.qty*(100-99)-p.qty*100*.00045-p.qty*99*.00045)
 def test_spread_not_subtracted_twice(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);p=chosen(e);row=e.position_view(p,1000150);expected=p.qty*(100-100.01)-p.qty*(100+100.01)*.00045;self.assertAlmostEqual(row['net_usd'],expected)
 def test_lighter_delay_and_route_selection_not_hindsight(self):
  bs,e,r=setup(True);e.signal(signal(1000000),1000000);self.assertEqual(e.last_gate['FARTCOIN']['selected'],'lighter');tick_book(bs,e,1000150);self.assertFalse(any(p.scenario=='ROTA' for p in e.positions.values()))
  for key in bs.ingest(li(1000401,2,1,False)):e.on_book(key,1000401)
  self.assertEqual(chosen(e).venue,'lighter');self.assertEqual(chosen(e).fee_entry,0)
 def test_cost_gate(self):
  bs,e,r=setup();bs.ingest(hl(1000001,bid=99,ask=101));e.signal(signal(1000001),1000001);self.assertFalse(e.orders);self.assertEqual(e.last_gate['FARTCOIN']['status'],'BLOCKED')
 def test_order_expiry_not_fake_fill(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);e.tick(1010000);self.assertFalse(e.orders);self.assertFalse(e.positions);self.assertGreater(e.counts['expired_orders'],0)
 def test_entry_cancelled_when_connection_changed(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150,gen=2);self.assertFalse(e.positions);self.assertGreater(e.counts['connection_cancelled_entries'],0)
 def test_capital_is_bounded_per_scenario(self):
  bs,e,r=setup()
  for i in range(30):e.signal(signal(1000000,'x'+str(i)),1000000)
  for scenario in ('ROTA','HYPERLIQUID'):
   for policy in POLICIES:self.assertGreaterEqual(e.equity_available(scenario,policy,1000000),0)
  self.assertGreater(e.counts['capital_rejections'],0)
 def test_no_close_on_session_end(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);e.finalize(1000160);self.assertIsNone(chosen(e).closed_ms);self.assertTrue(e.finalized)
 def test_stale_close_now_is_null(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);row=e.position_view(chosen(e),1005000);self.assertIsNone(row['net_usd']);self.assertEqual(row['quote_error'],'BOOK_STALE')
 def test_funding_crossing_unknown_not_zero(self):
  start=HOUR-1000;bs,e,r=setup(start=start);e.signal(signal(start),start);tick_book(bs,e,start+150);tick_book(bs,e,HOUR+10);p=chosen(e);row=e.position_view(p,HOUR+10);self.assertIsNone(row['funding_model_usd']);self.assertIsNone(row['net_usd']);self.assertIsNotNone(row['net_ex_funding_usd'])
 def test_positive_funding_credits_short_once(self):
  start=HOUR-1000;bs,e,r=setup(start=start);e.signal(signal(start),start);tick_book(bs,e,start+150);e.index_history[('hyperliquid','FARTCOIN')].append((HOUR-100,100.));tick_book(bs,e,HOUR+10);p=chosen(e);e.funding_rate(('hyperliquid','FARTCOIN'),HOUR,.0001,HOUR+10,'FIXTURE');e.funding_rate(('hyperliquid','FARTCOIN'),HOUR,.0001,HOUR+10,'FIXTURE');self.assertAlmostEqual(p.funding_total(HOUR+10)[0],p.qty*100*.0001);self.assertEqual(len(p.funding),1)
 def test_funding_future_not_applied(self):
  bs,e,r=setup();e.funding_rate(('hyperliquid','FARTCOIN'),HOUR,.01,1000000,'FIXTURE');self.assertFalse(e.settled_rates)
 def test_missing_index_funding_remains_unknown(self):
  start=HOUR-1000;bs,e,r=setup(start=start);e.signal(signal(start),start);tick_book(bs,e,start+150);tick_book(bs,e,HOUR+10);e.funding_rate(('hyperliquid','FARTCOIN'),HOUR,.0001,HOUR+10,'FIXTURE');self.assertIsNone(chosen(e).funding_total(HOUR+10)[0])
 def test_stop_uses_perp_ask_not_spot_reference(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);tick_book(bs,e,1000200,101.1,101.11);self.assertEqual(chosen(e).exit_reason,'STOP');self.assertIsNone(chosen(e).closed_ms)
 def test_expired_exit_keeps_exit_intent(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);p=chosen(e);e.request_exit(p,1000200,'STOP');e.tick(1010000);self.assertIsNone(p.closed_ms);self.assertTrue(any(o['kind']=='EXIT' and o['id']==p.id for o in e.orders));self.assertEqual(p.exit_reason,'STOP')
 def test_policies_not_summed(self):
  bs,e,r=setup();e.signal(signal(1000000),1000000);tick_book(bs,e,1000150);agg=e.aggregate('ROTA','STOP100_TRAIL75',1000150);self.assertEqual(agg['entries'],1);self.assertEqual(len(e.positions),6)
 def test_unsafe_config_refused(self):
  bs,e,r=setup();cfg=copy.deepcopy(CFG);cfg['orders']=True;self.assertRaises(ValueError,Engine,bs,cfg)

class MetadataTests(unittest.TestCase):
 def test_alias_multiplier(self):self.assertEqual(markets.identity('kBONK'),('BONK',1000));self.assertIsNone(markets.identity('SCAM-BONK'))
 def test_duplicate_symbol_rejected(self):self.assertRaises(ValueError,markets.unique,[meta(),meta()])
 def test_hl_delisted_excluded(self):self.assertEqual(markets.parse_hl([{'universe':[{'name':'FARTCOIN','szDecimals':3,'isDelisted':True}]},[{}]]),[])
 def test_lighter_spot_not_perp(self):self.assertEqual(markets.parse_li({'code':200,'order_book_details':[{'symbol':'FARTCOIN','market_type':'spot','status':'active'}]}),[])
 def test_unknown_schema_fails(self):self.assertRaises(ValueError,markets.parse_li,{'unexpected':[]})
if __name__=='__main__':unittest.main()
