#!/usr/bin/env python3
"""Minimal OpenCL 1.2 GPU core via Python ctypes.
No pyopencl, no CUDA toolkit and no compiler are required at runtime.
All model math is performed by the selected OpenCL GPU kernels.
"""
from __future__ import annotations
import ctypes as C,math,os,statistics,time
from array import array
from pathlib import Path
from typing import Iterable

CL_SUCCESS=0;CL_DEVICE_TYPE_GPU=1<<2;CL_TRUE=1
CL_PLATFORM_NAME=0x0902
CL_DEVICE_TYPE=0x1000;CL_DEVICE_MAX_COMPUTE_UNITS=0x1002;CL_DEVICE_MAX_WORK_GROUP_SIZE=0x1004
CL_DEVICE_GLOBAL_MEM_SIZE=0x101F;CL_DEVICE_NAME=0x102B;CL_DEVICE_VENDOR=0x102C;CL_DRIVER_VERSION=0x102D;CL_DEVICE_VERSION=0x102F
CL_MEM_READ_WRITE=1<<0;CL_MEM_READ_ONLY=1<<2
CL_PROGRAM_BUILD_LOG=0x1183
MAX_DIM=8192;MAX_HEADS=32;MAX_POOLS=12;BASE_DIM=64;MAX_BATCH=256

KERNEL=r'''\
#define BASE_DIM 64u
#define MAX_DIM 8192u
#define MAX_HEADS 32u
inline uint mix32(uint x){ x^=x>>16; x*=0x7feb352du; x^=x>>15; x*=0x846ca68bu; x^=x>>16; return x; }
inline float fexp(__global const float* x,uint sample,uint j){
  if(j==0u)return 1.0f;
  if(j<=64u)return x[sample*BASE_DIM+j-1u];
  uint k=j-65u;
  uint h1=mix32(k*0x9e3779b1u+0x85ebca6bu);
  uint h2=mix32(k*0xc2b2ae35u+0x27d4eb2fu);
  uint a=h1&63u,b=h2&63u;float va=x[sample*BASE_DIM+a],vb=x[sample*BASE_DIM+b];uint mode=(k>>3)&3u;float v;
  if(mode==0u)v=va*vb;
  else if(mode==1u)v=va*vb*(0.5f+0.5f*fabs(va-vb));
  else if(mode==2u)v=va*va*(vb>=0.0f?1.0f:-1.0f);
  else v=0.5f*(va+vb)*fabs(va-vb);
  if(h1&0x80000000u)v=-v;
  return clamp(v,-1.0f,1.0f);
}
__kernel void predict(__global const float* x,__global const float* w,__global float* out,
                      uint pool,uint dim,uint heads,uint batch){
  uint gid=get_global_id(0);uint total=heads*batch;if(gid>=total)return;
  uint sample=gid/heads,head=gid-heads*sample;
  uint off=(pool*MAX_HEADS+head)*MAX_DIM;float z=0.0f;
  for(uint j=0u;j<dim;j++)z += w[off+j]*fexp(x,sample,j);
  z=clamp(z,-20.0f,20.0f);out[gid]=1.0f/(1.0f+exp(-z));
}
__kernel void train_one(__global const float* x,__global float* w,uint pool,uint head,uint dim,
                        float lr,float l2,float label,float prob){
  uint j=get_global_id(0);if(j>=dim)return;uint idx=(pool*MAX_HEADS+head)*MAX_DIM+j;
  float old=w[idx],reg=(j==0u?0.0f:l2*old);float g=(prob-label)*fexp(x,0u,j)+reg;
  w[idx]=clamp(old-lr*g,-20.0f,20.0f);
}
'''

class OpenCLError(RuntimeError):pass

def _ck(code,where):
    if int(code)!=CL_SUCCESS:raise OpenCLError(f'{where}:CL_ERROR_{int(code)}')

