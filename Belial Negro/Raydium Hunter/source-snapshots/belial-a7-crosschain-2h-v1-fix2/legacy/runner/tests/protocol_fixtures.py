"""Synthetic byte-layout fixtures. NOT chain observations or execution outcomes."""
import base64,hashlib,json
from decimal import Decimal as D, localcontext
import preflight_pools as p
import protocol_reference as r

def ratio(pq,pm):
 with localcontext() as ctx:
  ctx.prec=80
  return str(D(pq)*D(10)**6/(D(pm)*D(10)**9))

def key(i):return p.b58(bytes([i])*32)
def bkey(s):return p.pubkey_bytes(s)
def put(raw,offset,n,size=8):raw[offset:offset+size]=n.to_bytes(size,'little')
def account(owner,raw):return dict(owner=owner,data=[base64.b64encode(raw).decode(),'base64'],executable=False)
def pool_config(kind='CPMM',meme0=False):
 owner=r.CPMM if kind=='CPMM' else r.CLMM;m=p.CANONICAL_MINTS['PENGU']
 c,q=(m,r.WSOL) if meme0 else (r.WSOL,m);d0,d1=(6,9) if meme0 else (9,6)
 cfg=dict(symbol='PENGU',pool=key(12),meme_mint=m,owner=owner,pool_kind=kind,
   coin_mint=c,pc_mint=q,coin_decimals=d0,pc_decimals=d1,coin_vault=key(13),pc_vault=key(14),amm_config=key(15),protocol_fee_rate=120000,fund_fee_rate=40000)
 state=bytearray(637 if kind=='CPMM' else 1544);state[:8]=p.POOL_DISCRIMINATOR
 if kind=='CPMM':
  fields=[(8,cfg['amm_config']),(72,cfg['coin_vault']),(104,cfg['pc_vault']),(168,c),(200,q),(232,p.TOKEN),(264,p.TOKEN)]
  state[331]=d0;state[332]=d1
 else:
  fields=[(9,cfg['amm_config']),(73,c),(105,q),(137,cfg['coin_vault']),(169,cfg['pc_vault'])]
  state[233]=d0;state[234]=d1;put(state,253,1<<64,16)
 for at,value in fields:state[at:at+32]=bkey(value)
 conf=bytearray(236);conf[:8]=p.CONFIG_DISCRIMINATOR
 for at,value in [(12,2500),(20,120000),(28,40000)]:put(conf,at,value)
 cfg['cpmm_config_data_b64']=base64.b64encode(conf).decode()
 cfg['pool_data_b64']=base64.b64encode(state).decode()
 return cfg,account(owner,state),account(owner,conf)

def token_vault(mint,amount=1000000000000):
 data=bytearray(165);data[:32]=bkey(mint);put(data,64,amount);data[108]=1
 return account(p.TOKEN,data)

def cpmm(sell=True,meme0=False,creator_input=True,base_input=True):
 cfg,state,conf=pool_config('CPMM',meme0)
 ib,ob,ia,oa=(1000000000000,2000000000000,1000000000,1980000000) if sell else (2000000000000,1000000000000,2000000000,990000000)
 trade,creator=2500000,500000
 raw=bytearray(170);raw[:8]=r.SWAP;raw[8:40]=bkey(cfg['pool'])
 for at,v in [(40,ib),(48,ob),(56,ia),(64,oa),(153,trade),(161,creator)]:put(raw,at,v)
 raw[88]=int(base_input);raw[169]=int(creator_input)
 im,om=(cfg['meme_mint'],r.WSOL) if sell else (r.WSOL,cfg['meme_mint'])
 raw[89:121]=bkey(im);raw[121:153]=bkey(om)
 ip=ib+ia-300000-100000-(creator if creator_input else 0);op=ob-oa-(0 if creator_input else creator)
 bm,bq,pm,pq=(ib,ob,ip,op) if sell else (ob,ib,op,ip)
 expected=dict(side='SELL' if sell else 'BUY',log_type=5,direction=2 if im==cfg['coin_mint'] else 1,
  pre_meme_raw=bm,pre_quote_raw=bq,post_meme_raw=pm,post_quote_raw=pq,
  reference_post=ratio(pq,pm))
 return case(cfg,raw,expected),state,conf

