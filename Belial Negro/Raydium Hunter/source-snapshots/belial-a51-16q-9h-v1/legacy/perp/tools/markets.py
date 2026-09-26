#!/usr/bin/env python3
"""Public metadata only: no user addresses, no keys, no order endpoints."""
from __future__ import annotations
import argparse,json,time,urllib.request,hashlib
from pathlib import Path
SYMBOLS=('FARTCOIN','BOME','WIF','POPCAT','RAY','JUP','BONK','PENGU','PUMP','USELESS','GRASS','PYTH')
HL_INFO='https://api.hyperliquid.xyz/info'
LI_INFO='https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails?filter=perp'

def http(url,body=None):
 req=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','User-Agent':'Belial-T3-PAPER/1'})
 err=None
 for attempt in range(3):
  try:
   with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
  except Exception as e:err=e;time.sleep(.5*2**attempt)
 raise RuntimeError(str(err))

def identity(name):
 for sym in SYMBOLS:
  if name in (sym,sym+'-USD',sym+'/USD'):return sym,1
  if name in ('k'+sym,'1000'+sym,'1000'+sym+'-USD'):return sym,1000
 return None

def parse_hl(data):
 if not isinstance(data,list) or len(data)!=2 or not isinstance(data[0].get('universe'),list):raise ValueError('HYPERLIQUID_META_SCHEMA')
 out=[]
 for i,x in enumerate(data[0]['universe']):
  ident=identity(x.get('name'))
  if not ident or x.get('isDelisted') is True:continue
  sym,mult=ident;dec=int(x['szDecimals'])
  if not 0<=dec<=18:raise ValueError('SIZE_DECIMALS_INVALID')
  out.append({'venue':'hyperliquid','symbol':sym,'market':x['name'],'market_index':i,'multiplier':mult,'size_step':mult*10.**(-dec),'min_size':mult*10.**(-dec),'min_notional':10.0,'status':'ACTIVE','quote':'USDC','source':'core universe / no HIP-3','initial_ctx':data[1][i] if i<len(data[1]) else {}})
 return unique(out)

def parse_li(data):
 if not isinstance(data,dict) or data.get('code') not in (None,200):raise ValueError('LIGHTER_META_RESPONSE')
 rows=data.get('order_book_details')
 if not isinstance(rows,list):raise ValueError('LIGHTER_META_SCHEMA')
 out=[]
 for x in rows:
  ident=identity(x.get('symbol'))
  if not ident or str(x.get('market_type')).lower() not in ('perp','perpetual'):continue
  if str(x.get('status')).lower()!='active':continue
  sym,mult=ident;dec=int(x['supported_size_decimals'])
  if not 0<=dec<=18:raise ValueError('SIZE_DECIMALS_INVALID')
  out.append({'venue':'lighter','symbol':sym,'market':str(x['market_id']),'market_index':int(x['market_id']),'multiplier':mult,'size_step':mult*10.**(-dec),'min_size':float(x['min_base_amount'])*mult,'min_notional':float(x['min_quote_amount']),'status':'ACTIVE','quote':'USD','source':'perpetual orderBookDetails','metadata_maker_fee':x.get('maker_fee'),'metadata_taker_fee':x.get('taker_fee')})
 return unique(out)

def unique(rows):
 seen=set()
 for r in rows:
  key=r['venue'],r['symbol']
  if key in seen:raise ValueError('AMBIGUOUS_PERP_IDENTITY:'+str(key))
  seen.add(key)
 return rows

def discover():
 results=[];failures={};rawhash={}
 for venue,url,body,parser in [('hyperliquid',HL_INFO,{'type':'metaAndAssetCtxs'},parse_hl),('lighter',LI_INFO,None,parse_li)]:
  try:
   raw=http(url,body);rawhash[venue]=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest();results.extend(parser(raw))
  except Exception as e:failures[venue]=str(e)
 coverage={s:{v:any(x['symbol']==s and x['venue']==v for x in results) for v in ('hyperliquid','lighter')} for s in SYMBOLS}
 return {'schema':'belial_t3_perp_markets_v1','created_ms':time.time_ns()//1000000,'ok':bool(results),'markets':results,'coverage':coverage,'metadata_failures':failures,'metadata_sha256':rawhash,'orders':False,'paper_only':True}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();data=discover();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(data,indent=2)+'\n')
 print(json.dumps({'PERP_MARKETS':len(data['markets']),'COVERAGE':data['coverage'],'FAILURES':data['metadata_failures']},indent=2))
 if not data['ok']:raise SystemExit('NO_PUBLIC_PERP_METADATA_AVAILABLE')
if __name__=='__main__':main()
