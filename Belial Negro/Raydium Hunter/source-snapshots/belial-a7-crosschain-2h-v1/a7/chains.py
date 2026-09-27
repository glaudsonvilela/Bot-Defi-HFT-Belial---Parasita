"""Public Solana transactions + finalized Pancake V2/V3 swap receipts. No writes."""
import asyncio,collections,contextlib,json,queue,threading,time
from decimal import Decimal
from a51.common import now_ms,canonical
from a51.ws_relay import upstream_connect,read_message,frame
from a7.net import Denied

V2='0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
V3='0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83'
TRANSFER='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

def token_delta(meta,wallet,mint):
    sides=[]
    for key in ('preTokenBalances','postTokenBalances'):
        side={}
        for b in meta.get(key) or []:
            if b.get('owner')==wallet and b.get('mint')==mint:
                i=b['accountIndex'];v=b['uiTokenAmount']
                if i in side:raise ValueError('DUPLICATE_BALANCE_ACCOUNT')
                side[i]=Decimal(v['amount']).scaleb(-v['decimals'])
        sides.append(side)
    if not sides[0] or sides[0].keys()!=sides[1].keys():return None
    return sum(sides[1].values())-sum(sides[0].values())

def solana_events(tx,signature,pool,log_received_ms,available_ms):
    if not tx or not tx.get('meta') or tx['meta'].get('err') is not None:return []
    message=tx['transaction']['message'];static=message['accountKeys']
    if signature not in tx['transaction']['signatures']:raise ValueError('SOL_TX_SIGNATURE_MISMATCH')
    keys=static+(tx['meta'].get('loadedAddresses') or {}).get('writable',[])+(tx['meta'].get('loadedAddresses') or {}).get('readonly',[])
    if pool['pool'] not in keys or pool['program'] not in keys:return []
    logs=tx['meta'].get('logMessages') or []
    if not any(l.startswith('Program '+pool['program']+' invoke [') for l in logs):return []
    events=[]
    for wallet in static[:message['header']['numRequiredSignatures']]:
        delta=token_delta(tx['meta'],wallet,pool['mint'])
        quote=token_delta(tx['meta'],wallet,pool['quote'])
        status='UNKNOWN' if delta is None else 'NET_ZERO' if delta==0 else 'NET_REDUCTION' if delta<0 else 'NET_INCREASE'
        # A signer whose token balance falls and quote balance increases in a
        # transaction touching the pool is evidence of disposal, not proof of HFT intent.
        confirmed=status=='NET_REDUCTION' and quote is not None and quote>0
        events.append(dict(kind='chain_trade',chain='solana',symbol=pool['symbol'],pool=pool['pool'],
            mint=pool['mint'],wallet=wallet,tx=signature,slot=tx['slot'],
            available_ms=available_ms,received_ms=log_received_ms,
            chain_time_ms=tx['blockTime']*1000 if tx.get('blockTime') else None,
            net_status=status,token_delta=str(delta) if delta is not None else None,
            quantity=float(-delta) if confirmed else 0.,qualified_sell=confirmed,
            attribution='SIGNER_NET_DISPOSAL_IN_POOL_TRANSACTION_NOT_INSTRUCTION_OWNER',
            quote_delta=str(quote) if quote is not None else None))
    return events

def words(data):
    raw=data.removeprefix('0x')
    if len(raw)%64:raise ValueError('EVM_LOG_WORDS')
    return [int(raw[i:i+64],16) for i in range(0,len(raw),64)]

def bnb_events(log,receipt,tx,pool,block_time_ms,available_ms):
    ident=log['transactionHash'].lower();wallet=tx['from'].lower()
    if (receipt['transactionHash'].lower()!=ident or tx['hash'].lower()!=ident
        or receipt['blockHash'].lower()!=log['blockHash'].lower()
        or int(receipt['status'],16)!=1 or log.get('removed')):return []
    matching=[r for r in receipt['logs'] if r['address'].lower()==pool['pool']
              and r.get('logIndex')==log.get('logIndex') and r.get('topics')==log.get('topics') and r.get('data')==log.get('data')]
    if len(matching)!=1:raise ValueError('SWAP_RECEIPT_MISMATCH')
    if len(log.get('topics',[]))!=3:raise ValueError('SWAP_INDEXED_FIELDS')
    values=words(log['data']);index=0 if pool['mint']==pool['token0'] else 1
    if log['topics'][0].lower()==V2:
        if pool['protocol']!='PANCAKE_V2' or len(values)!=4:raise ValueError('SWAP_V2_ABI')
        pool_delta=values[index]-values[index+2]
    elif log['topics'][0].lower()==V3:
        if pool['protocol']!='PANCAKE_V3' or len(values)!=7:raise ValueError('SWAP_V3_ABI')
        v=values[index];pool_delta=v-(1<<256) if v>=(1<<255) else v
    else:return []
    if pool_delta<=0:return []
    delta=0;found=False
    for row in receipt['logs']:
        topics=row.get('topics',[])
        if row['address'].lower()!=pool['mint'] or len(topics)!=3 or topics[0].lower()!=TRANSFER:continue
        amount=words(row['data'])
        if len(amount)!=1:raise ValueError('TRANSFER_ABI')
        source='0x'+topics[1][-40:].lower();dest='0x'+topics[2][-40:].lower()
        if source==wallet:delta-=amount[0];found=True
        if dest==wallet:delta+=amount[0];found=True
    qualified=found and delta<0
    return [dict(kind='chain_trade',chain='bsc',symbol=pool['symbol'],pool=pool['pool'],mint=pool['mint'],
        wallet=wallet,tx=ident,block=int(log['blockNumber'],16),available_ms=available_ms,received_ms=available_ms,
        chain_time_ms=block_time_ms,net_status='NET_REDUCTION' if qualified else 'NET_ZERO' if found and delta==0 else 'UNKNOWN',
        token_delta=str(Decimal(delta).scaleb(-pool['decimals'])) if found else None,
        quantity=float(Decimal(-delta).scaleb(-pool['decimals'])) if qualified else 0.,qualified_sell=qualified,
        attribution='TX_SENDER_NET_TRANSFER_OUT_NOT_FULL_BALANCE_OR_HFT_IDENTITY')]

