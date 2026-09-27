"""Explicit readiness diagnostics. Trading still requires fresh executable books."""
import math
from a51.common import now_ms,atomic
from a8.chains import V2,V3
from a8.net import error_label
BNB_DEFAULT_RPC='https://bsc-rpc.publicnode.com'
FACTORY='0xca143ce32fe78f1f7019d7d551a6402fc5350c73'

def check_bnb_capabilities(rpc,session):
    proof=dict(at_ms=now_ms(),orders=False,purpose='CAPABILITY_ONLY_NOT_TRADE_EVIDENCE',passed=False,checks={})
    try:
        chain=rpc.call('eth_chainId');proof['checks']['chain_id']=chain
        if int(chain,16)!=56:raise RuntimeError('BNB_MAINNET_CHAINID_MISMATCH')
        block=rpc.call('eth_getBlockByNumber',['finalized',False])
        if not block or not block.get('hash') or not block.get('number'):raise RuntimeError('FINALIZED_BLOCKS_REQUIRED')
        proof['checks']['finalized_block']=block['number']
        # Same filtered method/shape as collection, only one block; zero matches is valid.
        logs=rpc.call('eth_getLogs',[dict(fromBlock=block['number'],toBlock=block['number'],address=[FACTORY],topics=[[V2,V3]])])
        if not isinstance(logs,list):raise RuntimeError('BNB_LOGS_RESPONSE_NOT_LIST')
        proof['checks']['eth_getLogs']=dict(ok=True,count=len(logs));proof['passed']=True
    except Exception as e:
        proof['error']=error_label(e);raise
    finally:atomic(session/'NETWORK_CHECK_BNB.json',proof,True)
    return proof

def snapshot(lab,at):
    a=lab.arms[0];missing=[];stale=[];fresh=[];blocked=[]
    for s in lab.symbols:
        b=a.books.get(s)
        if not b:missing.append(s)
        elif not (0<=at-b['received_ms']<=2000 and -500<=at-b['event_ms']<=2000):stale.append(s)
        else:fresh.append(s)
    catalog_ok=set(a.catalog)==set(lab.symbols) and at-a.metadata_ms<=1200000
    if not catalog_ok:blocked.append('BINANCE_METADATA')
    if missing:blocked.append('BOOKS_MISSING:'+','.join(missing))
    if stale:blocked.append('BOOKS_STALE:'+','.join(stale))
    for channel in ('wide','depth','flow'):
        if a.channel_state.get(channel)!='CONNECTED':blocked.append('BINANCE_CHANNEL_'+channel+':'+str(a.channel_state.get(channel,'WAITING')))
    chains={}
    for chain in ('solana','bsc'):
        s=lab.chain.statuses.get(chain,{});age=at-s.get('last',0)
        healthy=s.get('state')=='CONNECTED' and 0<=age<=20000
        chains[chain]=dict(state=s.get('state','WAITING'),healthy=healthy,age_ms=age if s else None,reason=s.get('reason'))
        if not healthy:blocked.append(chain.upper()+':'+chains[chain]['state']+(':'+str(s['reason']) if s.get('reason') else ''))
    return dict(at_ms=at,ready=not blocked,required_books=len(lab.symbols),fresh_books=len(fresh),missing_books=missing,stale_books=stale,
        catalog_ok=catalog_ok,channels=dict(a.channel_state),chains=chains,blocked=blocked,orders=False)

def capture_readiness(lab,at,prepared_ms,min_preparation_ms=120000,min_chain_stable_ms=60000):
    status=snapshot(lab,at)
    for chain in ('solana','bsc'):
        state=lab.chain.statuses.get(chain,{})
        if at-state.get('since',at)<min_chain_stable_ms:status['blocked'].append(chain.upper()+'_STABILITY_WARMUP')
    if prepared_ms<min_preparation_ms:status['blocked'].append('PREPARATION_120_SECONDS')
    missing=sorted(set(lab.symbols)-lab.price_seen)
    if missing:status['blocked'].append('BOOK_PRICE_NOT_OBSERVED:'+','.join(missing))
    prices={s:lab.price_arm.price_reason(s,at) for s in lab.symbols}
    status.update(price_quality=prices,price_ready=sum(v is None for v in prices.values()),pool_coverage={s:lab.entry_arm.coverage_reason(s,at) for s in lab.symbols})
    required=math.ceil(len(lab.symbols)*.8);status['required_price_ready']=required
    if status['price_ready']<required:status['blocked'].append('PRICE_HISTORY_READY:'+str(status['price_ready'])+'/'+str(required))
    status['ready']=not status['blocked'];return status
