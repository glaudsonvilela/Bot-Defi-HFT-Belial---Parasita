from pathlib import Path
import tempfile,unittest,json,os,subprocess,sys,time,shutil
from unittest import mock
from array import array
from helpers import *
from a3.agent_io import DeferredTail
from a3.causal_observer import CausalObserver
from a3.n2_worker import AgentJournal,Pump
from a3 import model_store
import observer as o, live

class ReaderTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'late.jsonl';self.r=DeferredTail(self.p)
    def tearDown(self):self.r.close();self.tmp.cleanup()
    def test_missing_does_not_open_or_raise(self):self.assertEqual(self.r.read(),[]);self.assertFalse(self.r.available());self.assertFalse(self.r.caught_up())
    def test_late_producer_creation(self):self.assertEqual(self.r.read(),[]);self.p.write_text('{"x":1}\n');self.assertEqual(self.r.read()[0][1],{'x':1})
    def test_partial_line_does_not_advance(self):
        self.p.write_text('{"x":');self.assertEqual(self.r.read(),[]);self.assertEqual(self.r.view()['offset'],0)
        with self.p.open('a') as f:f.write('1}\n')
        self.assertEqual(self.r.read()[0][1]['x'],1)
    def test_append_read_once(self):self.p.write_text('{}\n');self.assertEqual(len(self.r.read()),1);self.assertEqual(self.r.read(),[])
    def test_rotate_fails(self):self.p.write_text('{}\n');self.r.read();self.p.unlink();self.p.write_text('{}\n');self.assertRaises(RuntimeError,self.r.read)
    def test_truncate_fails(self):self.p.write_text('{}\n');self.r.read();self.p.write_text('');self.assertRaises(RuntimeError,self.r.read)
    def test_removed_fails(self):self.p.write_text('{}\n');self.r.read();self.p.unlink();self.assertRaises(RuntimeError,self.r.read)
    def test_symlink_refused(self):other=self.p.with_suffix('.real');other.write_text('{}\n');self.p.symlink_to(other);self.assertRaises(Exception,self.r.read)
    def test_invalid_json_refused(self):self.p.write_text('{bad}\n');self.assertRaises(Exception,self.r.read)
    def test_nonfinite_refused(self):self.p.write_text('{"x":NaN}\n');self.assertRaises(Exception,self.r.read)
    def test_list_refused(self):self.p.write_text('[]\n');self.assertRaises(Exception,self.r.read)
    def test_maxline_refused(self):self.r.max_line=8;self.p.write_text('{"long":10}\n');self.assertRaises(Exception,self.r.read)
    def test_batch_bounds(self):self.p.write_text('{}\n'*10);self.assertEqual(len(self.r.read(2)),2);self.assertFalse(self.r.caught_up());self.assertEqual(len(self.r.read()),8)
    def test_view_does_not_fake_readiness(self):self.assertEqual(self.r.view(),{'exists':False,'opened':False,'rows_read':0,'offset':0,'bytes':None})

