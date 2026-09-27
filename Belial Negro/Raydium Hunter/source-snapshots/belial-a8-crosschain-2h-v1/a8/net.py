"""Read-only HTTP, bounded pacing and redacted method-level diagnostics."""
import json,threading,time,urllib.request,urllib.error,urllib.parse
from a51.common import now_ms,canonical
from a6.http_pool import Pool
_DIRECT_POOL=Pool(capacity=2)
class Denied(RuntimeError):
    def __init__(self,code=None,method=None):
        self.http_status=code if isinstance(code,int) else None;self.method=method
        super().__init__('ACCESS_DENIED'+('_HTTP_'+str(self.http_status) if self.http_status else '')+('_'+method if method else ''))
class HttpError(RuntimeError):
    def __init__(self,code,retry_after=None):
        self.http_status=code;self.retry_after=retry_after
        super().__init__('HTTP_'+str(code))
class RpcError(RuntimeError):
    def __init__(self,method,code,message=''):
        message=str(message).lower();self.method=method;self.code=code
        self.category=('METHOD_UNAVAILABLE' if code==-32601 or any(v in message for v in ('disabled','not supported','not available','method not found')) else 'LIMIT_OR_METHOD_RESTRICTION' if code==-32005 else 'RPC_ERROR')
        super().__init__('RPC_'+method+'_'+self.category+'_'+str(code))
def error_label(error):
    if isinstance(error,(RpcError,Denied,HttpError)):return str(error)
    value=str(error)
    return value if value and '://' not in value and len(value)<=2048 and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:,./-' for c in value) else type(error).__name__
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def transport_mode():
    return 'CONFIGURED_PROXY' if urllib.request.getproxies() else 'HTTPS_KEEPALIVE'
def open_request(req,timeout):
    # Respect configured proxies. Never bypass one to retry a refused endpoint.
    if urllib.request.getproxies():return urllib.request.build_opener(NoRedirect).open(req,timeout=timeout)
    return _DIRECT_POOL.open(req,timeout=timeout)
def fetch(url,data=None):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.fragment:raise ValueError('HTTPS_REQUIRED')
    req=urllib.request.Request(url,data=None if data is None else json.dumps(data).encode(),headers={'User-Agent':'Belial-A8-PAPER/1','Accept':'application/json','Content-Type':'application/json'})
    try:
        with open_request(req,timeout=12) as r:
            raw=r.read(24*1024*1024+1)
            if len(raw)>24*1024*1024:raise ValueError('RESPONSE_TOO_LARGE')
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code in (401,403,451):raise Denied(e.code) from None
        retry=None
        try:retry=min(120,max(5,float(e.headers.get('Retry-After','5'))))
        except (ValueError,TypeError):pass
        raise HttpError(e.code,retry) from None
    except (urllib.error.URLError,TimeoutError):raise RuntimeError('HTTP_TRANSPORT_FAILED') from None

def audit_writer(path):
    lock=threading.Lock()
    def emit(row):
        with lock:
            with path.open('a') as f:f.write(canonical(row)+'\n')
    return emit
class RPC:
    METHODS={'getGenesisHash','getAccountInfo','getTransaction','getSlot','eth_chainId','eth_call','eth_getLogs','eth_getBlockByNumber','eth_getTransactionReceipt','eth_getTransactionByHash'}
    def __init__(self,url,rps=3,audit=None,chain=None):
        self.url=url;self.lock=threading.Lock();self.next=0.;self.rps=rps;self.disabled=False;self.calls=0;self.audit=audit;self.chain=chain
    def call(self,method,params=None):
        if method not in self.METHODS:raise ValueError('READ_ONLY_RPC_METHOD_REQUIRED')
        with self.lock:
            if self.disabled:raise Denied(method=method)
            wait=max(0,self.next-time.monotonic());self.next=max(self.next,time.monotonic())+1/self.rps;self.calls+=1
        if wait:time.sleep(wait)
        # Another worker may have disabled this endpoint during the pacing wait.
        if self.disabled:raise Denied(method=method)
        begin=time.monotonic();row=dict(at_ms=now_ms(),chain=self.chain,method=method,transport=transport_mode(),orders=False)
        if method=='eth_getLogs' and params and isinstance(params[0],dict):
            arg=params[0];row['filter_address_count']=len(arg.get('address',[])) if isinstance(arg.get('address'),list) else 1
            try:row['block_span']=int(arg['toBlock'],16)-int(arg['fromBlock'],16)+1
            except (KeyError,ValueError,TypeError):pass
        try:
            response=fetch(self.url,{'jsonrpc':'2.0','id':1,'method':method,'params':params or []})
            if response.get('error'):
                err=response['error'];raise RpcError(method,err.get('code'),err.get('message',''))
            if 'result' not in response:raise ValueError('RPC_RESULT_MISSING')
            row.update(ok=True,http_status=200);return response['result']
        except Denied as e:
            self.disabled=True;e=Denied(e.http_status,method);row.update(ok=False,error=str(e),http_status=e.http_status);raise e from None
        except Exception as e:
            row.update(ok=False,error=error_label(e),http_status=getattr(e,'http_status',None),rpc_code=getattr(e,'code',None))
            with self.lock:self.next=max(self.next,time.monotonic()+max(5,getattr(e,'retry_after',None) or 5))
            raise
        finally:
            row['latency_ms']=round((time.monotonic()-begin)*1000,3)
            if self.audit:self.audit(row)
