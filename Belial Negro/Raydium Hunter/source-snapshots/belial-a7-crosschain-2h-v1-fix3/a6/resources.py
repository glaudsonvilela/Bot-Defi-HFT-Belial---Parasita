"""Five-second passive resource samples; never exports argv/environment."""
import os,time
from pathlib import Path
from a51.common import now_ms,canonical,read_json

def sample(session):
    nodes={};hz=os.sysconf('SC_CLK_TCK');page=os.sysconf('SC_PAGE_SIZE')
    for p in Path('/proc').glob('[0-9]*'):
        try:
            text=(p/'stat').read_text();fields=text[text.rfind(')')+2:].split();nodes[int(p.name)]=dict(ppid=int(fields[1]),cpu_seconds=(int(fields[11])+int(fields[12]))/hz,rss_bytes=int(fields[21])*page,start_ticks=int(fields[19]))
        except (OSError,ValueError,IndexError):pass
    descendants={os.getpid()}
    for _ in range(10):
        new={pid for pid,n in nodes.items() if n['ppid'] in descendants};old=len(descendants);descendants|=new
        if len(descendants)==old:break
    processes={str(pid):nodes[pid] for pid in descendants if pid in nodes}
    def raw(name):
        try:return Path(name).read_text()
        except OSError:return None
    row=dict(at_ms=now_ms(),monotonic=time.monotonic(),processes=processes,rss_sum_includes_shared_pages=True,meminfo=raw('/proc/meminfo'),cpu_stat=raw('/proc/stat'),pressure={k:raw('/proc/pressure/'+k) for k in ('cpu','memory','io')},gpu=(read_json(session/'n2/status.json',{}) or {}).get('gpu'))
    with (session/'RESOURCE_SAMPLES.jsonl').open('a') as f:f.write(canonical(row)+'\n')
