"""Runs the production Python PAPER worker with synthetic IPC peers. No external network."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import a3.paper_worker as worker
from a3.common import ROOT
worker.command=lambda module,*args:[sys.executable,'-B',str(ROOT/'tests/fixture_venue.py'),*map(str,args)]
worker.public_json=lambda *args:[]
raise SystemExit(worker.run(Path(sys.argv[1]),ROOT/'tests/fixture_venue.py'))
