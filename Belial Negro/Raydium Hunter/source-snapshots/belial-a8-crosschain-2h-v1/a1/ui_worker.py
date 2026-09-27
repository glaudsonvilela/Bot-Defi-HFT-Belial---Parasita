"""Read-only loopback dashboard consumer. Its process is disposable, not the ledger."""
from __future__ import annotations
import argparse,threading
from a1.common import *
add_legacy('perp')
import serve

def run(session):
    session=Path(session);out=session/'ui';out.mkdir(exist_ok=True);stop=Stop().install();serve.STOP.clear()
    bus=serve.Bus();server=serve.make_server(bus,ROOT/'a1/web/dashboard.html');url=f'http://127.0.0.1:{server.server_port}/'
    atomic(out/'URL.json',{'url':url,'pid':os.getpid(),'read_only':True});print('DASHBOARD_URL='+url,flush=True)
    paths={'state':session/'paper/state.json','books':session/'paper/books.json','platform':session/'PLATFORM.json'};mtimes={};last=0
    try:
        while not stop.value:
            for kind,path in paths.items():
                try:mt=path.stat().st_mtime_ns
                except FileNotFoundError:continue
                if mtimes.get(kind)!=mt:
                    data=read_json(path)
                    if data is not None:bus.publish(kind,data);mtimes[kind]=mt
            if now_ms()-last>=1000:heartbeat(out/'status.json','SERVING',clients=bus.clients,consumer_only=True);last=now_ms()
            if (session/'UI_STOP').exists():break
            time.sleep(.04)
    finally:serve.STOP.set();server.shutdown();server.server_close()
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
