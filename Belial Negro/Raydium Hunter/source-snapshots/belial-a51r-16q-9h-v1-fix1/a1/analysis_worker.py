"""Adapter to the unchanged wallet/Tail rules; only fresh, qualified source rows enter."""
from __future__ import annotations
import argparse,collections,heapq,sqlite3
from a1.common import *
from a1.catalog import source_ready
add_legacy('mp12','runner')
import common as historic
import tail as principal
import shadow_compare as shadow

def run(session):
    session=Path(session);out=session/'aggregate';out.mkdir(exist_ok=True);stop=Stop().install()
    cohort=read_json(session/'COHORT.json');allowed=cohort['qualified_at_start'];mapping={s:read_json(session/'spot_prep'/s/'MANIFEST.json')['pools'][0]['pool'] for s in allowed}
    shadow.POOLS.clear();shadow.POOLS.update(mapping)
    journals={'m2_tail':historic.Journal(out/'m2_tail'),'shadow_compare':historic.Journal(out/'shadow_compare')}
    blocked={};counts=collections.Counter();seen=sqlite3.connect(out/'source_offsets.sqlite3');seen.execute('PRAGMA journal_mode=WAL');seen.execute('PRAGMA synchronous=FULL')
    seen.execute('CREATE TABLE seen(kind TEXT,symbol TEXT,id TEXT,PRIMARY KEY(kind,symbol,id))')
    files={k:(out/(k+'s.jsonl')).open('a') for k in ('swap','exit')};readers={(s,k):JsonTail(session/'sources'/s/('m2-'+s)/(k+'s.jsonl')) for s in allowed for k in ('swap','exit')}
    def active(s,at):
        if s in blocked:return False
        state=read_json(session/'sources'/s/'status.json',{})
        return source_ready(s,cohort,state,at)[0]
    def emit(name):
        def write(kind,row):
            if row.get('kind')=='EPISODE_OPEN' and not active(row.get('symbol'),now_ms()):
                journals[name].emit('events',dict(kind='A1_DEPENDENCY_REJECTED',at_unix_ms=now_ms(),symbol=row.get('symbol'),paper_only=True,orders=False));return
            journals[name].emit(kind,row)
        return write
    models={'m2_tail':principal.Tail(emit('m2_tail'),session.name),'shadow_compare':shadow.Tail(emit('shadow_compare'),session.name)}
    last=0;error=None
    try:
        while not stop.value and not session_expired(session):
            at=now_ms();batch=[]
            for (s,kind),reader in readers.items():
                if s in blocked:continue
                try:
                    for offset,row in reader.read(128):
                        batch.append((int(row.get('writer_observed_unix_ms') or row.get('signal_available_unix_ms') or row.get('received_unix_ms') or at),0 if kind=='swap' else 1,s,kind,offset,row))
                except Exception as e:blocked[s]='SOURCE_READER:'+str(e)
            batch.sort(key=lambda r:(r[0],r[1],r[2],r[4]))
            for _,__,s,kind,offset,row in batch:
                observed=now_ms()
                if not active(s,observed):counts['rejected_unavailable_source']+=1;continue
                try:
                    if row.get('symbol')!=s or (kind=='swap' and row.get('pool')!=mapping[s]):raise ValueError('SOURCE_IDENTITY_MISMATCH')
                    available=int(row.get('signal_available_unix_ms') if kind=='exit' else row.get('received_unix_ms'))
                    if available>observed or observed-available>30_000:counts['expired_or_future_input']+=1;continue
                    key=str(row.get('event_id') if kind=='exit' else row.get('signature'))
                    if not key or key=='None':raise ValueError('EVENT_ID_MISSING')
                    cur=seen.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?)',(kind,s,key))
                    if cur.rowcount==0:counts['duplicate_input']+=1;continue
                    # The native receipt/actor availability is preserved. Adapter arrival is an additional field, not backdating.
                    value=dict(row,a1_available_ms=observed,a1_source=s,a1_source_offset=offset,a1_source_generation=0)
                    shadow_model=models['shadow_compare'];shadow_model.feed(kind,value,max(observed,shadow_model.clock))
                    if s in principal.POOLS:
                        model=models['m2_tail'];model.feed(kind,value,max(observed,model.clock))
                    files[kind].write(canonical(value)+'\n');files[kind].flush();counts[kind]+=1
                except (ValueError,KeyError,TypeError,RuntimeError) as e:blocked[s]='NORMALIZATION:'+str(e)
            # Withdraw only undecided confirmations/entries of an unavailable source.
            # Existing reference positions are not removed; perp exits are managed independently.
            for model in models.values():
                retained=[]
                for item in model.pending:
                    due,seq,kind,obj=item
                    s=obj.get('symbol') if isinstance(obj,dict) else None
                    if kind in ('confirm','entry') and s and not active(s,now_ms()):counts['withdrawn_pending_'+kind]+=1
                    else:retained.append(item)
                model.pending=retained;heapq.heapify(model.pending);model.advance(max(now_ms(),model.clock))
            if now_ms()-last>=500:
                seen.commit()
                for name,model in models.items():atomic(out/name/'state.json',dict(model.state(),a1_blocked=blocked,a1_cohort=allowed))
                atomic(session/'ANALYSIS_BLOCKED.json',blocked);heartbeat(out/'status.json','ACTIVE',counts=dict(counts),blocked=blocked,orders=False);last=now_ms()
            if not batch:time.sleep(.02)
    except Exception as e:
        error=str(e);atomic(session/'INTEGRITY.json',{'ok':False,'reason':'ANALYSIS_WRITER_OR_GLOBAL_FAILURE:'+error})
        heartbeat(out/'status.json','FAILED',error=error)
    finally:
        for reader in readers.values():reader.close()
        for f in files.values():f.flush();os.fsync(f.fileno());f.close()
        seen.commit();seen.close()
        for name,model in models.items():
            atomic(out/name/'state.json',dict(model.state(),complete=True,a1_blocked=blocked,open_positions_censored=True));journals[name].close()
        (out/'SESSION_COMPLETE').touch();atomic(out/'COMPLETE.json',{'ok':error is None,'error':error,'counts':dict(counts),'blocked':blocked,'orders':False})
    return 0 if error is None else 2
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
