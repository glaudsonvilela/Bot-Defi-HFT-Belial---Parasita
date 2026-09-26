"""Independent, read-only on-chain pool-state observations for N2 LABELS ONLY.
No artificial swap/volume/wallet event; no trading feedback. Missing samples stay gaps.
Each pool and all reserve inputs are read in ONE getMultipleAccounts response/slot.
"""
from __future__ import annotations
import argparse,base64,hashlib,math,urllib.request
from decimal import Decimal,localcontext
from a5.common import *
from a5.journal import Journal
add_legacy('runner')
import preflight_pools as pf
import token_capabilities as tc
from protocol_reference import uint,b58
INTERVAL_SECONDS=10.;MAX_RPC_AGE_MS=10_000;MAX_GAP_MS=30_000

def raw_account(value,owner=None):
    if not isinstance(value,dict) or value.get('executable') or (owner and value.get('owner')!=owner):raise ValueError('ACCOUNT_OWNER_OR_EXECUTABLE')
    data=value.get('data')
    if not isinstance(data,list) or len(data)!=2 or data[1]!='base64':raise ValueError('ACCOUNT_ENCODING')
    return base64.b64decode(data[0],validate=True)

def dependencies(row):
    keys=[row['pool'],row['coin_mint'],row['pc_mint']]
    if row['pool_kind']=='AMM_V4':
        raw=base64.b64decode(row['pool_data_b64'],validate=True)
        keys += [b58(raw[336:368]),b58(raw[368:400]),b58(raw[496:528])]
    elif row['pool_kind']=='CPMM':keys += [row['coin_vault'],row['pc_vault']]
    # CLMM reference comes from sqrt, NOT the ratio of vaults.
    return list(dict.fromkeys(keys))

def batches(rows,maximum=100):
    group=[];keys=set()
    for s,row in rows.items():
        wanted=set(dependencies(row))
        if len(keys|wanted)>maximum and group:yield group,sorted(keys);group=[];keys=set()
        if len(wanted)>maximum:raise ValueError('POOL_INPUT_BATCH_TOO_LARGE')
        group.append(s);keys |= wanted
    if group:yield group,sorted(keys)

def quote_snapshot(row,accounts):
    original=row;account=accounts[row['pool']];raw=raw_account(account,row['owner'])
    nowrow=pf.validate_pool(row['symbol'],row['pool'],row['meme_mint'],account)
    for k in ('owner','pool_kind','coin_mint','pc_mint','coin_decimals','pc_decimals'):
        if nowrow[k]!=row[k]:raise ValueError('POOL_IDENTITY_CHANGED:'+k)
    profiles={}
    for side in ('coin','pc'):
        mint=row[side+'_mint'];profile=tc.validate_mint(accounts[mint],mint)
        if profile['program']!=row[side+'_token_program'] or profile['decimals']!=row[side+'_decimals']:raise ValueError('MINT_CONTRACT_CHANGED')
        profiles[side]=profile
    d0,d1=row['coin_decimals'],row['pc_decimals'];meme0=row['coin_mint']==row['meme_mint']
    with localcontext() as ctx:
        ctx.prec=80
        if row['pool_kind']=='CLMM':
            sqrt=uint(raw,253,16)
            if sqrt<=0:raise ValueError('SQRT_NONPOSITIVE')
            p=Decimal(sqrt)**2/Decimal(1<<128)*Decimal(10)**(d0-d1)
            basis='CLMM_SQRT_X64_SNAPSHOT'
        else:
            if row['pool_kind']=='AMM_V4':
                # V4 status 1 initialized / 6 swap-only; orderbook-only/disabled not admitted.
                if uint(raw,0) not in (1,6):raise ValueError('AMM_V4_SWAP_STATUS_UNSUPPORTED')
                v0,v1=b58(raw[336:368]),b58(raw[368:400]);old=base64.b64decode(original['pool_data_b64'])
                if raw[336:400]!=old[336:400] or raw[496:592]!=old[496:592]:raise ValueError('AMM_RESERVE_DEPENDENCIES_CHANGED')
                oo_key=b58(raw[496:528]);oo_owner=b58(raw[560:592]);market=b58(raw[528:560]);oo=raw_account(accounts[oo_key],oo_owner)
                if len(oo)!=3228 or oo[:5]!=b'serum' or oo[-7:]!=b'padding':raise ValueError('OPEN_ORDERS_LAYOUT')
                flags=uint(oo,5)
                if flags & 5 != 5 or b58(oo[13:45])!=market:raise ValueError('OPEN_ORDERS_IDENTITY_FLAGS')
                a0=tc.validate_vault(accounts[v0],row['coin_mint'],profiles['coin']['program'],v0)
                a1=tc.validate_vault(accounts[v1],row['pc_mint'],profiles['pc']['program'],v1)
                # Effective reserves = vault + open orders total - unsettled PnL. Never vault-only.
                r0=int(a0['amount_raw'])+uint(oo,85)-uint(raw,192)
                r1=int(a1['amount_raw'])+uint(oo,101)-uint(raw,200)
                basis='AMM_V4_VAULT_PLUS_OPENORDERS_MINUS_NEED_TAKE_PNL'
            else:
                if nowrow['coin_vault']!=row['coin_vault'] or nowrow['pc_vault']!=row['pc_vault']:raise ValueError('CPMM_VAULT_CHANGED')
                for side in ('coin','pc'):
                    if nowrow[side+'_token_program']!=row[side+'_token_program']:raise ValueError('CPMM_PROGRAM_CHANGED')
                r=[]
                for side,offsets in [('coin',(341,357,397)),('pc',(349,365,405))]:
                    vault=tc.validate_vault(accounts[row[side+'_vault']],row[side+'_mint'],profiles[side]['program'],row[side+'_vault'])
                    r.append(int(vault['amount_raw'])-sum(uint(raw,k) for k in offsets))
                r0,r1=r;basis='CPMM_VAULT_MINUS_PROTOCOL_FUND_CREATOR_FEES'
            if min(r0,r1)<=0:raise ValueError('EFFECTIVE_RESERVE_NONPOSITIVE')
            p=Decimal(r1)/Decimal(r0)*Decimal(10)**(d0-d1)
        p=p if meme0 else 1/p
        value=float(p)
        if not math.isfinite(value) or value<=0:raise ValueError('INVALID_REFERENCE_PRICE')
        return {'symbol':row['symbol'],'pool':row['pool'],'price':value,'price_exact':str(p),'basis':basis,
                'quote':'WSOL','not_execution_price':True,'not_a_swap':True,'volume':None,'wallet':None}

