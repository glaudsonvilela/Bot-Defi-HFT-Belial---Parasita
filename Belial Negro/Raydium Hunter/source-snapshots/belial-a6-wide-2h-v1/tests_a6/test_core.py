import copy,json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
from a6.tails import Tails,contracts,fill
from a6.discovery import Discovery
from a51.common import ROOT,DURATION_MS
from a51.economics import Engine
from a51.paper_controller import SafeBooks
from a51 import model_store
T=1_000_000
META={'symbols':[dict(symbol='WIFUSDT',baseAsset='WIF',quoteAsset='USDT',marginAsset='USDT',status='TRADING',contractType='PERPETUAL',filters=[dict(filterType='MARKET_LOT_SIZE',stepSize='0.1',minQty='0.1',maxQty='100000'),dict(filterType='MIN_NOTIONAL',notional='5')])]}
def engine():
    events=[];e=Tails(events.append);e.catalog=contracts(META);e.metadata_ms=T;e.start=T-200000;e.deadline=T+7200000
    for t in range(T-180000,T+1,2000):e.history['WIFUSDT'].append((t,1+(T-t)/10000000))
    for t in range(T-59000,T+1,1000):e.flows['WIFUSDT'].append((t,10.,200.,3))
    e.books['WIFUSDT']=dict(bids=[(.9999,1000.)],asks=[(1.0001,1000.)],received_ms=T,event_ms=T,generation=1,sequence=1)
    return e,events
