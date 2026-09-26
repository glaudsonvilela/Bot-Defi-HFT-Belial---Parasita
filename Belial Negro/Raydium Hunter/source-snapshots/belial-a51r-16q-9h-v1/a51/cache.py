"""mtime/size keyed JSON cache for hot-path status files."""
from __future__ import annotations
from pathlib import Path
from a51.common import read_json

class JsonCache:
    def __init__(self):
        self._items={};self.hits=0;self.misses=0;self.missing=0
    def get(self,path,default=None):
        path=Path(path)
        try:stat=path.stat()
        except OSError:self.missing+=1;return default
        sig=(stat.st_mtime_ns,stat.st_size)
        cached=self._items.get(path)
        if cached is not None and cached[0]==sig:
            self.hits+=1;return cached[1]
        value=read_json(path,default);self._items[path]=(sig,value);self.misses+=1;return value
    def view(self):
        total=self.hits+self.misses
        return {'hits':self.hits,'misses':self.misses,'missing':self.missing,'entries':len(self._items),'hit_ratio':self.hits/total if total else None}
