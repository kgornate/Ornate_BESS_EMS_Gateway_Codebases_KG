"""One dedicated archive data connection; shared token broker, bounded load."""
from __future__ import annotations
import asyncio
from collections import deque
import json
import math
from pathlib import Path
import time
from .client import CentralBackendClient
from .uploader import CentralSyncUploader

SETTINGS_PATH='/etc/kinetics-gateway/archive_transport.json'
DEFAULTS={'fast_interval_sec':10.0,'limited_interval_sec':30.0,
          'fast_live_count':10,'fast_live_age_sec':15.0,
          'max_live_count':25,'max_live_age_sec':30.0,
          'fast_load_per_cpu':1.5,'max_load_per_cpu':2.0,
          'healthy_samples':2,'failure_cooldown_sec':30.0}

def load_settings(path=SETTINGS_PATH):
    result=dict(DEFAULTS)
    if Path(path).exists():
        extra=json.loads(Path(path).read_text())
        if not isinstance(extra,dict) or set(extra)-set(result):raise ValueError('Unknown archive transport settings')
        result.update(extra)
    for k,v in result.items():
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError('Invalid archive transport setting: '+k)
    for k in ('healthy_samples','fast_live_count','max_live_count'):
        if int(result[k])!=result[k]:raise ValueError(k+' must be an integer')
    if result['healthy_samples']<2 or result['fast_interval_sec']<5 or result['limited_interval_sec']<result['fast_interval_sec']:
        raise ValueError('Invalid observation/pacing intervals')
    for a,b in [('fast_live_count','max_live_count'),('fast_live_age_sec','max_live_age_sec'),('fast_load_per_cpu','max_load_per_cpu')]:
        if result[a]>result[b]:raise ValueError('Fast threshold exceeds hard threshold')
    return result

class DeferredBeforeHTTP(Exception):pass

class AdaptivePolicy:
    def __init__(self,settings,clock=time.monotonic):
        self.settings=settings;self.clock=clock;self.cooldown_until=0.;self.streak=0;self.counts=deque(maxlen=3)
        self.mode='settling';self.delay=settings['limited_interval_sec'];self.pressure=False
    def hard_reason(self,health,config):
        s=self.settings;live=health['live'];count=int(live.get('unacked_count') or 0);age=live.get('oldest_age_sec')
        if health.get('transport_paused'):return 'transport_paused'
        if not live.get('available'):return 'live_probe_unavailable'
        if health['mem_available_mb']<config.min_available_memory_mb:return 'memory_pressure'
        load=health['load1'];limit=max(1,health['cpu_count'])*s['max_load_per_cpu']
        if not math.isfinite(load) or load>limit:return 'load_pressure'
        if count<0 or count>s['max_live_count']:return 'live_queue_pressure'
        if count and (age is None or not math.isfinite(float(age))):return 'live_age_unavailable'
        if age is not None and age>s['max_live_age_sec']:return 'live_age_pressure'
        if self.clock()<self.cooldown_until:return 'backend_cooldown'
        return None
    def decide(self,health,config):
        reason=self.hard_reason(health,config);count=int(health['live'].get('unacked_count') or 0)
        self.counts.append(count)
        c=list(self.counts)
        if reason is None and len(c)==3 and count>=20 and c[0]<c[1]<c[2] and count-c[0]>=10:reason='live_queue_growing'
        if reason:
            self.streak=0;self.mode='yield';self.pressure=True;self.delay=self.settings['limited_interval_sec']
            return False,reason
        age=health['live'].get('oldest_age_sec') or 0;s=self.settings
        fast=(count<=s['fast_live_count'] and age<=s['fast_live_age_sec'] and
              health['load1']<=max(1,health['cpu_count'])*s['fast_load_per_cpu'])
        if self.pressure and (count>15 or age>20):
            self.streak=0;self.mode='yield';return False,'waiting_for_live_recovery'
        self.streak+=1
        if self.streak<s['healthy_samples']:
            self.mode='settling';return False,'healthy_observation_wait'
        self.pressure=False;self.mode='catch_up' if fast else 'limited'
        self.delay=s['fast_interval_sec'] if fast else s['limited_interval_sec']
        return True,None
    def cooldown(self,seconds):self.cooldown_until=max(self.cooldown_until,self.clock()+seconds)