class Sensors:
    def __init__(self,pools,sol,bnb,ws,emit,stop):
        self.pools=pools;self.sol=sol;self.bnb=bnb;self.ws=ws;self.emit=emit;self.stop=stop
        self.jobs=queue.Queue(maxsize=128);self.threads=[];self.cache=collections.OrderedDict();self.cache_lock=threading.Lock()
        self.pending_pools=set();self.pending_lock=threading.Lock();self.last_heartbeat={}
    def status(self,chain,state,reason=None):
        at=now_ms()
        if state=='HEARTBEAT' and at-self.last_heartbeat.get(chain,0)<5000:return
        if state=='HEARTBEAT':self.last_heartbeat[chain]=at
        self.emit(dict(kind='chain_status',chain=chain,state=state,reason=reason,available_ms=at))
    def start(self):
        for target in (self.sol_thread,self.sol_worker,self.sol_worker,self.bnb_worker):
            t=threading.Thread(target=target,daemon=True);t.start();self.threads.append(t)
    def sol_thread(self):
        try:asyncio.run(self.sol_loop())
        except Exception as e:self.status('solana','FAILED',type(e).__name__)
    async def sol_loop(self):
        pools=[p for p in self.pools if p['chain']=='solana'];backoff=1
        while not self.stop.is_set():
            writer=None;ping=None;reader_task=None
            try:
                reader,writer=await upstream_connect(self.ws);lock=asyncio.Lock();by_id={i+1:p for i,p in enumerate(pools)};subs={}
                async def send(obj):
                    async with lock:writer.write(frame(1,canonical(obj),True));await writer.drain()
                for ident,pool in by_id.items():
                    await send(dict(jsonrpc='2.0',id=ident,method='logsSubscribe',params=[{'mentions':[pool['pool']]},{'commitment':'confirmed'}]));await asyncio.sleep(.12)
                async def heartbeat():
                    while not self.stop.is_set():
                        await asyncio.sleep(15)
                        async with lock:writer.write(frame(9,b'a7',True));await writer.drain()
                ping=asyncio.create_task(heartbeat());last=time.monotonic();ready=False
                reader_task=asyncio.create_task(read_message(reader,writer,expect_mask=False,send_lock=lock))
                while not self.stop.is_set():
                    done,_=await asyncio.wait([reader_task],timeout=1)
                    if not done:
                        if ready and time.monotonic()-last<40:self.status('solana','HEARTBEAT')
                        if time.monotonic()-last>45:raise RuntimeError('SOL_WS_HEARTBEAT_TIMEOUT')
                        continue
                    row=reader_task.result();last=time.monotonic()
                    reader_task=asyncio.create_task(read_message(reader,writer,expect_mask=False,send_lock=lock))
                    if row is None:continue
                    if 'id' in row:
                        if row.get('error') or row['id'] not in by_id or not isinstance(row.get('result'),int):raise RuntimeError('SOL_SUBSCRIPTION_REJECTED')
                        subs[row['result']]=by_id[row['id']]
                        if len(subs)==len(pools):self.status('solana','CONNECTED');ready=True;backoff=1
                        continue
                    if ready:self.status('solana','HEARTBEAT')
                    p=row.get('params',{});pool=subs.get(p.get('subscription'));result=p.get('result',{});value=result.get('value',{})
                    if not pool or value.get('err') is not None:continue
                    signature=value.get('signature')
                    if not signature:continue
                    with self.pending_lock:
                        scheduled=pool['pool'] not in self.pending_pools
                        if scheduled:self.pending_pools.add(pool['pool'])
                    if scheduled:
                        try:self.jobs.put_nowait((signature,pool,now_ms()))
                        except queue.Full:
                            with self.pending_lock:self.pending_pools.discard(pool['pool'])
                            self.status('solana','GAP','TRANSACTION_QUEUE_OVERFLOW')
                    else:self.emit(dict(kind='sampling',chain='solana',pool=pool['pool'],available_ms=now_ms()))
                    if ready:self.status('solana','HEARTBEAT')
            except Exception as e:
                self.status('solana','GAP',type(e).__name__)
                if getattr(e,'status',None) in (401,403,451):self.status('solana','FAILED','ACCESS_DENIED');return
            finally:
                for task in (ping,reader_task):
                    if task:
                        task.cancel()
                        with contextlib.suppress(Exception,asyncio.CancelledError):await task
                if writer:
                    writer.close()
                    with contextlib.suppress(Exception):await writer.wait_closed()
            for _ in range(backoff*2):
                if self.stop.is_set():return
                await asyncio.sleep(.5)
            backoff=min(backoff*2,60)
    def sol_worker(self):
        while not self.stop.is_set():
            try:signature,pool,received=self.jobs.get(timeout=.5)
            except queue.Empty:continue
            try:
                if now_ms()-received>10000:self.status('solana','SAMPLE_ERROR','TX_QUEUE_STALE');continue
                with self.cache_lock:tx=self.cache.get(signature)
                if tx is None:
                    tx=self.sol.call('getTransaction',[signature,{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':1}])
                    if tx is None:raise ValueError('TX_UNAVAILABLE')
                    with self.cache_lock:
                        self.cache[signature]=tx
                        while len(self.cache)>512:self.cache.popitem(last=False)
                at=now_ms()
                self.emit(dict(kind='chain_evidence',chain='solana',signature=signature,pool=pool['pool'],available_ms=at,transaction=tx))
                for e in solana_events(tx,signature,pool,received,at):self.emit(e)
            except Denied:self.status('solana','FAILED','RPC_ACCESS_DENIED');return
            except Exception as e:self.status('solana','SAMPLE_ERROR',str(e) if isinstance(e,(RuntimeError,ValueError)) else type(e).__name__)
            finally:
                with self.pending_lock:self.pending_pools.discard(pool['pool'])
    def bnb_worker(self):
        pools={p['pool']:p for p in self.pools if p['chain']=='bsc'};last=None;anchor=None;seen=collections.OrderedDict()
        while not self.stop.is_set():
            try:
                head=self.bnb.call('eth_getBlockByNumber',['finalized',False])
                if not head:raise RuntimeError('BNB_FINALIZED_TAG_UNAVAILABLE')
                number=int(head['number'],16)
                if last is None:last=number;anchor=head['hash'];self.status('bsc','CONNECTED');continue
                check=self.bnb.call('eth_getBlockByNumber',[hex(last),False])
                if not check or check['hash']!=anchor:raise RuntimeError('FINALIZED_ANCHOR_CHANGED')
                if number==last:self.status('bsc','HEARTBEAT');self.stop.wait(1);continue
                if number<last or number-last>32:
                    self.status('bsc','GAP','FINALIZED_RANGE_GAP');last=number;anchor=head['hash'];continue
                logs=self.bnb.call('eth_getLogs',[{'fromBlock':hex(last+1),'toBlock':hex(number),'address':list(pools),'topics':[[V2,V3]]}])
                if len(logs)>128:
                    self.status('bsc','GAP','SWAP_BACKLOG_OVERFLOW');last=number;anchor=head['hash'];continue
                batch_start=time.monotonic();served=set()
                for log in sorted(logs,key=lambda r:(int(r['blockNumber'],16),int(r['logIndex'],16)),reverse=True):
                    # Bounded newest-first fair sample; no backlog is represented as complete coverage.
                    if time.monotonic()-batch_start>6 or log['address'].lower() in served:
                        self.emit(dict(kind='sampling',chain='bsc',pool=log['address'].lower(),available_ms=now_ms()));continue
                    served.add(log['address'].lower())
                    if self.stop.is_set():return
                    if log['address'].lower() not in pools or not last<int(log['blockNumber'],16)<=number:raise ValueError('UNREQUESTED_LOG')
                    key=(log['transactionHash'],log['address'].lower())
                    if key in seen:continue
                    pool=pools[key[1]];receipt=self.bnb.call('eth_getTransactionReceipt',[key[0]]);tx=self.bnb.call('eth_getTransactionByHash',[key[0]])
                    block=self.bnb.call('eth_getBlockByNumber',[log['blockNumber'],False])
                    if block['hash'].lower()!=log['blockHash'].lower():raise ValueError('LOG_BLOCK_MISMATCH')
                    at=now_ms();self.emit(dict(kind='chain_evidence',chain='bsc',available_ms=at,pool=pool['pool'],log=log,receipt=receipt,transaction=tx,block=block))
                    events=bnb_events(log,receipt,tx,pool,int(block['timestamp'],16)*1000,at)
                    for e in events:self.emit(e)
                    if events:seen[key]=True
                    while len(seen)>4096:seen.popitem(last=False)
                last=number;anchor=head['hash'];self.status('bsc','HEARTBEAT')
            except Denied:self.status('bsc','FAILED','ACCESS_DENIED');return
            except Exception as e:self.status('bsc','GAP',str(e) if isinstance(e,(ValueError,RuntimeError)) else type(e).__name__);self.stop.wait(5)
            self.stop.wait(1)
