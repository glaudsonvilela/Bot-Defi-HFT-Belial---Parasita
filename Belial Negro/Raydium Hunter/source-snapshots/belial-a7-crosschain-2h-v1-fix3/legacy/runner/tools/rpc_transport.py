"""Bounded, read-only RPC for preflight/qualification. Never bypasses access control.

HTTP 401/403 and JSON-RPC application errors are not retried. 429 obeys
Retry-After, within a bounded deadline; no hot retry/rotation loop. Live market
transport remains the existing Rust engine, not this Python module.
"""
from __future__ import annotations
import datetime as dt
import email.utils
import hashlib
import json
import math
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'
PUBLIC_PROFILES = (
    {'name':'solana-mainnet-public','rpc':'https://api.mainnet.solana.com','ws':'wss://api.mainnet.solana.com'},
    {'name':'publicnode-solana','rpc':'https://solana-rpc.publicnode.com','ws':'wss://solana-rpc.publicnode.com'},
)
READ_METHODS = frozenset(('getGenesisHash','getMultipleAccounts','getAccountInfo',
                         'getSignaturesForAddress','getTransaction','getSlot'))
MAX_BYTES = 32 * 1024 * 1024


def validate_url(value: str, scheme: str) -> str:
    if not isinstance(value,str) or not value or any(c.isspace() or ord(c)<32 for c in value):
        raise ValueError('RPC_URL_INVALID')
    try:
        p=urllib.parse.urlsplit(value);port=p.port
        if p.scheme != scheme or not p.hostname or p.username is not None or p.password is not None or p.fragment:
            raise ValueError('RPC_URL_SCHEME_HOST_OR_USERINFO')
        p.hostname.encode('ascii')
    except (ValueError,UnicodeError):
        raise ValueError('RPC_URL_INVALID_USE_HTTPS_AND_WSS_NO_USERINFO') from None
    return value


def public_endpoint(value: str) -> str:
    """Do not print path/query API keys. Retain public root URLs for diagnosis."""
    try:
        p=urllib.parse.urlsplit(value)
        host=p.hostname or 'invalid';host=('['+host+']') if ':' in host else host
        if p.port:host+=':'+str(p.port)
        return p.scheme+'://'+host+('/[REDACTED]' if p.path not in ('','/') or p.query or p.username else p.path)
    except Exception:return '[INVALID_ENDPOINT]'


def redact_text(text: str, endpoints=()) -> str:
    sensitive=[value for value in set(endpoints) if value and public_endpoint(value)!=value]
    hosts=set()
    for endpoint in sorted(sensitive,key=len,reverse=True):
        text=text.replace(endpoint,public_endpoint(endpoint))
        text=text.replace(endpoint.replace('&','&amp;'),public_endpoint(endpoint))
        hosts.add(urllib.parse.urlsplit(endpoint).hostname)
    if not sensitive:return text
    # Public metadata URLs stay byte-identical; only configured private RPC hosts
    # have paths/queries hidden in archive copies and diagnostics.
    def hide(match):
        value=match.group()
        return public_endpoint(value) if urllib.parse.urlsplit(value).hostname in hosts else value
    return re.sub(r"(?:https?|wss?)://[^\s\"'<>\\]+",hide,text)


def archive_file(z, path, name, endpoints=()):
    """Redact text in archive copies only; original local evidence stays unchanged."""
    path=Path(path)
    if not any(e and public_endpoint(e)!=e for e in endpoints):
        z.write(path,str(name));return
    if path.suffix.lower() in ('.json','.jsonl','.ndjson','.txt','.log','.md','.html','.csv','.py','.sh') or path.name in ('SESSION_COMPLETE',):
        with path.open('r',encoding='utf-8',errors='surrogateescape') as source, z.open(str(name),'w',force_zip64=True) as target:
            for line in source:
                target.write(redact_text(line,endpoints).encode('utf-8',errors='surrogateescape'))
    else:
        z.write(path,str(name))


def save_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('RPC_REPORT_SYMLINK_REFUSED')
    tmp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def strict_json(raw):
    def invalid(value):raise ValueError('NONFINITE_JSON')
    return json.loads(raw,parse_constant=invalid)