class DedicatedArchiveClient(CentralBackendClient):
    def __init__(self,config,gateway_id,token_broker,owner):
        super().__init__(config,gateway_id)
        self.token_broker=token_broker;self.owner=owner
    async def _obtain_token(self,*,force=False):
        # Share only token issuance/cache/refresh lock, not ingest connections.
        return await self.token_broker._obtain_token(force=force)
    async def _post_once(self,body,token):
        # Runs AFTER authentication, immediately before each ingest attempt,
        # including the existing bounded 401 retry / 413 split requests.
        reason=await asyncio.to_thread(self.owner._send_guard)
        if reason:
            self.owner.counters.deferred_before_http_count+=1
            raise DeferredBeforeHTTP(reason)
        if not self.owner.byte_budget.consume(len(body)):
            self.owner.counters.deferred_before_http_count+=1
            raise DeferredBeforeHTTP('archive_budget_wait')
        self.owner.counters.archive_http_attempt_count+=1
        self.owner.counters.archive_active_requests+=1
        self.owner.counters.archive_peak_active_requests=max(self.owner.counters.archive_peak_active_requests,self.owner.counters.archive_active_requests)
        getattr(self.owner,"_write_status",lambda:None)()
        http_started=time.perf_counter()
        self.owner.counters.archive_last_request_bytes=len(body)
        try:
            response=await super()._post_once(body,token)
            self.owner._http_outcome(response)
            return response
        finally:
            elapsed=(time.perf_counter()-http_started)*1000
            self.owner.counters.archive_last_http_duration_ms=round(elapsed,3)
            self.owner.counters.archive_http_duration_total_ms+=elapsed
            self.owner.counters.archive_http_duration_max_ms=max(self.owner.counters.archive_http_duration_max_ms,elapsed)
            self.owner.counters.archive_active_requests-=1
            getattr(self.owner,"_write_status",lambda:None)()

def release_unsent_claims(store,items):
    """Release OUR unsent leases only; never ACK/delete/increment attempts."""
    with store._lock:
        try:
            store.conn.execute('BEGIN IMMEDIATE')
            for item in items:
                store.conn.execute("""UPDATE outbox_messages SET state='retry',next_attempt_epoch=?
                    WHERE message_id=? AND state='inflight' AND next_attempt_epoch=? AND last_attempt_utc=?""",
                    (time.time()+2,item.message_id,item.next_attempt_epoch,item.last_attempt_utc))
            store.conn.commit();store._invalidate_stats_cache()
        except Exception:store.conn.rollback();raise

class ArchiveUploader(CentralSyncUploader):
    def __init__(self,*,owner,**kw):
        super().__init__(**kw);self.owner=owner
    async def _run_transport_once(self):
        if self.pause_control.is_paused():return False
        if await asyncio.to_thread(self.owner._send_guard):return False
        prepared=await asyncio.to_thread(self.batcher.prepare)
        if not prepared:return False
        try:
            await self._send_prepared(prepared)
            return True
        except DeferredBeforeHTTP as exc:
            self.owner.counters.last_defer_reason=str(exc)
            await asyncio.to_thread(release_unsent_claims,self.outbox,prepared.items)
            return False
    async def _apply_partial_ack(self,items,response):
        before={k:getattr(self.counters,k) for k in ('accepted_count','already_processed_count','retryable_rejected_count','permanent_rejected_count','missing_ack_count')}
        await super()._apply_partial_ack(items,response)
        for k,old in before.items():
            setattr(self.owner.counters,'archive_'+k,getattr(self.owner.counters,'archive_'+k)+getattr(self.counters,k)-old)
        if self.counters.retryable_rejected_count>before['retryable_rejected_count']:
            # Keep existing per-message backoff. A partial 200 response does
            # not put unrelated archives into a global transport cooldown.
            body=response.body or {}
            entries=body.get('rejected',body.get('messages',[]))
            reasons=[]
            for entry in entries if isinstance(entries,list) else []:
                if not isinstance(entry,dict) or entry.get('status') in ('accepted','already_processed'): continue
                reasons.append({'message_id':str(entry.get('message_id') or ''),
                                'code':str(entry.get('error_code') or entry.get('code') or entry.get('status') or 'rejected')[:120],
                                'detail':str(entry.get('detail') or entry.get('reason') or '')[:300]})
            self.owner._recent_archive_rejections=reasons[-10:]
        try:
            values=await asyncio.to_thread(self._receipt_bytes,items)
            for key,value in values.items():setattr(self.owner.counters,key,getattr(self.owner.counters,key)+value)
        except Exception:
            self.owner.counters.archive_ack_metric_error_count+=1
    def _receipt_bytes(self,items):
        ids=[x.message_id for x in items]
        result={'archive_accepted_bytes':0,'archive_accepted_records':0,'archive_duplicate_bytes':0}
        if not ids:return result
        with self.outbox._lock:
            rows=self.outbox.conn.execute('SELECT ack_status,payload_bytes,record_count FROM delivery_receipts_v1 WHERE message_id IN ('+','.join('?'for _ in ids)+')',ids).fetchall()
        for status,size,records in rows:
            if status=='accepted':result['archive_accepted_bytes']+=int(size);result['archive_accepted_records']+=int(records)
            elif status=='already_processed':result['archive_duplicate_bytes']+=int(size)
        return result