class ForecastTests(unittest.TestCase):
    def make(self,start=1_000_000,delay=0):
        j=J();core=FakeCore();m=o.Model(core,8192,32,j);clock=[start]
        class TimedCore:
            def predict_batch(self,*args):clock[0]+=delay;return core.predict_batch(*args)
            def train(self,*args):return core.train(*args)
        m.core=TimedCore();ob=CausalObserver(m,j,'SYNTHETIC',clock=lambda:clock[0],deadline=start+1900000);history(ob,start);return j,core,m,ob,clock
    def test_publication_after_inference(self):
        j,core,m,ob,clk=self.make(delay=123);ob.feed_exit(ex(clk[0]),clk[0]);r=next(r for n,r,_ in j.rows if n=='predictions');self.assertEqual(r['published_ms']-r['features_frozen_ms'],123);self.assertFalse(r['approved_for_decisions'])
    def test_gpu_nan_prediction_stops_agent(self):
        j,_,m,ob,clk=self.make();m.core.predict_batch=lambda *args:[[float('nan')]*32]
        self.assertRaisesRegex(RuntimeError,'GPU_PREDICTION_INVALID',ob.feed_exit,ex(clk[0]),clk[0])
    def test_gpu_wrong_shape_stops_agent(self):
        j,_,m,ob,clk=self.make();m.core.predict_batch=lambda *args:[[.5]*2]
        self.assertRaisesRegex(RuntimeError,'GPU_PREDICTION_INVALID',ob.feed_exit,ex(clk[0]),clk[0])
    def test_gpu_out_of_range_stops_agent(self):
        j,_,m,ob,clk=self.make();m.core.predict_batch=lambda *args:[[2.]*32]
        self.assertRaisesRegex(RuntimeError,'GPU_PREDICTION_INVALID',ob.feed_exit,ex(clk[0]),clk[0])
    def test_no_retroactive_start(self):
        j,_,_,ob,clk=self.make(delay=200);ob.feed_exit(ex(clk[0]),clk[0]);self.assertEqual(next(iter(ob.samples.values())).start,clk[0])
    def test_duplicate_exit_not_forecast_twice(self):
        j,_,_,ob,clk=self.make();ob.feed_exit(ex(clk[0]),clk[0]);ob.feed_exit(ex(clk[0]),clk[0]);self.assertEqual(ob.counts['predictions'],1)
    def test_future_exit_rejected(self):j,_,_,ob,clk=self.make();ob.feed_exit(ex(clk[0]+1000),clk[0]);self.assertEqual(ob.counts.get('predictions',0),0)
    def test_stale_exit_not_backfilled(self):j,_,_,ob,clk=self.make();ob.feed_exit(ex(clk[0]-100000),clk[0]);self.assertEqual(ob.counts.get('predictions',0),0)
    def test_backfill_explicit_refused(self):j,_,_,ob,clk=self.make();r=ex(clk[0]);r['source']='BACKFILL';ob.feed_exit(r,clk[0]);self.assertEqual(ob.counts.get('predictions',0),0)
    def test_source_ineligible_refused(self):j,_,_,ob,clk=self.make();r=ex(clk[0]);r['paper_entry_eligible']=False;ob.feed_exit(r,clk[0]);self.assertEqual(ob.counts.get('predictions',0),0)
    def test_after_deadline_not_published(self):j,_,_,ob,clk=self.make(delay=123);ob.deadline=clk[0]+100;ob.feed_exit(ex(clk[0]),clk[0]);self.assertFalse(any(n=='predictions' for n,_,_ in j.rows))
    def test_journal_failure_not_admitted_to_labels(self):
        j,_,_,ob,clk=self.make();j.write=lambda *args:(_ for _ in ()).throw(OSError('DISK'))
        self.assertRaises(OSError,ob.feed_exit,ex(clk[0]),clk[0]);self.assertEqual(len(ob.samples),0)
    def test_long_horizons_actually_label(self):
        j,core,m,ob,clk=self.make();start=clk[0];ob.feed_exit(ex(start),start)
        for sec in range(5,1801,5):
            clk[0]=start+sec*1000;ob.feed_swap(swap(clk[0],1 if sec<1200 else .99,str(sec)),clk[0]);ob.advance(clk[0])
        labs=[r for n,r,_ in j.rows if n=='labels'];self.assertEqual(len(labs),32);self.assertTrue(all(r['valid'] for r in labs));self.assertEqual(core.train_calls,32)
        self.assertEqual(next(r for r in labs if r['head_id']=='SHORT_50BP_300S')['label'],0);self.assertEqual(next(r for r in labs if r['head_id']=='SHORT_50BP_1800S')['label'],1)
    def test_missing_gap_stays_censored(self):
        j,core,_,ob,clk=self.make();ob.feed_exit(ex(clk[0]),clk[0]);ob.advance(clk[0]+60000);self.assertEqual(core.train_calls,0);self.assertTrue(all(r['label'] is None for n,r,_ in j.rows if n=='labels'))
    def test_finishing_window_not_zero_label(self):
        j,core,_,ob,clk=self.make();ob.feed_exit(ex(clk[0]),clk[0]);ob.finalize(clk[0]+1000);self.assertEqual(core.train_calls,0);self.assertTrue(all(r['label'] is None for n,r,_ in j.rows if n=='labels'))
    def test_all_24_symbols_forecast_without_new_strategy(self):
        j,core,m,ob,clk=self.make()
        for s in o.c.POOLS:history(ob,clk[0],s);ob.feed_exit(ex(clk[0],'same-source',s),clk[0])
        self.assertEqual(ob.counts['predictions'],24);self.assertEqual(core.train_calls,0)

class PumpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Path(self.tmp.name);(self.s/'aggregate').mkdir();(self.s/'price_samples').mkdir();self.j=J();self.core=FakeCore();self.model=o.Model(self.core,8192,32,self.j);self.ob=CausalObserver(self.model,self.j,'TEST',clock=lambda:1000000);self.p=Pump(self.s,self.ob)
    def tearDown(self):self.p.close();self.tmp.cleanup()
    def inputs(self):
        for p in self.p.readers.values():p.path.touch()
    def test_waits_for_all_channels_without_crashing(self):self.assertEqual(self.p.step(1000000,2000000),0);self.assertEqual(set(self.p.waiting),{'swap','exit','sample'})
    def test_does_not_consume_signal_while_sample_file_absent(self):
        append(self.s/'aggregate/exits.jsonl',ex(1000000));(self.s/'aggregate/swaps.jsonl').touch();self.assertEqual(self.p.step(1000000,2000000),0);self.assertEqual(self.p.readers['exit'].rows,0)
    def test_late_channels_start_pipeline(self):
        self.assertEqual(self.p.step(1000000,2000000),0);self.inputs()
        for k in range(10):append(self.s/'aggregate/swaps.jsonl',swap(991000+k*900,1,str(k)))
        append(self.s/'aggregate/exits.jsonl',ex(1000000));self.assertEqual(self.p.step(1000000,2000000),11);self.assertEqual(self.ob.counts['predictions'],1)
    def test_post_deadline_rejected(self):self.inputs();append(self.s/'aggregate/swaps.jsonl',swap(3000000));self.p.step(4000000,2000000);self.assertEqual(self.p.late_rows,1)
    def test_future_payload_refused(self):self.inputs();append(self.s/'aggregate/swaps.jsonl',swap(1500000));self.p.step(1000000,2000000);self.assertEqual(self.p.future_rows,1)
    def test_drain_no_new_forecast(self):self.inputs();history(self.ob,1000000);append(self.s/'aggregate/exits.jsonl',ex(1000000));self.p.step(1000000,2000000,False);self.assertEqual(self.ob.counts.get('predictions',0),0)

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.runtime=self.p/'n2';self.runtime.mkdir();self.core=FakeCore();self.model=o.Model(self.core,8192,32,J());self.store=self.p/'store'
    def tearDown(self):self.tmp.cleanup()
    def save(self):live.save_state(self.core,self.model,self.runtime);return model_store.publish(self.runtime,'m2-a3-synthetic',self.store)
    def restore(self,cutoff=10**15):return model_store.restore(self.core,self.model,self.runtime,live,self.store,cutoff)
    def dir(self):return self.store/model_store.contract()['sha256']/'m2-a3-synthetic'
    def test_fresh_when_no_checkpoint(self):self.assertFalse(self.restore()['loaded'])
    def test_explicit_fresh_ignores_previous_generation(self):self.save();self.assertFalse(model_store.restore(self.core,self.model,self.runtime,live,self.store,10**15,'fresh')['loaded'])
    def test_roundtrip_preserves_training_resets_evaluation(self):
        st=self.model.stats[('FARTCOIN',0)];st.update(updates=3,positives=2,eval_n=3,brier_sum=1,primary_eval_n=3);self.model.last_primary[('FARTCOIN',0)]=1;self.core.weights[0]=.75;self.save();self.core.weights[0]=0
        self.assertTrue(self.restore()['loaded']);self.assertAlmostEqual(self.core.weights[0],.75);self.assertEqual(st['updates'],3);self.assertEqual(st['primary_eval_n'],0);self.assertEqual(self.model.last_primary,{})
    def test_invalid_mode_fails(self):self.assertRaises(ValueError,model_store.restore,self.core,self.model,self.runtime,live,self.store,None,'anything')
    def test_generation_immutable(self):self.save();self.assertRaises(RuntimeError,self.save)
    def test_generation_needs_a3_identity(self):live.save_state(self.core,self.model,self.runtime);self.assertRaises(ValueError,model_store.publish,self.runtime,'m2-a2-old',self.store)
    def test_future_checkpoint_rejected(self):self.save();self.assertRaises(RuntimeError,self.restore,0)
    def test_weight_corruption_rejected(self):self.save();(self.dir()/'model.weights.bin').write_bytes(b'changed');self.assertRaises(RuntimeError,self.restore)
    def test_missing_meta_rejected(self):self.save();(self.dir()/'model.meta.json').unlink();self.assertRaises(Exception,self.restore)
    def test_incomplete_rejected(self):
        self.save();p=self.dir()/'CONTRACT.json';v=json.loads(p.read_text());v['complete']=False;p.write_text(json.dumps(v));self.assertRaises(RuntimeError,self.restore)
    def test_contract_mismatch_rejected(self):
        self.save();p=self.dir()/'CONTRACT.json';v=json.loads(p.read_text());v['contract']['revision']='X';p.write_text(json.dumps(v));self.assertRaises(RuntimeError,self.restore)
    def test_path_traversal_refused(self):
        self.save();p=self.dir().parent/'LATEST.json';p.write_text(json.dumps({'generation':'../outside'}));self.assertRaises(RuntimeError,self.restore)
    def test_only_known_contract_discovered(self):
        self.save();other=self.store/'unknown';other.mkdir();(other/'LATEST.json').write_text('{}');self.assertTrue(self.restore()['loaded'])
    def test_metadata_shape_rejected_even_if_rehashed(self):
        self.save();p=self.dir()/'model.meta.json';v=json.loads(p.read_text());v['dimension']=123;p.write_text(json.dumps(v));q=self.dir()/'CONTRACT.json';m=json.loads(q.read_text());m['files'][p.name]=model_store.digest(p);q.write_text(json.dumps(m));self.assertRaises(RuntimeError,self.restore)
    def test_nonfinite_weights_rejected_even_if_rehashed(self):
        self.core.weights[0]=float('nan');self.save();self.assertRaises(RuntimeError,self.restore)
    def test_symlink_generation_rejected(self):
        self.save();d=self.dir();d.rename(d.with_name('real'));d.symlink_to(d.with_name('real'),target_is_directory=True);self.assertRaises(Exception,self.restore)

