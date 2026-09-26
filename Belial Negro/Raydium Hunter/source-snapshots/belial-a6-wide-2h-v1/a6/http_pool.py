"""Small HTTPS keep-alive pool. No redirects, credentials or silent retries."""
import http.client,io,queue,ssl,threading,urllib.error,urllib.parse,urllib.request
class Response(io.BytesIO):
    def __init__(self,data,status,headers):super().__init__(data);self.status=status;self.headers=headers
class Pool:
    def __init__(self,capacity=4,max_bytes=24<<20):self.capacity=capacity;self.max_bytes=max_bytes;self.lock=threading.Lock();self.hosts={};self.context=ssl.create_default_context()
    def open(self,request,timeout=10):
        url=request.full_url;p=urllib.parse.urlsplit(url)
        if p.scheme!='https' or p.username or p.password:raise ValueError('HTTPS_ONLY')
        key=(p.hostname,p.port or 443)
        with self.lock:
            if key not in self.hosts:
                q=queue.LifoQueue(self.capacity)
                for _ in range(self.capacity):q.put(None)
                self.hosts[key]=q
            q=self.hosts[key]
        slot=q.get(timeout=timeout);conn=slot or http.client.HTTPSConnection(*key,timeout=timeout,context=self.context);reusable=False
        try:
            conn.timeout=timeout
            if conn.sock:conn.sock.settimeout(timeout)
            path=urllib.parse.urlunsplit(('', '',p.path or '/',p.query,''))
            conn.request(request.get_method(),path,body=request.data,headers=dict(request.header_items()))
            r=conn.getresponse();data=r.read(self.max_bytes+1);status=r.status;headers=r.headers
            if len(data)>self.max_bytes:raise ValueError('HTTP_RESPONSE_TOO_LARGE')
            reusable=not r.will_close
            if not 200<=status<300:raise urllib.error.HTTPError(url,status,r.reason,headers,io.BytesIO(data))
            return Response(data,status,headers)
        except http.client.HTTPException as exc:raise urllib.error.URLError(type(exc).__name__) from None
        finally:
            if not reusable:conn.close()
            q.put(conn if reusable else None)
    def json(self,url):
        import json
        req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'Belial-A6-PUBLIC-READONLY/1'})
        with self.open(req,timeout=10) as r:return json.load(r)
