"""Choose ONE explicitly identified HTTP+WS mainnet pair for the whole session.

Default providers are independently public, tried once each. Identity failures
never trigger a provider switch. No IP rotation, proxy alteration, key discovery,
or fallback to test networks. No mid-session route changes.
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from rpc_transport import (Client, RpcFailure, MAINNET_GENESIS, profiles,
                           public_endpoint, save_json, validate_url)
from rpc_ws_probe import probe


def fingerprint(rpc,ws):
    return hashlib.sha256(json.dumps([rpc,ws],separators=(',',':')).encode()).hexdigest()


def probe_history(client, pool):
    rows=client.call('getSignaturesForAddress',[pool,{'limit':6,'commitment':'confirmed'}])
    if not isinstance(rows,list):raise RpcFailure('RPC_SIGNATURES_SCHEMA',client.url,'getSignaturesForAddress')
    tried=0
    for row in rows:
        if not isinstance(row,dict) or row.get('err') is not None:continue
        signature=row.get('signature');slot=row.get('slot')
        if not isinstance(signature,str) or not signature or type(slot) is not int:continue
        tried+=1
        tx=client.call('getTransaction',[signature,{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':0}])
        if (isinstance(tx,dict) and tx.get('slot')==slot and
            isinstance(tx.get('meta'),dict) and 'err' in tx['meta'] and tx['meta']['err'] is None and
            isinstance(tx['meta'].get('logMessages'),list) and
            signature in (tx.get('transaction') or {}).get('signatures',[])):
            return {'ok':True,'signature':signature,'slot':slot,'requests':tried,
                    'role':'READ_CAPABILITY_ONLY_NOT_TRAINING'}
        if tried>=3:break
    raise RpcFailure('RPC_RECENT_HISTORY_NOT_AVAILABLE',client.url,'getTransaction')


def select(targets,validate_accounts,evidence,*,rpc=None,ws=None,client_factory=Client,ws_probe=probe):
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    if len(targets)!=12 or len({t[1] for t in targets})!=12:
        raise RuntimeError('RPC_SELECTION_REQUIRES_12_POOLS')
    candidates=profiles(rpc,ws)
    report={'schema':'belial_t3_fix3_rpc_selection_v1','ok':False,
            'scan_started':False,'orders':False,'policy':'ONE_PINNED_PAIR_NO_LIVE_ROTATION',
            'explicit_user_endpoint':bool(rpc),'attempts':[]}
    path=evidence/'RPC_SELECTION.json';save_json(path,report)
    for index,candidate in enumerate(candidates,1):
        attempt={'profile':candidate['name'],'rpc':public_endpoint(candidate['rpc']),
                 'ws':public_endpoint(candidate['ws']),'ok':False}
        report['attempts'].append(attempt)
        folder=evidence/f'candidate_{index}';folder.mkdir(parents=True,exist_ok=True)
        print(f'RPC_FIX3_TRY={candidate["name"]}; rpc={attempt["rpc"]}; ws={attempt["ws"]}',flush=True)
        client=client_factory(candidate['rpc'],folder/'RPC_ATTEMPTS.jsonl',deadline=time.monotonic()+150)
        try:
            genesis=client.call('getGenesisHash')
            if genesis!=MAINNET_GENESIS:
                raise RpcFailure('RPC_NOT_SOLANA_MAINNET',candidate['rpc'],'getGenesisHash')
            attempt['genesis_hash']=genesis
            # Full 12-pool/mint/vault/config validation, not just getHealth HTTP 200.
            manifest=validate_accounts(candidate['rpc'],folder,client)
            if manifest.get('ok') is not True or len(manifest.get('pools',[]))!=12:
                raise RuntimeError('RPC_ACCOUNT_VALIDATION_NOT_APPROVED')
            original=manifest['pools'][0]
            account=client.call('getAccountInfo',[original['pool'],{'encoding':'base64','commitment':'confirmed'}])
            if (not isinstance(account,dict) or not isinstance(account.get('value'),dict) or
                account['value'].get('owner')!=original['owner']):
                raise RuntimeError('RPC_GET_ACCOUNT_INFO_IDENTITY_MISMATCH')
            attempt['history']=probe_history(client,original['pool'])
            print('RPC_FIX3_HTTP_OK='+candidate['name']+'; VALIDATED_POOLS=12; HISTORY_READ_OK=true',flush=True)
            attempt['websocket']=ws_probe(candidate['ws'],[t[1] for t in targets])
            if (attempt['websocket'].get('ok') is not True or
                attempt['websocket'].get('subscriptions_acknowledged')!=12 or
                attempt['websocket'].get('subscriptions_cancelled')!=12):
                raise RpcFailure('WS_12_SUBSCRIPTIONS_NOT_APPROVED',candidate['ws'],'logsSubscribe')
            attempt['ok']=True
            manifest.update(rpc=candidate['rpc'],ws=candidate['ws'])
            manifest['rpc_connection']={'schema':'belial_t3_fix3_connection_v1',
                'profile':candidate['name'],'genesis_hash':genesis,
                'endpoint_pair_sha256':fingerprint(candidate['rpc'],candidate['ws']),
                'validated_pools':12,'history_read_ok':True,
                'ws_subscriptions_acknowledged':12,'ws_subscriptions_cancelled':12,
                'selected_ms':time.time_ns()//1_000_000,'live_failover':False}
            manifest['fix']='T3_9H_FIX3_RPC_CONNECTION'
            report.update(ok=True,selected=attempt['profile'],connection=manifest['rpc_connection'])
            save_json(path,report)
            print('RPC_FIX3_SELECTED='+candidate['name']+'; MAINNET=true; WS_SUBSCRIPTIONS_OK=12',flush=True)
            return manifest
        except RpcFailure as exc:
            attempt['error']=str(exc)
            print('RPC_FIX3_REJECTED='+candidate['name']+'; reason='+str(exc),flush=True)
            save_json(path,report)
            # Try each alternative once, never recycle a rejected service.
        except Exception as exc:
            attempt['error']=str(exc);attempt['validation_error_no_fallback']=True
            save_json(path,report)
            raise
    save_json(path,report)
    raise RuntimeError('RPC_ALL_CANDIDATES_REJECTED:see='+str(path))


def connection_for_session(manifest):
    if not isinstance(manifest,dict) or manifest.get('ok') is not True:
        raise RuntimeError('APPROVED_RPC_MANIFEST_REQUIRED')
    config=manifest.get('rpc_connection') or {}
    rpc=manifest.get('rpc');ws=manifest.get('ws')
    validate_url(rpc,'https');validate_url(ws,'wss')
    if (config.get('schema')!='belial_t3_fix3_connection_v1' or
        config.get('genesis_hash')!=MAINNET_GENESIS or
        config.get('endpoint_pair_sha256')!=fingerprint(rpc,ws) or
        config.get('validated_pools')!=12 or config.get('history_read_ok') is not True or
        config.get('ws_subscriptions_acknowledged')!=12 or
        config.get('ws_subscriptions_cancelled')!=12 or config.get('live_failover') is not False):
        raise RuntimeError('RPC_MANIFEST_CONNECTION_NOT_APPROVED_OR_CHANGED')
    return rpc,ws
