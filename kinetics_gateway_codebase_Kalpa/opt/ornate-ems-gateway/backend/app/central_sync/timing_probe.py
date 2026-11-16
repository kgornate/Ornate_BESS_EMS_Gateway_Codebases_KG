"""Opt-in-duration request timing; no headers, tokens or telemetry values retained."""
import asyncio
from collections import Counter
import contextvars
from datetime import datetime,timezone
import functools
import json
import os
from pathlib import Path
import sqlite3
import time

PATH='/var/lib/ornate-central-sync/request_timing.db'
DEADLINE=datetime(2026,11,3,5,30,tzinfo=timezone.utc).timestamp()
CURRENT=contextvars.ContextVar('kalpa_timing',default=None)
NETWORK=contextvars.ContextVar('kalpa_network_timing',default=None)

class NetworkSpan:
    def __init__(self):self.started=time.perf_counter();self.events=[]
    async def trace(self,event,info):
        # Never inspect info: it can contain request headers and response bodies.
        try:
            if len(self.events)<120:self.events.append((str(event),(time.perf_counter()-self.started)*1000))
        except Exception:pass
    def result(self,response):
        durations={};starts={};failed=[]
        for name,stamp in self.events:
            base,_,suffix=name.rpartition('.')
            if suffix=='started':starts[base]=stamp
            elif suffix in ('complete','failed') and base in starts:
                durations[base]=durations.get(base,0)+stamp-starts.pop(base)
                if suffix=='failed':failed.append(base)
        def phase(fragment):
            values=[v for k,v in durations.items() if fragment in k]
            return sum(values) if values else None
        return {'http_status':getattr(response,'status_code',None),
          'error_type':str(getattr(response,'exception','') or '').split(':',1)[0] or None,
          'network_total_ms':(time.perf_counter()-self.started)*1000,
          'connect_tcp_ms':phase('connect_tcp'),'tls_ms':phase('start_tls'),
          'send_headers_ms':phase('send_request_headers'),'send_body_ms':phase('send_request_body'),
          'wait_headers_ms':phase('receive_response_headers'),'read_body_ms':phase('receive_response_body'),
          'event_durations_ms':durations,'failed_phases':failed,'event_count':len(self.events)}

async def trace_callback(event,info):
    span=NETWORK.get()
    if span is not None:await span.trace(event,info)

def timed_network(func):
    @functools.wraps(func)
    async def wrapped(*args,**kwargs):
        probe=CURRENT.get()
        if probe is None:return await func(*args,**kwargs)
        span=NetworkSpan();token=NETWORK.set(span);response=None
        try:
            response=await func(*args,**kwargs);return response
        finally:
            try:probe['network_attempts'].append(span.result(response))
            finally:NETWORK.reset(token)
    return wrapped

def measured_phase(name):
    def decorate(func):
        @functools.wraps(func)
        async def wrapped(*args,**kwargs):
            start=time.perf_counter()
            try:return await func(*args,**kwargs)
            finally:
                probe=CURRENT.get()
                if probe is not None:probe[name]=probe.get(name,0)+(time.perf_counter()-start)*1000
        return wrapped
    return decorate

def timed_prepare(func):
    @functools.wraps(func)
    def wrapped(*args,**kwargs):
        start=time.perf_counter();result=func(*args,**kwargs)
        if result is not None and time.time()<DEADLINE:
            # Observer metadata only: not a dataclass field, not serialized.
            try:object.__setattr__(result,'_kalpa_prepare_ms',(time.perf_counter()-start)*1000)
            except Exception:pass
        return result
    return wrapped

def lock_started():
    probe=CURRENT.get()
    if probe is not None:probe['_lock_start']=time.perf_counter()
def lock_acquired():
    probe=CURRENT.get()
    if probe is not None and '_lock_start' in probe:probe['lock_wait_ms']=(time.perf_counter()-probe.pop('_lock_start'))*1000

def store(row,path=PATH):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=.05)
    try:
        db.execute('PRAGMA busy_timeout=50')
        db.execute('CREATE TABLE IF NOT EXISTS request_timing(id INTEGER PRIMARY KEY,request_id TEXT,completed_utc TEXT,completed_epoch REAL,data TEXT)')
        with db:
            now=time.time()
            db.execute('INSERT INTO request_timing(request_id,completed_utc,completed_epoch,data) VALUES(?,?,?,?)',
              (row['request_id'],datetime.now(timezone.utc).isoformat(),now,json.dumps(row,separators=(',',':'))))
            # At most 100k completed batches; cleanup is outside transport/ACK policy.
            last=db.execute('SELECT max(id) FROM request_timing').fetchone()[0]
            if last%100==0:
                db.execute('DELETE FROM request_timing WHERE id<=?',(last-100000,))
        os.chmod(path,0o600)
    finally:db.close()

def timed_batch(func):
    @functools.wraps(func)
    async def wrapped(self,prepared,*args,**kwargs):
        if time.time()>=DEADLINE:return await func(self,prepared,*args,**kwargs)
        start=time.perf_counter();groups=Counter((i.stream,i.substream or '') for i in prepared.items)
        probe={'request_id':str(prepared.request_id),'started_utc':datetime.now(timezone.utc).isoformat(),
          'source_db':str(self.outbox.path),'logical_bytes':len(prepared.json_bytes),'message_count':len(prepared.items),
          'prepare_ms':getattr(prepared,'_kalpa_prepare_ms',None),'network_attempts':[],
          'groups':[{'stream':k[0],'substream':k[1],'messages':v} for k,v in sorted(groups.items())]}
        token=CURRENT.set(probe)
        try:return await func(self,prepared,*args,**kwargs)
        finally:
            probe['send_and_ack_total_ms']=(time.perf_counter()-start)*1000
            probe.pop('_lock_start',None);CURRENT.reset(token)
            try:await asyncio.to_thread(store,probe)
            except Exception as error:
                # Measurement failure never changes upload or ACK decisions.
                try:
                    self._kalpa_timing_write_failures=getattr(self,'_kalpa_timing_write_failures',0)+1
                    if self._kalpa_timing_write_failures==1 or self._kalpa_timing_write_failures%100==0:
                        import logging
                        logging.getLogger(__name__).warning('Request timing recording unavailable: %s; observer failures=%d',type(error).__name__,self._kalpa_timing_write_failures)
                except Exception:pass
    return wrapped
