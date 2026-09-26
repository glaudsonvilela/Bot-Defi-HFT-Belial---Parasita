"""FIX2 independent integer/Decimal audit of Raydium CPMM/CLMM swap records.
No orders, no RPC, no imports from the trading engines. FIX4 token-program aware.
CPMM uses frozen on-chain config rates. CLMM uses sqrt price, not vault ratio.
"""
from __future__ import annotations
import base64, hashlib, math
from decimal import Decimal as D, localcontext

AMM='675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
CPMM='CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C'
CLMM='CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK'
WSOL='So11111111111111111111111111111111111111112'
ALPH='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
SWAP=hashlib.sha256(b'event:SwapEvent').digest()[:8]
U64_MAX=(1<<64)-1

def b58(raw):
 n=int.from_bytes(raw,'big');s=''
 while n:n,i=divmod(n,58);s=ALPH[i]+s
 return '1'*(len(raw)-len(raw.lstrip(b'\0')))+s

def uint(raw,offset,n=8):
 if offset<0 or len(raw)<offset+n:raise ValueError('TRUNCATED_INTEGER')
 return int.from_bytes(raw[offset:offset+n],'little')

def boolean(raw,offset):
 if offset>=len(raw) or raw[offset] not in (0,1):raise ValueError('INVALID_BOOL')
 return bool(raw[offset])

def scoped_swaps(logs, owner, pool):
 """Only log frames that committed; failed CPI emissions are discarded.
 Return pool-matched Anchor SwapEvent payloads. A truncated stack fails closed.
 """
 stack=[];committed=[]
 for line in logs:
  if not isinstance(line,str):raise ValueError('LOG_NOT_STRING')
  parts=line.split()
  if len(parts)==4 and parts[0]=='Program' and parts[2]=='invoke':
   try:depth=int(parts[3].strip('[]'))
   except ValueError:raise ValueError('BAD_LOG_DEPTH')
   if depth!=len(stack)+1:raise ValueError('LOG_STACK_DISCONTINUITY')
   stack.append([parts[1],[]])
  elif len(parts)>=3 and parts[0]=='Program' and (parts[2]=='success' or parts[2].startswith('failed')):
   if not stack or stack[-1][0]!=parts[1]:raise ValueError('LOG_STACK_MISMATCH')
   _,events=stack.pop()
   if parts[2]=='success':
    if stack:stack[-1][1].extend(events)
    else:committed.extend(events)
  elif line.startswith('Program data: ') and stack and stack[-1][0]==owner:
   raw=base64.b64decode(line[len('Program data: '):].strip(),validate=True)
   if raw[:8]==SWAP and len(raw)>=40 and b58(raw[8:40])==pool:
    stack[-1][1].append(raw)
 if stack:raise ValueError('TRUNCATED_LOG_STACK')
 return committed

def _identity(cfg):
 owner=cfg.get('owner') or cfg.get('program_id')
 c,p=cfg['coin_mint'],cfg['pc_mint'];m=cfg['meme_mint']
 if owner not in (CPMM,CLMM) or {c,p}!={m,WSOL}:raise ValueError('PROTOCOL_PAIR_IDENTITY')
 d0,d1=int(cfg['coin_decimals']),int(cfg['pc_decimals'])
 if not (0<=d0<=18 and 0<=d1<=18) or (d1 if c==m else d0)!=9:raise ValueError('DECIMALS')
 return owner,c,p,m,d0,d1

