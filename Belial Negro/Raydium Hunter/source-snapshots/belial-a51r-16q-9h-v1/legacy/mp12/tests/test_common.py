import unittest,tempfile,json,os,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import common as c
class IOTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_new_json_refuses_overwrite(self):
  p=self.p/'x';c.new_json(p,{'a':1});self.assertRaises(FileExistsError,c.new_json,p,{'a':2});self.assertEqual(json.loads(p.read_text()),{'a':1})
 def test_symlink_refused(self):
  p=self.p/'a';p.symlink_to(self.p/'b');self.assertRaises(RuntimeError,c.safe,p)
 def test_parent_symlink_refused(self):
  (self.p/'dir').mkdir();p=self.p/'alias';p.symlink_to(self.p/'dir');self.assertRaises(RuntimeError,c.safe,p/'x')
 def test_atomic_changes_json(self):p=self.p/'x';c.atomic(p,{'a':1});c.atomic(p,{'a':2});self.assertEqual(json.loads(p.read_text())['a'],2)
 def test_manifest_hash(self):p=self.p/'x';p.write_text('hello');c.verify(self.p,{'x':c.digest(p)})
 def test_manifest_mismatch(self):p=self.p/'x';p.write_text('hello');self.assertRaises(RuntimeError,c.verify,self.p,{'x':'0'*64})
 def test_manifest_path(self):self.assertRaises(ValueError,c.verify,self.p,{'../x':'a'})
 def test_exact_members(self):p=self.p/'x';p.write_text('x');self.assertRaises(RuntimeError,c.verify,self.p,{},True)
 def test_zip_crc_and_hash(self):
  root=self.p/'data';root.mkdir();(root/'a').write_text('ok');out=self.p/'a.zip';h=c.zip_new(root,out)
  self.assertEqual(h,c.digest(out))
  with zipfile.ZipFile(out) as z:self.assertIsNone(z.testzip());self.assertEqual(z.read('data/a'),b'ok')
 def test_zip_never_overwrites(self):
  root=self.p/'data';root.mkdir();out=self.p/'a.zip';out.write_text('preserve');self.assertRaises(FileExistsError,c.zip_new,root,out)
 def test_partial_line_not_consumed(self):
  p=self.p/'x';p.write_bytes(b'{"a":1}');r=c.Reader(p);self.assertEqual(r.read(),[])
  with p.open('ab') as f:f.write(b'\n')
  self.assertEqual(r.read(),[{'a':1}]);r.close()
 def test_reader_truncate(self):
  p=self.p/'x';p.write_bytes(b'{}\n');r=c.Reader(p);r.read();p.write_bytes(b'');self.assertRaises(RuntimeError,r.read);r.close()
 def test_reader_rotate(self):
  p=self.p/'x';p.write_bytes(b'{}\n');r=c.Reader(p);p.rename(self.p/'old');p.write_bytes(b'{}\n');self.assertRaises(RuntimeError,r.read);r.close()
 def test_reader_limit(self):
  p=self.p/'x';p.write_bytes(b'{}\n'*10);r=c.Reader(p);self.assertEqual(len(r.read(2)),2);r.close()
 def test_long_line_fails(self):
  p=self.p/'x';p.write_bytes(b' '*65537+b'\n');r=c.Reader(p);self.assertRaises(RuntimeError,r.read);r.close()
 def test_journal_appends_only(self):
  j=c.Journal(self.p/'journal');j.emit('events',{'i':0});prefix=(j.root/'events.jsonl').read_bytes();j.emit('events',{'i':1});j.close()
  self.assertTrue((j.root/'events.jsonl').read_bytes().startswith(prefix));self.assertEqual(j.rows,2)
 def test_bad_journal_filename_refused(self):j=c.Journal(self.p/'journal');self.assertRaises(ValueError,j.emit,'../x',{});j.close()
if __name__=='__main__':unittest.main()