class RpcFailure(RuntimeError):
    def __init__(self,kind,endpoint,method,*,status=None,code=None):
        self.kind=kind;self.endpoint=public_endpoint(endpoint);self.method=method
        self.status=status;self.code=code
        super().__init__(f'{kind}:endpoint={self.endpoint}:method={method}'+
                         (f':http={status}' if status is not None else '')+
                         (f':rpc_code={code}' if code is not None else ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward API-key URLs / POST bodies to an unapproved redirect.
        return None


def retry_after_seconds(value, wall_time=None):
    if value is None:return 1.0
    try:
        number=float(value)
    except (TypeError,ValueError):
        try:
            date=email.utils.parsedate_to_datetime(str(value))
            if date.tzinfo is None:date=date.replace(tzinfo=dt.timezone.utc)
            number=date.timestamp()-(time.time() if wall_time is None else wall_time)
        except Exception:return 1.0
    if not math.isfinite(number):return 1.0
    return max(0.0,number)


class Client:
    def __init__(self,url,log=None,*,deadline=None,open_url=None,clock=None,sleep=None):
        self.url=validate_url(url,'https');self.log=Path(log) if log else None
        self.clock=clock or time.monotonic;self.sleep=sleep or time.sleep
        self.deadline=deadline
        self.open_url=open_url or urllib.request.build_opener(NoRedirect()).open
        self.last_start=None;self.sequence=0;self.blocked=None
        # At most ~3 calls/sec during preparation; never increases live RPC rate.
        self.min_interval=0.36

    def emit(self, row):
        if self.log:
            self.log.parent.mkdir(parents=True,exist_ok=True)
            with self.log.open('a',encoding='utf-8') as f:
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')

    def request(self, body):
        method=body.get('method') if isinstance(body,dict) else None
        if method not in READ_METHODS:raise RpcFailure('RPC_METHOD_NOT_READ_ONLY',self.url,str(method))
        if self.blocked:raise self.blocked
        request_id=body.get('id')
        data=json.dumps(body,allow_nan=False).encode()
        # Limit each logical call; a provider-wide selection deadline can shorten it.
        end=min(self.clock()+35.0,self.deadline if self.deadline is not None else float('inf'))
        for attempt in range(1,4):
            if self.last_start is not None:
                wait=self.min_interval-(self.clock()-self.last_start)
                if wait>0:
                    if self.clock()+wait>=end:raise RpcFailure('RPC_DEADLINE',self.url,method)
                    self.sleep(wait)
            remain=end-self.clock()
            if remain<=0:raise RpcFailure('RPC_DEADLINE',self.url,method)
            self.last_start=start=self.clock()
            row={'at_ms':time.time_ns()//1_000_000,'endpoint':public_endpoint(self.url),
                 'method':method,'attempt':attempt,'ok':False}
            failure=None;delay=None;raw=b''
            try:
                req=urllib.request.Request(self.url,data=data,headers={
                    'Content-Type':'application/json','Accept':'application/json',
                    'User-Agent':'Belial-T3-9H-FIX3-PAPER-read-only/1'})
                with self.open_url(req,timeout=min(10.0,remain)) as response:
                    raw=response.read(MAX_BYTES+1);row['http_status']=getattr(response,'status',200)
                if len(raw)>MAX_BYTES:raise RpcFailure('RPC_RESPONSE_TOO_LARGE',self.url,method)
                try:value=strict_json(raw)
                except (ValueError,UnicodeError):raise RpcFailure('RPC_NON_JSON_OR_NONFINITE',self.url,method) from None
                if not isinstance(value,dict) or value.get('jsonrpc')!='2.0' or value.get('id')!=request_id or type(value.get('id')) is not type(request_id):
                    raise RpcFailure('RPC_ENVELOPE_INVALID',self.url,method)
                if value.get('error') is not None:
                    error=value['error'];code=error.get('code') if isinstance(error,dict) else None
                    if type(code) is not int:code=None
                    raise RpcFailure('RPC_REMOTE_ERROR',self.url,method,code=code)
                if 'result' not in value:raise RpcFailure('RPC_RESULT_MISSING',self.url,method)
                row.update(ok=True,bytes=len(raw))
                return value
            except urllib.error.HTTPError as exc:
                status=exc.code;row['http_status']=status
                try:raw=exc.read(2048)
                except Exception:raw=b''
                finally:exc.close()
                kind={401:'RPC_HTTP_UNAUTHORIZED',403:'RPC_HTTP_FORBIDDEN',429:'RPC_HTTP_RATE_LIMITED'}.get(status,'RPC_HTTP_ERROR')
                failure=RpcFailure(kind,self.url,method,status=status)
                if status in (401,403) or 300<=status<400:
                    self.blocked=failure
                elif status==429:
                    delay=retry_after_seconds(exc.headers.get('Retry-After') if exc.headers else None)
                    delay=max(1.0,delay)
                    row['retry_after_seconds']=delay
                    if delay>15.0 or self.clock()+delay>=end:
                        self.blocked=failure;delay=None
                elif status in (408,500,502,503,504):delay=float(attempt)
            except (ssl.SSLCertVerificationError,ssl.CertificateError):
                failure=RpcFailure('RPC_TLS_CERTIFICATE_ERROR',self.url,method)
            except (urllib.error.URLError,TimeoutError,socket.timeout,OSError) as exc:
                reason=getattr(exc,'reason',exc)
                if isinstance(reason,ssl.SSLCertVerificationError):
                    failure=RpcFailure('RPC_TLS_CERTIFICATE_ERROR',self.url,method)
                else:
                    failure=RpcFailure('RPC_TRANSPORT_UNAVAILABLE',self.url,method);delay=float(attempt)
            except RpcFailure as exc:failure=exc
            finally:
                row['elapsed_ms']=round((self.clock()-start)*1000,3)
                if raw and not row['ok']:
                    row['error_body_prefix_sha256']=hashlib.sha256(raw[:2048]).hexdigest()
                    row['error_body_recorded']=False
                if failure:row['error']=str(failure)
                self.emit(row)
            if delay is None or attempt==3 or self.clock()+delay>=end:
                if failure and failure.status==429:self.blocked=failure
                raise failure from None
            self.sleep(delay)
        raise AssertionError('UNREACHABLE')

    def call(self,method,params=None):
        self.sequence+=1
        body={'jsonrpc':'2.0','id':self.sequence,'method':method}
        if params is not None:body['params']=params
        return self.request(body)['result']


def profiles(rpc=None,ws=None):
    if not rpc:
        if ws:raise ValueError('RPC_REQUIRED_WITH_CUSTOM_WS')
        return [dict(p) for p in PUBLIC_PROFILES]
    validate_url(rpc,'https')
    if not ws:
        for profile in PUBLIC_PROFILES:
            if profile['rpc'].rstrip('/')==rpc.rstrip('/'):ws=profile['ws'];break
    if not ws:raise ValueError('CUSTOM_RPC_REQUIRES_EXPLICIT_BELIAL_SOLANA_WS_URL')
    validate_url(ws,'wss')
    # An explicitly configured service never silently falls back to another provider.
    return [{'name':'explicit-user-rpc','rpc':rpc,'ws':ws}]