def _pctl(xs,q):
    if not xs:return None
    a=sorted(xs);k=(len(a)-1)*q;i=int(k);j=min(len(a)-1,i+1);t=k-i;return a[i]*(1-t)+a[j]*t

def _handle_value(x):
    """Normalize ctypes opaque handles.

    Elements read from a ctypes c_void_p array are returned as Python ints on
    CPython/Linux, while some mocks/wrappers may expose c_void_p instances.
    Keep both forms valid before storing the handle.
    """
    v=getattr(x,'value',x)
    if v is None:raise OpenCLError('NULL_OPENCL_HANDLE')
    return int(v)

def _probe_platform_count(lib,fn_name='clGetPlatformIDs'):
    """Return (rc, count) for a standard or ICD platform enumerator."""
    fn=getattr(lib,fn_name)
    fn.argtypes=[C.c_uint,C.POINTER(C.c_void_p),C.POINTER(C.c_uint)]
    fn.restype=C.c_int
    n=C.c_uint()
    rc=int(fn(0,None,C.byref(n)))
    return rc,int(n.value)

def _candidate_libraries(preferred_vendor='NVIDIA'):
    # Keep the system ICD loader first.  On some Linux/NVIDIA installations
    # the ICD file is present while the loader still reports -1001; in that
    # case calling NVIDIA's implementation directly is a discovery fallback.
    out=['libOpenCL.so.1']
    if 'nvidia' in str(preferred_vendor).lower():
        out += ['/lib/x86_64-linux-gnu/libnvidia-opencl.so.1',
                '/usr/lib/x86_64-linux-gnu/libnvidia-opencl.so.1',
                'libnvidia-opencl.so.1']
    seen=set();return [x for x in out if not (x in seen or seen.add(x))]

def _load_opencl_library(preferred_vendor='NVIDIA'):
    attempts=[]
    mode=getattr(C,'RTLD_GLOBAL',0)
    for name in _candidate_libraries(preferred_vendor):
        try:
            lib=C.CDLL(name,mode=mode)
        except Exception as e:
            attempts.append(f'{name}:LOAD_ERROR:{type(e).__name__}:{e}')
            continue
        # NVIDIA ICD implementations may expose the ICD enumerator even when
        # the generic loader path cannot enumerate a platform. Try both.
        for fn_name in ('clGetPlatformIDs','clIcdGetPlatformIDsKHR'):
            try:
                rc,count=_probe_platform_count(lib,fn_name)
            except AttributeError:
                attempts.append(f'{name}:{fn_name}:MISSING_SYMBOL')
                continue
            except Exception as e:
                attempts.append(f'{name}:{fn_name}:PROBE_ERROR:{type(e).__name__}:{e}')
                continue
            attempts.append(f'{name}:{fn_name}:rc={rc}:platforms={count}')
            if rc==CL_SUCCESS and count>0:
                return lib,name,fn_name,attempts
    env_bits=[]
    for k in ('OCL_ICD_VENDORS','OCL_ICD_FILENAMES'):
        if k in os.environ:env_bits.append(f'{k}={os.environ.get(k)!r}')
    icd='/etc/OpenCL/vendors/nvidia.icd'
    try:icd_text=Path(icd).read_text(errors='replace').strip()
    except Exception as e:icd_text=f'<unreadable:{type(e).__name__}:{e}>'
    detail='; '.join(attempts)
    envtxt=', '.join(env_bits) if env_bits else '<none>'
    raise OpenCLError('NO_OPENCL_PLATFORM; attempts=['+detail+']; env_overrides='+envtxt+'; nvidia_icd='+repr(icd_text))

