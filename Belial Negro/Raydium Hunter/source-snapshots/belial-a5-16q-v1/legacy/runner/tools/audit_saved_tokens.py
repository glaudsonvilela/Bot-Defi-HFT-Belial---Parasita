#!/usr/bin/env python3
"""Read the latest already-saved mint RPC response. No HTTP, Cargo or processes.
Historical diagnosis only: never approves the new capture or rewrites evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import token_capabilities as tc
BASE = Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')


def audit(base: Path = BASE) -> dict:
    root = Path(base)/'runtime/r5d-t3-90m-mp12-ultra/install-reports'
    result = {'role':'HISTORICAL_TOKEN_AUDIT_NOT_LIVE_APPROVAL','scan_started':False,
              'orders':False,'source':None,'tokens':[]}
    candidates = []
    if root.is_dir() and not root.is_symlink():
        for path in root.glob('*/pool_metadata_fix2/rpc_fix3/candidate_*/RPC_MINT_ACCOUNTS.json'):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            if any(parent.is_symlink() for parent in path.parents if parent != root):
                continue
            if path.stat().st_size <= 16*1024*1024:
                candidates.append(path)
    if not candidates:
        result['status'] = 'NO_SAVED_MINT_SNAPSHOT'
        print('FIX4_LOCAL_AUDIT=NO_SAVED_MINT_SNAPSHOT; no network requested',flush=True)
        return result
    path = max(candidates, key=lambda p:(p.stat().st_mtime_ns,str(p)))
    raw = path.read_bytes(); data = json.loads(raw)
    addresses, accounts = data.get('addresses'), data.get('accounts')
    if not isinstance(addresses,list) or not isinstance(accounts,list) or len(addresses)!=len(accounts) or len(addresses)>100:
        raise RuntimeError('SAVED_RPC_MINT_SNAPSHOT_SCHEMA')
    result.update(source=str(path),sha256=hashlib.sha256(raw).hexdigest(),slot=data.get('slot'))
    for address,value in zip(addresses,accounts):
        record = {'address':address,'owner':value.get('owner') if isinstance(value,dict) else None}
        try:
            record.update(tc.validate_mint(value,address)); record['status']='STRUCTURE_SUPPORTED'
        except Exception as exc:
            record.update(status='BLOCKED',error=str(exc))
        result['tokens'].append(record)
        print('FIX4_SAVED_MINT=' + str(address) + '; owner=' + str(record['owner']) +
              '; status=' + record['status'] + '; HISTORICAL_ONLY=true',flush=True)
    result['status'] = 'DIAGNOSIS_COMPLETE_NOT_CAPTURE_APPROVAL'
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',type=Path,default=BASE);ap.add_argument('--out',type=Path)
    args=ap.parse_args();result=audit(args.base)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    else:
        print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
