"""Public read-only HTTP with bounded responses, pacing and explicit failures."""
import json, threading, time, urllib.request, urllib.error, urllib.parse

class Denied(RuntimeError): pass
class RpcError(RuntimeError):
    def __init__(self,method,code,message=''):
        message=str(message).lower()
        category=('METHOD_UNAVAILABLE' if code==-32601 or any(v in message for v in ('disabled','not supported','not available','method not found'))
                  else 'LIMIT_OR_METHOD_RESTRICTION' if code==-32005 else 'RPC_ERROR')
        self.method=method;self.code=code;self.category=category
        super().__init__('RPC_'+method+'_'+category+'_'+str(code))
def error_label(error):
    if isinstance(error,RpcError):return str(error)
    value=str(error)
    if value and len(value)<=160 and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-' for c in value):return value
    return type(error).__name__
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def fetch(url, data=None):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.fragment: raise ValueError('HTTPS_REQUIRED')
    req=urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
        headers={'User-Agent':'Belial-A7-PAPER/1','Accept':'application/json','Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=12) as r:
            raw=r.read(24*1024*1024+1)
            if len(raw)>24*1024*1024: raise ValueError('RESPONSE_TOO_LARGE')
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code in (401,403,451): raise Denied('HTTP_ACCESS_DENIED_'+str(e.code)) from None
        if e.code==429: raise RuntimeError('HTTP_RATE_LIMIT_429') from None
        raise RuntimeError('HTTP_'+str(e.code)) from None
    except (urllib.error.URLError,TimeoutError): raise RuntimeError('HTTP_TRANSPORT_FAILED') from None

class RPC:
    METHODS={'getGenesisHash','getAccountInfo','getTransaction','getSlot',
        'eth_chainId','eth_call','eth_getLogs','eth_getBlockByNumber','eth_getTransactionReceipt','eth_getTransactionByHash'}
    def __init__(self,url,rps=3):
        self.url=url;self.lock=threading.Lock();self.next=0.;self.rps=rps;self.disabled=False;self.calls=0
    def call(self,method,params=None):
        if method not in self.METHODS: raise ValueError('READ_ONLY_RPC_METHOD_REQUIRED')
        with self.lock:
            if self.disabled: raise Denied('RPC_DISABLED_ACCESS_DENIED')
            wait=max(0,self.next-time.monotonic());self.next=max(self.next,time.monotonic())+1/self.rps;self.calls+=1
        if wait: time.sleep(wait)
        try:
            response=fetch(self.url,{'jsonrpc':'2.0','id':1,'method':method,'params':params or []})
            if response.get('error'):
                err=response['error'];raise RpcError(method,err.get('code'),err.get('message',''))
            if 'result' not in response: raise ValueError('RPC_RESULT_MISSING')
            return response['result']
        except Denied:
            self.disabled=True;raise
        except Exception:
            with self.lock:self.next=max(self.next,time.monotonic()+5)
            raise
