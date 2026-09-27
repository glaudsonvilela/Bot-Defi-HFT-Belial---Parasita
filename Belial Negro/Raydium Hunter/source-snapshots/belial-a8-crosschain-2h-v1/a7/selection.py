"""Frozen, address-based universe. No automatic ticker-to-token guesses."""
import base64, json, math, time
from pathlib import Path
from a51.common import atomic,now_ms
from a6.tails import contracts,fill
from a7.net import fetch,Denied

SOL_PROGRAMS={
 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA':'PUMPSWAP',
 '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8':'RAYDIUM_AMM_V4',
 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C':'RAYDIUM_CPMM',
 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK':'RAYDIUM_CLMM',
}
TOKEN_PROGRAM='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
SOL_QUOTES={'So11111111111111111111111111111111111111112',
 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
BNB_QUOTES={'0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
 '0x55d398326f99059ff775485246999027b3197955',
 '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d'}
FACTORIES={'0xca143ce32fe78f1f7019d7d551a6402fc5350c73':'PANCAKE_V2',
 '0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865':'PANCAKE_V3'}

def b58(s):
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=0
    for c in s:n=n*58+alphabet.index(c)
    return b'\0'*(len(s)-len(s.lstrip('1')))+(n.to_bytes((n.bit_length()+7)//8,'big') if n else b'')

def book_metrics(raw):
    bids=[(float(p),float(q)) for p,q in raw['bids']];asks=[(float(p),float(q)) for p,q in raw['asks']]
    if not bids or not asks or any(not math.isfinite(x) or x<=0 for r in bids+asks for x in r):raise ValueError('INVALID_DEPTH')
    if bids!=sorted(bids,reverse=True) or asks!=sorted(asks) or bids[0][0]>=asks[0][0]:raise ValueError('CROSSED_OR_UNORDERED_DEPTH')
    mid=(bids[0][0]+asks[0][0])/2;spread=(asks[0][0]-bids[0][0])/mid*10000
    depth=min(sum(p*q for p,q in bids if p>=mid*.999),sum(p*q for p,q in asks if p<=mid*1.001))
    entry=fill(bids,100/mid);close=fill(asks,100/mid)
    cost=(close['quote']/entry['quote']-1)*10000+10+3
    return dict(mid=mid,spread_bps=spread,depth_10bps_usdt=depth,roundtrip_100usdt_bps=cost)

def candidates_ranked(registry,metadata,tickers):
    cat=contracts(metadata);ticker={r['symbol']:r for r in tickers};out=[]
    for c in registry:
        if c['symbol'] not in cat or cat[c['symbol']]['base']!=c['base']:continue
        t=ticker.get(c['symbol'],{});volume=float(t.get('quoteVolume',0))
        if math.isfinite(volume) and volume>=500000:out.append(dict(c,volume_24h_usdt=volume))
    return sorted(out,key=lambda r:(-r['volume_24h_usdt'],r['symbol']))

def pool_candidates(rows,chain,mint):
    eq=(lambda a,b:a.lower()==b.lower()) if chain=='bsc' else (lambda a,b:a==b)
    result=[]
    for row in rows:
        if row.get('chainId')!=chain:continue
        a=row.get('baseToken',{}).get('address','');b=row.get('quoteToken',{}).get('address','')
        quote=b if eq(a,mint) else a if eq(b,mint) else None
        if not quote:continue
        if chain=='bsc':quote=quote.lower()
        if quote not in (BNB_QUOTES if chain=='bsc' else SOL_QUOTES):continue
        liquidity=float((row.get('liquidity') or {}).get('usd') or 0)
        if not math.isfinite(liquidity) or liquidity<50000:continue
        if chain=='solana' and row.get('dexId') not in ('raydium','pumpswap'):continue
        if chain=='bsc' and row.get('dexId')!='pancakeswap':continue
        result.append(dict(chain=chain,pool=row['pairAddress'],mint=mint,quote=quote,dex=row['dexId'],liquidity_usd=liquidity))
    return sorted(result,key=lambda r:-r['liquidity_usd'])[:5]

def eth_address(rpc,to,data):
    value=rpc.call('eth_call',[{'to':to,'data':data},'latest'])
    if not isinstance(value,str) or len(value)!=66:raise ValueError('EVM_ABI_ADDRESS')
    return '0x'+value[-40:].lower()

def qualify_pool(row,rpc):
    row=dict(row)
    if row['chain']=='solana':
        r=rpc.call('getAccountInfo',[row['pool'],{'encoding':'base64','commitment':'confirmed'}]);account=r['value']
        if not account or account['owner'] not in SOL_PROGRAMS:raise ValueError('SOL_POOL_OWNER_NOT_SUPPORTED')
        raw=base64.b64decode(account['data'][0]);mint=b58(row['mint']);quote=b58(row['quote'])
        if len(mint)!=32 or len(quote)!=32 or mint not in raw or quote not in raw:raise ValueError('POOL_MINT_BYTES_MISMATCH')
        m=rpc.call('getAccountInfo',[row['mint'],{'encoding':'jsonParsed','commitment':'confirmed'}])['value']
        if not m or m['owner']!=TOKEN_PROGRAM:raise ValueError('ONLY_STANDARD_SPL_MINT_SUPPORTED')
        parsed=m['data']['parsed']
        if parsed['type']!='mint' or not parsed['info']['isInitialized']:raise ValueError('INVALID_MINT')
        row.update(program=account['owner'],protocol=SOL_PROGRAMS[account['owner']],decimals=parsed['info']['decimals'])
    else:
        row['pool']=row['pool'].lower();row['mint']=row['mint'].lower()
        factory=eth_address(rpc,row['pool'],'0xc45a0155')
        if factory not in FACTORIES:raise ValueError('PANCAKE_FACTORY_MISMATCH')
        token0=eth_address(rpc,row['pool'],'0x0dfe1681');token1=eth_address(rpc,row['pool'],'0xd21220a7')
        if {token0,token1}!={row['mint'],row['quote']}:raise ValueError('EVM_POOL_TOKENS_MISMATCH')
        # Factory lookup proves the pool address, rather than trusting a spoofed factory() getter.
        encoded=token0[2:].zfill(64)+token1[2:].zfill(64)
        if FACTORIES[factory]=='PANCAKE_V2':selector='0xe6a43905';args=encoded
        else:
            fee=rpc.call('eth_call',[{'to':row['pool'],'data':'0xddca3f43'},'latest'])
            selector='0x1698ee82';args=encoded+fee[2:].zfill(64)
        if eth_address(rpc,factory,selector+args)!=row['pool']:raise ValueError('FACTORY_LOOKUP_MISMATCH')
        decimals=int(rpc.call('eth_call',[{'to':row['mint'],'data':'0x313ce567'},'latest']),16)
        if not 0<=decimals<=24:raise ValueError('TOKEN_DECIMALS_UNSUPPORTED')
        row.update(protocol=FACTORIES[factory],factory=factory,token0=token0,token1=token1,decimals=decimals)
    row['qualified_ms']=now_ms();return row

def prepare(out,sol,bnb,progress=lambda *_:None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    registry=json.loads((Path(__file__).parent/'candidates.json').read_text())
    metadata=fetch('https://fapi.binance.com/fapi/v1/exchangeInfo');tickers=fetch('https://fapi.binance.com/fapi/v1/ticker/24hr')
    coins={r['id']:r for r in fetch('https://api.coingecko.com/api/v3/coins/list?include_platform=true')}
    atomic(out/'exchangeInfo.json',metadata);atomic(out/'ticker24h.json',tickers)
    rows=[];rejected=[]
    for c in candidates_ranked(registry,metadata,tickers):
        progress(c['symbol'],len(rows))
        try:
            current=coins.get(c['coin_id'],{})
            if any(current.get('platforms',{}).get(k)!=v for k,v in c['platforms'].items()):raise ValueError('CANONICAL_MAPPING_CHANGED')
            book=fetch('https://fapi.binance.com/fapi/v1/depth?symbol='+c['symbol']+'&limit=100')
            atomic(out/(c['symbol']+'_depth.json'),book);metrics=book_metrics(book)
            if metrics['spread_bps']>15 or metrics['depth_10bps_usdt']<100 or metrics['roundtrip_100usdt_bps']>30:raise ValueError('BINANCE_LIQUIDITY_GATE')
            sources=[];errors=[]
            for platform,mint in c['platforms'].items():
                chain='solana' if platform=='solana' else 'bsc';rpc=sol if chain=='solana' else bnb
                try:pairs=fetch('https://api.dexscreener.com/token-pairs/v1/'+chain+'/'+mint)
                except Denied:raise
                except Exception as e:errors.append(dict(chain=chain,reason=str(e)));continue
                atomic(out/(c['symbol']+'_'+chain+'_pairs.json'),pairs)
                for pool in pool_candidates(pairs,chain,mint):
                    try:sources.append(qualify_pool(pool,rpc));break
                    except Denied:raise
                    except Exception as e:errors.append(dict(pool=pool['pool'],reason=str(e)))
            # A missing pool is visible coverage, never a fabricated token mapping.
            if not sources:errors.append(dict(reason='NO_QUALIFIED_ONCHAIN_POOL_NEW_ENTRY_DISABLED'))
            rows.append(dict(c,**metrics,sources=sources,qualification_errors=errors))
        except Denied:raise
        except Exception as e:rejected.append(dict(symbol=c['symbol'],reason=str(e)))
        atomic(out/'PROGRESS.json',dict(qualified=len(rows),rejected=rejected,orders=False))
        print('A7_SELECAO qualificadas='+str(len(rows))+' avaliando='+c['symbol'],flush=True)
    # Depth is the primary executable-liquidity metric; volume is the tie-breaker.
    rows.sort(key=lambda r:(-r['depth_10bps_usdt'],-r['volume_24h_usdt'],r['symbol']))
    chosen=rows[:20]
    doc=dict(at_ms=now_ms(),orders=False,selected=chosen,reserves=rows[20:],rejected=rejected,
        mapping_basis='CURATED_BINANCE_CONTRACT_TO_COINGECKO_ID_AND_FROZEN_ADDRESSES_RPC_POOL_CHECK',
        coverage={c['symbol']:[p['chain'] for p in c['sources']] for c in chosen},
        criteria=dict(count=20,min_volume_24h_usdt=500000,min_depth_10bps_usdt=100,max_spread_bps=15,min_pool_liquidity_usd=50000))
    atomic(out/'UNIVERSE.json',doc,True)
    if len(chosen)!=20:raise RuntimeError('NEED_20_QUALIFIED_NEW_MEMES_FOUND_'+str(len(chosen)))
    chains={p['chain'] for c in chosen for p in c['sources']}
    if chains!={'solana','bsc'}:raise RuntimeError('BOTH_CHAINS_REQUIRED_IN_UNIVERSE')
    return doc,metadata
