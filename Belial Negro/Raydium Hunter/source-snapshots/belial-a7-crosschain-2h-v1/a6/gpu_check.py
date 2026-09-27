"""On-device equivalence check for the single-head inference optimization."""
from array import array
import json,sys,math
from a51.common import ROOT
sys.path.insert(0,str(ROOT/'n2_a2/tools'))
from opencl_core import Core,MAX_DIM,MAX_HEADS

def run():
    core=Core()
    try:
        weights=core.read_weights()
        for h in range(32):
            for j in range(128):weights[h*MAX_DIM+j]=((j+h)%17-8)*.003
        core.write_weights(weights);x=[((i%11)-5)/8 for i in range(64)]
        ref=core.predict_batch(0,[x],8192,32)[0];err=0.
        for h in (0,1,15,31):
            p=core.predict_batch(0,[x],8192,1,first_head=h)[0][0];err=max(err,abs(p-ref[h]))
            if abs(p-ref[h])>1e-6:raise RuntimeError('GPU_HEAD_OFFSET_MISMATCH')
        core.train(0,31,x,8192,1,.001,.0001);after=core.read_weights()
        if after[:31*MAX_DIM]!=weights[:31*MAX_DIM]:raise RuntimeError('GPU_UPDATED_OTHER_HEAD')
        if after[31*MAX_DIM:32*MAX_DIM]==weights[31*MAX_DIM:32*MAX_DIM]:raise RuntimeError('GPU_HEAD_NOT_UPDATED')
        return dict(ok=True,device=core.info,max_probability_error=err,native_gpu_executed=True)
    finally:core.close()
if __name__=='__main__':print(json.dumps(run(),indent=2))