def clmm(sell=True,meme0=False,new=True):
 cfg,state,conf=pool_config('CLMM',meme0);raw=bytearray(221 if new else 205)
 raw[:8]=r.SWAP;raw[8:40]=bkey(cfg['pool']);raw[40:72]=bkey(key(16))
 z=sell==meme0
 for at in [136,152]:put(raw,at,1000000000)
 raw[168]=int(z);put(raw,169,1<<64,16);put(raw,185,123000000000,16)
 expected=dict(side='SELL' if sell else 'BUY',log_type=6,direction=2 if z else 1,reference_post='0.001')
 c=case(cfg,raw,expected);pre=[4000000000000,8000000000000]
 post=[pre[0]+(1000000000 if z else -1000000000),pre[1]+(-1000000000 if z else 1000000000)]
 def bal(index,n,m,dec):return dict(accountIndex=index,mint=m,uiTokenAmount=dict(amount=str(n),decimals=dec))
 sig=c['signature'];payer=key(16)
 tx={'slot':c['slot'],'transaction':{'signatures':[sig],'message':{'header':{'numRequiredSignatures':1},
   'accountKeys':[payer,cfg['amm_config'],cfg['pool'],cfg['coin_vault'],cfg['pc_vault'],cfg['owner']],
   'instructions':[dict(programIdIndex=5,accounts=[0,1,2,3,4],data=p.b58(hashlib.sha256(b'global:swap_v2').digest()[:8]+bytes(33)))]}},
   'meta':{'err':None,'innerInstructions':[],'loadedAddresses':{'writable':[],'readonly':[]},
    'logMessages':logs(cfg,bytes(raw)),
    'preTokenBalances':[bal(3,pre[0],cfg['coin_mint'],cfg['coin_decimals']),bal(4,pre[1],cfg['pc_mint'],cfg['pc_decimals'])],
    'postTokenBalances':[bal(3,post[0],cfg['coin_mint'],cfg['coin_decimals']),bal(4,post[1],cfg['pc_mint'],cfg['pc_decimals'])]}}
 c['tx']=tx
 expected.update(pre_meme_raw=pre[0 if meme0 else 1],pre_quote_raw=pre[1 if meme0 else 0],post_meme_raw=post[0 if meme0 else 1],post_quote_raw=post[1 if meme0 else 0])
 return c,state,conf

def case(cfg,raw,expected):
 return {'cfg':cfg,'event_b64':base64.b64encode(raw).decode(),'pool_data_b64':cfg['pool_data_b64'],
  'signature':p.b58(bytes([27])*64),'slot':123456,'received_unix_ns':1790000000000000000,
  'expected':expected,'fixture':'SYNTHETIC_LAYOUT_NOT_CHAIN_OBSERVATION'}

def logs(cfg,raw):
 return ['Program '+cfg['owner']+' invoke [1]','Program data: '+base64.b64encode(raw).decode(),'Program '+cfg['owner']+' success']

def row_from_case(c):
 """Python-normalized fixture. Tests call Rust separately on the user's host."""
 cfg=c['cfg'];d=r.decode_event(base64.b64decode(c['event_b64']),cfg)
 if d['kind']=='CLMM':d.update(r.tx_vaults(c['tx'],cfg,d,c['signature'],c['slot']))
 evidence={'event_b64':c['event_b64'],'metadata':cfg,'pool_kind':d['kind'],'reference_basis':d['reference_basis'],
  'reference_post':float(d['reference_post'])}
 for k in ['pre_meme_raw','pre_quote_raw','post_meme_raw','post_quote_raw']:evidence[k]=str(d[k])
 if d['kind']=='CLMM':
  evidence['vaults_verified_with_same_transaction']=True
  evidence['transaction_evidence']=c['tx']
 q=D(d['meme_raw'])/D(10)**d['meme_decimals'];sol=D(d['quote_raw'])/D(10)**9
 return dict(symbol=cfg['symbol'],pool=cfg['pool'],signature=c['signature'],slot=c['slot'],received_unix_ms=c['received_unix_ns']//1000000,
   received_unix_ns=c['received_unix_ns'],log_type=d['log_type'],direction=d['direction'],side=d['side'],price_sol_per_meme=float(sol/q),
   meme_qty=float(q),quote_sol=float(sol),pool_meme_reserve=float(D(d['pre_meme_raw'])/D(10)**d['meme_decimals']),
   pool_quote_reserve_sol=float(D(d['pre_quote_raw'])/D(10)**9),protocol=evidence,reference_post_price=float(d['reference_post']))
