"""Short local lifecycle fixture: real child process, journal/replay and result ZIP."""
import copy,json,os,sys,tempfile,unittest,zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from a7 import run as runner
from tests_a6.test_core import META
class Lifecycle(unittest.TestCase):
 def test_complete_supervisor_with_read_only_fixtures(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);base=root/'base';base.mkdir();output=root/'out';meta=copy.deepcopy(META);template=meta['symbols'][0];meta['symbols']=[];selected=[]
   for i in range(20):
    symbol='MEME'+str(i)+'USDT';m=copy.deepcopy(template);m.update(symbol=symbol,baseAsset='MEME'+str(i));meta['symbols'].append(m)
    selected.append(dict(symbol=symbol,base=m['baseAsset'],multiplier=1,depth_10bps_usdt=1000,sources=[]))
   native=root/'feed';native.write_text('#!'+sys.executable+'\nimport json,time\nsymbols='+repr([r['symbol'] for r in selected])+'''\nfor i,s in enumerate(symbols):
 t=time.time_ns()//1000000
 print(json.dumps(dict(kind='depth',received_ms=t,generation=1,data=dict(s=s,E=t,u=1,b=[['.9999','1000']],a=[['1.0001','1000']]))),flush=True)
time.sleep(30)
''');native.chmod(0o700)
   def prepare(out,*args):
    from a51.common import atomic
    doc=dict(selected=selected);atomic(out/'UNIVERSE.json',doc);return doc,meta
   class RPC:
    def __init__(self,*args):pass
    def call(self,name,*args):return {'getGenesisHash':'5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d','eth_chainId':'0x38','eth_getBlockByNumber':{'number':'0x1'}}[name]
   class Sensors:
    def __init__(self,pools,sol,bnb,ws,emit,stop):self.emit=emit;self.threads=[]
    def start(self):
     for chain in ('solana','bsc'):self.emit(dict(kind='chain_status',chain=chain,state='CONNECTED'))
   with patch.object(runner,'BASE',base),patch.object(runner,'DURATION_MS',30),patch.object(runner,'RPC',RPC),patch.object(runner,'Sensors',Sensors),patch.object(runner,'prepare',prepare),patch.object(runner,'native_build',return_value=native),patch.object(runner,'active_scans',return_value=[]),patch.object(runner.os,'geteuid',return_value=1000),patch.object(Path,'is_mount',return_value=True),patch.object(runner.shutil,'disk_usage',return_value=SimpleNamespace(free=20*1024**3)),patch.dict(os.environ,{'BELIAL_A1_NO_BROWSER':'1'}):
    self.assertEqual(runner.run(output),0)
   files=list(output.glob('*.zip'));self.assertEqual(len(files),1)
   with zipfile.ZipFile(files[0]) as z:
    prefix=z.namelist()[0].split('/')[0]+'/'
    summary=json.loads(z.read(prefix+'A7_SUMMARY.json'));self.assertTrue(summary['capture_complete']);self.assertFalse(summary['failures'])
    self.assertTrue(json.loads(z.read(prefix+'REPLAY.json'))['ok']);self.assertIn(prefix+'EVIDENCE_MANIFEST_SHA256.json',z.namelist())
if __name__=='__main__':unittest.main()
