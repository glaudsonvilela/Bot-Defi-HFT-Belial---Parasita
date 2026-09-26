import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'runner/tools'))
import session_config as cfg
import run90m

class NinetyMinuteSessionTests(unittest.TestCase):
 def test_exact_ninety_minute_units(self):
  self.assertEqual(cfg.CAPTURE_MINUTES,90)
  self.assertEqual(cfg.CAPTURE_SECONDS,5400)
  self.assertEqual(cfg.CAPTURE_MS,5400000)
 def test_deadline_full_ninety_minutes_after_capture_start(self):
  start=1790290000000000000
  self.assertEqual(cfg.deadline_ns(start)-start,5400000000000)
 def test_watchdog_does_not_expire_at_old_one_hour_limit(self):
  for elapsed in (0,3600,3841,5399,5400,5640):
   self.assertFalse(cfg.watchdog_expired(elapsed),str(elapsed))
  self.assertTrue(cfg.watchdog_expired(5640.001))
 def test_hours_minutes_seconds_countdown(self):
  for seconds,want in [(5400,'01:30:00'),(5399,'01:29:59'),(1800,'00:30:00'),(3599,'00:59:59'),(1,'00:00:01'),(0,'00:00:00'),(-1,'00:00:00')]:
   self.assertEqual(cfg.format_countdown(seconds),want)
 def test_cli_defaults_to_90_and_delegates_without_running_collector(self):
  argv=['run90m.py','--pool-manifest','fixture-pools.json','--markets','fixture-markets.json']
  with patch.object(sys,'argv',argv),patch.object(run90m,'run90m',return_value=17) as execute,patch.object(run90m.signal,'signal'):
   self.assertEqual(run90m.main(),17)
   execute.assert_called_once_with(Path('fixture-pools.json'),Path('fixture-markets.json'))
 def test_cli_rejects_60_in_90m_variant(self):
  argv=['run90m.py','--minutes','60','--pool-manifest','fixture','--markets','fixture']
  with patch.object(sys,'argv',argv),patch.object(run90m,'run90m') as execute,contextlib.redirect_stderr(io.StringIO()):
   with self.assertRaises(SystemExit) as caught:run90m.main()
   self.assertEqual(caught.exception.code,2);execute.assert_not_called()
 def test_duration_is_forwarded_to_collector_service_and_installer(self):
  runner=(ROOT/'runner/tools/run90m.py').read_text();install=(ROOT/'runner/tools/install_and_run.py').read_text()
  self.assertIn("'--minutes',str(CAPTURE_MINUTES)",runner)
  self.assertIn("'--minutes',str(CAPTURE_MINUTES)",install)
  self.assertIn("'--duration-ms',str(CAPTURE_MS)",runner)
  self.assertIn('deadline_ns=capture_deadline_ns(started_ns)',runner)
  self.assertIn('watchdog_expired(mono-startmono)',runner)
 def test_hourly_funding_and_display_rate_preserved(self):
  paper=json.loads((ROOT/'perp/CONFIG_PAPER.json').read_text())
  self.assertEqual(paper['funding']['boundary_period_ms'],3600000)
  self.assertEqual(paper['dashboard_push_hz'],25)
  self.assertTrue(paper['paper_only']);self.assertFalse(paper['orders'])
 def test_all_preserved_sources_are_byte_identical(self):
  expected=json.loads((ROOT/'PRESERVED_T3_COMPONENTS.json').read_text())['files']
  self.assertGreater(len(expected),30)
  for rel,want in expected.items():
   self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),want,rel)
 def test_short_collector_result_cannot_pass_as_ninety_minutes(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td);(path/'swaps.jsonl').write_text('')
   summary={'minutes':60,'session_complete':True,'capture_target_reached':True,'logging_failed':False,'dropped_events':0,'swaps_total':0,'rpc_errors':0,'decode_errors':0,'pools':[{'symbol':s,'swaps':0} for s in run90m.POOLS]}
   (path/'summary.json').write_text(json.dumps(summary))
   got=run90m.validate(path,0,True)
   self.assertFalse(got['T3_TECHNICAL_PASS']);self.assertFalse(got['duration_matches_requested'])
   self.assertEqual(got['requested_minutes'],90)
 def test_dashboard_uses_declared_duration_not_one_hour(self):
  html=(ROOT/'perp/web/dashboard.html').read_text();serve=(ROOT/'perp/tools/serve.py').read_text()
  self.assertIn('id="timer">01:30:00<',html)
  self.assertIn('Number(s.duration_ms)',html)
  self.assertNotIn('left/3600000',html)
  self.assertIn("'duration_ms':duration_ms",serve)
 def test_distinct_runner_panel_runtime_and_results(self):
  runner=(ROOT/'runner/tools/run90m.py').read_text();install=(ROOT/'runner/tools/install_and_run.py').read_text()
  self.assertIn('r5d-t3-90m-mp12-ultra-v1',runner)
  self.assertIn('r5d-t3-perp-90m-fix4-v1',runner)
  self.assertIn('r5d-t3-perp-90m-fix4-v1',install)
  self.assertIn('BELIAL_R5D_T3_90M_MP12_ULTRA_RESULTADO_',runner)
  self.assertIn('ACTIVE_COLLECTOR_REFUSED',runner)

 def test_explicit_90_minutes_is_accepted(self):
  argv=['run90m.py','--minutes','90','--pool-manifest','pools.json','--markets','markets.json']
  with patch.object(sys,'argv',argv),patch.object(run90m,'run90m',return_value=0) as execute,patch.object(run90m.signal,'signal'):
   self.assertEqual(run90m.main(),0)
   execute.assert_called_once_with(Path('pools.json'),Path('markets.json'))
 def test_old_nine_hours_and_invalid_durations_are_rejected(self):
  for minutes in ('540','60','0','-1','91'):
   with self.subTest(minutes=minutes):
    argv=['run90m.py','--minutes',minutes,'--pool-manifest','pools.json','--markets','markets.json']
    with patch.object(sys,'argv',argv),patch.object(run90m,'run90m') as execute,contextlib.redirect_stderr(io.StringIO()):
     with self.assertRaises(SystemExit) as caught:run90m.main()
     self.assertEqual(caught.exception.code,2);execute.assert_not_called()
 def test_results_only_accept_requested_ninety_minute_duration(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td);(path/'swaps.jsonl').write_text('')
   for minutes in (60,90,540):
    summary={'minutes':minutes,'session_complete':True,'capture_target_reached':True,'logging_failed':False,'dropped_events':0,'swaps_total':0,'rpc_errors':0,'decode_errors':0,'pools':[{'symbol':s,'swaps':0} for s in run90m.POOLS]}
    (path/'summary.json').write_text(json.dumps(summary))
    result=run90m.validate(path,0,True)
    self.assertEqual(result['duration_matches_requested'],minutes==90)
    self.assertEqual(result['requested_minutes'],90)
 def test_fix4_preserved_components_retain_fix3_bytes(self):
  manifest=json.loads((ROOT/'PRESERVED_FIX4_COMPONENTS.json').read_text())
  expected=manifest['preserved'];self.assertGreaterEqual(len(expected),60)
  for rel,want in expected.items():
   self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),want,rel)
  for rel in ('runner/tools/rpc_transport.py','runner/tools/rpc_selection.py','runner/tools/rpc_ws_probe.py','runner/tools/qualify_protocols.py','n2_mp12/CONFIG_N2.json','perp/CONFIG_PAPER.json','perp/tools/paper.py','perp/tools/books.py','perp/tools/serve.py'):
   self.assertIn(rel,expected)
  for rel in ('mp12/engine/src/raydium_protocols.rs','runner/tools/protocol_reference.py'):
   self.assertIn(rel,manifest['changed'])
   self.assertNotIn(rel,expected)
 def test_ninety_minute_binary_release_is_isolated(self):
  runner=(ROOT/'runner/tools/run90m.py').read_text();install=(ROOT/'runner/tools/install_and_run.py').read_text()
  self.assertEqual(run90m.MP12.name,'r5d-mp12-live-v1-fix4-90m')
  self.assertIn('r5d-mp12-live-v1-fix4-90m',install)
  self.assertIn('BELIAL_T3_90M_FIX4_PREFLIGHT_FALHA_',install)
  self.assertIn('BELIAL_R5D_T3_90M_FIX4_FALHA_',runner)
 def test_no_nine_hour_capture_labels_in_current_runtime_or_ui(self):
  for rel in ('runner/tools/run90m.py','runner/tools/install_and_run.py','runner/tools/session_config.py','perp/web/dashboard.html'):
   text=(ROOT/rel).read_text()
   for marker in ('CAPTURE_HOURS=9','540 min','9 horas','09:00:00','32400000','run9h.py'):
    self.assertNotIn(marker,text,rel)

if __name__=='__main__':unittest.main()
