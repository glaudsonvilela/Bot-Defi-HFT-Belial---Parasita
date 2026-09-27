"""Small RFC6455 client ONLY for read-only preflight; no added pip dependency.

The production feed continues to use tokio-tungstenite. This probe verifies TLS,
upgrade, subscription acknowledgements and cancellation; it does not feed events
to a strategy or claim a sustained throughput measurement.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.request
from rpc_transport import RpcFailure, strict_json, validate_url

GUID='258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_FRAME=4*1024*1024


def masked_frame(opcode,data):
    if isinstance(data,str):data=data.encode('utf-8')
    if len(data)>MAX_FRAME:raise ValueError('WS_SEND_TOO_LARGE')
    n=len(data);head=bytes((0x80|opcode,0x80|(n if n<126 else (126 if n<65536 else 127))))
    if n>=65536:head+=struct.pack('!Q',n)
    elif n>=126:head+=struct.pack('!H',n)
    mask=os.urandom(4)
    return head+mask+bytes(b^mask[i%4] for i,b in enumerate(data))


class Connection:
    def __init__(self,url,deadline,*,socket_factory=None,context_factory=None):
        self.url=validate_url(url,'wss');self.deadline=deadline;self.sock=None;self.buf=b''
        self.factory=socket_factory or socket.create_connection
        self.context_factory=context_factory or ssl.create_default_context

    def fail(self,kind,method='logsSubscribe',**kwargs):
        return RpcFailure(kind,self.url,method,**kwargs)

    def remaining(self):
        remain=self.deadline-time.monotonic()
        if remain<=0:raise self.fail('WS_DEADLINE')
        return min(10.0,remain)

    def __enter__(self):
        p=urllib.parse.urlsplit(self.url);host=p.hostname;port=p.port or 443
        # The inherited tokio WS path is direct. Do not silently bypass an explicit proxy.
        proxy=urllib.request.getproxies().get('https')
        if proxy and not urllib.request.proxy_bypass(host):
            raise self.fail('WS_EXPLICIT_PROXY_UNSUPPORTED')
        raw=None
        try:
            raw=self.factory((host,port),timeout=self.remaining())
            self.sock=self.context_factory().wrap_socket(raw,server_hostname=host)
            self.sock.settimeout(self.remaining())
            key=base64.b64encode(os.urandom(16)).decode('ascii')
            authority=('['+host+']') if ':' in host else host
            if port!=443:authority+=':'+str(port)
            path=(p.path or '/')+('?' + p.query if p.query else '')
            request=(f'GET {path} HTTP/1.1\r\nHost: {authority}\r\nUpgrade: websocket\r\n'
                     'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                     f'Sec-WebSocket-Key: {key}\r\nUser-Agent: Belial-T3-FIX3-read-only/1\r\n\r\n')
            self.sock.sendall(request.encode('ascii'))
            header=b''
            while b'\r\n\r\n' not in header:
                self.sock.settimeout(self.remaining());part=self.sock.recv(4096)
                if not part:raise self.fail('WS_HANDSHAKE_EOF')
                header+=part
                if len(header)>32768:raise self.fail('WS_HANDSHAKE_TOO_LARGE')
            head,self.buf=header.split(b'\r\n\r\n',1)
            lines=head.decode('iso-8859-1').split('\r\n');fields=lines[0].split()
            status=int(fields[1]) if len(fields)>1 and fields[1].isdigit() else 0
            if status!=101:raise self.fail('WS_HTTP_REJECTED',status=status)
            headers={}
            for line in lines[1:]:
                if ':' not in line:raise self.fail('WS_HANDSHAKE_INVALID')
                k,v=line.split(':',1);k=k.strip().lower();v=v.strip()
                if k in headers:headers[k]+=','+v
                else:headers[k]=v
            expected=base64.b64encode(hashlib.sha1((key+GUID).encode('ascii')).digest()).decode('ascii')
            if (headers.get('sec-websocket-accept')!=expected or
                headers.get('upgrade','').lower()!='websocket' or
                'upgrade' not in [v.strip().lower() for v in headers.get('connection','').split(',')] or
                'sec-websocket-extensions' in headers):
                raise self.fail('WS_HANDSHAKE_INVALID')
            return self
        except RpcFailure:
            if self.sock:self.sock.close()
            elif raw:raw.close()
            raise
        except (OSError,ValueError,UnicodeError):
            if self.sock:self.sock.close()
            elif raw:raw.close()
            raise self.fail('WS_CONNECT_OR_TLS_FAILED') from None

    def __exit__(self,*_):
        if self.sock:
            try:self.sock.sendall(masked_frame(8,struct.pack('!H',1000)))
            except OSError:pass
            finally:self.sock.close()

    def take(self,n):
        while len(self.buf)<n:
            self.sock.settimeout(self.remaining())
            block=self.sock.recv(min(65536,max(n-len(self.buf),4096)))
            if not block:raise self.fail('WS_EOF')
            self.buf+=block
        data,self.buf=self.buf[:n],self.buf[n:]
        return data

    def send(self,value):
        self.sock.settimeout(self.remaining())
        self.sock.sendall(masked_frame(1,json.dumps(value,separators=(',',':'),allow_nan=False)))

    def recv(self):
        fragments=bytearray();fragmented=False
        while True:
            first,second=self.take(2);fin=bool(first&0x80);op=first&15;n=second&127
            if first&0x70 or second&0x80:raise self.fail('WS_UNSUPPORTED_FRAME')
            if n==126:n=struct.unpack('!H',self.take(2))[0]
            elif n==127:n=struct.unpack('!Q',self.take(8))[0]
            if n>MAX_FRAME or len(fragments)+n>MAX_FRAME:raise self.fail('WS_FRAME_TOO_LARGE')
            if op>=8 and (not fin or n>125):raise self.fail('WS_CONTROL_INVALID')
            data=self.take(n)
            if op==8:raise self.fail('WS_REMOTE_CLOSE')
            if op==9:
                self.sock.sendall(masked_frame(10,data));continue
            if op==10:continue
            if op==1:
                if fragmented:raise self.fail('WS_FRAGMENT_INVALID')
                fragmented=True
            elif op==0:
                if not fragmented:raise self.fail('WS_FRAGMENT_INVALID')
            else:raise self.fail('WS_UNSUPPORTED_OPCODE')
            fragments.extend(data)
            if fin:
                try:value=strict_json(fragments)
                except (ValueError,UnicodeError):raise self.fail('WS_JSON_INVALID') from None
                if not isinstance(value,dict):raise self.fail('WS_JSON_INVALID')
                return value


def probe(url, pools, *, timeout=35.0, connection_factory=Connection):
    if len(pools)!=12 or len(set(pools))!=12:raise ValueError('WS_REQUIRES_12_DISTINCT_POOLS')
    deadline=time.monotonic()+timeout;seen=set();notifications=0;count=0
    try:
        with connection_factory(url,deadline) as ws:
            def wait(request_id):
                nonlocal notifications,count
                while time.monotonic()<deadline:
                    row=ws.recv();count+=1
                    if count>20000:raise RpcFailure('WS_PROBE_MESSAGE_LIMIT',url,'logsSubscribe')
                    if row.get('jsonrpc')!='2.0':raise RpcFailure('WS_ENVELOPE_INVALID',url,'logsSubscribe')
                    if 'id' in row:
                        if type(row['id']) is not int or row['id']!=request_id:
                            raise RpcFailure('WS_RESPONSE_ID_INVALID',url,'logsSubscribe')
                        if row.get('error') is not None:
                            error=row['error'];code=error.get('code') if isinstance(error,dict) else None
                            if type(code) is not int:code=None
                            raise RpcFailure('WS_RPC_REJECTED',url,'logsSubscribe',code=code)
                        if 'result' not in row:raise RpcFailure('WS_RESULT_MISSING',url,'logsSubscribe')
                        return row['result']
                    if row.get('method')=='logsNotification':notifications+=1
                raise RpcFailure('WS_DEADLINE',url,'logsSubscribe')
            for i,pool in enumerate(pools,1):
                ws.send({'jsonrpc':'2.0','id':i,'method':'logsSubscribe',
                         'params':[{'mentions':[pool]},{'commitment':'confirmed'}]})
                subscription=wait(i)
                if type(subscription) is not int or subscription<0 or subscription in seen:
                    raise RpcFailure('WS_SUBSCRIPTION_INVALID',url,'logsSubscribe')
                seen.add(subscription)
            for i,subscription in enumerate(sorted(seen),101):
                ws.send({'jsonrpc':'2.0','id':i,'method':'logsUnsubscribe','params':[subscription]})
                if wait(i) is not True:raise RpcFailure('WS_UNSUBSCRIBE_FAILED',url,'logsUnsubscribe')
        return {'ok':True,'subscriptions_acknowledged':12,'subscriptions_cancelled':12,
                'notifications_ignored':notifications,'data_used_for_training':False,
                'probe_only_not_throughput_test':True}
    except RpcFailure:raise
    except (OSError,TimeoutError,ValueError):
        raise RpcFailure('WS_TRANSPORT_UNAVAILABLE',url,'logsSubscribe') from None