def book(at,bid=.9999,ask=1.0001,seq=2,gen=1):return dict(kind='depth',received_ms=at,generation=gen,data=dict(s='WIFUSDT',E=at,u=seq,b=[[str(bid),'1000']],a=[[str(ask),'1000']]))
class WideTests(unittest.TestCase):
    def test_market_filters(self):
        for key,value in [('status','BREAK'),('contractType','CURRENT_QUARTER'),('quoteAsset','USDC'),('marginAsset','BTC')]:
            m=copy.deepcopy(META);m['symbols'][0][key]=value;self.assertFalse(contracts(m))
    def test_missing_metadata_blocks(self):
        e,_=engine();e.catalog={}
        with self.assertRaises(ValueError):e.quote('WIFUSDT',T)
    def test_depth_is_required(self):
        with self.assertRaises(ValueError):fill([(1,2)],3)
    def test_no_entry_before_latency(self):
        e,events=engine();e.tick(T,['WIFUSDT']);self.assertEqual(len(e.pending),1);self.assertFalse(e.positions)
        e.ingest(book(T+200),T+200);self.assertFalse(e.positions)
        e.ingest(book(T+300,seq=3),T+300);self.assertEqual(len(e.positions),1)
    def test_stop_and_exit_require_later_book(self):
        e,events=engine();e.tick(T,['WIFUSDT']);e.ingest(book(T+300),T+300)
        e.ingest(book(T+1000,1.02,1.0202,3),T+1000);self.assertIn('WIFUSDT',e.positions);self.assertIsNotNone(e.positions['WIFUSDT']['exit_request'])
        e.ingest(book(T+1200,1.02,1.0202,4),T+1200);self.assertFalse(e.closed)
        e.ingest(book(T+1300,1.02,1.0202,5),T+1300);self.assertEqual(len(e.closed),1);self.assertLess(e.realized,0);self.assertIsNone(e.closed[0]['all_in_net'])
    def test_reversal_before_fill_cancels(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.history['WIFUSDT'].append((T+300,1.1));e.ingest(book(T+300),T+300);self.assertFalse(e.positions);self.assertFalse(e.pending)
    def test_reconnect_cancels_pending(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.ingest(book(T+300,gen=2),T+300);self.assertFalse(e.positions);self.assertFalse(e.pending)
    def test_stale_producer_cannot_refresh(self):
        e,_=engine();e.ingest(book(T-10000),T);self.assertEqual(e.books['WIFUSDT']['sequence'],1);self.assertEqual(e.counts['stale_envelopes'],1)
    def test_duplicate_book_no_fill(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.ingest(book(T+300,seq=1),T+300);self.assertFalse(e.positions)
    def test_zero_quantity_invalidates_book(self):
        e,_=engine();r=book(T+300);r['data']['b'][0][1]='0';e.ingest(r,T+300);self.assertNotIn('WIFUSDT',e.books)
    def test_cost_rejection_has_quote(self):
        e,_=engine();e.books['WIFUSDT']['asks']=[(1.01,1000)];reason,f=e.reason('WIFUSDT',T);self.assertEqual(reason,'COST_GATE');self.assertGreater(f['quote']['cost_bps'],30)
    def test_buying_flow_is_not_short_signal(self):
        e,_=engine();e.flows['WIFUSDT'].clear()
        for t in range(T-59000,T+1,1000):e.flows['WIFUSDT'].append((t,200.,10.,3))
        self.assertEqual(e.reason('WIFUSDT',T)[0],'SELL_FLOW_NOT_CONFIRMED')
    def test_warmup_and_future_refusal(self):
        e,_=engine();e.history['WIFUSDT'].clear();e.ingest(dict(kind='wide',received_ms=T,data=[dict(e='24hrMiniTicker',s='WIFUSDT',E=T+10000,c='1')]),T);self.assertFalse(e.history['WIFUSDT']);self.assertEqual(e.reason('WIFUSDT',T)[0],'PRICE_WARMUP')
    def test_queue_overflow_invalidates(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.ingest(dict(kind='ack',loss_epoch=1,received_ms=T+1),T+1);self.assertFalse(e.pending);self.assertFalse(e.books)
    def test_open_position_pinned(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.ingest(book(T+300),T+300);e.history.clear();self.assertIn('WIFUSDT',e.selected(T+500))
    def test_end_censors_without_fabricated_exit(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.ingest(book(T+300),T+300);e.finish(T+500);self.assertTrue(e.positions['WIFUSDT']['right_censored']);self.assertFalse(e.closed)
    def test_expiry(self):
        e,_=engine();e.tick(T,['WIFUSDT']);e.tick(T+6000,[]);self.assertFalse(e.pending)
    def test_capture_deadline_blocks(self):
        e,_=engine();e.deadline=T;e.tick(T,['WIFUSDT']);self.assertFalse(e.pending)
    def test_spot_cannot_enter(self):
        e,_=engine();e.ingest(dict(kind='wide',received_ms=T,data=[dict(e='24hrMiniTicker',s='FAKEPOOL',E=T,c='1')]),T);self.assertNotIn('FAKEPOOL',e.history)
    def test_cooled_signal_not_duplicated(self):
        e,ev=engine();e.tick(T,['WIFUSDT']);e.tick(T+100,['WIFUSDT']);self.assertEqual(e.counts['signals'],1)

class LegacyGateTests(unittest.TestCase):
    def make(self):
        cfg=json.loads((ROOT/'legacy/perp/CONFIG_PAPER.json').read_text());events=[];e=Engine(SafeBooks({'markets':[]}),cfg,lambda k,r:events.append((k,r)))
        row=dict(kind='EPISODE_OPEN',episode_id='x',symbol='WIF',at_unix_ms=T,paper_only=True,orders=False)
        return e,row,events
    def test_temporary_book_can_be_reconsidered_once(self):
        e,row,events=self.make();e.route_quote=lambda *a:(_ for _ in ()).throw(ValueError('WAITING_SNAPSHOT'));e.signal(row,T);self.assertIn('x',e.waiting_signals);self.assertNotIn('x',e.seen)
        e.route_quote=lambda *a:(_ for _ in ()).throw(ValueError('NO_PERP_MARKET'));e.retry_waiting(T+500,lambda *_:(True,'OK'));self.assertNotIn('x',e.waiting_signals);self.assertIn('x',e.seen);self.assertEqual(e.counts['signals'],1)
    def test_dependency_cancellation(self):
        e,row,events=self.make();e.route_quote=lambda *a:(_ for _ in ()).throw(ValueError('BOOK_STALE'));e.signal(row,T);e.retry_waiting(T+10,lambda *_:(False,'SOURCE_GAP'));self.assertFalse(e.waiting_signals);self.assertIn('x',e.seen)
    def test_retry_deadline(self):
        e,row,_=self.make();e.route_quote=lambda *a:(_ for _ in ()).throw(ValueError('WAITING_SNAPSHOT'));e.signal(row,T);e.retry_waiting(T+2000,lambda *_:(True,'OK'));self.assertFalse(e.waiting_signals)
    def test_rejected_cost_preserved(self):
        e,row,events=self.make();e.route_quote=lambda v,*a:dict(venue=v,cost_gate_pass=False,cost_with_stress_bps=31.25);e.signal(row,T);self.assertEqual(e.last_gate['WIF']['route_diagnostics']['lighter']['cost_with_stress_bps'],31.25);self.assertFalse(e.waiting_signals)
    def test_completed_episode_not_repeated(self):
        e,row,_=self.make();e.route_quote=lambda *a:(_ for _ in ()).throw(ValueError('NO_PERP_MARKET'));e.signal(row,T);e.signal(row,T+1);self.assertEqual(e.counts['signals'],1)
    def test_unsafe_signal_rejected(self):
        e,row,_=self.make();row['orders']=True
        with self.assertRaises(ValueError):e.signal(row,T)

class StoreTests(unittest.TestCase):
    def test_a6_contract_and_immutable_publish(self):
        contract=model_store.contract();self.assertIn('a51/n2_worker.py',contract['files']);self.assertNotIn('a4/n2_worker.py',contract['files'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);runtime=root/'runtime';runtime.mkdir();(runtime/'model.weights.bin').write_bytes(b'\0'*16);(runtime/'model.meta.json').write_text('{}')
            r=model_store.publish(runtime,'m2-a6-test',store=root/'models');self.assertTrue(r['published'])
            with self.assertRaises(RuntimeError):model_store.publish(runtime,'m2-a6-test',store=root/'models')
    def test_old_session_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):model_store.publish(d,'m2-a4-test',store=Path(d)/'store')
    def test_duration(self):self.assertEqual(DURATION_MS,7200000)

class DiscoveryTests(unittest.TestCase):
    def test_protocol_pool_identity_not_symbol(self):
        rows=[dict(chainId='solana',pairAddress='pool1',dexId='meteora',baseToken=dict(symbol='SAME',address='a'),quoteToken=dict(symbol='SOL',address='b')),dict(chainId='solana',pairAddress='pool2',dexId='orca',baseToken=dict(symbol='SAME',address='c'),quoteToken=dict(symbol='SOL',address='b'))]
        def fetch(url):return [dict(chainId='solana',tokenAddress='a'*32)] if 'profiles' in url else rows
        d=Discovery(fetch);d.dex();self.assertEqual(len(d.pools),2);self.assertTrue(all(not r['short_eligible'] for r in d.pools.values()))
    def test_denial_does_not_rotate(self):
        import urllib.error
        calls=[]
        def fetch(url):calls.append(url);raise urllib.error.HTTPError(url,403,'blocked',{},None)
        d=Discovery(fetch);d.raydium();d.raydium();self.assertEqual(len(calls),1);self.assertEqual(d.sources['raydium']['state'],'ACCESS_DENIED')
    def test_ray_page_rotation(self):
        urls=[];d=Discovery(lambda u:urls.append(u) or {'data':{'data':[]}})
        for _ in range(12):d.raydium()
        self.assertTrue(urls[0].endswith('page=1'));self.assertTrue(urls[10].endswith('page=1'))
if __name__=='__main__':unittest.main()
