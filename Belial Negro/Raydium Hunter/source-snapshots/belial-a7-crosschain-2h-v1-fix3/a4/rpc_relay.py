"""Bounded shared RPC. Positive cache + single-flight, provider-wide cooldown.
Upstream remains pinned after preflight. No rotating providers to evade 429/403.
"""
from __future__ import annotations
import argparse,collections,json,secrets,threading,time,urllib.error
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from a4.common import *
add_legacy('runner')
from rpc_transport import Client,MAINNET_GENESIS,RpcFailure,retry_after_seconds
ALLOWED={'getAccountInfo','getMultipleAccounts','getTransaction','getSignaturesForAddress','getSlot','getGenesisHash'}

class Budget:
    def __init__(self,rps=3,capacity=16,clock=None,sleep=None):
        self.clock=clock or time.monotonic;self.sleep=sleep or time.sleep
        self.base_interval=1/rps;self.interval=self.base_interval;self.lock=threading.Lock();self.next=0.;self.cooldown=0.
        self.slots=threading.BoundedSemaphore(capacity);self.calls=0;self.errors=0;self.blocked=False;self.reason=None
        self.rate_limits=0;self.denials=0;self.shed=0;self.cache_hits=0;self.coalesced=0;self.last_rate_limit=0.
    def pace(self,deadline):
        # Serialize only scheduling, never HTTP. Recheck global cooldown under
        # the lock immediately before a send; old reservations cannot burst.
        while True:
            with self.lock:
                if self.blocked:raise RuntimeError('UPSTREAM_ACCESS_BLOCKED')
                at=self.clock();due=max(at,self.next,self.cooldown)
                if due>=deadline:raise TimeoutError('RPC_SHARED_BUDGET_DEADLINE')
                wait=due-at
                if wait<=0:
                    if at-self.last_rate_limit>60:self.interval=max(self.base_interval,self.interval*.98)
                    self.next=at+self.interval;self.calls+=1;return
            self.sleep(min(wait,.25))
    def failure(self,status,retry_after=None):
        with self.lock:
            if status in (401,403):self.denials+=1;self.blocked=True;self.reason='ACCESS_DENIED_NO_RETRY'
            if status==429:
                self.rate_limits+=1;self.last_rate_limit=self.clock();self.interval=min(2.,max(self.base_interval,self.interval*1.5))
                self.cooldown=max(self.cooldown,self.clock()+max(1.,retry_after_seconds(retry_after)))
    def view(self):
        return dict(calls=self.calls,errors=self.errors,rate_limits=self.rate_limits,access_denials=self.denials,
                    shed_requests=self.shed,cache_hits=self.cache_hits,coalesced=self.coalesced,
                    max_rps=1/self.base_interval,current_target_rps=1/self.interval,
                    cooldown_remaining_seconds=max(0.,self.cooldown-self.clock()),upstream_pinned=True)

