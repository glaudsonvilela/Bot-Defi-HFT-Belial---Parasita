import importlib.util,json,tempfile,time,unittest
from pathlib import Path
P=Path(__file__).parents[1]/'tools/dashboard_ultra.py'
spec=importlib.util.spec_from_file_location('d2',P);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
class T(unittest.TestCase):
 def test_countdown(self):
  self.assertEqual(d.fmt_countdown(3_601_123_456_000,1_000_000_000),'T-01:00:00.123456')
 def test_pct(self):
  self.assertEqual(d.pct([1,2,3],.5),2)
 def test_event_driven_microsecond_contract(self):
  src=P.read_text()
  self.assertIn('createfilehandler',src);self.assertIn('inotify_to_widget_p95',src)
  self.assertIn('fast_event_render(changed)',src);self.assertIn("root.after(8",src)
  self.assertIn('microseconds_are_measurement_precision_not_screen_refresh_rate',src)
 def test_partial_tail(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'x';p.write_bytes(b'{"a":1}\n{"a":2')
   r=d.TailReader(p);self.assertEqual(r.read(),[{'a':1}]);
   with p.open('ab') as f:f.write(b'}\n')
   self.assertEqual(r.read(),[{'a':2}]);r.close()
if __name__=='__main__':unittest.main()
