"""Display projections, accounting invariants, and local read-only HTTP/SSE."""
import copy,http.client,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from a3.common import ROOT,atomic,digest
from a3.terminal_projection import envelope,compact_state,compact_agent
from a3.ui_worker import Bus,make_server
from a3.paper_controller import Controller
sys.path.insert(0,str(ROOT/'tests'))
import test_a2_core as old

class ProjectionTests(unittest.TestCase):
    def test_small_history_unchanged(self):p=[[1,1.],[2,None],[3,2.]];self.assertEqual(envelope(p),p)
    def test_large_history_bounded_preserving_extrema(self):
        p=[[i,float(i%301)] for i in range(10000)];p[50][1]=-500;p[55][1]=900
        out=envelope(p);self.assertLessEqual(len(out),480);self.assertIn([50,-500],out);self.assertIn([55,900],out);self.assertEqual(out[0],p[0]);self.assertEqual(out[-1],p[-1])
    def test_null_gap_not_interpolated(self):p=[[i,float(i)] for i in range(2000)];p[100][1]=None;self.assertTrue(any(x[1] is None for x in envelope(p)))
    def test_input_unchanged(self):s={'positions':[{'id':'1','closed_ms':None}],'curves':{'X':[[i,i] for i in range(800)]}};before=copy.deepcopy(s);compact_state(s);self.assertEqual(s,before)
    def test_all_open_survive_truncation(self):
        pos=[{'id':str(i),'closed_ms':i+1} for i in range(400)]+[{'id':'open'+str(i),'closed_ms':None} for i in range(50)]
        s=compact_state({'groups':{'CORE12':{'positions':pos}}});r=s['groups']['CORE12']['positions'];self.assertEqual(len(r),150);self.assertEqual(sum(x['closed_ms'] is None for x in r),50)
    def test_remove_only_duplicate_root_data(self):
        s=compact_state({'curves':{'X':[[1,0.]]},'positions':[],'groups':{'CORE12':{'curves':{'X':[[1,0.]]},'positions':[]}},'at_ms':10});self.assertNotIn('curves',s);self.assertEqual(s['groups']['CORE12']['curves']['X'],[[1,0.]])
    def test_unknown_costs_not_zeroed(self):s=compact_state({'groups':{'CORE12':{'scenarios':[{'all_in_model_usd':None}]}}});self.assertIsNone(s['groups']['CORE12']['scenarios'][0]['all_in_model_usd'])
    def test_group_names_remain_independent(self):s=compact_state({'groups':{'CORE12':{'counts':{'x':1}},'EXPANSION12':{'counts':{'x':2}}}});self.assertEqual(s['groups']['CORE12']['counts']['x'],1);self.assertEqual(s['groups']['EXPANSION12']['counts']['x'],2)
    def test_agent_projection_weighted_metrics(self):
        data={'models':[{'head_id':'A','primary_eval_n':2,'primary_brier':.1,'baseline_primary_brier':.3},{'head_id':'A','primary_eval_n':6,'primary_brier':.2,'baseline_primary_brier':.3}],'latest_predictions':{'X':{'published_ms':1}}};r=compact_agent(data);self.assertAlmostEqual(r['head_scores']['A']['brier'],.175);self.assertNotIn('models',r);self.assertEqual(r['latest_predictions'],data['latest_predictions'])
    def test_agent_empty_not_success(self):self.assertEqual(compact_agent({})['head_scores'],{})

class UIHTTPTests(unittest.TestCase):
    def setUp(self):self.bus=Bus();self.server=make_server(self.bus,ROOT/'a3/web/dashboard.html');self.port=self.server.server_port
    def tearDown(self):self.bus.closed=True;self.server.shutdown();self.server.server_close()
    def call(self,path,method='GET',headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2);c.request(method,path,headers=headers or {});r=c.getresponse();data=r.read();status=r.status;c.close();return status,data
    def test_index_has_terminal_not_demo_quotes(self):status,data=self.call('/');self.assertEqual(status,200);self.assertIn(b'BELIAL',data);self.assertNotIn(b'__HEADS__',data)
    def test_health_read_only(self):status,data=self.call('/health');self.assertTrue(json.loads(data)['read_only']);self.assertFalse(json.loads(data)['orders'])
    def test_post_refused(self):self.assertEqual(self.call('/api/snapshot','POST')[0],501)
    def test_arbitrary_host_refused(self):self.assertEqual(self.call('/',headers={'Host':'attacker.invalid'})[0],403)
    def test_unknown_path_refused(self):self.assertEqual(self.call('/private')[0],404)
    def test_snapshot_has_no_private_environment(self):self.bus.publish('state',{'x':3});status,data=self.call('/api/snapshot');self.assertEqual(json.loads(data),{'state':{'x':3}})
    def test_bad_origin_stream_refused(self):self.assertEqual(self.call('/events',headers={'Origin':'http://evil.invalid'})[0],403)
    def test_sse_coalesces_to_latest_state(self):
        for i in range(1000):self.bus.publish('books',{'sequence':i})
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2);c.request('GET','/events');r=c.getresponse();lines=[r.readline() for _ in range(4)];c.close();self.assertIn(b'event: books',lines[0]);self.assertEqual(json.loads(lines[2].split(b'data: ')[1])['sequence'],999);self.assertEqual(len(self.bus.payloads),1)
    def test_payload_limit_fails_explicitly(self):self.assertRaises(RuntimeError,self.bus.publish,'state',{'x':'a'*(2<<20)})
    def test_stream_clients_bounded(self):self.bus.clients=4;self.assertEqual(self.call('/events')[0],429)

