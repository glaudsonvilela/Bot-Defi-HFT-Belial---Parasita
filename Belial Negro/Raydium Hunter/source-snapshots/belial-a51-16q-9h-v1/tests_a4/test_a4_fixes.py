import re,unittest
from pathlib import Path
from a4.common import ROOT
from a4.sensor_worker import Monitor
from a4.book_health import classify_book

class EventLocalRecoveryTests(unittest.TestCase):
    def frames(self,t,rpc=0,dropped=0,decode=0,generation=0,state='ACTIVE',gap=0):
        d=dict(updated_unix_ms=t,rpc_errors=rpc,dropped_events=dropped,decode_errors=decode,engine_status='RUNNING')
        h=dict(state=state,at_ms=t,generation=generation,actor_gap_ms=gap)
        return d,h
    def call(self,m,t,**kw):
        d,h=self.frames(t,**kw);return m.update(d,h,t)
    def test_actor_rpc_error_is_event_local_not_60s_source_recovery(self):
        m=Monitor();self.assertEqual(self.call(m,1000)['state'],'ACTIVE')
        r=self.call(m,2000,rpc=1);self.assertEqual(r['state'],'ACTIVE');self.assertTrue(r['actor_ready'])
        self.assertEqual(r['event_local_actor_failures'],1);self.assertEqual(r['actor_epoch'],0)
    def test_decode_quarantine_is_event_local(self):
        m=Monitor();self.call(m,1000);r=self.call(m,2000,decode=1);self.assertEqual(r['state'],'ACTIVE');self.assertEqual(r['event_local_decode_quarantines'],1)
    def test_actor_queue_drop_remains_source_continuity_fault(self):
        m=Monitor();self.call(m,1000);r=self.call(m,2000,dropped=1);self.assertEqual(r['state'],'RECOVERING');self.assertFalse(r['actor_ready']);self.assertGreater(r['actor_epoch'],0)
    def test_native_source_gap_still_recovers(self):
        m=Monitor();self.call(m,1000);r=self.call(m,2000,gap=1900);self.assertEqual(r['state'],'RECOVERING');self.assertFalse(r['actor_ready'])
    def test_ws_disconnect_still_recovers(self):
        m=Monitor();self.call(m,1000);r=self.call(m,2000,state='DISCONNECTED');self.assertIn(r['state'],('DEGRADED','RECOVERING'))

class RustContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.src=(ROOT/'engine_a4/src/main.rs').read_text()
    def test_actor_rpc_failure_does_not_clear_symbol(self):
        m=re.search(r'Err\(error\) => \{\s*\{\s*let mut guard = state\.lock\(\)\.unwrap\(\);\s*guard\.rpc_errors \+= 1;(.*?)let _ = log_tx\.send\(LogRecord::Error',self.src,re.S)
        self.assertIsNotNone(m);self.assertIn('a4_event_local_fault()',m.group(1));self.assertNotIn('a2_invalidate_actor_history',m.group(1))
    def test_source_disconnect_still_invalidates_history(self):
        self.assertRegex(self.src,r'a2_invalidate_actor_history\(&mut guard, &cfg\.symbol\); \} health\("DISCONNECTED"')
    def test_writer_exposes_separate_event_local_counter(self):
        self.assertIn('value["a4_event_local_faults"]',self.src);self.assertIn('value["a2_actor_not_before_ms"]',self.src)
    def test_build_worker_targets_a4_engine(self):
        s=(ROOT/'a4/build_worker.py').read_text();self.assertIn("ROOT/'engine_a4/Cargo.toml'",s);self.assertNotIn("ROOT/'engine_a2/Cargo.toml'",s)

class BookDisplayTests(unittest.TestCase):
    def row(self,age=100,ready=True,status='OK'):return {'age_ms':age,'ready':ready,'status':status}
    def test_fresh_connected(self):self.assertEqual(classify_book(self.row(),'CONNECTED')['display_state'],'FRESH')
    def test_stale_is_not_disconnect(self):
        r=classify_book(self.row(3141,False,'BOOK_STALE'),'CONNECTED');self.assertEqual(r['display_state'],'STALE');self.assertFalse(r['ready_for_fills'])
    def test_reconnecting_has_distinct_state(self):self.assertEqual(classify_book(self.row(),'RECONNECTING')['display_state'],'RECONNECTING')
    def test_disconnected_has_distinct_state(self):self.assertEqual(classify_book(self.row(),'DISCONNECTED')['display_state'],'DISCONNECTED')
    def test_stored_snapshot_age_advances(self):self.assertEqual(classify_book(self.row(1200),'CONNECTED',900)['display_state'],'STALE')
    def test_execution_gate_not_relaxed(self):
        r=classify_book(self.row(2100,True,'OK'),'CONNECTED');self.assertEqual(r['display_state'],'STALE');self.assertFalse(r['ready_for_fills'])

class DashboardSemanticsTests(unittest.TestCase):
    def test_ui_does_not_use_sse_as_market_connection(self):
        s=(ROOT/'a4/web/dashboard.html').read_text();start=s.index('function health(b)');end=s.index('function currentBook',start) if 'function currentBook' in s[start:] else start+2500
        block=s[start:end];self.assertIn('venue_runtime',block);self.assertNotIn('&&!!U.connected',block)
    def test_four_states_visible(self):
        s=(ROOT/'a4/web/dashboard.html').read_text()
        for word in ('FRESH','STALE','RECONNECTING','DISCONNECTED'):self.assertIn(word,s)
    def test_stale_explanation_present(self):self.assertIn('STALE não significa socket desconectado',(ROOT/'a4/web/dashboard.html').read_text())


class SupervisorGuardTests(unittest.TestCase):
    def test_a4_guard_excludes_its_own_supervisor_pid(self):
        s=(ROOT/'a4/supervisor.py').read_text()
        self.assertIn('current_pid=os.getpid()',s)
        self.assertIn("if int(proc.name)==current_pid:continue",s)
        self.assertIn("b'a4.supervisor' in raw",s)

if __name__=='__main__':unittest.main()
