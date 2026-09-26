"""Independent spot preparation: expected incompatibility is a per-asset result."""
from __future__ import annotations
import argparse,base64,os,subprocess,time
from pathlib import Path
from a51.common import *
from a51.catalog import initial,blocked
from a51.spot_validation import validate_batch,pf,tc
from rpc_transport import Client,RpcFailure,MAINNET_GENESIS,profiles,public_endpoint
from rpc_ws_probe import Connection
from protocol_reference import CPMM,CLMM,scoped_swaps,decode_event,tx_vaults,audit_row

def ws_check(url,pool):
    deadline=time.monotonic()+20
    with Connection(url,deadline) as ws:
        ws.send({'jsonrpc':'2.0','id':1,'method':'logsSubscribe','params':[{'mentions':[pool]},{'commitment':'confirmed'}]})
        for _ in range(4096):
            row=ws.recv()
            if row.get('id')==1:
                sub=row.get('result')
                if type(sub) is not int or sub<0:raise RuntimeError('WS_SUBSCRIPTION_REJECTED')
                ws.send({'jsonrpc':'2.0','id':2,'method':'logsUnsubscribe','params':[sub]})
                for _ in range(4096):
                    response=ws.recv()
                    if response.get('id')==2:
                        if response.get('result') is not True:raise RuntimeError('WS_UNSUBSCRIBE_REJECTED')
                        return
                raise RuntimeError('WS_UNSUBSCRIBE_NO_ACK')
        raise RuntimeError('WS_NO_ACK')

