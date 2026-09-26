"""A2 dependency gate with independent cohorts and explicit cost/funding model.
The gate can cancel ENTRY intents, never delete an existing position or manufacture an EXIT.
"""
from __future__ import annotations
from a2.common import add_legacy,SYMBOLS,DURATION_MS,CORE12,EXPANSION12,cohort_of
add_legacy('perp')
from books import Books,Book
from a2.economics import Engine
from a2.catalog import source_ready
from a2.journal import OrderedDedup

class SafeBook(Book):
    def update_li(self,data,received_ms,generation):
        payload=data['order_book'];nonce=int(payload['nonce'])
        # Repeated/replayed deltas and identical snapshots cannot become new quotes.
        if self.valid and generation==self.generation and self.nonce is not None and nonce==self.nonce:return False
        return super().update_li(data,received_ms,generation)
    def reset(self,reason):
        super().reset(reason);self.context={};self.context_ms=0

class SafeBooks(Books):
    def ingest(self,row):
        venue=row.get('venue');gen=row.get('generation')
        if type(gen) is not int:raise ValueError('GENERATION_REQUIRED')
        if venue in self.generations and gen<self.generations[venue]:return []
        # The inherited Books handles update_li's return as unconditional. Ignore
        # a duplicate at the envelope layer so no pending fill sees a new quote.
        data=row.get('data') or {};channel=str(data.get('channel',''))
        if venue=='lighter' and channel.startswith('order_book:'):
            key=self.market_index.get((venue,channel.split(':',1)[1]));b=self.books.get(key)
            payload=data.get('order_book') or {}
            try:
                if b and b.valid and b.generation==gen and b.nonce is not None and int(payload['nonce'])==b.nonce:return []
            except (KeyError,TypeError,ValueError):pass
        return super().ingest(row)

