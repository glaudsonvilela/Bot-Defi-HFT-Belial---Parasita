"""Local terminal display server. Latest-state coalescing; never used as a market feed."""
from __future__ import annotations
import argparse,threading,socket
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from a5.common import *
from a5.terminal_projection import compact_state,compact_agent

class Bus:
    def __init__(self):
        self.cv=threading.Condition();self.payloads={};self.seq=0;self.clients=0;self.closed=False
        self.max_payload=0;self.published=0
    def publish(self,kind,value):
        data=canonical(value)
        if len(data.encode())>2<<20:raise RuntimeError('UI_PROJECTION_TOO_LARGE')
        with self.cv:
            self.seq+=1;self.payloads[kind]=(self.seq,data);self.published+=1
            self.max_payload=max(self.max_payload,len(data.encode()));self.cv.notify_all()
    def snapshot(self):
        with self.cv:return {k:strict_loads(v[1]) for k,v in self.payloads.items()}

def make_server(bus,html):
    content=Path(html).read_bytes()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def response(self,data,ctype='application/json',status=200):
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; font-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):self.send_error(403);return
            if self.path=='/':self.response(content,'text/html; charset=utf-8');return
            if self.path=='/health':self.response(canonical({'ok':True,'read_only':True,'orders':False,'seq':bus.seq,'clients':bus.clients}).encode());return
            if self.path=='/api/snapshot':self.response(canonical(bus.snapshot()).encode());return
            if self.path!='/events':self.send_error(404);return
            origin=self.headers.get('Origin')
            if origin and origin not in ('http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
                self.send_error(403);return
            with bus.cv:
                if bus.clients>=4:self.send_error(429);return
                bus.clients+=1
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);self.connection.settimeout(1.0)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store')
                self.send_header('X-Accel-Buffering','no');self.end_headers();last={};beat=0.
                while not bus.closed:
                    with bus.cv:
                        updates=sorted([(kind,seq,data) for kind,(seq,data) in bus.payloads.items() if seq!=last.get(kind)],key=lambda x:x[1])
                        if not updates:bus.cv.wait(.1);continue
                    for kind,seq,data in updates:
                        self.wfile.write(f'event: {kind}\nid: {seq}\ndata: {data}\n\n'.encode());last[kind]=seq
                    self.wfile.flush()
            except (OSError,ConnectionError):pass
            finally:
                with bus.cv:bus.clients-=1
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start();return server

def run(session):
    session=Path(session);out=session/'ui';out.mkdir(exist_ok=True);stop=Stop().install();bus=Bus()
    server=make_server(bus,ROOT/'a4/web/dashboard.html');url=f'http://127.0.0.1:{server.server_port}/'
    atomic(out/'URL.json',{'url':url,'pid':os.getpid(),'read_only':True});print('DASHBOARD_URL='+url,flush=True)
    paths={'state':session/'paper/state.json','books':session/'paper/books.json','platform':session/'PLATFORM.json',
           'agent':session/'n2/AGENT_VIEW.json','agent_status':session/'n2/status.json','sampler':session/'price_samples/status.json','ws_relay':session/'ws_relay/status.json'}
    signatures={};last=0;previous=0;projection_error=None
    try:
        while not stop.value:
            for kind,path in paths.items():
                try:mt=path.stat().st_mtime_ns
                except FileNotFoundError:continue
                if signatures.get(kind)==mt:continue
                value=read_json(path)
                if value is None:continue
                if kind=='state':value=compact_state(value)
                elif kind=='agent':value=compact_agent(value)
                bus.publish(kind,value);signatures[kind]=mt
            at=now_ms()
            if at-last>=1000:
                metrics={'at_ms':at,'display_updates_per_second':bus.published-previous,'maximum_payload_bytes':bus.max_payload,'clients':bus.clients,
                         'coalescing':'LATEST_VIEW_ONLY_MARKET_LEDGER_UNTOUCHED','push_target_hz':25,'orders':False}
                bus.publish('ui',metrics);heartbeat(out/'status.json','SERVING',**{k:v for k,v in metrics.items() if k!='at_ms'})
                previous=bus.published;last=at
            if (session/'UI_STOP').exists():break
            time.sleep(.04)
    except Exception as exc:projection_error=str(exc);heartbeat(out/'status.json','FAILED',error=projection_error);raise
    finally:
        bus.closed=True
        with bus.cv:bus.cv.notify_all()
        server.shutdown();server.server_close()
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