def qualify_one(row,manifest_path,binary,out,client):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    env=clean_env();env['BELIAL_A1_SENSOR']='1';env['TOKIO_WORKER_THREADS']='1'
    output=out/'native_contract.json'
    p=subprocess.run([str(binary),'--fix4-token-manifest',str(manifest_path),'--output',str(output)],
                     capture_output=True,text=True,timeout=90,env=env)
    (out/'native_contract.log').write_text(p.stdout+'\n'+p.stderr)
    if p.returncode or (read_json(output,{}) or {}).get('pools')!=[row['symbol']]:raise RuntimeError('NATIVE_TOKEN_CONTRACT_REJECTED')
    if row['pool_kind']=='AMM_V4':
        # AMM reader is the preserved, previously exercised T2 reader. This is
        # contract qualification, not a new live-data smoke-test certificate.
        result={'ok':True,'mode':'PRESERVED_AMM_V4_READER_NATIVE_CONTRACT_CHECK',
                'real_historical_swap_crosscheck':False,'n2_training':False}
        atomic(out/'QUALIFIED.json',result);return result
    attempts=[];case=None
    signatures=client.call('getSignaturesForAddress',[row['pool'],{'limit':12,'commitment':'confirmed'}])
    if not isinstance(signatures,list):raise RuntimeError('SIGNATURES_SCHEMA')
    atomic(out/'signatures.json',signatures)
    for item in signatures:
        if item.get('err') is not None:continue
        sig=item.get('signature');attempt={'signature':sig};attempts.append(attempt)
        try:
            tx=client.call('getTransaction',[sig,{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':0}])
            if not tx or 'err' not in (tx.get('meta') or {}) or tx['meta']['err'] is not None:raise ValueError('TX_UNAVAILABLE_OR_FAILED')
            if sig not in tx['transaction']['signatures'] or tx.get('slot')!=item.get('slot'):raise ValueError('TX_IDENTITY')
            payloads=scoped_swaps(tx['meta']['logMessages'],row['owner'],row['pool'])
            if len(payloads)!=1:raise ValueError('SINGLE_COMMITTED_SWAP_REQUIRED')
            event=decode_event(payloads[0],row)
            if row['owner']==CLMM:tx_vaults(tx,row,event,sig,tx['slot'])
            case={'cfg':row,'pool_data_b64':row['pool_data_b64'],'event_b64':base64.b64encode(payloads[0]).decode(),
                  'signature':sig,'slot':tx['slot'],'tx':tx,'received_unix_ns':time.time_ns()}
            atomic(out/'accepted_transaction.json',tx);attempt['ok']=True;break
        except RpcFailure:raise
        except Exception as e:attempt['error']=str(e)
        finally:atomic(out/'attempts.json',attempts)
        time.sleep(.35)
    if case is None:raise RuntimeError('NO_QUALIFIED_MODERN_SWAP_IN_12_TRANSACTIONS')
    casefile=out/'case.jsonl';casefile.write_text(canonical(case)+'\n');decoded=out/'native_decoded.jsonl'
    p=subprocess.run([str(binary),'--fix2-decode-file',str(casefile),'--output',str(decoded)],capture_output=True,text=True,timeout=90,env=env)
    (out/'native_decode.log').write_text(p.stdout+'\n'+p.stderr)
    if p.returncode:raise RuntimeError('NATIVE_DECODER_REJECTED')
    rows=[strict_loads(x) for x in decoded.read_text().splitlines() if x.strip()]
    if len(rows)!=1:raise RuntimeError('NATIVE_DECODE_COUNT')
    r=rows[0]
    if r['pool']!=row['pool'] or r['signature']!=case['signature'] or r['slot']!=case['slot']:raise RuntimeError('NATIVE_DECODE_IDENTITY')
    expected=float(audit_row(r)['reference_post']);actual=float(r['reference_post_price'])
    if abs(actual-expected)>max(1e-15,abs(expected)*1e-10):raise RuntimeError('NATIVE_PYTHON_PRICE_MISMATCH')
    result={'ok':True,'mode':'HISTORICAL_NATIVE_AND_PYTHON_CROSSCHECK','real_historical_swap_crosscheck':True,
            'n2_training':False,'not_replayed_to_live':True};atomic(out/'QUALIFIED.json',result);return result

def prepare(session,binary):
    root=Path(session)/'spot_prep';root.mkdir(parents=True,exist_ok=True)
    caps=initial();targets=list(pf.ORIGINAL);sources={};rows={};connection=None
    def publish(state):
        atomic(root/'CAPABILITIES.json',caps);heartbeat(root/'status.json',state,qualified=sum(c.get('qualified') is True for c in caps.values()))
    publish('DISCOVERING')
    # Each metadata query is bounded; a failure never deletes the other targets.
    registry=read_json(ROOT/'UNIVERSE_A5_16Q.json')
    extra={r['symbol']:r['mint'] for r in registry['focus4'] if r['cohort']=='FOCUS4'}
    for sym,mint in dict(pf.canonical_mints(),**extra).items():
        try:
            value,source=pf.resolve_pool(sym,mint,root/'metadata');targets.append((sym,value['id'],mint));sources[sym]=source
        except Exception as e:caps[sym]=blocked(sym,'DISCOVERY:'+str(e))
        publish('DISCOVERING')
    atomic(root/'TARGETS.json',targets)
    errors=[]
    for profile in profiles(os.environ.get('BELIAL_SOLANA_RPC_URL') or None,os.environ.get('BELIAL_SOLANA_WS_URL') or None):
        directory=root/profile['name'];directory.mkdir(exist_ok=True)
        client=Client(profile['rpc'],directory/'RPC_ATTEMPTS.jsonl',deadline=time.monotonic()+180)
        try:
            if client.call('getGenesisHash')!=MAINNET_GENESIS:raise RpcFailure('RPC_NOT_MAINNET',profile['rpc'],'getGenesisHash')
            validated,results,slot=validate_batch(targets,client,directory)
            # Identity/capability failures are not a reason to change provider.
            rows.update(validated);caps.update(results);publish('CONTRACTS_INSPECTED')
            raw_targets=[t for t in targets if caps[t[0]].get('identity')]
            if raw_targets:ws_check(profile['ws'],raw_targets[0][1])
            connection={'rpc':profile['rpc'],'ws':profile['ws'],'genesis':MAINNET_GENESIS,'profile':profile['name'],'slot':slot}
            break
        except RpcFailure as e:errors.append(str(e));publish('RPC_UNAVAILABLE')
        except Exception as e:
            errors.append(str(e));publish('SPOT_PREPARATION_ERROR');break
    atomic(root/'CONNECTION_ATTEMPTS.json',{'errors':errors,'orders':False})
    if connection is not None:
        atomic(root/'CONNECTION.json',connection,durable=True)
        raw=[]
        for target in targets:
            s,p,m=target
            if caps[s].get('identity'):raw.append({'symbol':s,'pool':p,'mint':m})
        atomic(root/'RAW_SOURCES.json',dict(ok=True,ws=connection['ws'],sources=raw,orders=False,read_only=True))
        for s in SYMBOLS:
            if s not in rows:continue
            row=rows[s];d=root/s;d.mkdir(exist_ok=True)
            try:
                if sources.get(s,{}).get('program_id',row['owner'])!=row['owner']:raise RuntimeError('METADATA_RPC_OWNER_MISMATCH')
                manifest={'ok':True,'schema':'belial_a1_single_source_contract_v1','pools':[row],
                          'token_validation_revision':tc.REVISION,'rpc':connection['rpc'],'ws':connection['ws'],
                          'genesis':MAINNET_GENESIS,'orders':False,'paper_only':True}
                atomic(d/'MANIFEST.json',manifest,durable=True)
                if not binary.is_file():raise RuntimeError('NATIVE_SPOT_BINARY_UNAVAILABLE')
                client=Client(connection['rpc'],d/'QUALIFICATION_RPC.jsonl',deadline=time.monotonic()+90)
                result=qualify_one(row,d/'MANIFEST.json',binary,d/'qualification',client)
                caps[s].update(state='QUALIFIED',qualified=True,price=True,actor=True,reason='OK',
                               native_qualification=result,manifest=str(d/'MANIFEST.json'))
            except Exception as e:
                caps[s].update(state='RAW_ONLY',qualified=False,price=False,actor=False,reason='QUALIFICATION:'+str(e))
            publish('QUALIFYING')
    else:
        for s in SYMBOLS:
            if caps[s]['state'] in ('DISCOVERING','CONTRACT_VERIFIED'):
                caps[s]=blocked(s,'SPOT_RPC_OR_WS_UNAVAILABLE:'+(';'.join(errors)[-1600:]))
    publish('COMPLETE')
    atomic(root/'DONE.json',{'ok':True,'qualified':sum(c.get('qualified') is True for c in caps.values()),
                           'platform_may_run_degraded':True,'orders':False},durable=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args()
    try:prepare(a.session,a.binary)
    except Exception as e:
        heartbeat(a.session/'spot_prep/status.json','FAILED',error=str(e));atomic(a.session/'spot_prep/DONE.json',{'ok':False,'error':str(e)})
    return 0
if __name__=='__main__':raise SystemExit(main())
