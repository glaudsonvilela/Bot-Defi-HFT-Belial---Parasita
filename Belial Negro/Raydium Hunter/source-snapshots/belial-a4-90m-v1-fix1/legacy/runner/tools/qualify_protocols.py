#!/usr/bin/env python3
"""FIX2 decoder qualification, public history only; NEVER feeds trading or N2.
Each selected modern pool must supply one committed supported swap. Cases go
through the exact release collector decoder plus an independent Decimal audit.
"""
from __future__ import annotations
import argparse,base64,json,subprocess,time,urllib.request
from pathlib import Path
from protocol_reference import CPMM,CLMM,scoped_swaps,decode_event,tx_vaults,audit_row

def save(path,value):
 Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

from rpc_transport import Client, RpcFailure

def call(url,method,params):
 return Client(url).call(method,params)

def qualify(manifest,binary,out,rpc_call=None):
 out=Path(out);out.mkdir(parents=True,exist_ok=False)
 if manifest.get('ok') is not True or len(manifest.get('pools',[]))!=12:raise RuntimeError('APPROVED_MP12_MANIFEST_REQUIRED')
 url=manifest['rpc']
 if rpc_call is None:
  client=Client(url,out/'RPC_ATTEMPTS.jsonl',deadline=time.monotonic()+480)
  rpc_call=lambda _url,method,params: client.call(method,params)
 modern=[p for p in manifest['pools'] if p['owner'] in (CPMM,CLMM)]
 cases=[];report={'ok':False,'role':'HISTORICAL_DECODER_QUALIFICATION_ONLY','scan_started':False,
   'n2_training':False,'orders':False,'pools':{},'required_modern_pools':len(modern)}
 try:
  for cfg in modern:
   sym=cfg['symbol'];report['pools'][sym]={'kind':cfg['pool_kind'],'pool':cfg['pool'],'attempts':[],'ok':False}
   signatures=rpc_call(url,'getSignaturesForAddress',[cfg['pool'],{'limit':12,'commitment':'confirmed'}])
   if not isinstance(signatures,list):raise RuntimeError(sym+':SIGNATURES_SCHEMA')
   save(out/(sym+'_signatures.json'),signatures)
   for item in signatures:
    if item.get('err') is not None:continue
    sig=item.get('signature');attempt={'signature':sig};report['pools'][sym]['attempts'].append(attempt)
    try:
     if not isinstance(sig,str) or not sig:raise RuntimeError('SIGNATURE_MISSING')
     tx=rpc_call(url,'getTransaction',[sig,{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':0}])
     if not tx or 'err' not in (tx.get('meta') or {}) or tx['meta']['err'] is not None:raise RuntimeError('TX_FAILED_OR_UNAVAILABLE')
     if sig not in tx['transaction']['signatures'] or tx.get('slot')!=item.get('slot'):raise RuntimeError('TX_IDENTITY_MISMATCH')
     payloads=scoped_swaps(tx['meta']['logMessages'],cfg['owner'],cfg['pool'])
     if len(payloads)!=1:raise RuntimeError('SINGLE_COMMITTED_SWAP_REQUIRED:'+str(len(payloads)))
     event=decode_event(payloads[0],cfg)
     if cfg['owner']==CLMM:tx_vaults(tx,cfg,event,sig,tx['slot'])
     case={'cfg':cfg,'pool_data_b64':cfg['pool_data_b64'],'event_b64':base64.b64encode(payloads[0]).decode(),
        'signature':sig,'slot':tx['slot'],'tx':tx,'received_unix_ns':time.time_ns()}
     cases.append(case);attempt['ok']=True;report['pools'][sym]['ok']=True
     save(out/(sym+'_accepted_transaction.json'),tx)
     break
    except RpcFailure as exc:
     attempt['error']=str(exc);raise
    except Exception as exc:attempt['error']=str(exc)
    finally:
     save(out/'REPORT.json',report)
     time.sleep(.2)
   if not report['pools'][sym]['ok']:raise RuntimeError(sym+':NO_QUALIFIED_SWAP_IN_LAST_12_TRANSACTIONS')
  path=out/'cases.jsonl';output=out/'rust_decoded.jsonl'
  path.write_text(''.join(json.dumps(c,ensure_ascii=False,allow_nan=False)+'\n' for c in cases))
  process=subprocess.run([str(binary),'--fix2-decode-file',str(path),'--output',str(output)],capture_output=True,text=True,timeout=60)
  (out/'rust_decoder.log').write_text(process.stdout+'\n'+process.stderr)
  if process.returncode:raise RuntimeError('RUST_DECODER_QUALIFICATION_FAILED')
  rows=[json.loads(line) for line in output.read_text().splitlines() if line.strip()]
  if len(rows)!=len(cases):raise RuntimeError('RUST_QUALIFICATION_COUNT_MISMATCH')
  for row,case in zip(rows,cases):
   if row['pool']!=case['cfg']['pool'] or row['signature']!=case['signature'] or row['slot']!=case['slot']:raise RuntimeError('RUST_QUALIFICATION_IDENTITY')
   decoded=audit_row(row);value=float(decoded['reference_post'])
   if abs(float(row['reference_post_price'])-value)>max(1e-15,abs(value)*1e-10):raise RuntimeError('RUST_PYTHON_REFERENCE_MISMATCH')
  report.update(ok=True,rust_python_crosschecked=len(rows),historical_data_never_replayed_into_engine=True)
  save(out/'REPORT.json',report)
  print('FIX2_PROTOCOL_QUALIFICATION_OK=true; MODERN_POOLS='+str(len(rows))+'; SCAN_STARTED=false',flush=True)
  return report
 except Exception as exc:
  report['error']=str(exc);save(out/'REPORT.json',report);raise

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
 a=ap.parse_args();qualify(json.loads(a.manifest.read_text()),a.binary,a.out)
if __name__=='__main__':main()
