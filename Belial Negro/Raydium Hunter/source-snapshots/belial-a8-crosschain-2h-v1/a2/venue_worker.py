"""One process per venue. Native feed failure cannot take down its peer."""
from __future__ import annotations
import argparse,collections,json,queue,subprocess,threading,time,urllib.error,urllib.request
from a2.common import *
add_legacy('perp')
import markets
markets.SYMBOLS=SYMBOLS

class Denied(RuntimeError):pass

def public_json(url,body=None):
    request=urllib.request.Request(url,data=None if body is None else canonical(body).encode(),
                                  headers={'User-Agent':'Belial-A1-READ-ONLY/1','Content-Type':'application/json'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request,timeout=15) as r:raw=r.read((32<<20)+1)
            if len(raw)>(32<<20):raise ValueError('METADATA_RESPONSE_TOO_LARGE')
            return strict_loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in (401,403):raise Denied('ACCESS_DENIED_NO_RETRY:'+str(e.code)) from None
            if attempt:raise
            if e.code==429:
                raw=e.headers.get('Retry-After','2')
                try:delay=float(raw)
                except ValueError:delay=2
                if not 0<=delay<=10:raise RuntimeError('RATE_LIMIT_WAIT_EXCEEDS_BUDGET')
                time.sleep(delay)
            else:time.sleep(1)
        except (OSError,TimeoutError):
            if attempt:raise
            time.sleep(1)
    raise RuntimeError('METADATA_UNAVAILABLE')

def discover_venue(venue):
    if venue=='hyperliquid':raw=public_json(markets.HL_INFO,{'type':'metaAndAssetCtxs'});rows=markets.parse_hl(raw)
    elif venue=='lighter':raw=public_json(markets.LI_INFO);rows=markets.parse_li(raw)
    else:raise ValueError('UNKNOWN_VENUE')
    return raw,rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--venue',choices=['hyperliquid','lighter'],required=True)
    ap.add_argument('--session',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args()
    stop=Stop().install();out=a.session/'venues'/a.venue;out.mkdir(parents=True,exist_ok=True);seq=0
    write_lock=threading.Lock();current=[None];native_lock=threading.Lock()
    def emit(kind,body):
        nonlocal seq
        with write_lock:
            seq+=1;print(canonical({'source':a.venue,'epoch':0,'sequence':seq,'kind':kind,'body':body}),flush=True)
    def controls():
        for line in sys.stdin:
            if stop.value:return
            try:
                row=strict_loads(line)
                if row.get('stop') is True:stop.value=True
                with native_lock:
                    p=current[0]
                    if p and p.poll() is None and p.stdin:p.stdin.write((canonical(row)+'\n').encode());p.stdin.flush()
            except Exception:pass
    threading.Thread(target=controls,name='venue-controls',daemon=True).start()
    try:
        heartbeat(out/'status.json','DISCOVERING')
        raw,rows=discover_venue(a.venue);atomic(out/'METADATA_RAW.json',raw)
        manifest={'ok':bool(rows),'venue':a.venue,'markets':rows,'orders':False,'paper_only':True}
        atomic(out/'MARKETS.json',manifest,durable=True);emit('markets',manifest)
        if not rows:
            heartbeat(out/'status.json','NO_REQUESTED_MARKETS');emit('venue_status',{'venue':a.venue,'state':'NO_REQUESTED_MARKETS'});return 0
        if not a.binary.is_file():
            heartbeat(out/'status.json','BUILD_UNAVAILABLE');emit('venue_status',{'venue':a.venue,'state':'BUILD_UNAVAILABLE'});return 0
        for restart in range(4):
            if stop.value or session_expired(a.session):break
            log=(out/f'native_{restart}.log').open('w')
            p=subprocess.Popen([str(a.binary),str(out/'MARKETS.json')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
            current[0]=p;denied=False
            try:
                heartbeat(out/'status.json','CONNECTING',native_pid=p.pid,restart=restart)
                for line in iter(p.stdout.readline,b''):
                    if len(line)>(32<<20):raise RuntimeError('FRAME_TOO_LARGE')
                    row=strict_loads(line)
                    if not isinstance(row,dict) or row.get('venue')!=a.venue:raise RuntimeError('WRONG_VENUE_ENVELOPE')
                    row['generation']=(restart+1)*1_000_000+int(row['generation'])
                    if row.get('transport')=='ACCESS_DENIED':denied=True
                    emit('book',row)
                    heartbeat(out/'status.json',row.get('transport','ACTIVE'),native_pid=p.pid,restart=restart)
                    if stop.value or session_expired(a.session):break
            finally:
                current[0]=None
                try:p.stdin.write(b'{"stop":true}\n');p.stdin.flush();p.stdin.close();p.wait(timeout=5)
                except Exception:
                    p.terminate()
                    try:p.wait(timeout=3)
                    except subprocess.TimeoutExpired:p.kill();p.wait()
                log.close()
            emit('book',{'venue':a.venue,'generation':(restart+1)*1_000_000+999999,'received_ns':time.time_ns(),
                         'transport':'ACCESS_DENIED' if denied else 'DISCONNECTED'})
            if denied:break
            if not stop.value and not session_expired(a.session):time.sleep(min(2**restart,8))
        heartbeat(out/'status.json','STOPPED' if stop.value or session_expired(a.session) else 'UNAVAILABLE')
    except Exception as e:
        heartbeat(out/'status.json','UNAVAILABLE',error=str(e));emit('venue_status',{'venue':a.venue,'state':'UNAVAILABLE','error':str(e)})
    return 0
if __name__=='__main__':raise SystemExit(main())