class Controller:
    def __init__(self,config,emit=None):
        self.cfg=config;self.books=SafeBooks({'markets':[]})
        self.engines={g:Engine(self.books,config,lambda k,r,g=g:self._emit(k,dict(r,cohort=g))) for g in ('CORE12','EXPANSION12')}
        self.engine=self.engines['CORE12']
        self.emit=emit or (lambda *_:None);self.dedup=OrderedDedup();self.cohort=None;self.statuses={};self.start=None;self.deadline=None
        self.audit_ok=True;self.error=None;self.last_at=0;self.blocked_signals=0;self.markets={};self.finalized=False
    def _emit(self,kind,row):
        try:self.emit(kind,row)
        except Exception as e:self.audit_ok=False;self.error='OUTPUT_AUDIT_FAILED:'+str(e)
    def eligible(self,s,at):
        if self.start is None or at<self.start or at>=self.deadline:return False,'OUTSIDE_CAPTURE_WINDOW'
        return source_ready(s,self.cohort,self.statuses.get(s),at,self.audit_ok)
    def cancel_blocked_entries(self,at):
        for cohort,engine in self.engines.items():
            for order in list(engine.orders):
                if order['kind']!='ENTRY':continue
                ok,reason=self.eligible(order['symbol'],at)
                if not ok:
                    engine.orders.remove(order);engine.counts['dependency_cancelled_entries']+=1
                    self._emit('orders',{'cohort':cohort,'at_ms':at,'id':order['id'],'kind':'ENTRY','status':'DEPENDENCY_CANCELLED','reason':reason,'paper_only':True})
    def add_markets(self,manifest):
        venue=manifest['venue']
        if venue not in ('hyperliquid','lighter'):raise ValueError('UNKNOWN_VENUE')
        if self.start is not None:raise ValueError('MARKET_MANIFEST_FROZEN')
        rows=manifest['markets']
        if venue in self.markets:
            if self.markets[venue]!=rows:raise ValueError('MARKET_MANIFEST_CHANGED')
            return
        for m in rows:
            if m['venue']!=venue or m['symbol'] not in SYMBOLS:raise ValueError('MARKET_UNIVERSE_MISMATCH')
            key=(venue,m['symbol']);idx=(venue,str(m['market']))
            if key in self.books.books or idx in self.books.market_index:raise ValueError('AMBIGUOUS_MARKET')
            self.books.books[key]=SafeBook(m);self.books.market_index[idx]=key
        self.markets[venue]=rows
    def apply(self,event):
        if not self.dedup.accept(event['source'],event['epoch'],event['sequence']):return False
        at=event['at_ms']
        if type(at) is not int or at<self.last_at:raise ValueError('PROCESSING_CLOCK_REGRESSION')
        self.last_at=at
        kind=event['kind'];row=event['body']
        if kind=='markets':self.add_markets(row);return True
        if kind=='cohort':
            if self.cohort is not None:raise ValueError('COHORT_ALREADY_FROZEN')
            if row['duration_ms']!=DURATION_MS:raise ValueError('CAPTURE_MUST_BE_90_MINUTES')
            self.cohort=row['cohort'];self.start=row['started_ms'];self.deadline=self.start+DURATION_MS
            return True
        if kind=='source_status':self.statuses[row['symbol']]=row['status'];self.cancel_blocked_entries(at);return True
        if kind=='integrity':
            if row.get('ok') is not True:self.audit_ok=False;self.error=row.get('reason','INTEGRITY_FAILED')
            self.cancel_blocked_entries(at);return True
        if self.start is not None and at>=self.deadline and not self.finalized:self.finish(self.deadline)
        if kind=='book':
            if self.finalized:return True
            self.cancel_blocked_entries(at)
            try:
                if not isinstance(row.get('data',{}),dict):raise ValueError('BOOK_DATA_NOT_OBJECT')
                if row.get('venue')=='hyperliquid' and not isinstance((row.get('data') or {}).get('data',{}),dict):raise ValueError('BOOK_PAYLOAD_NOT_OBJECT')
                keys=self.books.ingest(row)
            except (ValueError,KeyError,TypeError,AttributeError,IndexError) as exc:
                venue=row.get('venue');keys=[]
                for (v,_),book in self.books.books.items():
                    if v==venue:book.reset('INVALID_ENVELOPE')
                self.books.errors.append(dict(at_ms=at,venue=venue,error=str(exc)));self.books.errors=self.books.errors[-100:]
                self._emit('book_errors',dict(at_ms=at,venue=venue,error=str(exc),orders=False))
                self.books.request_reset(venue,at)
            if self.start is not None and at>=self.start:
                for key,b in self.books.books.items():self.engines[cohort_of(key[1])].record_context(key,b,at)
                for key in keys:self.engines[cohort_of(key[1])].on_book(key,at)
        elif kind=='signal':
            good,reason=self.eligible(row.get('symbol'),at)
            # No raw log, shadow price-only trigger or unqualified source is fabricated here.
            if good and not self.finalized:self.engines[cohort_of(row['symbol'])].signal(row,at)
            else:
                self.blocked_signals+=1;self._emit('signals',{'symbol':row.get('symbol'),'at_ms':at,'status':'DEPENDENCY_BLOCKED','reason':reason,'paper_only':True})
        elif kind=='funding':
            if self.start is not None:
                self.engines[cohort_of(row['key'][1])].funding_rate(tuple(row['key']),row['at'],row['rate'],row['known_ms'],row['origin'])
        elif kind=='tick':
            self.cancel_blocked_entries(at)
            if self.start is not None and not self.finalized:
                for engine in self.engines.values():engine.tick(at)
        elif kind=='finalize':self.finish(min(at,self.deadline) if self.deadline else at)
        elif kind not in ('venue_status','funding_error'):raise ValueError('UNKNOWN_INPUT_KIND:'+str(kind))
        return True
    def finish(self,at):
        if self.finalized:return
        self.cancel_blocked_entries(at)
        for engine in self.engines.values():engine.finalize(at)
        self.finalized=True
    def snapshot(self,at):
        if self.finalized:at=min(at,self.deadline) if self.deadline else at
        groups={g:dict(engine.snapshot(at),cohort=g) for g,engine in self.engines.items()}
        return dict(groups['CORE12'],groups=groups,selected_cohort='CORE12',cohorts_are_independent=True,a1_integrity=self.audit_ok,a1_error=self.error,
                    a1_blocked_signals=self.blocked_signals,a1_duplicate_inputs=self.dedup.duplicates,
                    duration_ms=DURATION_MS,started_ms=self.start,deadline_ms=self.deadline,
                    a1_market_discovery_frozen=self.start is not None,a1_cohort=self.cohort)