class Core:
    def __init__(self,preferred_vendor='NVIDIA'):
        self.lib,self.opencl_library,self.platform_enum,self.discovery_attempts=_load_opencl_library(preferred_vendor)
        self._bind();self.platform,self.device=self._pick(preferred_vendor)
        self.info=self._device_info()
        err=C.c_int()
        dev=C.c_void_p(self.device)
        self.context=self.lib.clCreateContext(None,1,C.byref(dev),None,None,C.byref(err));_ck(err.value,'clCreateContext')
        self.queue=self.lib.clCreateCommandQueue(self.context,self.device,0,C.byref(err));_ck(err.value,'clCreateCommandQueue')
        src=KERNEL.encode();sp=C.c_char_p(src);ln=C.c_size_t(len(src))
        self.program=self.lib.clCreateProgramWithSource(self.context,1,C.byref(sp),C.byref(ln),C.byref(err));_ck(err.value,'clCreateProgramWithSource')
        rc=self.lib.clBuildProgram(self.program,1,C.byref(dev),b'-cl-std=CL1.2',None,None)
        if rc!=0:
            log=self._build_log();raise OpenCLError(f'clBuildProgram:CL_ERROR_{rc}:\n{log}')
        self.kpred=self.lib.clCreateKernel(self.program,b'predict',C.byref(err));_ck(err.value,'clCreateKernel(predict)')
        self.ktrain=self.lib.clCreateKernel(self.program,b'train_one',C.byref(err));_ck(err.value,'clCreateKernel(train_one)')
        self.weight_count=MAX_POOLS*MAX_HEADS*MAX_DIM
        self.weights=self.lib.clCreateBuffer(self.context,CL_MEM_READ_WRITE,self.weight_count*4,None,C.byref(err));_ck(err.value,'clCreateBuffer(weights)')
        self.xbuf=self.lib.clCreateBuffer(self.context,CL_MEM_READ_ONLY,MAX_BATCH*BASE_DIM*4,None,C.byref(err));_ck(err.value,'clCreateBuffer(x)')
        self.outbuf=self.lib.clCreateBuffer(self.context,CL_MEM_READ_WRITE,MAX_BATCH*MAX_HEADS*4,None,C.byref(err));_ck(err.value,'clCreateBuffer(out)')
        zeros=(C.c_float*self.weight_count)()
        _ck(self.lib.clEnqueueWriteBuffer(self.queue,self.weights,CL_TRUE,0,self.weight_count*4,C.byref(zeros),0,None,None),'zero_weights')
        self.closed=False
    def _bind(self):
        L=self.lib;vp=C.c_void_p;u=C.c_uint;sz=C.c_size_t;ull=C.c_ulonglong;i=C.c_int
        self._platform_ids=getattr(L,self.platform_enum);self._platform_ids.argtypes=[u,C.POINTER(vp),C.POINTER(u)];self._platform_ids.restype=i
        L.clGetPlatformInfo.argtypes=[vp,u,sz,vp,C.POINTER(sz)];L.clGetPlatformInfo.restype=i
        L.clGetDeviceIDs.argtypes=[vp,C.c_ulong,u,C.POINTER(vp),C.POINTER(u)];L.clGetDeviceIDs.restype=i
        L.clGetDeviceInfo.argtypes=[vp,u,sz,vp,C.POINTER(sz)];L.clGetDeviceInfo.restype=i
        L.clCreateContext.argtypes=[vp,u,C.POINTER(vp),vp,vp,C.POINTER(i)];L.clCreateContext.restype=vp
        L.clCreateCommandQueue.argtypes=[vp,vp,C.c_ulong,C.POINTER(i)];L.clCreateCommandQueue.restype=vp
        L.clCreateProgramWithSource.argtypes=[vp,u,C.POINTER(C.c_char_p),C.POINTER(sz),C.POINTER(i)];L.clCreateProgramWithSource.restype=vp
        L.clBuildProgram.argtypes=[vp,u,C.POINTER(vp),C.c_char_p,vp,vp];L.clBuildProgram.restype=i
        L.clGetProgramBuildInfo.argtypes=[vp,vp,u,sz,vp,C.POINTER(sz)];L.clGetProgramBuildInfo.restype=i
        L.clCreateKernel.argtypes=[vp,C.c_char_p,C.POINTER(i)];L.clCreateKernel.restype=vp
        L.clCreateBuffer.argtypes=[vp,C.c_ulong,sz,vp,C.POINTER(i)];L.clCreateBuffer.restype=vp
        L.clSetKernelArg.argtypes=[vp,u,sz,vp];L.clSetKernelArg.restype=i
        L.clEnqueueWriteBuffer.argtypes=[vp,vp,u,sz,sz,vp,u,vp,vp];L.clEnqueueWriteBuffer.restype=i
        L.clEnqueueReadBuffer.argtypes=[vp,vp,u,sz,sz,vp,u,vp,vp];L.clEnqueueReadBuffer.restype=i
        L.clEnqueueNDRangeKernel.argtypes=[vp,vp,u,vp,C.POINTER(sz),vp,u,vp,vp];L.clEnqueueNDRangeKernel.restype=i
        L.clFinish.argtypes=[vp];L.clFinish.restype=i
        for name in ('clReleaseMemObject','clReleaseKernel','clReleaseProgram','clReleaseCommandQueue','clReleaseContext'):
            f=getattr(L,name);f.argtypes=[vp];f.restype=i
    def _sinfo(self,which,obj,fn):
        n=C.c_size_t();_ck(fn(obj,which,0,None,C.byref(n)),'info_size');buf=C.create_string_buffer(max(1,n.value));_ck(fn(obj,which,n.value,buf,None),'info');return buf.value.decode(errors='replace')
    def _pick(self,vendor):
        n=C.c_uint();rc=self._platform_ids(0,None,C.byref(n));
        if rc!=0 or n.value==0:raise OpenCLError(f'NO_OPENCL_PLATFORM_AFTER_SELECTION:{self.opencl_library}:{self.platform_enum}:rc={int(rc)}:count={int(n.value)}')
        plats=(C.c_void_p*n.value)();_ck(self._platform_ids(n.value,plats,None),self.platform_enum)
        candidates=[]
        for p in plats:
            dn=C.c_uint();rc=self.lib.clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,0,None,C.byref(dn))
            if rc!=0 or dn.value==0:continue
            ds=(C.c_void_p*dn.value)();_ck(self.lib.clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,dn.value,ds,None),'clGetDeviceIDs')
            for d in ds:
                name=self._sinfo(CL_DEVICE_NAME,d,self.lib.clGetDeviceInfo);ven=self._sinfo(CL_DEVICE_VENDOR,d,self.lib.clGetDeviceInfo)
                score=(100 if vendor.lower() in ven.lower() or vendor.lower() in name.lower() else 0)
                candidates.append((score,_handle_value(p),_handle_value(d),name,ven))
        if not candidates:raise OpenCLError('NO_OPENCL_GPU_DEVICE')
        candidates.sort(reverse=True);_,p,d,_,_=candidates[0];return p,d
    def _num(self,which,ctype):
        v=ctype();_ck(self.lib.clGetDeviceInfo(self.device,which,C.sizeof(v),C.byref(v),None),'clGetDeviceInfo(num)');return int(v.value)
    def _device_info(self):
        return {'opencl_library':self.opencl_library,
                'platform_enumerator':self.platform_enum,
                'discovery_attempts':list(self.discovery_attempts),
                'platform':self._sinfo(CL_PLATFORM_NAME,self.platform,self.lib.clGetPlatformInfo),
                'name':self._sinfo(CL_DEVICE_NAME,self.device,self.lib.clGetDeviceInfo),
                'vendor':self._sinfo(CL_DEVICE_VENDOR,self.device,self.lib.clGetDeviceInfo),
                'device_version':self._sinfo(CL_DEVICE_VERSION,self.device,self.lib.clGetDeviceInfo),
                'driver_version':self._sinfo(CL_DRIVER_VERSION,self.device,self.lib.clGetDeviceInfo),
                'global_mem_bytes':self._num(CL_DEVICE_GLOBAL_MEM_SIZE,C.c_ulonglong),
                'compute_units':self._num(CL_DEVICE_MAX_COMPUTE_UNITS,C.c_uint),
                'max_work_group_size':self._num(CL_DEVICE_MAX_WORK_GROUP_SIZE,C.c_size_t)}
    def _build_log(self):
        n=C.c_size_t();self.lib.clGetProgramBuildInfo(self.program,self.device,CL_PROGRAM_BUILD_LOG,0,None,C.byref(n));buf=C.create_string_buffer(max(1,n.value));self.lib.clGetProgramBuildInfo(self.program,self.device,CL_PROGRAM_BUILD_LOG,n.value,buf,None);return buf.value.decode(errors='replace')
    def _set(self,k,idx,obj):_ck(self.lib.clSetKernelArg(k,idx,C.sizeof(obj),C.byref(obj)),f'clSetKernelArg[{idx}]')
    def _write_x(self,bases:list[list[float]]):
        if not 1<=len(bases)<=MAX_BATCH:raise ValueError('BATCH_RANGE')
        flat=[]
        for x in bases:
            if len(x)!=BASE_DIM:raise ValueError('BASE_DIM')
            if not all(math.isfinite(float(v)) and -1.0001<=float(v)<=1.0001 for v in x):raise ValueError('BASE_VALUE')
            flat.extend(float(v) for v in x)
        a=(C.c_float*len(flat))(*flat);_ck(self.lib.clEnqueueWriteBuffer(self.queue,self.xbuf,CL_TRUE,0,len(flat)*4,C.byref(a),0,None,None),'write_x')
    def predict_batch(self,pool:int,bases:list[list[float]],dim:int,heads:int=32)->list[list[float]]:
        if not 0<=pool<MAX_POOLS or not 65<=dim<=MAX_DIM or not 1<=heads<=MAX_HEADS:raise ValueError('PREDICT_RANGE')
        self._write_x(bases);u=C.c_uint
        for idx,obj in enumerate((C.c_void_p(self.xbuf),C.c_void_p(self.weights),C.c_void_p(self.outbuf),u(pool),u(dim),u(heads),u(len(bases)))):self._set(self.kpred,idx,obj)
        gs=C.c_size_t(len(bases)*heads);_ck(self.lib.clEnqueueNDRangeKernel(self.queue,self.kpred,1,None,C.byref(gs),None,0,None,None),'predict_kernel');_ck(self.lib.clFinish(self.queue),'predict_finish')
        n=len(bases)*heads;out=(C.c_float*n)();_ck(self.lib.clEnqueueReadBuffer(self.queue,self.outbuf,CL_TRUE,0,n*4,C.byref(out),0,None,None),'read_probs')
        vals=[float(out[i]) for i in range(n)];return [vals[i*heads:(i+1)*heads] for i in range(len(bases))]
    def train(self,pool:int,head:int,base:list[float],dim:int,label:int,lr:float,l2:float)->float:
        if label not in (0,1):raise ValueError('LABEL')
        p=self.predict_batch(pool,[base],dim,max(head+1,1))[0][head]
        self._write_x([base]);u=C.c_uint;f=C.c_float
        args=(C.c_void_p(self.xbuf),C.c_void_p(self.weights),u(pool),u(head),u(dim),f(lr),f(l2),f(float(label)),f(float(p)))
        for idx,obj in enumerate(args):self._set(self.ktrain,idx,obj)
        gs=C.c_size_t(dim);_ck(self.lib.clEnqueueNDRangeKernel(self.queue,self.ktrain,1,None,C.byref(gs),None,0,None,None),'train_kernel');_ck(self.lib.clFinish(self.queue),'train_finish');return float(p)
    def read_weights(self)->array:
        out=(C.c_float*self.weight_count)();_ck(self.lib.clEnqueueReadBuffer(self.queue,self.weights,CL_TRUE,0,self.weight_count*4,C.byref(out),0,None,None),'read_weights');return array('f',out)
    def write_weights(self,a:array):
        if a.typecode!='f' or len(a)!=self.weight_count:raise ValueError('WEIGHT_STATE_SHAPE')
        buf=(C.c_float*len(a)).from_buffer(a);_ck(self.lib.clEnqueueWriteBuffer(self.queue,self.weights,CL_TRUE,0,len(a)*4,C.byref(buf),0,None,None),'write_weights')
    def benchmark(self,stages,iterations=40,batch=128):
        bases=[]
        for i in range(batch):bases.append([math.sin((i+1)*(j+3)*0.017) for j in range(BASE_DIM)])
        rows=[]
        for dim,heads in stages:
            times=[]
            self.predict_batch(0,bases,dim,heads)
            for _ in range(iterations):
                t=time.perf_counter_ns();self.predict_batch(0,bases,dim,heads);times.append((time.perf_counter_ns()-t)/1e6)
            rows.append({'dimension':dim,'heads':heads,'batch':batch,'iterations':iterations,'p50_ms':_pctl(times,.5),'p95_ms':_pctl(times,.95),'max_ms':max(times),'throughput_forecasts_s':1000.0*batch/(statistics.mean(times) or 1e-9)})
        return rows
    def self_test(self,dim=512):
        from features import expand_cpu
        base=[math.sin((j+1)*0.37)*0.7 for j in range(BASE_DIM)]
        p0=self.predict_batch(0,[base],dim,1)[0][0]
        if abs(p0-.5)>2e-6:raise OpenCLError('SELFTEST_ZERO_NOT_HALF')
        for _ in range(5):self.train(0,0,base,dim,1,.03,.001)
        pg=self.predict_batch(0,[base],dim,1)[0][0]
        weights=self.read_weights();cpu_x=expand_cpu(base,dim);off=0;z=sum(float(weights[off+j])*cpu_x[j] for j in range(dim));pc=1/(1+math.exp(-max(-20,min(20,z))))
        if abs(pg-pc)>2e-4:raise OpenCLError(f'SELFTEST_GPU_CPU_MISMATCH:{pg}:{pc}')
        if pg<=p0:raise OpenCLError('SELFTEST_TRAIN_DIRECTION')
        # Detect pool/head stride mistakes at the last of twelve GPU partitions.
        last_before=self.predict_batch(MAX_POOLS-1,[base],dim,MAX_HEADS)[0][-1]
        if abs(last_before-.5)>2e-6:raise OpenCLError('SELFTEST_POOL_ISOLATION')
        self.train(MAX_POOLS-1,MAX_HEADS-1,base,dim,1,.03,.001)
        last_after=self.predict_batch(MAX_POOLS-1,[base],dim,MAX_HEADS)[0][-1]
        if last_after<=last_before:raise OpenCLError('SELFTEST_LAST_POOL_HEAD')
        other=self.predict_batch(MAX_POOLS-1,[base],dim,MAX_HEADS)[0][0]
        if abs(other-.5)>2e-6:raise OpenCLError('SELFTEST_HEAD_ISOLATION')
        # Restore a clean model after the diagnostic.
        zeros=array('f',[0.0])*self.weight_count;self.write_weights(zeros)
        return {'zero_probability':p0,'trained_probability':pg,'cpu_reference_probability':pc,'abs_error':abs(pg-pc),'dimension':dim}
    def close(self):
        if getattr(self,'closed',True):return
        self.closed=True
        for obj,fn in ((self.outbuf,self.lib.clReleaseMemObject),(self.xbuf,self.lib.clReleaseMemObject),(self.weights,self.lib.clReleaseMemObject),(self.ktrain,self.lib.clReleaseKernel),(self.kpred,self.lib.clReleaseKernel),(self.program,self.lib.clReleaseProgram),(self.queue,self.lib.clReleaseCommandQueue),(self.context,self.lib.clReleaseContext)):
            try:fn(obj)
            except Exception:pass
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