class JournalTests(unittest.TestCase):
    def test_true_counters_and_primary_brier(self):
        with tempfile.TemporaryDirectory() as d:
            j=AgentJournal(Path(d)/'run');model=o.Model(FakeCore(),8192,32,j);ob=CausalObserver(model,j,'TEST',clock=lambda:1000000);history(ob,1000000);ob.feed_exit(ex(1000000),1000000)
            for ms in [1001000,1005000,1009000]:ob.feed_swap(swap(ms,.99),ms)
            ob.advance(1010000);view=j.view(model);self.assertEqual(view['horizons']['10']['updates'],3);self.assertEqual(view['evaluation']['primary_scored_head_labels'],3);self.assertFalse(view['evaluation']['trading_improvement_proven']);self.assertEqual(view['evaluation']['brier'],.25)
    def test_journal_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            j=AgentJournal(Path(d)/'run');self.assertRaises(FileExistsError,AgentJournal,Path(d)/'run')

class WorkerProcessTest(unittest.TestCase):
    def test_production_worker_waits_for_delayed_producer_and_trains(self):
        with tempfile.TemporaryDirectory() as d:
            env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
            p=subprocess.run([sys.executable,'-B',str(ROOT/'tests_a3/worker_fixture.py'),d],env=env,cwd=ROOT,capture_output=True,text=True,timeout=35)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr);s=Path(d)/'m2-a3-synthetic-delayed';n=json.loads((s/'n2/COMPLETE.json').read_text());self.assertTrue(n['ok']);self.assertTrue(n['qualification']['prospective_pipeline_demonstrated']);self.assertGreaterEqual(n['qualification']['updates'],3)
            lifecycle=[json.loads(x) for x in (s/'n2/prospective/lifecycle.jsonl').read_text().splitlines()];self.assertFalse(any(r['kind']=='A3_AGENT_FAILED' for r in lifecycle));self.assertEqual(n['checkpoint']['published'],True)
