"""Deterministic A5 IPC fixture. Stdlib-only. Never contacts a network."""
import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument('--venue',choices=('hyperliquid','lighter'),required=True)
ap.add_argument('--session',type=Path,required=True)
ap.add_argument('--binary')
ap.add_argument('--oneshot',action='store_true')
a=ap.parse_args()

stop=threading.Event()
reconnect=threading.Event()
generation=1

def now_ms():
    return time.time_ns()//1_000_000

def write_status(state,**extra):
    path=a.session/'venues'/a.venue/'status.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    tmp.write_text(json.dumps(dict(state=state,venue=a.venue,fixture_only=True,orders=False,**extra),sort_keys=True)+'\n',encoding='utf-8')
    os.replace(tmp,path)

def emit(kind,body):
    sys.stdout.write(json.dumps(dict(source=a.venue,epoch=0,sequence=emit.seq,kind=kind,body=body),separators=(',',':'))+'\n')
    sys.stdout.flush()
    emit.seq+=1
emit.seq=1

def market():
    return dict(
        venue=a.venue,
        symbol='FARTCOIN',
        market='FARTCOIN' if a.venue=='hyperliquid' else '42',
        multiplier=1,
        size_step=.001,
        min_size=.001,
        min_notional=10.0,
        status='ACTIVE',
    )

def hyper_book(t,gen):
    return dict(
        venue='hyperliquid',
        generation=gen,
        received_ns=time.time_ns(),
        data=dict(
            channel='l2Book',
            data=dict(
                coin='FARTCOIN',
                time=t,
                levels=[
                    [dict(px='100',sz='10000')],
                    [dict(px='100.01',sz='10000')],
                ],
            ),
        ),
    )

def lighter_book(t,nonce,begin,snapshot,gen):
    return dict(
        venue='lighter',
        generation=gen,
        received_ns=time.time_ns(),
        data=dict(
            channel='order_book:42',
            type='subscribed/order_book' if snapshot else 'update/order_book',
            timestamp=t,
            order_book=dict(
                nonce=nonce,
                begin_nonce=begin,
                last_updated_at=t*1000,
                bids=[dict(price='100',size='10000')],
                asks=[dict(price='100.01',size='10000')],
            ),
        ),
    )

def signal_stop(*_):
    stop.set()

signal.signal(signal.SIGTERM,signal_stop)
signal.signal(signal.SIGINT,signal_stop)

def controls():
    for line in sys.stdin:
        try:
            row=json.loads(line)
        except Exception:
            continue
        if row.get('stop') is True:
            stop.set()
            return
        if row.get('reconnect')==a.venue:
            reconnect.set()

threading.Thread(target=controls,name='a5-fixture-controls',daemon=True).start()

write_status('FIXTURE_READY')
emit('markets',dict(venue=a.venue,markets=[market()]))

i=0
nonce=0
snapshot=True
while not stop.is_set():
    i+=1
    t=now_ms()
    if reconnect.is_set():
        reconnect.clear()
        generation+=1
        nonce=0
        snapshot=True

    if a.venue=='hyperliquid':
        body=hyper_book(t,generation)
    else:
        nonce+=1
        begin=None if snapshot else nonce-1
        # One deliberate gap exercises quarantine/reconnect behavior.
        if i==20 and generation==1:
            nonce+=2
            begin=nonce-1
        body=lighter_book(t,nonce,begin,snapshot,generation)
        snapshot=False

    emit('book',body)
    write_status('FIXTURE_STREAMING',generation=generation,frames=i)

    if a.oneshot and i>=3:
        break
    stop.wait(.012)

write_status('FIXTURE_STOPPED',generation=generation,frames=i)
