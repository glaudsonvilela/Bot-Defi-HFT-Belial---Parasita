import copy,tempfile,unittest,sys
from pathlib import Path
from a51.common import ROOT,DURATION_MS,atomic,digest
sys.path.insert(0,str(ROOT/'legacy/perp/tests'))
from test_books_paper import CFG,hl,signal,meta
from a51.paper_controller import Controller
from a51.catalog import initial,freeze
from a51.journal import Journal
from a51.replay import replay
class RetryReplayTests(unittest.TestCase):
    def test_waiting_signal_fills_after_new_book_and_replays_exactly(self):
        t=1000000
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);outputs=Journal(root/'outputs.jsonl');inputs=Journal(root/'inputs.jsonl');c=Controller(copy.deepcopy(CFG),lambda k,r:outputs.append({'kind':k,'body':r}));seq=0
            def event(kind,body,at):
                nonlocal seq
                seq+=1;e=dict(kind=kind,body=body,at_ms=at,source='fixture',epoch=0,sequence=seq);inputs.append(e);c.apply(e)
            event('markets',dict(venue='hyperliquid',markets=[meta()]),t)
            caps=initial();caps['FARTCOIN'].update(qualified=True,state='QUALIFIED',identity=True,price=True,actor=True)
            event('cohort',dict(cohort=freeze(caps,t),started_ms=t,duration_ms=DURATION_MS),t)
            event('source_status',dict(symbol='FARTCOIN',status=dict(state='ACTIVE',at_ms=t,actor_ready=True,ready_since_ms=t,integrity=True,generation=0)),t)
            event('signal',signal(t),t);self.assertEqual(len(c.engine.waiting_signals),1)
            event('book',hl(t+500),t+500);self.assertFalse(c.engine.positions)
            event('book',hl(t+650),t+650);self.assertEqual(len(c.engine.positions),6)
            event('finalize',{},t+700);inputs.close();outputs.close();atomic(root/'CONFIG_EFFECTIVE.json',CFG)
            result=replay(root/'inputs.jsonl',root/'replay');self.assertEqual(result['output_sha256'],digest(root/'outputs.jsonl'))
