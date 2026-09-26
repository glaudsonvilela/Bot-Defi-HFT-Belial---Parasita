"""Deferred, bounded append-only readers. Missing producers are not corrupt data."""
from __future__ import annotations
from a51.common import Path, safe, strict_loads

class DeferredTail:
    def __init__(self, path, max_line=131072):
        self.path=Path(path);self.file=None;self.identity=None;self.max_line=max_line;self.rows=0
    def available(self):
        if self.file is not None:return True
        if not self.path.exists():return False
        safe(self.path)
        if not self.path.is_file():raise RuntimeError('INPUT_NOT_REGULAR:'+self.path.name)
        self.file=self.path.open('rb');st=self.file.fileno()
        import os
        s=os.fstat(st);self.identity=(s.st_dev,s.st_ino)
        return True
    def check(self):
        if not self.available():return False
        try:s=self.path.stat()
        except FileNotFoundError:raise RuntimeError('SOURCE_REMOVED_AFTER_OPEN:'+self.path.name) from None
        if (s.st_dev,s.st_ino)!=self.identity or s.st_size<self.file.tell():
            raise RuntimeError('SOURCE_ROTATED_OR_TRUNCATED:'+self.path.name)
        return True
    def read(self, limit=2048):
        if not self.check():return []
        rows=[]
        while len(rows)<limit:
            pos=self.file.tell();line=self.file.readline(self.max_line+1)
            if not line:break
            if len(line)>self.max_line:raise RuntimeError('SOURCE_LINE_TOO_LONG:'+self.path.name)
            if not line.endswith(b'\n'):self.file.seek(pos);break
            if not line.strip():continue
            row=strict_loads(line)
            if not isinstance(row,dict):raise RuntimeError('SOURCE_ROW_NOT_OBJECT')
            rows.append((pos,row));self.rows+=1
        return rows
    def caught_up(self):return bool(self.check() and self.file.tell()>=self.path.stat().st_size)
    def view(self):
        exists=self.path.is_file()
        return {'exists':exists,'opened':self.file is not None,'rows_read':self.rows,
                'offset':self.file.tell() if self.file else 0,
                'bytes':self.path.stat().st_size if exists else None}
    def close(self):
        if self.file:self.file.close();self.file=None