class SharedReads:
    def __init__(self,url,log,budget,client_factory=Client,clock=None):
        self.url=url;self.log=log;self.budget=budget;self.factory=client_factory;self.clock=clock or time.monotonic
        self.lock=threading.Lock();self.cache=collections.OrderedDict();self.inflight={};self.bytes=0
    def _key(self,method,params):return canonical([method,params])
    def _ttl(self,method,result):
        if method=='getTransaction':return 30. if result is not None else .35
        if method=='getAccountInfo':return .25 if result is not None else 0.
        # Price samples must see a NEW RPC snapshot, not an old cached context slot.
        return 0.
    def call(self,method,params):
        key=self._key(method,params);deadline=self.clock()+18.
        with self.lock:
            found=self.cache.get(key)
            if found and found[0]>self.clock():self.budget.cache_hits+=1;self.cache.move_to_end(key);return found[1]
            if found:self.bytes-=found[2];del self.cache[key]
            flight=self.inflight.get(key)
            if flight:self.budget.coalesced+=1;owner=False
            else:flight={'event':threading.Event()};self.inflight[key]=flight;owner=True
        if not owner:
            if not flight['event'].wait(max(0.,deadline-self.clock())):raise TimeoutError('RPC_SINGLE_FLIGHT_TIMEOUT')
            if 'error' in flight:raise flight['error']
            return flight['result']
        try:
            if self.budget.blocked or not self.budget.slots.acquire(blocking=False):
                self.budget.shed+=1;raise RuntimeError('RPC_SHARED_QUEUE_FULL_OR_ACCESS_BLOCKED')
            try:
                client=self.factory(self.url,self.log,deadline=deadline);original=getattr(client,'open_url',None)
                if original:
                    def paced(request,**kwargs):
                        self.budget.pace(deadline)
                        kwargs['timeout']=min(float(kwargs.get('timeout',10)),max(.05,deadline-self.clock()))
                        try:return original(request,**kwargs)
                        except urllib.error.HTTPError as e:
                            self.budget.failure(e.code,e.headers.get('Retry-After') if e.headers else None);raise
                    client.open_url=paced
                else:self.budget.pace(deadline)
                result=client.call(method,params)
            finally:self.budget.slots.release()
            flight['result']=result;ttl=self._ttl(method,result)
            if ttl:
                size=len(canonical(result).encode())
                with self.lock:
                    if size<=8*1024*1024:
                        self.cache[key]=(self.clock()+ttl,result,size);self.bytes+=size
                        while len(self.cache)>1024 or self.bytes>64*1024*1024:
                            _,(_,_,n)=self.cache.popitem(last=False);self.bytes-=n
            return result
        except Exception as e:
            self.budget.errors+=1;self.budget.reason=str(e);flight['error']=e
            if getattr(e,'status',None) in (401,403):self.budget.failure(e.status)
            raise
        finally:
            flight['event'].set()
            with self.lock:self.inflight.pop(key,None)

def make_relay(url,log,token=None,client_factory=Client):
    token=token or secrets.token_hex(24);budget=Budget();reads=SharedReads(url,log,budget,client_factory)
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*_):pass
        def send_json(self,status,data):
            raw=canonical(data).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            with contextlib.suppress(BrokenPipeError,ConnectionResetError):self.wfile.write(raw)
        def do_POST(self):
            self.connection.settimeout(24)
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost') or self.path!='/'+token:
                self.send_json(403,{'error':'LOCAL_READ_RPC_ONLY'});return
            try:n=int(self.headers.get('Content-Length','0'))
            except ValueError:n=0
            if not 0<n<=65536:self.send_json(400,{'error':'BODY_SIZE'});return
            try:body=strict_loads(self.rfile.read(n))
            except (ValueError,OSError):self.send_json(400,{'error':'JSON_INVALID'});return
            ident=body.get('id') if isinstance(body,dict) else None;method=body.get('method') if isinstance(body,dict) else None
            if method not in ALLOWED:
                self.send_json(403,{'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'READ_METHOD_NOT_ALLOWED'}});return
            try:
                result=reads.call(method,body.get('params',[]));self.send_json(200,{'jsonrpc':'2.0','id':ident,'result':result})
            except Exception:
                self.send_json(503,{'jsonrpc':'2.0','id':ident,'error':{'code':-32005,'message':'READ_TEMPORARILY_UNAVAILABLE_NO_EVENT_APPROVAL'}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    return server,budget,'http://127.0.0.1:'+str(server.server_port)+'/'+token

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();stop=Stop().install()
    conn=read_json(a.session/'spot_prep/CONNECTION.json',{})
    if conn.get('genesis')!=MAINNET_GENESIS:raise RuntimeError('MAINNET_CONNECTION_NOT_APPROVED')
    out=a.session/'rpc_relay';out.mkdir(exist_ok=True)
    server,budget,url=make_relay(conn['rpc'],out/'upstream.jsonl');threading.Thread(target=server.serve_forever,daemon=True).start()
    atomic(out/'ENDPOINT.json',{'url':url,'read_only':True,'rps_budget':3,'upstream_profile':conn['profile'],'cache_positive_tx_seconds':30,'live_provider_rotation':False},durable=True)
    try:
        while not stop.value and not session_expired(a.session):
            heartbeat(out/'status.json','BLOCKED' if budget.blocked else 'COOLDOWN' if budget.cooldown>budget.clock() else 'ACTIVE',reason=budget.reason,**budget.view());time.sleep(.5)
    finally:server.shutdown();server.server_close()
    return 0
if __name__=='__main__':raise SystemExit(main())
