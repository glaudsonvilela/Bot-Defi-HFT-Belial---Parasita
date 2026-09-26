import hashlib,json,tempfile,unittest
from pathlib import Path
from a1.common import ROOT,atomic,now_ms
from a1.verify import verify_files
from a1.supervisor import observation_available
class ReleaseSafety(unittest.TestCase):
 def sealed(self,p):
  (p/'a.txt').write_text('Belial PAPER')
  atomic(p/'MANIFEST_SHA256.json',{'schema':'belial_a1_package_v1','files':{'a.txt':hashlib.sha256(b'Belial PAPER').hexdigest()}})
 def test_exact_manifest(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);self.sealed(p);self.assertEqual(verify_files(p),1)
 def test_changed_content_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);self.sealed(p);(p/'a.txt').write_text('changed')
   with self.assertRaisesRegex(RuntimeError,'HASH_MISMATCH'):verify_files(p)
 def test_extra_file_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);self.sealed(p);(p/'extra').touch()
   with self.assertRaisesRegex(RuntimeError,'MEMBERS_CHANGED'):verify_files(p)
 def test_symlink_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);self.sealed(p);(p/'link').symlink_to(p/'a.txt')
   with self.assertRaisesRegex(RuntimeError,'SYMLINK'):verify_files(p)
 def test_no_sources_is_not_a_capture(self):
  with tempfile.TemporaryDirectory() as td:self.assertFalse(observation_available(Path(td),{'qualified_at_start':[]}))
 def test_one_qualified_does_not_require_twelve(self):
  with tempfile.TemporaryDirectory() as td:self.assertTrue(observation_available(Path(td),{'qualified_at_start':['FARTCOIN']}))
 def test_book_only_can_observe_without_spot(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);atomic(p/'paper/books.json',{'at_ms':now_ms(),'books':[{'ready':True}]})
   self.assertTrue(observation_available(p,{'qualified_at_start':[]}))
 def test_stale_book_does_not_start_empty_experiment(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);atomic(p/'paper/books.json',{'at_ms':now_ms()-3000,'books':[{'ready':True}]})
   self.assertFalse(observation_available(p,{'qualified_at_start':[]}))
 def test_no_science_constants_changed_by_installer(self):
  text=(ROOT/'a1/install.py').read_text();self.assertNotIn('pkill',text);self.assertNotIn('systemctl',text);self.assertIn('verify_files(destination)',text)
if __name__=='__main__':unittest.main()
