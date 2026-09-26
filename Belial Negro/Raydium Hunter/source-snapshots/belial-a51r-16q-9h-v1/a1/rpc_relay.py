"""Loopback-only read RPC relay, with one shared rate/queue budget for all sensors.
No mutation method is exposed. Upstream choice remains pinned for the session.
"""
from __future__ import annotations
import argparse,collections,json,secrets,socket,threading,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from a1.common import *
add_legacy('runner')
from rpc_transport import Client,MAINNET_GENESIS,NoRedirect,RpcFailure
import urllib.request
ALLOWED={'getAccountInfo','getMultipleAccounts','getTransaction','getSignaturesForAddress','getSlot','getGenesisHash'}

class Budget:
    def __init__(self,rps=3,capacity=16):
        self.interval=1/rps;self.lock=threading.Lock();self.next=0.;self.slots=threading.BoundedSemaphore(capacity)
        self.calls=0;self.errors=0;self.blocked=False;self.reason=None
    def pace(self):
        with self.lock:
            delay=max(0,self.next-time.monotonic())
            if delay:time.sleep(delay)
            self.next=time.monotonic()+self.interval
            self.calls+=1

def make_relay(url,log,token=None,client_factory=Client):
    token=token or secrets.token_hex(24);budget=Budget()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*_):pass
        def send_json(self,status,data):
            raw=canonical(data).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            self.connection.settimeout(25)
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost') or self.path!='/'+token:
                self.send_json(403,{'error':'LOCAL_READ_RPC_ONLY'});return
            try:n=int(self.headers.get('Content-Length','0'))
            except ValueError:n=0
            if not 0<n<=65536:self.send_json(400,{'error':'BODY_SIZE'});return
            try:body=strict_loads(self.rfile.read(n))
            except (ValueError,OSError):self.send_json(400,{'error':'JSON_INVALID'});return
            ident=body.get('id') if isinstance(body,dict) else None
            method=body.get('method') if isinstance(body,dict) else None
            if method not in ALLOWED:
                self.send_json(403,{'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'READ_METHOD_NOT_ALLOWED'}});return
            if budget.blocked or not budget.slots.acquire(blocking=False):
                self.send_json(503,{'jsonrpc':'2.0','id':ident,'error':{'code':-32005,'message':'SPOT_RELAY_BLOCKED_OR_QUEUE_FULL'}});return
            try:
                client=client_factory(url,log,deadline=time.monotonic()+20)
                original_open=getattr(client,'open_url',None)
                if original_open:
                    def paced_open(request,**kwargs):
                        budget.pace()
                        if budget.blocked:raise RpcFailure('RPC_ACCESS_DENIED_GLOBALLY',url,method,status=403)
                        return original_open(request,**kwargs)
                    client.open_url=paced_open
                else:budget.pace()
                result=client.call(method,body.get('params',[]))
                self.send_json(200,{'jsonrpc':'2.0','id':ident,'result':result})
            except Exception as e:
                budget.errors+=1;budget.reason=str(e)
                # Permanent access denial is not hammered again by eleven workers.
                if getattr(e,'status',None) in (401,403):budget.blocked=True
                self.send_json(503,{'jsonrpc':'2.0','id':ident,'error':{'code':-32005,'message':'UPSTREAM_READ_FAILED'}})
            finally:budget.slots.release()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    return server,budget,'http://127.0.0.1:'+str(server.server_port)+'/'+token

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();stop=Stop().install()
    conn=read_json(a.session/'spot_prep/CONNECTION.json',{})
    if conn.get('genesis')!=MAINNET_GENESIS:raise RuntimeError('MAINNET_CONNECTION_NOT_APPROVED')
    out=a.session/'rpc_relay';out.mkdir(exist_ok=True)
    server,budget,url=make_relay(conn['rpc'],out/'upstream.jsonl')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    atomic(out/'ENDPOINT.json',{'url':url,'read_only':True,'rps_budget':3,'upstream_profile':conn['profile']},durable=True)
    try:
        while not stop.value and not session_expired(a.session):
            heartbeat(out/'status.json','BLOCKED' if budget.blocked else 'ACTIVE',calls=budget.calls,errors=budget.errors,reason=budget.reason)
            time.sleep(.5)
    finally:server.shutdown();server.server_close()
    return 0
if __name__=='__main__':raise SystemExit(main())
