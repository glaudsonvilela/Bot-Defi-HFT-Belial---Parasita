import json,os,subprocess,sys,tempfile,time,unittest,urllib.request,urllib.error
from pathlib import Path
from a1.common import ROOT,atomic,read_json,now_ms,DURATION_MS,terminate,command,clean_env,heartbeat,digest
from a1.catalog import initial,freeze
from a1.replay import replay
from a1.supervisor import Supervisor,package_result

def wait(fn,seconds=10):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  value=fn()
  if value:return value
  time.sleep(.04)
 raise AssertionError('TIMEOUT_WAITING_FOR_LOCAL_FIXTURE')

class ProcessIsolation(unittest.TestCase):
 def setUp(self):
  self.previous=os.environ.get('BELIAL_A1_NO_BROWSER');os.environ['BELIAL_A1_NO_BROWSER']='1'
 def tearDown(self):
  if self.previous is None:os.environ.pop('BELIAL_A1_NO_BROWSER',None)
  else:os.environ['BELIAL_A1_NO_BROWSER']=self.previous
 def test_ui_crash_spot_quarantine_and_book_gap_do_not_stop_paper(self):
  with tempfile.TemporaryDirectory() as td:
   s=Path(td)/'m2-a1-fixture';s.mkdir();(s/'aggregate/shadow_compare').mkdir(parents=True)
   (s/'aggregate/shadow_compare/events.jsonl').touch();atomic(s/'SESSION.json',dict(phase='PREPARING',duration_ms=DURATION_MS));atomic(s/'INTEGRITY.json',{'ok':True})
   logs=[];procs=[]
   def launch(argv,name):
    f=(s/name).open('w');logs.append(f);p=subprocess.Popen(argv,stdout=f,stderr=f,cwd=ROOT,env=clean_env(),start_new_session=True);procs.append(p);return p
   paper=launch([sys.executable,'-B',str(ROOT/'tests/paper_harness.py'),str(s)],'paper-test.log')
   ui=launch(command('ui_worker','--session',s),'ui-test.log')
   try:
    ready=wait(lambda:read_json(s/'paper/status.json',{}).get('metadata_finished'))
    url=wait(lambda:read_json(s/'ui/URL.json',{}).get('url'))
    with urllib.request.urlopen(url+'health',timeout=2) as r:self.assertTrue(json.load(r)['ok'])
    caps=initial();caps['FARTCOIN'].update(qualified=True,state='QUALIFIED');caps['PUMP'].update(state='RAW_ONLY',reason='TransferHook',qualified=False)
    t=now_ms();atomic(s/'COHORT.json',freeze(caps,t));heartbeat(s/'aggregate/status.json','ACTIVE')
    heartbeat(s/'sources/FARTCOIN/status.json','ACTIVE',generation=0,integrity=True)
    heartbeat(s/'sources/PUMP/status.json','QUARANTINED',generation=0,integrity=False,reason='TransferHook')
    atomic(s/'SESSION.json',dict(phase='CAPTURING',duration_ms=DURATION_MS,started_ms=t,deadline_ms=t+DURATION_MS,deadline_monotonic_ns=time.monotonic_ns()+8_000_000_000))
    wait(lambda:read_json(s/'paper/state.json',{}).get('started_ms'))
    before=read_json(s/'paper/books.json',{})['ingest_messages'];terminate(ui,3)
    # Simulated RPC outage is published only to the spot capability.
    heartbeat(s/'sources/FARTCOIN/status.json','QUARANTINED',generation=0,integrity=False,reason='RPC_DISCONNECTED_FIXTURE')
    wait(lambda:read_json(s/'paper/books.json',{}).get('ingest_messages',0)>before+25)
    self.assertIsNone(paper.poll());self.assertGreater(read_json(s/'paper/books.json')['ingest_messages'],before)
    last=read_json(s/'paper/books.json');self.assertTrue(next(b for b in last['books'] if b['venue']=='hyperliquid')['ready'])
    self.assertGreater(next(b for b in last['books'] if b['venue']=='lighter')['gaps'],0)
    ui2=launch(command('ui_worker','--session',s),'ui-restart.log');wait(lambda:read_json(s/'ui/URL.json',{}).get('pid')==ui2.pid)
    with urllib.request.urlopen(read_json(s/'ui/URL.json')['url'],timeout=2) as r:self.assertIn('Fontes independentes',r.read().decode())
    with (s/'aggregate/shadow_compare/events.jsonl').open('a') as f:f.write(json.dumps(dict(kind='EPISODE_OPEN',episode_id='PUMP-FIXTURE',symbol='PUMP',at_unix_ms=now_ms(),paper_only=True,orders=False))+'\n')
    wait(lambda:read_json(s/'paper/state.json',{}).get('a1_blocked_signals',0)>0)
    self.assertFalse(read_json(s/'paper/state.json')['positions'])
    state=read_json(s/'SESSION.json');state['phase']='STOPPING';atomic(s/'SESSION.json',state);paper.wait(timeout=12)
    self.assertEqual(paper.returncode,0,(s/'paper-test.log').read_text());self.assertTrue(read_json(s/'paper/COMPLETE.json')['ok'])
    result=replay(s/'paper/inputs.jsonl',s/'replay');self.assertEqual(result['output_sha256'],digest(s/'paper/outputs.jsonl'))
    (s/'PROCESS_ISOLATION.json').write_text(json.dumps(dict(passed=True,fixture_only=True,external_network=False,native_rust_executed=False,book_messages=read_json(s/'paper/books.json')['ingest_messages'],paper_output_sha256=result['output_sha256'],ui_restarted=True,spot_failure_isolated=True,book_gap_isolated=True),indent=2))
   finally:
    for p in procs:terminate(p,3)
    for f in logs:f.close()
 def test_supervisor_ui_restart_does_not_require_pool_approval(self):
  with tempfile.TemporaryDirectory() as td:
   s=Path(td);sup=Supervisor(s,base=s)
   try:
    sup.setup();wait(lambda:read_json(s/'ui/URL.json',{}));pid=sup.procs['ui'].pid;terminate(sup.procs['ui'],3)
    platform=sup.publish();self.assertEqual(platform['coverage']['qualified_at_start'],0);self.assertEqual(platform['coverage']['requested'],12)
    self.assertNotEqual(sup.procs['ui'].pid,pid);wait(lambda:read_json(s/'ui/URL.json',{}).get('pid')==sup.procs['ui'].pid)
   finally:
    for p in sup.procs.values():terminate(p,3)
    for f in sup.logs.values():f.close()
 def test_archive_inventories_exported_bytes(self):
  import zipfile,hashlib
  with tempfile.TemporaryDirectory() as td:
   s=Path(td)/'m2-a1-test';s.mkdir();atomic(s/'a.json',{'orders':False});zpath=package_result(s,Path(td)/'out')
   with zipfile.ZipFile(zpath) as z:
    inv=json.loads(z.read('ZIP_SHA256.json'))
    for name,h in inv.items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),h)
