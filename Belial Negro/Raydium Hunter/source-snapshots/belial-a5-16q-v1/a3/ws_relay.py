"""A3 one upstream Solana WebSocket, many isolated loopback sensor clients.
No RPC writes, no provider rotation, no stale replay. Raw recorder remains one separate
upstream connection. Queue overflow disconnects that consumer rather than dropping deltas.
"""
from __future__ import annotations
import argparse,asyncio,base64,hashlib,json,math,os,secrets,ssl,struct,time,urllib.parse,urllib.request
from email.utils import parsedate_to_datetime
from a3.common import *
GUID='258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_FRAME=4<<20

class HandshakeError(Exception):
    def __init__(self,status,headers=None):self.status=status;self.headers=headers or {};super().__init__('WS_HANDSHAKE_HTTP_'+str(status))

def retry_after(headers,at=None):
    value=headers.get('retry-after','');at=time.time() if at is None else at
    try:
        seconds=float(value)
        return max(0.,seconds) if math.isfinite(seconds) else 0.
    except (ValueError,TypeError):
        try:return max(0.,parsedate_to_datetime(value).timestamp()-at)
        except (ValueError,TypeError,OverflowError):return 0.

def retry_delay(attempt,status=None,headers=None):
    if status in (401,403):return None
    base=min(300.,2.**min(attempt,8))
    if status==429:base=max(30.,base,retry_after(headers or {}))
    return base+secrets.randbelow(1000)/1000.

def frame(op,data=b'',mask=False):
    if isinstance(data,str):data=data.encode()
    n=len(data)
    if n>MAX_FRAME:raise ValueError('FRAME_TOO_LARGE')
    flag=128 if mask else 0
    header=bytes([128|op,flag|(n if n<126 else 126 if n<65536 else 127)])
    if n>=65536:header+=struct.pack('!Q',n)
    elif n>=126:header+=struct.pack('!H',n)
    if mask:
        key=os.urandom(4);return header+key+bytes(b^key[i%4] for i,b in enumerate(data))
    return header+data

async def read_message(reader,writer,*,expect_mask,send_lock=None):
    data=bytearray();fragmented=False
    while True:
        a,b=await reader.readexactly(2);fin=bool(a&128);op=a&15;size=b&127;masked=bool(b&128)
        if a&112 or masked!=expect_mask:raise ValueError('INVALID_WS_FLAGS_OR_MASK')
        if size==126:size=struct.unpack('!H',await reader.readexactly(2))[0]
        elif size==127:size=struct.unpack('!Q',await reader.readexactly(8))[0]
        if size>MAX_FRAME or len(data)+size>MAX_FRAME:raise ValueError('WS_SIZE_LIMIT')
        if op>=8 and (not fin or size>125):raise ValueError('INVALID_WS_CONTROL')
        key=await reader.readexactly(4) if masked else None
        part=await reader.readexactly(size)
        if key:part=bytes(v^key[i%4] for i,v in enumerate(part))
        if op==8:raise ConnectionError('WS_PEER_CLOSED')
        if op==9:
            async def pong():writer.write(frame(10,part,not expect_mask));await writer.drain()
            if send_lock:
                async with send_lock:await pong()
            else:await pong()
            if not fragmented:return None
            continue
        if op==10:
            if fragmented:continue
            return None
        if op==1:
            if fragmented:raise ValueError('WS_NESTED_FRAGMENT')
            fragmented=True
        elif op==0:
            if not fragmented:raise ValueError('WS_ORPHAN_FRAGMENT')
        else:raise ValueError('WS_OPCODE_NOT_SUPPORTED')
        data.extend(part)
        if fin:
            obj=strict_loads(data)
            if not isinstance(obj,dict):raise ValueError('WS_OBJECT_REQUIRED')
            return obj

async def headers(reader):
    raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),15)
    if len(raw)>16384:raise ValueError('HTTP_HEADERS_TOO_LARGE')
    lines=raw.decode('iso-8859-1').split('\r\n');fields={}
    for s in lines[1:]:
        if not s:continue
        k,sep,v=s.partition(':')
        if not sep:raise ValueError('HTTP_HEADER_INVALID')
        k=k.strip().lower()
        if k in fields:fields[k]+=','+v.strip()
        else:fields[k]=v.strip()
    return lines[0],fields

