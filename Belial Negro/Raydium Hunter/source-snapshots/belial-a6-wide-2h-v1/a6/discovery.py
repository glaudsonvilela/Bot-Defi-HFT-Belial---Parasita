"""Sampled public DEX discovery; cached API observations are not on-chain fills."""
import collections,concurrent.futures,time,urllib.error
from a6.http_pool import Pool
from a51.common import atomic,now_ms,session_expired,Stop

class Discovery:
    def __init__(self,fetch=None):
        self.http=Pool();self.fetch=fetch or self.http.json;self.pools={};self.tokens=collections.OrderedDict();self.sources={};self.page=1;self.cursor=0;self.backoff={}
    def call(self,source,url):
        now=now_ms()
        if now<self.backoff.get(source,0):return None
        try:
            value=self.fetch(url);self.sources[source]=dict(state='ACTIVE',last_success_ms=now)
            return value
        except Exception as e:
            code=getattr(e,'code',None);delay=120
            if code in (401,403,451):delay=86400
            elif code==429:
                try:delay=max(120,float(e.headers.get('Retry-After','120')))
                except Exception:delay=120
            self.backoff[source]=now+int(delay*1000);self.sources[source]=dict(state='ACCESS_DENIED' if code in (401,403,451) else 'BACKOFF',http=code,error=type(e).__name__,retry_ms=self.backoff[source])
            return None
    def raydium(self):
        # Pages rotate: 100 per 15 seconds, first 1,000 by 24h volume.
        page=self.page;self.page=self.page%10+1
        value=self.call('raydium',f'https://api-v3.raydium.io/pools/info/list?poolType=all&poolSortField=volume24h&sortType=desc&pageSize=100&page={page}')
        if value is None:return
        if not isinstance(value,dict) or not isinstance(value.get('data'),dict) or not isinstance(value['data'].get('data'),list):
            self.sources['raydium']=dict(state='SCHEMA_ERROR',at_ms=now_ms());return
        rows=value['data']['data']
        for r in rows:
            if not isinstance(r,dict) or not isinstance(r.get('id'),str):continue
            a=r.get('mintA') or {};b=r.get('mintB') or {}
            self.pools['solana:'+r['id']]=dict(address=r['id'],chain='solana',protocol='raydium',symbol=str(a.get('symbol','?'))+'/'+str(b.get('symbol','?')),mint_a=a.get('address'),mint_b=b.get('address'),price_quote=r.get('price'),liquidity_usd=r.get('tvl'),volume24h=(r.get('day') or {}).get('volume'),observed_ms=now_ms(),source='raydium',short_eligible=False,cadence='PAGINATED_CACHED_API_NOT_SWAP_STREAM')
    def dex(self):
        value=self.call('dex_profiles','https://api.dexscreener.com/token-profiles/latest/v1')
        if isinstance(value,list):
            for r in value:
                mint=r.get('tokenAddress','')
                if r.get('chainId')=='solana' and 32<=len(mint)<=44 and mint.isalnum():self.tokens[mint]=now_ms();self.tokens.move_to_end(mint)
        while len(self.tokens)>600:self.tokens.popitem(last=False)
        mints=list(self.tokens)
        if not mints:return
        begin=self.cursor%len(mints);batch=(mints+mints)[begin:begin+min(30,len(mints))];self.cursor+=len(batch)
        rows=self.call('dex_pairs','https://api.dexscreener.com/tokens/v1/solana/'+','.join(batch))
        if not isinstance(rows,list):
            if rows is not None:self.sources['dex_pairs']=dict(state='SCHEMA_ERROR',at_ms=now_ms())
            return
        for r in rows:
            address=r.get('pairAddress');a=r.get('baseToken') or {};b=r.get('quoteToken') or {}
            if r.get('chainId')!='solana' or not isinstance(address,str):continue
            self.pools['solana:'+address]=dict(address=address,chain='solana',protocol=r.get('dexId'),symbol=str(a.get('symbol','?'))+'/'+str(b.get('symbol','?')),mint_a=a.get('address'),mint_b=b.get('address'),price_usd=r.get('priceUsd'),liquidity_usd=(r.get('liquidity') or {}).get('usd'),volume24h=(r.get('volume') or {}).get('h24'),observed_ms=now_ms(),source='dexscreener',short_eligible=False,cadence='SAMPLED_CACHED_API_NOT_SWAP_STREAM')
    def prune(self):
        now=now_ms();self.pools={k:v for k,v in self.pools.items() if now-v['observed_ms']<1800000}
        if len(self.pools)>4096:self.pools=dict(sorted(self.pools.items(),key=lambda kv:kv[1]['observed_ms'],reverse=True)[:4096])
    def view(self):
        self.prune();now=now_ms()
        return dict(at_ms=now,sources=self.sources,pools_seen=len(self.pools),pools_recent=sum(now-v['observed_ms']<300000 for v in self.pools.values()),protocols=dict(collections.Counter(v['protocol'] for v in self.pools.values())),rows=sorted(self.pools.values(),key=lambda r:r['observed_ms'],reverse=True),axiom=dict(state='NO_DOCUMENTED_PUBLIC_API_VERIFIED',direct_integration=False),coverage='Raydium first 1000 by volume + rotating latest-profile Solana tokens; not all pools',short_eligible=False,orders=False)

def run(session):
    stop=Stop().install();d=Discovery();out=session/'discovery';out.mkdir(exist_ok=True);iteration=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        while not stop.value and not session_expired(session):
            t=time.monotonic();jobs=[ex.submit(d.raydium)]
            if iteration%4==0:jobs.append(ex.submit(d.dex))
            for j in jobs:
                try:j.result(timeout=25)
                except Exception:pass
            state=d.view();atomic(out/'state.json',state);atomic(out/'status.json',dict(at_ms=now_ms(),state='SCANNING',pools=state['pools_seen'],sources=state['sources'],orders=False));iteration+=1
            until=t+15
            while not stop.value and not session_expired(session) and time.monotonic()<until:time.sleep(.5)
    atomic(out/'COMPLETE.json',dict(ok=True,orders=False))
if __name__=='__main__':
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);run(p.parse_args().session)