if __name__=='__main__':unittest.main()

class AnalysisIsolation(unittest.TestCase):
 def test_live_adapter_duplicate_and_quarantined_source_do_not_reach_n2(self):
  with tempfile.TemporaryDirectory() as td:
   s=Path(td);(s/'aggregate').mkdir();pool='Bzc9NZfMqkXR6fz1DBph7BDf9BroyEf6pnzESP7v5iiw';t=now_ms()
   caps=initial();caps['FARTCOIN'].update(qualified=True,state='QUALIFIED');caps['PUMP'].update(qualified=False,state='RAW_ONLY',reason='TransferHook')
   atomic(s/'COHORT.json',freeze(caps,t));atomic(s/'spot_prep/FARTCOIN/MANIFEST.json',{'pools':[{'pool':pool}]})
   atomic(s/'SESSION.json',dict(phase='CAPTURING',duration_ms=DURATION_MS,started_ms=t,deadline_ms=t+DURATION_MS,deadline_monotonic_ns=time.monotonic_ns()+10_000_000_000))
   atomic(s/'INTEGRITY.json',{'ok':True});heartbeat(s/'sources/FARTCOIN/status.json','ACTIVE',generation=0,integrity=True)
   native=s/'sources/FARTCOIN/m2-FARTCOIN';native.mkdir();(native/'swaps.jsonl').touch();(native/'exits.jsonl').touch()
   row=dict(symbol='FARTCOIN',pool=pool,signature='analysis-fixture-only',slot=1,received_unix_ms=t,side='SELL',log_type=3,direction=2,price_sol_per_meme=1.,meme_qty=1.,quote_sol=1.,pool_meme_reserve=1000.,pool_quote_reserve_sol=1002.)
   with (s/'analysis.log').open('w') as f:
    p=subprocess.Popen(command('analysis_worker','--session',s),stdout=f,stderr=f,cwd=ROOT,env=clean_env(),start_new_session=True)
    try:
     wait(lambda:read_json(s/'aggregate/status.json',{}).get('state')=='ACTIVE')
     with (native/'swaps.jsonl').open('a') as w:w.write(json.dumps(row)+'\n'+json.dumps(row)+'\n')
     wait(lambda:read_json(s/'aggregate/status.json',{}).get('counts',{}).get('swap')==1)
     self.assertEqual(read_json(s/'aggregate/status.json')['counts']['duplicate_input'],1)
     self.assertEqual(read_json(s/'aggregate/m2_tail/state.json')['counts']['marks'],1)
     heartbeat(s/'sources/FARTCOIN/status.json','QUARANTINED',generation=0,integrity=False,reason='RPC_TEST_ONLY')
     row['signature']='unavailable-fixture';row['received_unix_ms']=now_ms()
     with (native/'swaps.jsonl').open('a') as w:w.write(json.dumps(row)+'\n')
     wait(lambda:read_json(s/'aggregate/status.json',{}).get('counts',{}).get('rejected_unavailable_source',0)>=1)
     self.assertEqual(len((s/'aggregate/swaps.jsonl').read_text().splitlines()),1)
     state=read_json(s/'SESSION.json');state['phase']='STOPPING';atomic(s/'SESSION.json',state);p.wait(timeout=5)
     self.assertEqual(p.returncode,0,(s/'analysis.log').read_text());self.assertTrue(read_json(s/'aggregate/COMPLETE.json')['ok'])
    finally:terminate(p,3)
