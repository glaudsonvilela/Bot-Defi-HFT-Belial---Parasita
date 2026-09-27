"""Immutable checkpoint generations; no A1/A2 weight reuse or in-session restart replay."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from pathlib import Path
from a51.common import ROOT, BASE, canonical, safe, atomic, digest, now_ms

REVISION='A6_CAUSAL_OBSERVER_HEAD_OFFSET_1'

def contract():
    files=['n2_a2/CONFIG_N2.json','n2_a2/tools/features.py','n2_a2/tools/observer.py',
           'n2_a2/tools/opencl_core.py','n2_a2/tools/common.py','UNIVERSE_A2.json','a51/causal_observer.py','a51/agent_io.py','a51/n2_worker.py','a51/price_samples.py','a51/analysis_worker.py','a51/model_store.py']
    obj={'revision':REVISION,'files':{n:digest(ROOT/n) for n in files},
         'scope':'TOKEN_WSOL_OBSERVED_PATH_NOT_PERP_PNL','dimension':8192,'heads':32}
    return dict(obj,sha256=hashlib.sha256(canonical(obj).encode()).hexdigest())

def reset_evaluation(model):
    # Retain learned weights and training-only baseline counts; score this session separately.
    for st in model.stats.values():
        for key in tuple(st):
            if key not in ('updates','positives'):st[key]=0 if key.endswith('_n') else 0.0
    model.last_primary.clear()

def restore(core,model,runtime,live,store=None,cutoff_ms=None,mode='continue'):
    if mode not in ('continue','fresh'):raise ValueError('MODEL_MODE_MUST_BE_CONTINUE_OR_FRESH')
    runtime=Path(runtime);fingerprint=contract();root=safe(Path(store or BASE/'runtime/belial-a6/models')/fingerprint['sha256'])
    result={'loaded':False,'mode':mode,'contract':fingerprint,'reason':'NO_COMPATIBLE_A6_CHECKPOINT',
            'old_a2_weights_touched':False,'evaluation_reset_for_session':True}
    if mode=='fresh':result['reason']='FRESH_EXPLICIT';return result
    pointer=root/'LATEST.json'
    if not pointer.is_file():return result
    value=json.loads(safe(pointer).read_text());generation=value.get('generation')
    if not isinstance(generation,str) or Path(generation).name!=generation:raise RuntimeError('CHECKPOINT_GENERATION_INVALID')
    directory=safe(root/generation);manifest=json.loads(safe(directory/'CONTRACT.json').read_text())
    if manifest.get('contract')!=fingerprint or manifest.get('complete') is not True:
        raise RuntimeError('CHECKPOINT_CONTRACT_OR_COMPLETENESS_MISMATCH')
    if cutoff_ms is not None and manifest.get('saved_ms',0)>=cutoff_ms:raise RuntimeError('CHECKPOINT_NOT_STRICTLY_PRIOR')
    for name in ('model.weights.bin','model.meta.json'):
        if digest(directory/name)!=manifest['files'][name]:raise RuntimeError('CHECKPOINT_HASH_MISMATCH:'+name)
    # Validate shape and finite weights before they can reach OpenCL buffers.
    meta=json.loads((directory/'model.meta.json').read_text())
    if meta.get('dimension')!=8192 or meta.get('active_heads')!=32:raise RuntimeError('CHECKPOINT_SHAPE_MISMATCH')
    from array import array
    import math
    expected=len(core.read_weights())
    if meta.get('weight_count')!=expected or (directory/'model.weights.bin').stat().st_size!=expected*4:
        raise RuntimeError('CHECKPOINT_WEIGHT_LENGTH_MISMATCH')
    a=array('f');a.frombytes((directory/'model.weights.bin').read_bytes())
    if not all(math.isfinite(v) for v in a):raise RuntimeError('CHECKPOINT_NONFINITE')
    for name in ('model.weights.bin','model.meta.json'):shutil.copyfile(directory/name,safe(runtime/name))
    loaded=live.load_state(core,model,runtime)
    if loaded.get('loaded') is not True:raise RuntimeError('CHECKPOINT_LOAD_REJECTED:'+str(loaded.get('reason')))
    reset_evaluation(model)
    return dict(result,**loaded,reason='COMPATIBLE_COMPLETED_A6_SESSION',parent_generation=generation)

def publish(runtime,session,store=None):
    runtime=Path(runtime);fingerprint=contract();root=safe(Path(store or BASE/'runtime/belial-a6/models')/fingerprint['sha256'])
    root.mkdir(parents=True,exist_ok=True);name=Path(session).name
    if not name.startswith('m2-a6-') or not all(c.isalnum() or c in '-_' for c in name):raise ValueError('CHECKPOINT_SESSION_NAME')
    dest=safe(root/name)
    if dest.exists():raise RuntimeError('CHECKPOINT_IMMUTABLE_GENERATION_EXISTS')
    staging=Path(tempfile.mkdtemp(prefix='.new-',dir=root))
    try:
        for n in ('model.weights.bin','model.meta.json'):
            with safe(runtime/n).open('rb') as src,(staging/n).open('xb') as out:
                shutil.copyfileobj(src,out);out.flush();os.fsync(out.fileno())
        obj={'contract':fingerprint,'complete':True,'session':name,'saved_ms':now_ms(),
             'files':{n:digest(staging/n) for n in ('model.weights.bin','model.meta.json')},
             'approval_for_trading':False}
        atomic(staging/'CONTRACT.json',obj,True);staging.rename(dest)
        atomic(root/'LATEST.json',{'generation':name,'contract_sha256':fingerprint['sha256'],'saved_ms':obj['saved_ms']},True)
        return {'published':True,'generation':name,'contract_sha256':fingerprint['sha256']}
    finally:
        if staging.exists():shutil.rmtree(staging)