async def upstream_connect(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='wss' or not p.hostname or p.username or p.password or p.fragment:raise ValueError('UPSTREAM_REQUIRES_VALIDATED_WSS')
    if urllib.request.getproxies().get('https') and not urllib.request.proxy_bypass(p.hostname):
        raise RuntimeError('EXPLICIT_PROXY_UNSUPPORTED_NOT_BYPASSED')
    r,w=await asyncio.wait_for(asyncio.open_connection(p.hostname,p.port or 443,ssl=ssl.create_default_context(),server_hostname=p.hostname,limit=MAX_FRAME+16384),15)
    try:
        key=base64.b64encode(os.urandom(16)).decode();host=p.netloc;path=(p.path or '/')+('?' + p.query if p.query else '')
        w.write((f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\nUser-Agent: Belial-A3-PAPER/1\r\n\r\n').encode());await w.drain()
        line,h=await headers(r);parts=line.split();status=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0
        if status!=101:raise HandshakeError(status,h)
        accept=base64.b64encode(hashlib.sha1((key+GUID).encode()).digest()).decode()
        if h.get('sec-websocket-accept')!=accept or h.get('upgrade','').lower()!='websocket' or 'upgrade' not in [x.strip().lower() for x in h.get('connection','').split(',')] or 'sec-websocket-extensions' in h:
            raise ValueError('WS_UPSTREAM_UPGRADE_INVALID')
        return r,w
    except BaseException:
        w.close()
        with contextlib.suppress(Exception):await w.wait_closed()
        raise

class Client:
    def __init__(self,writer):
        self.writer=writer;self.subs={};self.queue=asyncio.Queue(maxsize=512);self.bytes=0;self.closed=False
    def push(self,row,received_ms=None):
        raw=canonical(row).encode();size=len(raw)
        if self.closed:return False
        if self.queue.full() or self.bytes+size>4<<20:
            self.close();return False
        self.bytes+=size;self.queue.put_nowait((now_ms() if received_ms is None else received_ms,raw));return True
    def close(self):
        self.closed=True;self.writer.close()

class Relay:
    def __init__(self,url,pools,out,stop=None,connect=upstream_connect):
        self.url=url;self.pools=list(dict.fromkeys(pools));self.out=Path(out);self.out.mkdir(exist_ok=True,parents=True)
        if not self.pools or len(self.pools)>24:raise ValueError('WS_ONE_TO_24_IDENTIFIED_POOLS')
        self.connect=connect;self.stop=stop or asyncio.Event();self.clients=set();self.upstream=None;self.lock=asyncio.Lock()
        self.ids={i+1:p for i,p in enumerate(self.pools)};self.up_ids={};self.acks={p:asyncio.Event() for p in self.pools};self.rejections={}
        self.last_up=0;self.state='STARTING';self.generation=0;self.next_id=0;self.cooldown_until=0;self.error=None
        self.stats=dict(connection_attempts=0,upstream_messages=0,forwarded=0,queue_overflow_disconnects=0,stale_delivery_disconnects=0,http_429=0,max_delivery_ms=0,upstream_connections_max=0)
    def mark(self,state,error=None):
        self.state=state;self.error=error
        atomic(self.out/'status.json',dict(state=state,error=error,at_ms=now_ms(),pid=os.getpid(),generation=self.generation,
            upstream_last_message_ms=self.last_up,upstream_connections=1 if self.upstream else 0,
            local_clients=len(self.clients),subscriptions_acknowledged=len(self.up_ids),requested=len(self.pools),
            cooldown_remaining_ms=max(0,self.cooldown_until-now_ms()),counters=self.stats,
            raw_recorder_is_separate_connection=True,subscription_errors=self.rejections,orders=False))
    async def send_up(self,obj):
        if not self.upstream:raise ConnectionError('UPSTREAM_DOWN')
        async with self.lock:
            self.upstream.write(frame(1,canonical(obj),True));await self.upstream.drain()
    async def consume_up(self,reader,writer):
        while not self.stop.is_set():
            row=await asyncio.wait_for(read_message(reader,writer,expect_mask=False,send_lock=self.lock),60)
            self.last_up=now_ms()
            if row is None:continue
            self.stats['upstream_messages']+=1
            if row.get('jsonrpc')!='2.0':raise ValueError('UPSTREAM_JSONRPC_VERSION')
            if 'id' in row:
                pool=self.ids.get(row['id'])
                if pool is None:raise ValueError('UPSTREAM_UNEXPECTED_RESPONSE')
                sub=row.get('result')
                if type(sub) is not int or sub<0:
                    self.rejections[pool]=row.get('error') or {'message':'INVALID_SUBSCRIPTION_ID'};self.acks[pool].set();continue
                if sub in self.up_ids:raise ValueError('UPSTREAM_DUPLICATE_SUBSCRIPTION_ID')
                self.up_ids[sub]=pool;self.acks[pool].set();continue
            if row.get('method')!='logsNotification':raise ValueError('UPSTREAM_METHOD_NOT_LOGS')
            pool=self.up_ids.get((row.get('params') or {}).get('subscription'))
            if pool is None:raise ValueError('UPSTREAM_UNKNOWN_SUBSCRIPTION')
            params=row.get('params') or {}
            if not isinstance(params.get('result'),dict):raise ValueError('UPSTREAM_MISSING_RESULT')
            for client in list(self.clients):
                for local_id,target in tuple(client.subs.items()):
                    if target!=pool:continue
                    out=dict(row,params=dict(params,subscription=local_id),belial_upstream_received_ms=self.last_up)
                    if not client.push(out,self.last_up):self.stats['queue_overflow_disconnects']+=1
    async def run_upstream(self):
        attempt=0
        while not self.stop.is_set():
            self.stats['connection_attempts']+=1;self.mark('CONNECTING');reader_task=None;writer=None;connected_at=None
            try:
                reader,writer=await self.connect(self.url);self.upstream=writer;connected_at=time.monotonic();self.generation+=1
                self.last_up=now_ms();self.stats['upstream_connections_max']=1;self.mark('SUBSCRIBING')
                reader_task=asyncio.create_task(self.consume_up(reader,writer))
                for ident,pool in self.ids.items():
                    await self.send_up({'jsonrpc':'2.0','id':ident,'method':'logsSubscribe','params':[{'mentions':[pool]},{'commitment':'confirmed'}]})
                    await asyncio.sleep(.12)
                for ack in self.acks.values():await asyncio.wait_for(ack.wait(),20)
                self.mark('ACTIVE')
                while not self.stop.is_set():
                    if reader_task.done():await reader_task;raise ConnectionError('UPSTREAM_READER_ENDED')
                    async with self.lock:writer.write(frame(9,b'a3',True));await writer.drain()
                    wait_stop=asyncio.create_task(self.stop.wait())
                    try:
                        done,_=await asyncio.wait((reader_task,wait_stop),timeout=20,return_when=asyncio.FIRST_COMPLETED)
                        if reader_task in done:await reader_task;raise ConnectionError('UPSTREAM_READER_ENDED')
                    finally:
                        wait_stop.cancel()
                        with contextlib.suppress(asyncio.CancelledError):await wait_stop
                break
            except asyncio.CancelledError:raise
            except Exception as exc:
                status=exc.status if isinstance(exc,HandshakeError) else None
                if status==429:self.stats['http_429']+=1
                if connected_at is not None and time.monotonic()-connected_at>=120:attempt=0
                attempt+=1;delay=retry_delay(attempt,status,getattr(exc,'headers',None));self.error=str(exc)
                self.cooldown_until=now_ms()+int((delay or 0)*1000)
                self.mark('ACCESS_DENIED' if delay is None else 'COOLDOWN',str(exc))
            finally:
                self.upstream=None
                if reader_task:
                    reader_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError,Exception):await reader_task
                if writer:
                    writer.close()
                    with contextlib.suppress(Exception):await writer.wait_closed()
                for client in list(self.clients):client.close()
                self.up_ids.clear();self.rejections.clear()
                for ack in self.acks.values():ack.clear()
            if self.state=='ACCESS_DENIED':await self.stop.wait();break
            delay=max(0,(self.cooldown_until-now_ms())/1000)
            try:await asyncio.wait_for(self.stop.wait(),delay)
            except asyncio.TimeoutError:pass
    async def writer_loop(self,client):
        while not client.closed:
            at,raw=await client.queue.get();client.bytes-=len(raw)
            lag=now_ms()-at;self.stats['max_delivery_ms']=max(self.stats['max_delivery_ms'],lag)
            if lag>500:
                self.stats['stale_delivery_disconnects']+=1;client.close();return
            client.writer.write(frame(1,raw));await asyncio.wait_for(client.writer.drain(),.5);self.stats['forwarded']+=1
    async def local_client(self,r,w):
        client=None;writer_task=None
        try:
            line,h=await headers(r);parts=line.split();host=h.get('host','')
            if len(parts)!=3 or parts[0]!='GET' or parts[1]!=self.path or host not in ('127.0.0.1:'+str(self.port),'localhost:'+str(self.port)) or h.get('origin'):
                w.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await w.drain();return
            key=h.get('sec-websocket-key','')
            if h.get('upgrade','').lower()!='websocket' or h.get('sec-websocket-version')!='13' or len(base64.b64decode(key,validate=True))!=16 or 'upgrade' not in [x.strip().lower() for x in h.get('connection','').split(',')]:raise ValueError('BAD_LOCAL_UPGRADE')
            if not self.upstream or len(self.clients)>=32:
                w.write(b'HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await w.drain();return
            accept=base64.b64encode(hashlib.sha1((key+GUID).encode()).digest()).decode()
            w.write(f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n'.encode());await w.drain()
            client=Client(w);self.clients.add(client);writer_task=asyncio.create_task(self.writer_loop(client))
            while not self.stop.is_set() and not client.closed:
                obj=await asyncio.wait_for(read_message(r,w,expect_mask=True),50)
                if obj is None:continue
                if now_ms()-self.last_up>45000:raise ConnectionError('UPSTREAM_HEARTBEAT_STALE')
                ident=obj.get('id')
                if type(ident) is not int or obj.get('jsonrpc')!='2.0':raise ValueError('LOCAL_JSONRPC_INVALID')
                method=obj.get('method');params=obj.get('params',[])
                if method=='logsSubscribe':
                    if len(params)!=2 or not isinstance(params[0],dict) or params[1]!={'commitment':'confirmed'}:raise ValueError('ONLY_CONFIRMED_LOGS_ALLOWED')
                    mentions=params[0].get('mentions')
                    if not isinstance(mentions,list) or len(mentions)!=1 or mentions[0] not in self.acks or len(client.subs)>=24:raise ValueError('POOL_NOT_APPROVED')
                    pool=mentions[0];await asyncio.wait_for(self.acks[pool].wait(),20)
                    if pool in self.rejections:client.push({'jsonrpc':'2.0','id':ident,'error':self.rejections[pool]});continue
                    self.next_id+=1;client.subs[self.next_id]=pool;client.push({'jsonrpc':'2.0','id':ident,'result':self.next_id})
                elif method=='logsUnsubscribe' and isinstance(params,list) and len(params)==1:
                    existed=params[0] in client.subs;client.subs.pop(params[0],None);client.push({'jsonrpc':'2.0','id':ident,'result':existed})
                else:raise ValueError('METHOD_NOT_READ_ONLY_LOGS')
        except (Exception,asyncio.CancelledError):pass
        finally:
            if client:self.clients.discard(client);client.close()
            if writer_task:
                writer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError,Exception):await writer_task
            w.close()
            with contextlib.suppress(Exception):await w.wait_closed()
    async def serve(self,session=None):
        self.path='/'+secrets.token_urlsafe(24)
        server=await asyncio.start_server(self.local_client,'127.0.0.1',0,limit=MAX_FRAME+16384)
        self.port=server.sockets[0].getsockname()[1]
        atomic(self.out/'ENDPOINT.json',{'url':f'ws://127.0.0.1:{self.port}{self.path}','read_only':True,'pools':self.pools,'upstream_connections_target':1},True)
        upstream=asyncio.create_task(self.run_upstream())
        try:
            while not self.stop.is_set():
                if session and session_expired(session):self.stop.set();break
                if upstream.done():await upstream;break
                self.mark(self.state,self.error)
                await asyncio.sleep(.5)
        finally:
            self.stop.set();upstream.cancel()
            with contextlib.suppress(asyncio.CancelledError,Exception):await upstream
            server.close();await server.wait_closed();self.mark('STOPPED',self.error)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args()
    conn=read_json(a.session/'spot_prep/CONNECTION.json',{}) or {};cohort=read_json(a.session/'COHORT.json',{}) or {}
    pools=[read_json(a.session/'spot_prep'/s/'MANIFEST.json')['pools'][0]['pool'] for s in cohort.get('qualified_at_start',[])]
    async def run():
        stop=asyncio.Event();loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
        await Relay(conn['ws'],pools,a.session/'ws_relay',stop).serve(a.session)
    asyncio.run(run());return 0
if __name__=='__main__':raise SystemExit(main())
