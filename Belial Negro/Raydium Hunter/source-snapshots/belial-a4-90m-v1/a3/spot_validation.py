"""Reuse FIX4 validators, but qualify each asset independently. Unsupported stays raw.
No extension is added to the whitelist; no hook execution; no token transfer.
"""
from __future__ import annotations
import base64,time
from pathlib import Path
from a3.common import add_legacy,atomic
add_legacy('runner')
import preflight_pools as pf
import token_capabilities as tc
from a3.catalog import blocked

def validate_asset(target,pool_account,mints,extra):
    sym,pool,mint=target
    try:row=pf.validate_pool(sym,pool,mint,pool_account)
    except Exception as e:return None,blocked(sym,'POOL:'+str(e),pool=pool)
    # Pool's embedded mints and owner have already been checked. This is only
    # identity for RAW logs, never approval of the token's transfer semantics.
    failures=[];profiles={}
    for which in ('coin','pc'):
        key=row[which+'_mint']
        try:
            profile=tc.validate_mint(mints.get(key),key);profiles[key]=profile
            if profile['decimals']!=row[which+'_decimals']:raise ValueError('MINT_DECIMALS_MISMATCH')
            if key==pf.WSOL and profile['program']!=pf.TOKEN:raise ValueError('WSOL_PROGRAM_MISMATCH')
            if row['pool_kind']=='AMM_V4' and profile['program']!=pf.TOKEN:raise ValueError('AMM_V4_REQUIRES_LEGACY_TOKEN')
            if row['pool_kind']=='CPMM' and row[which+'_token_program']!=profile['program']:raise ValueError('CPMM_MINT_PROGRAM_MISMATCH')
            row[which+'_token_program']=profile['program'];row[which+'_mint_capabilities']=profile
            row[which+'_mint_data_b64']=mints[key]['data'][0]
        except Exception as e:failures.append(which+'_MINT:'+str(e))
    if row['pool_kind']!='AMM_V4':
        for which in ('coin','pc'):
            key=row[which+'_mint'];vault_key=row[which+'_vault']
            if key not in profiles:continue
            try:
                profile=tc.validate_vault(extra.get(vault_key),key,profiles[key]['program'],vault_key)
                row[which+'_vault_amount_at_preflight']=profile['amount_raw']
                row[which+'_vault_capabilities']=profile;row[which+'_vault_data_b64']=extra[vault_key]['data'][0]
            except Exception as e:failures.append(which+'_VAULT:'+str(e))
        if row['pool_kind']=='CPMM':
            try:row.update(pf.validate_cpmm_config(extra.get(row['amm_config'])))
            except Exception as e:failures.append('CONFIG:'+str(e))
    if failures:
        return None,blocked(sym,';'.join(failures),identity=True,raw=True,pool=pool,protocol=row['pool_kind'])
    row['token_contract_revision']=tc.REVISION
    return row,dict(symbol=sym,pool=pool,protocol=row['pool_kind'],state='CONTRACT_VERIFIED',
                    identity=True,raw_capture=True,price=False,actor=False,qualified=False,
                    reason='NATIVE_QUALIFICATION_PENDING',at_ms=time.time_ns()//1000000)

def validate_batch(targets,client,out):
    """RPC calls are batched for rate budget; interpretation failures stay local."""
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    values,slot=pf.rpc(client.url,[t[1] for t in targets],client)
    atomic(out/'RPC_POOLS.json',{'slot':slot,'targets':targets,'accounts':values})
    keys=sorted({t[2] for t in targets}|{pf.WSOL})
    mint_values,mint_slot=pf.rpc(client.url,keys,client);mints=dict(zip(keys,mint_values))
    atomic(out/'RPC_MINTS.json',{'slot':mint_slot,'addresses':keys,'accounts':mint_values})
    extras=set()
    for t,value in zip(targets,values):
        try:
            row=pf.validate_pool(*t,value)
            if row['pool_kind']!='AMM_V4':extras.update([row['coin_vault'],row['pc_vault']])
            if row['pool_kind']=='CPMM':extras.add(row['amm_config'])
        except Exception:pass
    extra={}
    if extras:
        keys=sorted(extras);vals,xslot=pf.rpc(client.url,keys,client);extra=dict(zip(keys,vals))
        atomic(out/'RPC_VAULTS_CONFIGS.json',{'slot':xslot,'addresses':keys,'accounts':vals})
    rows={};capabilities={}
    for target,value in zip(targets,values):
        row,cap=validate_asset(target,value,mints,extra);capabilities[target[0]]=cap
        if row is not None:rows[target[0]]=row
    atomic(out/'VALIDATION.json',{'rows':rows,'capabilities':capabilities,'slot':slot,'orders':False})
    return rows,capabilities,slot
