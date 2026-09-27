import argparse,contextlib,json,threading,time,subprocess,os
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from a51.common import ROOT,Stop,atomic,read_json,now_ms,session_expired

def state(session):
    return {'at_ms':now_ms(),'session':read_json(session/'SESSION.json',{}),'wide':read_json(session/'wide/state.json',{}),'discovery':read_json(session/'discovery/state.json',{}),'baseline_url':read_json(session/'ui/URL.json',{}),'platform':read_json(session/'PLATFORM.json',{})}
def final(session):
    html=(ROOT/'a6/dashboard.html').read_text();data=json.dumps(state(session),ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    (session/'A6_DASHBOARD_FINAL.html').write_text(html.replace('/*FINAL_DATA*/','window.FINAL_A6='+data+';'))
def run(session):
    stop=Stop().install();html=(ROOT/'a6/dashboard.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):self.send_error(403);return
            if self.path=='/':body=html;ct='text/html; charset=utf-8'
            elif self.path=='/state':body=json.dumps(state(session),ensure_ascii=False,allow_nan=False).encode();ct='application/json'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',ct);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
            with contextlib.suppress(BrokenPipeError,ConnectionResetError):self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port)
    atomic(session/'wide_ui/URL.json',{'url':url});print('A6_SCANNER_URL='+url,flush=True)
    if not os.environ.get('BELIAL_A1_NO_BROWSER') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        with contextlib.suppress(Exception):subprocess.Popen(['xdg-open',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    while not stop.value and not session_expired(session):time.sleep(.5)
    server.shutdown();server.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);run(p.parse_args().session)
