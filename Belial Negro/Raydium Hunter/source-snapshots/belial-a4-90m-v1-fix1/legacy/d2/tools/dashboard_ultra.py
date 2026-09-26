#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, ctypes as C, json, math, os, signal, struct, subprocess, sys, time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
except Exception as e:
    raise SystemExit('TKINTER_REQUIRED:'+str(e))

BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
POOLS=('FARTCOIN','BOME','WIF','POPCAT','RAY','JUP')
STOP=False

def stop(*_):
    global STOP; STOP=True

def now_ns(): return time.time_ns()
def pct(xs,q):
    a=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not a:return None
    if len(a)==1:return a[0]
    k=(len(a)-1)*q;lo=int(k);hi=min(len(a)-1,lo+1);t=k-lo
    return a[lo]*(1-t)+a[hi]*t

def fmt_countdown(deadline_ns:int, at_ns:int|None=None)->str:
    left=max(0,(deadline_ns-(now_ns() if at_ns is None else at_ns))//1000)
    sec,us=divmod(left,1_000_000);m,s=divmod(sec,60);h,m=divmod(m,60)
    return f'T-{h:02d}:{m:02d}:{s:02d}.{us:06d}'

def wall_hms_us(ns:int)->str:
    sec=ns//1_000_000_000; us=(ns%1_000_000_000)//1000
    return time.strftime('%H:%M:%S',time.localtime(sec))+f'.{us:06d}'

def read_json(p:Path,default=None):
    try:return json.loads(p.read_text())
    except Exception:return default

def atomic_json(p:Path,obj):
    p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp-'+str(os.getpid()))
    t.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n');os.replace(t,p)

class TailReader:
    def __init__(self,p:Path):
        self.p=p;self.f=p.open('rb');self.offset=0
    def read(self,limit=4096):
        out=[]
        try:
            st=self.p.stat()
            if st.st_size<self.offset:raise RuntimeError('SOURCE_TRUNCATED')
        except FileNotFoundError:return out
        self.f.seek(self.offset)
        for _ in range(limit):
            pos=self.f.tell();b=self.f.readline(262145)
            if not b:break
            if len(b)>262144:raise RuntimeError('LINE_TOO_LONG')
            if not b.endswith(b'\n'):
                self.f.seek(pos);break
            self.offset=self.f.tell()
            if not b.strip():continue
            x=json.loads(b)
            if isinstance(x,dict):out.append(x)
        return out
    def close(self):self.f.close()

class Inotify:
    IN_MODIFY=0x00000002;IN_CLOSE_WRITE=0x00000008
    def __init__(self):
        self.lib=C.CDLL(None,use_errno=True)
        self.lib.inotify_init1.argtypes=[C.c_int];self.lib.inotify_init1.restype=C.c_int
        self.lib.inotify_add_watch.argtypes=[C.c_int,C.c_char_p,C.c_uint32];self.lib.inotify_add_watch.restype=C.c_int
        flags=os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0);self.fd=self.lib.inotify_init1(flags)
        if self.fd<0:raise OSError(C.get_errno(),'inotify_init1')
        self.watch={}
    def add(self,p:Path,label:str):
        wd=self.lib.inotify_add_watch(self.fd,os.fsencode(p),self.IN_MODIFY|self.IN_CLOSE_WRITE)
        if wd<0:raise OSError(C.get_errno(),'inotify_add_watch:'+str(p))
        self.watch[wd]=label
    def drain(self):
        labels=[]
        while True:
            try:b=os.read(self.fd,65536)
            except BlockingIOError:break
            if not b:break
            i=0
            while i+16<=len(b):
                wd,mask,cookie,nlen=struct.unpack_from('iIII',b,i);i+=16+nlen
                lab=self.watch.get(wd)
                if lab:labels.append(lab)
        return labels
    def close(self):
        try:os.close(self.fd)
        except OSError:pass

def cpu_sample(prev):
    vals=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]));tot=sum(vals);idle=vals[3]+(vals[4] if len(vals)>4 else 0);out=None
    if prev:
        dt=tot-prev[0];di=idle-prev[1]
        if dt>0:out=max(0.,min(100.,100.*(dt-di)/dt))
    return (tot,idle),out

