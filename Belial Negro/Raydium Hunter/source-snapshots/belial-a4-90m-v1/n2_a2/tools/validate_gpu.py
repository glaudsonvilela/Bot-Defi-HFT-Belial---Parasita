#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import common as c
import observer as o
from opencl_core import Core
from live import select_stage,gpu_telemetry

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path);a=ap.parse_args();cfg=o.CONFIG['gpu_benchmark']
    with Core(o.CONFIG.get('preferred_vendor','NVIDIA')) as core:
        if 'NVIDIA' not in core.info['vendor'].upper() and 'NVIDIA' not in core.info['name'].upper():raise RuntimeError('NVIDIA_OPENCL_GPU_NOT_SELECTED')
        if int(core.info['global_mem_bytes']) < 2*1024**3:raise RuntimeError('GPU_GLOBAL_MEMORY_LT_2GIB')
        st=core.self_test(512);rows=core.benchmark(cfg['stages'],int(cfg['iterations']),int(cfg['synthetic_batch']))
        dim,heads=select_stage(rows,float(cfg['p95_kernel_ms_limit']),cfg['minimum_live_stage'])
        result={'BELIAL_N2_GPU_VALIDATION_PASS':True,'device':core.info,'self_test':st,'stages':rows,'selected_dimension':dim,'selected_heads':heads,'telemetry':gpu_telemetry(),'paper_only':True,'signal_engine_feedback':False,'transactions_submitted':False,'at_ms':c.now_ms()}
    if a.out:c.atomic_json(a.out,result)
    print(json.dumps(result,indent=2,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