def decode_event(raw,cfg):
 """Return canonical raw quantities/reference; CLMM reserves require tx separately."""
 owner,c,p,m,d0,d1=_identity(cfg)
 if raw[:8]!=SWAP or b58(raw[8:40])!=cfg['pool']:raise ValueError('SWAP_IDENTITY')
 with localcontext() as ctx:
  ctx.prec=80
  if owner==CPMM:
   if len(raw)!=170:raise ValueError('CPMM_EVENT_LAYOUT:'+str(len(raw)))
   ib,ob,ia,oa=[uint(raw,x) for x in (40,48,56,64)]
   if uint(raw,72) or uint(raw,80):raise ValueError('TRANSFER_FEE_UNSUPPORTED')
   base_input=boolean(raw,88);im,om=b58(raw[89:121]),b58(raw[121:153])
   if {im,om}!={m,WSOL}:raise ValueError('CPMM_EVENT_MINTS')
   trade,creator=uint(raw,153),uint(raw,161);on_input=boolean(raw,169)
   pr,fr=int(cfg['protocol_fee_rate']),int(cfg['fund_fee_rate'])
   if min(pr,fr)<0 or pr+fr>1000000:raise ValueError('CPMM_FEE_RATES')
   if min(ib,ob,ia,oa)<=0 or trade>=ia:raise ValueError('CPMM_AMOUNTS')
   ip=ib+ia-(trade*pr//1000000)-(trade*fr//1000000)-(creator if on_input else 0)
   op=ob-oa-(0 if on_input else creator)
   if not (0<ip<=U64_MAX and 0<op<=U64_MAX):raise ValueError('CPMM_RESERVES_DEPLETED_OR_OVERFLOW')
   sell=im==m;md=d0 if c==m else d1
   qm,qq=(ia,oa) if sell else (oa,ia)
   bm,bq=(ib,ob) if sell else (ob,ib)
   pm,pq=(ip,op) if sell else (op,ip)
   pre=D(bq)*D(10)**md/(D(bm)*D(10)**9)
   post=D(pq)*D(10)**md/(D(pm)*D(10)**9)
   return dict(kind='CPMM',side='SELL' if sell else 'BUY',log_type=5,direction=2 if im==c else 1,
       meme_raw=qm,quote_raw=qq,meme_decimals=md,pre_meme_raw=bm,pre_quote_raw=bq,
       post_meme_raw=pm,post_quote_raw=pq,reference_pre=pre,reference_post=post,
       base_input=base_input,trade_fee_raw=trade,creator_fee_raw=creator,
       reference_basis='CPMM_POST_LP_RESERVES_FROZEN_CONFIG_NOT_EXECUTION')
  if len(raw) not in (205,221):raise ValueError('CLMM_EVENT_LAYOUT:'+str(len(raw)))
  a0,a1=uint(raw,136),uint(raw,152)
  if uint(raw,144) or uint(raw,160):raise ValueError('TRANSFER_FEE_UNSUPPORTED')
  z=boolean(raw,168);sqrt=uint(raw,169,16);liq=uint(raw,185,16)
  tick=int.from_bytes(raw[201:205],'little',signed=True)
  if min(a0,a1,sqrt)<=0 or not -443636<=tick<=443636:raise ValueError('CLMM_AMOUNTS_SQRT_TICK')
  # Tick interval and sqrt must agree (allow one boundary tick).
  tick_float=2*math.log(float(sqrt)/(2**64))/math.log(1.0001)
  if abs(tick_float-tick)>2.00001:raise ValueError('CLMM_SQRT_TICK_INCONSISTENT')
  raw_price=D(sqrt)*D(sqrt)/D(1<<128)*D(10)**(d0-d1)
  post=raw_price if c==m else 1/raw_price
  sell=z==(c==m);md=d0 if c==m else d1
  return dict(kind='CLMM',side='SELL' if sell else 'BUY',log_type=6,direction=2 if z else 1,
       meme_raw=a0 if c==m else a1,quote_raw=a1 if c==m else a0,meme_decimals=md,
       amount0_raw=a0,amount1_raw=a1,zero_for_one=z,reference_pre=None,reference_post=post,
       sqrt_price_x64=str(sqrt),liquidity=str(liq),tick=tick,
       reference_basis='CLMM_SQRT_PRICE_X64_NOT_VAULT_RATIO_NOT_EXECUTION')

def account_keys(tx):
 keys=[k.get('pubkey') if isinstance(k,dict) else k for k in tx['transaction']['message']['accountKeys']]
 loaded=(tx.get('meta') or {}).get('loadedAddresses') or {}
 return keys+list(loaded.get('writable') or [])+list(loaded.get('readonly') or [])

def tx_vaults(tx,cfg,event,signature=None,slot=None):
 if not isinstance(tx,dict) or 'err' not in (tx.get('meta') or {}) or tx['meta']['err'] is not None:raise ValueError('FAILED_OR_MISSING_TX')
 if signature is not None and signature not in tx['transaction']['signatures']:raise ValueError('TX_SIGNATURE_MISMATCH')
 if slot is not None and tx.get('slot')!=slot:raise ValueError('TX_SLOT_MISMATCH')
 keys=account_keys(tx)
 def amt(when,vault,mint,decimals):
  if keys.count(vault)!=1:raise ValueError('VAULT_KEY_AMBIGUOUS')
  ix=keys.index(vault);rows=[r for r in tx['meta'][when+'TokenBalances'] if r.get('accountIndex')==ix]
  if len(rows)!=1:raise ValueError('VAULT_BALANCE_MISSING_OR_AMBIGUOUS')
  r=rows[0];v=r['uiTokenAmount']
  if r['mint']!=mint or v['decimals']!=decimals:raise ValueError('TX_VAULT_IDENTITY')
  side='coin' if vault==cfg['coin_vault'] else 'pc'
  expected=cfg.get(side+'_token_program') or 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
  if 'programId' in r and r['programId']!=expected:raise ValueError('VAULT_BALANCE_PROGRAM_MISMATCH')
  n=int(v['amount'])
  if not 0<=n<=U64_MAX:raise ValueError('TX_VAULT_RANGE')
  return n
 pre=[amt('pre',cfg[k+'_vault'],cfg[k+'_mint'],cfg[k+'_decimals']) for k in ('coin','pc')]
 post=[amt('post',cfg[k+'_vault'],cfg[k+'_mint'],cfg[k+'_decimals']) for k in ('coin','pc')]
 a0,a1=event['amount0_raw'],event['amount1_raw'];z=event['zero_for_one']
 if [post[i]-pre[i] for i in (0,1)] != ([a0,-a1] if z else [-a0,a1]):
  raise ValueError('CLMM_TX_VAULT_DELTA_MISMATCH_OR_MULTI_OPERATION')
 if min(pre+post)<=0:raise ValueError('CLMM_VAULT_EMPTY')
 meme0=cfg['coin_mint']==cfg['meme_mint']
 return dict(pre_meme_raw=pre[0 if meme0 else 1],pre_quote_raw=pre[1 if meme0 else 0],
             post_meme_raw=post[0 if meme0 else 1],post_quote_raw=post[1 if meme0 else 0])

def audit_row(row):
 """Recompute reference from bytes. Refuse fields inconsistent with raw evidence."""
 ev=row.get('protocol')
 if not isinstance(ev,dict):raise ValueError('PROTOCOL_EVIDENCE_MISSING')
 cfg=ev['metadata'];raw=base64.b64decode(ev['event_b64'],validate=True)
 if cfg['pool']!=row['pool'] or cfg['symbol']!=row['symbol']:raise ValueError('ROW_IDENTITY_MISMATCH')
 d=decode_event(raw,cfg)
 if d['side']!=row['side'] or d['log_type']!=row['log_type'] or d['direction']!=row['direction']:raise ValueError('ROW_SIDE_OR_KIND')
 if d['kind']=='CPMM':
  config=base64.b64decode(cfg['cpmm_config_data_b64'],validate=True)
  if len(config)!=236 or config[:8]!=hashlib.sha256(b'account:AmmConfig').digest()[:8] or uint(config,20)!=cfg['protocol_fee_rate'] or uint(config,28)!=cfg['fund_fee_rate']:
   raise ValueError('CPMM_CONFIG_EVIDENCE_MISMATCH')
 if d['kind']=='CLMM':
  tx=ev['transaction_evidence']
  matches=scoped_swaps(tx['meta']['logMessages'],cfg['owner'],cfg['pool'])
  if matches!=[raw]:raise ValueError('CLMM_TX_EVENT_MISMATCH')
  verified=tx_vaults(tx,cfg,d,row['signature'],row['slot'])
  for k in ('pre_meme_raw','pre_quote_raw','post_meme_raw','post_quote_raw'):
   d[k]=int(ev[k])
   if d[k]!=verified[k]:raise ValueError('CLMM_EVIDENCE_DELTA_MISMATCH')
  delta_m=d['post_meme_raw']-d['pre_meme_raw'];delta_q=d['post_quote_raw']-d['pre_quote_raw']
  want=(d['meme_raw'],-d['quote_raw']) if d['side']=='SELL' else (-d['meme_raw'],d['quote_raw'])
  if (delta_m,delta_q)!=want or min(d[k] for k in ('pre_meme_raw','pre_quote_raw','post_meme_raw','post_quote_raw'))<=0:
   raise ValueError('CLMM_EVIDENCE_DELTA_MISMATCH')
 for field,value in [('price_sol_per_meme', D(d['quote_raw'])*D(10)**d['meme_decimals']/(D(d['meme_raw'])*D(10)**9)),('meme_qty',D(d['meme_raw'])/D(10)**d['meme_decimals']),('quote_sol',D(d['quote_raw'])/D(10)**9),
    ('pool_meme_reserve',D(d['pre_meme_raw'])/D(10)**d['meme_decimals']),('pool_quote_reserve_sol',D(d['pre_quote_raw'])/D(10)**9)]:
  got=D(str(row[field]));tol=max(D('1e-15'),abs(value)*D('1e-10'))
  if not got.is_finite() or abs(got-value)>tol:raise ValueError('ROW_AMOUNT_MISMATCH:'+field)
 return d
