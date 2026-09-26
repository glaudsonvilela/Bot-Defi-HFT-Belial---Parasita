"""Streaming hash-chain input ledger for deterministic offline PAPER replay."""
from __future__ import annotations
import hashlib,os,time
from a1.common import canonical,strict_loads,safe
ZERO='0'*64

class Journal:
    def __init__(self,path):
        self.path=safe(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.f=self.path.open('x',encoding='utf-8');self.seq=0;self.previous=ZERO;self.last_sync=time.monotonic()
    def append(self,event):
        self.seq+=1
        row={'schema':'belial_a1_journal_v1','sequence':self.seq,'previous_sha256':self.previous,'event':event}
        row['sha256']=hashlib.sha256(canonical(row).encode()).hexdigest()
        self.f.write(canonical(row)+'\n');self.f.flush();self.previous=row['sha256']
        if time.monotonic()-self.last_sync>=1:self.sync()
        return row['sha256']
    def sync(self):self.f.flush();os.fsync(self.f.fileno());self.last_sync=time.monotonic()
    def close(self):
        if not self.f.closed:self.sync();self.f.close()

def read_verified(path):
    previous=ZERO;sequence=0
    with safe(path).open('rb') as f:
        for raw in f:
            if not raw.endswith(b'\n'):raise ValueError('JOURNAL_INCOMPLETE_LAST_LINE')
            row=strict_loads(raw);received=row.pop('sha256',None);sequence+=1
            if row.get('schema')!='belial_a1_journal_v1' or row.get('sequence')!=sequence or row.get('previous_sha256')!=previous:raise ValueError('JOURNAL_SEQUENCE_OR_CHAIN')
            actual=hashlib.sha256(canonical(row).encode()).hexdigest()
            if received!=actual:raise ValueError('JOURNAL_HASH_MISMATCH')
            previous=actual;yield row['event']

class OrderedDedup:
    """O(sources) memory; duplicate/reordered IDs never refresh market freshness."""
    def __init__(self):self.last={};self.duplicates=0
    def accept(self,source,epoch,sequence):
        if not isinstance(source,str) or type(epoch) is not int or epoch<0 or type(sequence) is not int or sequence<1:raise ValueError('INPUT_ID_INVALID')
        key=(epoch,sequence);last=self.last.get(source)
        if last is not None and key<=last:self.duplicates+=1;return False
        self.last[source]=key;return True
