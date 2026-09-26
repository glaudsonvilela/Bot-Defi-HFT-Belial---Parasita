import ctypes,json,sys,tempfile,unittest
from unittest import mock
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import observer as o,opencl_core as oc,live
class T(unittest.TestCase):
 def test_safety(self):
  s=o.CONFIG['safety'];self.assertTrue(s['paper_only']);self.assertFalse(s['approved_for_decisions']);self.assertFalse(s['signal_engine_feedback']);self.assertFalse(s['transactions_submitted']);self.assertFalse(s['live_weights_seeded_from_replay'])
 def test_opencl_kernel_limits(self):
  self.assertEqual(oc.MAX_DIM,8192);self.assertEqual(oc.MAX_HEADS,32);self.assertEqual(oc.MAX_POOLS,24);self.assertIn('train_one',oc.KERNEL);self.assertIn('predict',oc.KERNEL);self.assertNotIn('double',oc.KERNEL)
 def test_mp12_pools(self):
  self.assertEqual(len(o.c.POOLS),24);self.assertEqual(o.c.POOLS[-1],'KMNO')
 def test_benchmark_levels(self):
  self.assertEqual(o.CONFIG['gpu_benchmark']['stages'][-1],[8192,32]);self.assertEqual(o.CONFIG['gpu_benchmark']['minimum_live_stage'],[8192,32])
 def test_n2_attaches_only_explicit_t3_mp12_session(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'swaps.jsonl').write_text('');(p/'exits.jsonl').write_text('')
   self.assertFalse(live.is_m2_session(p))
   (p/'T3_RUN_MANIFEST.json').write_text(json.dumps({'schema':'belial_t3_mp12_session_v1','pools':list(o.c.POOLS)}))
   self.assertTrue(live.is_m2_session(p))
   (p/'T3_RUN_MANIFEST.json').write_text(json.dumps({'schema':'belial_t3_mp12_session_v1','pools':['FARTCOIN']}))
   self.assertFalse(live.is_m2_session(p))
 def test_opencl_handle_normalization(self):
  arr=(ctypes.c_void_p*2)();arr[0]=0x1234;arr[1]=0x5678
  self.assertIsInstance(arr[0],int)
  self.assertEqual(oc._handle_value(arr[0]),0x1234)
  self.assertEqual(oc._handle_value(ctypes.c_void_p(0x5678)),0x5678)
  with self.assertRaises(oc.OpenCLError):oc._handle_value(ctypes.c_void_p(None))
 def test_opencl_loader_falls_back_to_nvidia_impl(self):
  class Fn:
   def __init__(self,rc,count):self.rc=rc;self.count=count
   def __call__(self,_n,_p,pn):ctypes.cast(pn,ctypes.POINTER(ctypes.c_uint))[0]=self.count;return self.rc
  class Lib:pass
  def fake_cdll(name,mode=0):
   x=Lib();x.clGetPlatformIDs=Fn(-1001,0)
   if name!='libOpenCL.so.1':x.clIcdGetPlatformIDsKHR=Fn(0,1)
   return x
  with mock.patch.object(oc.C,'CDLL',side_effect=fake_cdll):
   lib,name,enum,attempts=oc._load_opencl_library('NVIDIA')
  self.assertNotEqual(name,'libOpenCL.so.1')
  self.assertIn('libnvidia-opencl.so.1',name)
  self.assertEqual(enum,'clIcdGetPlatformIDsKHR')
  self.assertTrue(any('clGetPlatformIDs:rc=-1001:platforms=0' in x for x in attempts))
  self.assertTrue(any('clIcdGetPlatformIDsKHR:rc=0:platforms=1' in x for x in attempts))
if __name__=='__main__':unittest.main()