def mem_available_gib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):return int(line.split()[1])/1024/1024
    return None

def gpu_state():
    try:
        q='utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw'
        cp=subprocess.run(['nvidia-smi','--query-gpu='+q,'--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=1.5,check=True)
        v=[x.strip() for x in cp.stdout.splitlines()[0].split(',')]
        return {'util':float(v[0]),'used':float(v[1]),'total':float(v[2]),'temp':float(v[3]),'power':float(v[4])}
    except Exception:return {}

def pnl_text(state:dict)->str:
    rows=[]
    for pid,short in [('TAIL_STOP50_TRAIL35','S50'),('TAIL_STOP75_TRAIL50','S75'),('TAIL_STOP100_TRAIL75','S100')]:
        p=(state.get('policies') or {}).get(pid) or {};sc=p.get('scenarios') or {}
        def v(k):
            x=(sc.get(k) or {}).get('normalized_pnl_per_100_per_entry');return '—' if x is None else f'{float(x):+.3f}'
        rows.append(f'{short} n={p.get("n",0)} o={p.get("open",0)} | 20:{v("20")} 50:{v("50")} 75:{v("75")}')
    return '\n'.join(rows)

class App:
    def __init__(self,evidence:Path,deadline_ns:int,runtime:Path):
        self.evidence=evidence;self.deadline_ns=deadline_ns;self.runtime=runtime;runtime.mkdir(parents=True,exist_ok=True)
        self.root=tk.Tk();self.root.title('BELIAL MP12 — ULTRA EVENT TAPE');self.root.geometry('1500x900');self.root.minsize(1150,720)
        self.root.configure(bg='#090d12');self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.font=('DejaVu Sans Mono',10);self.big=('DejaVu Sans Mono',28,'bold')
        self.pool={s:{'swaps':0,'buy':0,'sell':0,'times':collections.deque(),'last_ref':None,'prev_ref':None,'lat':None,'last_seen_ns':None} for s in POOLS}
        self.tape=collections.deque(maxlen=28);self.c2w=collections.deque(maxlen=4096);self.w2p=collections.deque(maxlen=4096);self.decode=collections.deque(maxlen=4096);self.wake_parse=collections.deque(maxlen=4096);self.wake_widget=collections.deque(maxlen=4096);self.render_dt=collections.deque(maxlen=4096)
        self.total=0;self.prev_cpu=None;self.cpu=None;self.gpu={};self.mem=None;self.last_hw=0.;self.last_state=0.;self.last_report=0.;self.last_render_perf=None;self.last_event_wall_ns=None
        self.reader=TailReader(evidence/'swaps.jsonl');self.ino=Inotify();self.ino.add(evidence/'swaps.jsonl','swaps')
        self._build();self.root.createfilehandler(self.ino.fd,tk.READABLE,self.on_inotify)
        # Drain rows that may have arrived between file creation and panel attachment.
        self.ingest_rows(self.reader.read())
        self.render()
    def _label(self,parent,text='',font=None,anchor='w'):
        return tk.Label(parent,text=text,bg='#090d12',fg='#d9e2ec',font=font or self.font,anchor=anchor,justify='left')
    def _build(self):
        top=tk.Frame(self.root,bg='#090d12');top.pack(fill='x',padx=12,pady=(10,5))
        left=tk.Frame(top,bg='#090d12');left.pack(side='left',fill='x',expand=True)
        self.title=self._label(left,'BELIAL MP12  •  EVENT TAPE  •  PAPER/SHADOW  •  LOCAL µs TIMING',('DejaVu Sans Mono',14,'bold'));self.title.pack(anchor='w')
        self.subtitle=self._label(left,'Visual refresh ≈ monitor/frame rate; µs values below measure the local ingest pipeline, not Solana block propagation.',('DejaVu Sans Mono',9));self.subtitle.pack(anchor='w')
        self.count=self._label(top,'',self.big,anchor='e');self.count.pack(side='right')
        meters=tk.Frame(self.root,bg='#0e141c',bd=1,relief='solid');meters.pack(fill='x',padx=12,pady=5)
        self.latlabel=tk.Label(meters,text='',bg='#0e141c',fg='#8be9fd',font=self.font,justify='left',anchor='w');self.latlabel.pack(side='left',fill='both',expand=True,padx=10,pady=8)
        self.hwlabel=tk.Label(meters,text='',bg='#0e141c',fg='#a6e3a1',font=self.font,justify='left',anchor='w');self.hwlabel.pack(side='left',fill='both',expand=True,padx=10,pady=8)
        self.n2label=tk.Label(meters,text='',bg='#0e141c',fg='#f9e2af',font=self.font,justify='left',anchor='w');self.n2label.pack(side='left',fill='both',expand=True,padx=10,pady=8)
        mid=tk.Frame(self.root,bg='#090d12');mid.pack(fill='both',expand=True,padx=12,pady=5)
        cols=('pool','swaps','rate','buy','sell','ref','dbps','last','lat')
        self.tree=ttk.Treeview(mid,columns=cols,show='headings',height=8)
        widths={'pool':100,'swaps':80,'rate':90,'buy':70,'sell':70,'ref':160,'dbps':100,'last':150,'lat':120}
        titles={'pool':'POOL','swaps':'SWAPS','rate':'EVT/s 60s','buy':'BUY','sell':'SELL','ref':'REF SOL/TOKEN','dbps':'Δ REF bp','last':'LAST LOCAL','lat':'writer→panel µs'}
        for c in cols:self.tree.heading(c,text=titles[c]);self.tree.column(c,width=widths[c],anchor='e' if c!='pool' else 'w')
        self.tree.pack(fill='x')
        lower=tk.Frame(mid,bg='#090d12');lower.pack(fill='both',expand=True,pady=(8,0))
        self.tape_widget=tk.Text(lower,bg='#05080c',fg='#d9e2ec',insertbackground='white',font=self.font,wrap='none',height=20,bd=1,relief='solid');self.tape_widget.pack(side='left',fill='both',expand=True)
        side=tk.Frame(lower,bg='#090d12',width=470);side.pack(side='right',fill='y',padx=(8,0))
        self.mainpnl=tk.Label(side,text='',bg='#101620',fg='#89b4fa',font=self.font,justify='left',anchor='nw',width=52);self.mainpnl.pack(fill='x',pady=(0,6))
        self.shadowpnl=tk.Label(side,text='',bg='#101620',fg='#cba6f7',font=self.font,justify='left',anchor='nw',width=52);self.shadowpnl.pack(fill='both',expand=True)
        self.status=self._label(self.root,'',('DejaVu Sans Mono',9));self.status.pack(fill='x',padx=12,pady=(0,8))
    def on_inotify(self,fd,mask):
        t0=time.perf_counter_ns();labs=self.ino.drain()
        changed=set()
        if 'swaps' in labs:
            changed=self.ingest_rows(self.reader.read())
        parsed=time.perf_counter_ns();self.wake_parse.append((parsed-t0)/1000.)
        # Update event surfaces immediately from the inotify callback. The compositor/monitor
        # still decides when pixels become visible; this metric is local callback→widget work.
        if changed:self.fast_event_render(changed)
        self.wake_widget.append((time.perf_counter_ns()-t0)/1000.)
    def ingest_rows(self,rows):
        seen=now_ns();changed=set()
        for r in rows:
            s=str(r.get('symbol') or '')
            if s not in self.pool:continue
            changed.add(s);self.total+=1;st=self.pool[s];st['swaps']+=1;side=str(r.get('side') or '?');st['buy']+=side=='BUY';st['sell']+=side=='SELL'
            recv=int(r.get('received_unix_ns') or int(r.get('received_unix_ms') or 0)*1_000_000);wr=int(r.get('writer_observed_unix_ns') or int(r.get('writer_observed_unix_ms') or 0)*1_000_000)
            if recv>0 and wr>=recv:self.c2w.append((wr-recv)/1000.)
            if wr>0 and seen>=wr:self.w2p.append((seen-wr)/1000.);st['lat']=(seen-wr)/1000.
            du=r.get('collector_decode_us');
            if isinstance(du,(int,float)) and math.isfinite(float(du)):self.decode.append(float(du))
            t=int(r.get('received_unix_ms') or seen//1_000_000);st['times'].append(t)
            cutoff=t-60_000
            while st['times'] and st['times'][0]<cutoff:st['times'].popleft()
            ref=r.get('reference_post_price');
            try:ref=float(ref)
            except Exception:ref=None
            if ref and math.isfinite(ref) and ref>0:st['prev_ref']=st['last_ref'];st['last_ref']=ref
            self.last_event_wall_ns=seen;st['last_seen_ns']=seen
            q=r.get('quote_sol');
            try:q=float(q)
            except Exception:q=0.0
            self.tape.append(f'{wall_hms_us(seen)}  {s:<8} {side:<4} q={q:9.4f} SOL  ref={ref if ref else 0:.10g}  c2w={r.get("collector_to_writer_us","—")}µs  w2p={"—" if not st["lat"] else f"{st["lat"]:.0f}"}µs')
        return changed
    def pool_values(self,s):
        st=self.pool[s];r=len(st['times'])/60.;ref=st['last_ref'];prev=st['prev_ref'];db=None
        if ref and prev and prev>0:db=10000*(ref-prev)/prev
        return (s,st['swaps'],f'{r:.3f}',st['buy'],st['sell'],'—' if ref is None else f'{ref:.10g}',
                '—' if db is None else f'{db:+.2f}',wall_hms_us(st['last_seen_ns']) if st['last_seen_ns'] else '—',
                '—' if st['lat'] is None else f'{st["lat"]:.0f}')
    def fast_event_render(self,changed):
        for s in changed:
            vals=self.pool_values(s)
            if self.tree.exists(s):self.tree.item(s,values=vals)
            else:self.tree.insert('', 'end', iid=s, values=vals)
        self.tape_widget.configure(state='normal');self.tape_widget.delete('1.0','end');self.tape_widget.insert('1.0','\n'.join(self.tape));self.tape_widget.configure(state='disabled')
    def states(self):
        main=read_json(self.evidence/'m2_tail/state.json',{}) or {};shadow=read_json(self.evidence/'shadow_compare/state.json',{}) or {}
        n2=read_json(BASE/'runtime/r5d-n2-gpu-mp12/status.json',{}) or {}
        return main,shadow,n2
    def shadow_text(self,st):
        by=st.get('by_pool') or {}
        out=['SHADOW COMPARE — CONTRAFACTUAL / NÃO É PNL DA ESTRATÉGIA']
        if not by:return '\n'.join(out+['aguardando episódios comparativos…'])
        for s in POOLS:
            x=by.get(s) or {};out.append(f'{s:<8} ep={x.get("episodes",0):3} closed={x.get("closed",0):3} 20bp={x.get("pnl20","—")} 50bp={x.get("pnl50","—")} 75bp={x.get("pnl75","—")}')
        return '\n'.join(out)
    def hardware(self):
        mono=time.monotonic()
        if mono-self.last_hw>=.5:
            self.prev_cpu,self.cpu=cpu_sample(self.prev_cpu);self.mem=mem_available_gib();self.gpu=gpu_state();self.last_hw=mono
    def report(self,main,shadow,n2):
        mono=time.monotonic()
        if mono-self.last_report<1:return
        snap={'schema':'belial_d2_ultra_snapshot_v1','at_unix_ns':now_ns(),'evidence':str(self.evidence),'total_swaps_seen':self.total,
              'latency_us':{'collector_writer_p50':pct(self.c2w,.5),'collector_writer_p95':pct(self.c2w,.95),'writer_panel_p50':pct(self.w2p,.5),'writer_panel_p95':pct(self.w2p,.95),'decode_p95':pct(self.decode,.95),'inotify_to_parse_p95':pct(self.wake_parse,.95),'inotify_to_widget_p95':pct(self.wake_widget,.95),'render_interval_p50':pct(self.render_dt,.5)},
              'hardware':{'cpu_pct':self.cpu,'mem_available_gib':self.mem,'gpu':self.gpu},'n2':{'state':n2.get('state'),'dimension':n2.get('dimension'),'active_heads':n2.get('active_heads'),'gpu_runtime':n2.get('gpu_runtime')},
              'visual_contract':{'target_frame_interval_ms':8,'microseconds_are_measurement_precision_not_screen_refresh_rate':True,'actual_order_book':False,'event_tape':True}}
        atomic_json(self.runtime/'live_snapshot.json',snap);self.last_report=mono
    def render(self):
        if STOP:return self.close()
        tperf=time.perf_counter_ns()
        if self.last_render_perf:self.render_dt.append((tperf-self.last_render_perf)/1e6)
        self.last_render_perf=tperf
        self.hardware();main,shadow,n2=self.states();now=now_ns();self.count.config(text=fmt_countdown(self.deadline_ns,now))
        def f(x):return '—' if x is None else f'{x:.1f}'
        self.latlabel.config(text=('LOCAL PIPELINE µs\n'
          f'collector→writer p50/p95  {f(pct(self.c2w,.5))} / {f(pct(self.c2w,.95))}\n'
          f'writer→panel    p50/p95  {f(pct(self.w2p,.5))} / {f(pct(self.w2p,.95))}\n'
          f'decode p95                {f(pct(self.decode,.95))}\n'
          f'inotify→parse p95         {f(pct(self.wake_parse,.95))}\n'
          f'inotify→widget p95        {f(pct(self.wake_widget,.95))}\n'
          f'visual interval p50       {f(pct(self.render_dt,.5))} ms'))
        g=self.gpu;self.hwlabel.config(text=(f'HARDWARE\nCPU {"—" if self.cpu is None else f"{self.cpu:.0f}%"}   RAM avail {"—" if self.mem is None else f"{self.mem:.2f} GiB"}\n'
          f'GPU {g.get("util","—")}%  VRAM {g.get("used","—")}/{g.get("total","—")} MiB\nTemp {g.get("temp","—")}°C  Power {g.get("power","—")} W\nEvents seen {self.total}'))
        gr=n2.get('gpu_runtime') or {};self.n2label.config(text=(f'N2 GPU MP12\nstate {n2.get("state","—")}\n{n2.get("dimension","—")} features × {n2.get("active_heads","—")} heads\n'
          f'predict p95 {gr.get("predict_p95_ms","—")} ms\npending {n2.get("pending","—")}'))
        for s in POOLS:
            vals=self.pool_values(s)
            if self.tree.exists(s):self.tree.item(s,values=vals)
            else:self.tree.insert('', 'end', iid=s, values=vals)
        # The 8 ms cadence refreshes metrics/state; trade tape itself is also updated
        # immediately by the inotify callback above.
        self.mainpnl.config(text='PNL PRINCIPAL — FARTCOIN / POLÍTICAS ALTERNATIVAS\n'+pnl_text(main))
        self.shadowpnl.config(text=self.shadow_text(shadow))
        self.status.config(text='µs = relógio/local pipeline. O painel não afirma latência blockchain→tela em microssegundos; render visual alvo ≈125 Hz e é limitado pelo monitor/Tk.')
        self.report(main,shadow,n2)
        if now>=self.deadline_ns+30_000_000_000:return self.close()
        self.root.after(8,self.render)
    def close(self):
        global STOP;STOP=True
        try:self.root.deletefilehandler(self.ino.fd)
        except Exception:pass
        self.reader.close();self.ino.close()
        try:self.root.destroy()
        except Exception:pass
    def run(self):self.root.mainloop()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--deadline-ns',type=int,required=True);ap.add_argument('--runtime',type=Path,required=True);a=ap.parse_args()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop);os.nice(5)
    App(a.evidence.resolve(),a.deadline_ns,a.runtime.resolve()).run();return 0
if __name__=='__main__':raise SystemExit(main())