class AccountingTests(unittest.TestCase):
    def harness(self):
        with mock.patch.object(old,'Controller',Controller):return old.Harness()
    def test_display_fields_do_not_change_output_journal(self):
        prior=[];new=[]
        h=old.Harness(emit=lambda k,r:prior.append((k,r)));h.open();h.event('book',old.hl(h.t+250,102,102.01),h.t+250);h.event('book',old.hl(h.t+450,102,102.01),h.t+450)
        c=Controller(copy.deepcopy(old.CFG),lambda k,r:new.append((k,r)))
        for event in h.events:c.apply(event)
        self.assertEqual(len(new),len(prior))
        allowed={'trailing_activation_bps','trailing_armed','trailing_giveback_bps','stop_bps','max_hold_ms','gap_to_activation_bps','mfe_gross_usd','current_gross_bps','gross_giveback_bps','protection_is_display_of_frozen_rule'}
        for (nk,nr),(pk,pr) in zip(new,prior):
            self.assertEqual(nk,pk);self.assertEqual({k:v for k,v in nr.items() if k in pr},pr);self.assertTrue(set(nr)-set(pr)<=allowed)
    def test_protection_inactive_below100(self):
        h=self.harness();h.open();p=next(p for p in h.c.engine.positions.values() if p.policy=='STOP100_TRAIL75');p.mfe_bps=81.74;r=h.c.engine.position_view(p,h.t+150);self.assertFalse(r['trailing_armed']);self.assertAlmostEqual(r['gap_to_activation_bps'],18.26)
    def test_protection_armed_exactly100(self):
        h=self.harness();h.open();p=next(p for p in h.c.engine.positions.values() if p.policy=='STOP100_TRAIL75');p.mfe_bps=100;r=h.c.engine.position_view(p,h.t+150);self.assertTrue(r['trailing_armed'])
    def test_labels_gross_separate_from_net(self):
        h=self.harness();p=h.open();r=h.c.engine.position_view(p,h.t+150);self.assertAlmostEqual(r['mfe_gross_usd'],r['notional_usd']*r['mfe_bps']/10000);self.assertTrue(r['protection_is_display_of_frozen_rule'])
    def test_allin_unknown_stays_null(self):h=self.harness();p=h.open();self.assertIsNone(h.c.engine.position_view(p,h.t+150)['all_in_model_usd'])
    def test_snapshots_are_read_only_numeric(self):
        h=self.harness();h.open();before=copy.deepcopy(h.c.engine.counts);h.c.snapshot(h.t+200);h.c.snapshot(h.t+200);self.assertEqual(before,h.c.engine.counts)
    def test_more_than100_open_not_hidden(self):
        h=self.harness();p=h.open()
        for i in range(150):h.c.engine.positions['extra'+str(i)]=copy.deepcopy(p)
        s=h.c.engine.snapshot(h.t+150);self.assertEqual(sum(r['closed_ms'] is None for r in s['positions']),len(h.c.engine.positions))

class FrozenContractTests(unittest.TestCase):
    def test_all_preserved_source_hashes(self):
        doc=json.loads((ROOT/'docs/PRESERVATION_A3.json').read_text())
        for name,sha in doc['preserved'].items():self.assertEqual(digest(ROOT/name),sha,name)
    def test_native_rust_byte_identical_to_A2(self):
        doc=json.loads((ROOT/'docs/PRESERVATION_A3.json').read_text());rs=[n for n in doc['preserved'] if n.startswith('engine_a2/')]
        self.assertGreater(len(rs),4)
        for name in rs:self.assertEqual(digest(ROOT/name),doc['preserved'][name])
    def test_sensors_use_local_mux_not_direct_remote(self):
        src=(ROOT/'a3/sensor_worker.py').read_text();self.assertIn("'--ws',ws_relay['url']",src);self.assertNotIn("'--ws',conn['ws']",src)
    def test_inputs_owned_before_capture_phase(self):
        src=(ROOT/'a3/supervisor.py').read_text();self.assertIn("(self.session/'price_samples/observations.jsonl').touch(exist_ok=False)",src);self.assertIn('AGENT_VALIDATION.json',src)
    def test_final_html_includes_true_final_agent_status(self):self.assertIn("('AGENT_STATUS',",(ROOT/'a3/supervisor.py').read_text())
    def test_90m_and_paper_preserved(self):from a3.common import DURATION_MS,SYMBOLS;self.assertEqual(DURATION_MS,5400000);self.assertEqual(len(SYMBOLS),24)
    def test_no_bloomberg_feeds_or_cdn_dependency(self):
        s=(ROOT/'a3/web/dashboard.html').read_text();self.assertNotIn('<script src=',s);self.assertNotIn('fonts.googleapis',s);self.assertIn('sem vínculo com Bloomberg',s)
    def test_render_uses_browser_frames(self):self.assertIn('requestAnimationFrame(frame)',(ROOT/'a3/web/dashboard.html').read_text())