def rpc_snapshot(url,keys,min_slot=0):
    config={'encoding':'base64','commitment':'confirmed'}
    if min_slot:config['minContextSlot']=min_slot
    body={'jsonrpc':'2.0','id':1,'method':'getMultipleAccounts','params':[keys,config]}
    req=urllib.request.Request(url,data=canonical(body).encode(),headers={'Content-Type':'application/json'})
    begin=now_ms()
    with urllib.request.urlopen(req,timeout=22) as response:raw=response.read((32<<20)+1)
    end=now_ms()
    if len(raw)>(32<<20):raise ValueError('RPC_SNAPSHOT_TOO_LARGE')
    value=strict_loads(raw)
    if value.get('id')!=1 or value.get('jsonrpc')!='2.0' or value.get('error'):raise ValueError('RPC_SNAPSHOT_ERROR')
    result=value.get('result') or {};slot=(result.get('context') or {}).get('slot');accounts=result.get('value')
    if type(slot) is not int or slot<min_slot or not isinstance(accounts,list) or len(accounts)!=len(keys):raise ValueError('RPC_CONTEXT_OR_COUNT')
    if end-begin>MAX_RPC_AGE_MS:raise ValueError('RPC_SNAPSHOT_LATENCY_EXCEEDS_10S')
    return accounts,slot,begin,end

def run(session):
    session=Path(session);out=session/'price_samples';out.mkdir(exist_ok=True);stop=Stop().install()
    cohort=read_json(session/'COHORT.json') or {};rows={s:read_json(session/'spot_prep'/s/'MANIFEST.json')['pools'][0] for s in cohort.get('qualified_at_start',[])}
    relay=read_json(session/'rpc_relay/ENDPOINT.json') or {};url=relay.get('url')
    if not url:heartbeat(out/'status.json','UNAVAILABLE',reason='NO_APPROVED_RELAY');return 0
    evidence=Journal(out/'snapshots.jsonl');observations=safe(out/'observations.jsonl').open('a');
    atomic(out/'READY.json',{'schema':'a3_price_producer_ready_v1','pid':os.getpid(),'at_ms':now_ms(),'observations_path':'observations.jsonl','not_a_swap':True},True)
    last_slot={};counts={s:0 for s in rows};failures={};round_no=0
    try:
        while not stop.value and not session_expired(session):
            started=time.monotonic();round_no+=1
            for names,keys in batches(rows):
                if stop.value or session_expired(session):break
                try:
                    vals,slot,begin,end=rpc_snapshot(url,keys,max((last_slot.get(s,0) for s in names),default=0));accounts=dict(zip(keys,vals))
                    evidence_row={'round':round_no,'keys':keys,'accounts':vals,'slot':slot,'requested_ms':begin,'available_ms':end,'orders':False}
                    evidence.append(evidence_row);evidence_hash=__import__('hashlib').sha256(canonical(evidence_row).encode()).hexdigest()
                    for s in names:
                        try:
                            if slot<=last_slot.get(s,-1):raise ValueError('RPC_SLOT_NOT_ADVANCING')
                            quote=quote_snapshot(rows[s],accounts);quote.update(available_ms=end,slot=slot,round=round_no,evidence_sha256=evidence_hash,source='RPC_POOL_STATE_LABEL_ONLY',valid=True)
                            observations.write(canonical(quote)+'\n');counts[s]+=1;last_slot[s]=slot;failures.pop(s,None)
                        except Exception as e:failures[s]=str(e);observations.write(canonical({'symbol':s,'valid':False,'available_ms':end,'slot':slot,'reason':str(e)})+'\n')
                    observations.flush()
                except Exception as e:
                    for s in names:failures[s]='RPC_SAMPLE_UNAVAILABLE:'+str(e)
                    evidence.append({'round':round_no,'symbols':names,'available_ms':now_ms(),'error':str(e),'orders':False})
            heartbeat(out/'status.json','ACTIVE' if any(counts.values()) else 'WAITING_VERIFIED_SAMPLES',counts=counts,failures=failures,
                      interval_target_seconds=INTERVAL_SECONDS,max_gap_ms=MAX_GAP_MS,label_basis='OBSERVED_PATH_SWAPS_PLUS_VERIFIED_STATE_SAMPLES',fake_swaps=0,signal_feedback=False)
            while not stop.value and not session_expired(session) and time.monotonic()-started<INTERVAL_SECONDS:time.sleep(.1)
    finally:
        observations.flush();os.fsync(observations.fileno());observations.close();evidence.close()
        atomic(out/'COMPLETE.json',{'counts':counts,'failures':failures,'orders':False,'fake_swaps':0,'not_continuous_price_path':True})
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
