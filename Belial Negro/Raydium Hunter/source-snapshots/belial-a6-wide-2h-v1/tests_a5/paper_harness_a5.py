"""A5 production PAPER worker exercised with deterministic local IPC peers."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import a5.paper_worker as worker
from a5.common import ROOT

FIXTURE=ROOT/'tests_a5/fixture_venue_a5.py'

def fixture_command(module,*args):
    if module!='venue_worker':
        raise RuntimeError('TEST_FIXTURE_UNEXPECTED_MODULE:'+str(module))
    if not FIXTURE.is_file():
        raise RuntimeError('A5_FIXTURE_MISSING:'+str(FIXTURE))
    return [sys.executable,'-B',str(FIXTURE),*map(str,args)]

worker.command=fixture_command
worker.public_json=lambda *args:[]
raise SystemExit(worker.run(Path(sys.argv[1]),FIXTURE))
