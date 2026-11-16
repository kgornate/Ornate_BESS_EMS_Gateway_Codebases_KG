"""Uses actual supplied client/uploader method bodies with mocked network I/O."""
import ast
import asyncio
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).parent
PKG='_kalpa_transport_test'
package=types.ModuleType(PKG);package.__path__=[str(ROOT)];sys.modules[PKG]=package

def module_from_reference(name,filename,extras):
    tree=ast.parse((ROOT/filename).read_text())
    tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom)) or (isinstance(n,ast.ImportFrom) and n.module=='__future__')]
    mod=types.ModuleType(PKG+'.'+name);sys.modules[mod.__name__]=mod
    mod.__dict__.update({'asyncio':asyncio,'time':time,'logging':logging,'math':math,'hashlib':hashlib,'json':json,'os':os,
        'dataclass':dataclasses.dataclass,'field':dataclasses.field,'Any':object,'datetime':datetime,'timezone':timezone,
        'timed_network':lambda f:f,'timed_batch':lambda f:f,'measured_phase':lambda n:(lambda f:f),
        'lock_started':lambda:None,'lock_acquired':lambda:None,'trace_callback':None,'utc_now_iso':lambda:datetime.now(timezone.utc).isoformat(),
        'record_batch_trace':lambda *args:None,'CentralSyncConfig':object,'OutboxItem':object,'OutboxStore':object,
        'TransportPauseController':lambda p:NS(is_paused=lambda:False),**extras})
    exec(compile(tree,str(ROOT/filename),'exec'),mod.__dict__);return mod

client=module_from_reference('client','reference_client.py',{'gzip':__import__('gzip'),'httpx':NS()})
uploader=module_from_reference('uploader','reference_uploader.py',{'CentralBackendClient':client.CentralBackendClient,
    'TransportResponse':client.TransportResponse,'OutboxBatcher':lambda *a,**kw:NS(prepare=lambda:None), 'PreparedBatch':object})
spec=importlib.util.spec_from_file_location(PKG+'.archive_transport',ROOT/'archive_transport.py')
transport=importlib.util.module_from_spec(spec);sys.modules[spec.name]=transport;spec.loader.exec_module(transport)

FIELDS=('archive_accepted_count','archive_already_processed_count','archive_retryable_rejected_count','archive_permanent_rejected_count','archive_missing_ack_count','archive_accepted_bytes','archive_accepted_records','archive_duplicate_bytes','archive_ack_metric_error_count','archive_http_attempt_count','archive_http_success_count','archive_http_failure_count','archive_active_requests','archive_peak_active_requests','deferred_before_http_count')
def owner():
    x=NS(counters=NS(**{k:0 for k in FIELDS},last_defer_reason=None),settings=dict(transport.DEFAULTS))
    x.policy=transport.AdaptivePolicy(x.settings);x._send_guard=lambda:None;x._http_outcome=lambda r:None;return x
class Store:
    def __init__(self):
        self.conn=sqlite3.connect(':memory:',check_same_thread=False);self._lock=threading.RLock();self.path=':memory:'
        self.conn.executescript('''CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,state TEXT,next_attempt_epoch REAL,last_attempt_utc TEXT,attempt_count INTEGER,payload_json TEXT);
        CREATE TABLE delivery_receipts_v1(message_id TEXT PRIMARY KEY,ack_status TEXT,payload_bytes INTEGER,record_count INTEGER);''')
    def item(self,mid='m',**kw):
        self.conn.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?)',(mid,'inflight',123.,'claim',3,'PAYLOAD'))
        self.conn.commit()
        return NS(message_id=mid,next_attempt_epoch=123.,last_attempt_utc='claim',stream='general_asset_telemetry',substream='bms_extended',sequence=1,attempt_count=3,**kw)
    def _invalidate_stats_cache(self):pass
    def record_attempt(self,**kw):pass
    def apply_batch_updates(self,accepted,retries,dead):
        with self._lock:
            for mid,status,detail in accepted:
                self.conn.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?)',(mid,status,100,4));self.conn.execute('DELETE FROM outbox_messages WHERE message_id=?',(mid,))
            for mid,epoch,error,stamp in retries:self.conn.execute("UPDATE outbox_messages SET state='retry',next_attempt_epoch=? WHERE message_id=?",(epoch,mid))
            for mid,error,detail in dead:self.conn.execute("UPDATE outbox_messages SET state='dead' WHERE message_id=?",(mid,))
            self.conn.commit()

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.t=0.;self.p=transport.AdaptivePolicy(dict(transport.DEFAULTS),clock=lambda:self.t)
        self.c=NS(min_available_memory_mb=500)
        self.h={'live':{'available':True,'unacked_count':4,'oldest_age_sec':8},'transport_paused':False,'mem_available_mb':1200,'load1':2.5,'cpu_count':2}
    def test_two_healthy_samples_then_fast(self):
        self.assertFalse(self.p.decide(self.h,self.c)[0]);self.assertTrue(self.p.decide(self.h,self.c)[0]);self.assertEqual(self.p.delay,10)
    def test_moderate_pressure_slows(self):
        self.h['live']['unacked_count']=14;self.h['live']['oldest_age_sec']=18
        self.p.decide(self.h,self.c);self.assertTrue(self.p.decide(self.h,self.c)[0]);self.assertEqual(self.p.mode,'limited');self.assertEqual(self.p.delay,30)
    def test_hard_guards(self):
        for key,value in [('transport_paused',True),('mem_available_mb',499),('load1',4.1)]:
            h=dict(self.h,**{key:value});self.assertIsNotNone(self.p.hard_reason(h,self.c))
        for count,age in [(26,10),(1,31),(1,None)]:
            h=dict(self.h,live={'available':True,'unacked_count':count,'oldest_age_sec':age});self.assertIsNotNone(self.p.hard_reason(h,self.c))
    def test_nonfinite_load(self):
        self.h['load1']=float('nan');self.assertEqual(self.p.hard_reason(self.h,self.c),'load_pressure')
    def test_backend_cooldown(self):
        self.p.cooldown(120);self.t=119;self.assertEqual(self.p.hard_reason(self.h,self.c),'backend_cooldown');self.t=120;self.assertIsNone(self.p.hard_reason(self.h,self.c))
    def test_growth_yields(self):
        for count in (10,15,25):self.h['live']['unacked_count']=count;r=self.p.decide(self.h,self.c)
        self.assertEqual(r,(False,'live_queue_growing'))
    def test_pressure_recovery_hysteresis(self):
        self.h['live']['unacked_count']=40;self.p.decide(self.h,self.c)
        self.h['live']['unacked_count']=20;self.assertEqual(self.p.decide(self.h,self.c)[1],'waiting_for_live_recovery')
        self.h['live']['unacked_count']=4;self.assertFalse(self.p.decide(self.h,self.c)[0]);self.assertTrue(self.p.decide(self.h,self.c)[0])
    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'s.json'
            for v in ({'fast_interval_sec':1},{'healthy_samples':1},{'max_load_per_cpu':float('nan')},{'unknown':1},{'fast_live_count':99}):
                p.write_text(json.dumps(v))
                with self.assertRaises(ValueError):transport.load_settings(p)

class ClaimTests(unittest.TestCase):
    def test_deferred_claim_preserves_payload_and_attempts(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();transport.release_unsent_claims(s,[item])
        row=s.conn.execute('SELECT state,attempt_count,payload_json FROM outbox_messages').fetchone()
        self.assertEqual(row,('retry',3,'PAYLOAD'))
    def test_does_not_touch_another_lease(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();s.conn.execute('UPDATE outbox_messages SET next_attempt_epoch=999');s.conn.commit()
        transport.release_unsent_claims(s,[item]);self.assertEqual(s.conn.execute('SELECT state FROM outbox_messages').fetchone()[0],'inflight')
    def test_never_resurrects_acked_rows(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();s.conn.execute('DELETE FROM outbox_messages');s.conn.commit()
        transport.release_unsent_claims(s,[item]);self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM outbox_messages').fetchone()[0],0)

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    def make_uploader(self,s,o):
        cfg=NS(identity=NS(gateway_id='g'),uploader=NS(pause_file='unused',coalescing_window_sec=1,max_messages_per_request=25,max_request_bytes=131072,worker_count=1,max_retry_after_sec=600,backoff_initial_sec=5,backoff_multiplier=2,backoff_max_sec=300,jitter_percent=0))
        u=transport.ArchiveUploader(owner=o,config=cfg,outbox=s,client=NS(),transport_lock=asyncio.Lock())
        u._write_status_if_due=AsyncMock();return u
    async def test_real_ack_parser_keeps_missing_acks(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();o=owner();u=self.make_uploader(s,o)
        await u._apply_partial_ack([item],client.TransportResponse(status_code=200,body={}))
        self.assertEqual(s.conn.execute('SELECT state FROM outbox_messages').fetchone()[0],'retry');self.assertEqual(o.counters.archive_missing_ack_count,1);self.assertEqual(o.counters.archive_accepted_bytes,0)
    async def test_real_ack_parser_releases_valid_acks_and_counts_bytes(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();o=owner();u=self.make_uploader(s,o)
        await u._apply_partial_ack([item],client.TransportResponse(status_code=200,body={'accepted':['m']}))
        self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM outbox_messages').fetchone()[0],0)
        self.assertEqual(o.counters.archive_accepted_count,1);self.assertEqual(o.counters.archive_accepted_bytes,100);self.assertEqual(o.counters.archive_accepted_records,4)
    async def test_duplicate_ack_counts_separately(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();o=owner();u=self.make_uploader(s,o)
        await u._apply_partial_ack([item],client.TransportResponse(status_code=200,body={'accepted':[{'message_id':'m','status':'already_processed'}]}))
        self.assertEqual(o.counters.archive_accepted_count,0);self.assertEqual(o.counters.archive_already_processed_count,1);self.assertEqual(o.counters.archive_duplicate_bytes,100)
    async def test_retryable_rejection_retained_and_slows(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();o=owner();u=self.make_uploader(s,o)
        await u._apply_partial_ack([item],client.TransportResponse(status_code=200,body={'rejected':[{'message_id':'m','retryable':True,'code':'busy'}]}))
        self.assertEqual(s.conn.execute('SELECT state FROM outbox_messages').fetchone()[0],'retry');self.assertEqual(o.counters.archive_retryable_rejected_count,1);self.assertGreater(o.policy.cooldown_until,time.monotonic())
    async def test_pre_send_guard_requeues_without_sending(self):
        s=Store();self.addCleanup(s.conn.close);item=s.item();o=owner();u=self.make_uploader(s,o)
        u.batcher.prepare=lambda:NS(items=[item]);u._send_prepared=AsyncMock(side_effect=transport.DeferredBeforeHTTP('transport_paused'))
        self.assertFalse(await u._run_transport_once());self.assertEqual(s.conn.execute('SELECT state,attempt_count FROM outbox_messages').fetchone(),('retry',3))
    async def test_guard_before_preparation(self):
        s=Store();self.addCleanup(s.conn.close);o=owner();o._send_guard=lambda:'live_age_pressure';u=self.make_uploader(s,o)
        def bad():raise AssertionError('must not prepare')
        u.batcher.prepare=bad;self.assertFalse(await u._run_transport_once())
    async def test_dedicated_client_can_send_while_live_waits(self):
        o=owner();started=asyncio.Event();finish=asyncio.Event();fake_http=NS(aclose=AsyncMock());second_http=NS(aclose=AsyncMock())
        with patch.object(client.CentralBackendClient,'_build_http_client',side_effect=[fake_http,second_http]):
            live=client.CentralBackendClient(NS(),'g');archive=transport.DedicatedArchiveClient(NS(),'g',live,o)
        self.assertIsNot(live.http,archive.http)
        async def post(self,body,token):
            if self is live:started.set();await finish.wait()
            return client.TransportResponse(status_code=200,body={})
        live._obtain_token=AsyncMock(return_value='token')
        with patch.object(client.CentralBackendClient,'_post_once',post):
            task=asyncio.create_task(live.post_batch(b'live'));await started.wait()
            response=await asyncio.wait_for(archive.post_batch(b'archive'),timeout=1)
            self.assertEqual(response.status_code,200);self.assertFalse(task.done());finish.set();await task
        self.assertEqual(o.counters.archive_peak_active_requests,1)
        live._obtain_token.assert_awaited()
    async def test_actual_post_guard_runs_after_auth(self):
        o=owner();o._send_guard=lambda:'transport_paused';broker=NS(_obtain_token=AsyncMock(return_value='t'))
        fake=NS(aclose=AsyncMock())
        with patch.object(client.CentralBackendClient,'_build_http_client',return_value=fake):a=transport.DedicatedArchiveClient(NS(),'g',broker,o)
        with self.assertRaises(transport.DeferredBeforeHTTP):await a.post_batch(b'x')
        broker._obtain_token.assert_awaited_once();self.assertEqual(o.counters.archive_http_attempt_count,0)

recovery=module_from_reference('overflow_recovery','overflow_recovery.py',{
    'Path':Path,'sqlite3':sqlite3,'CentralBackendClient':client.CentralBackendClient,
    'AdaptivePolicy':transport.AdaptivePolicy,'DedicatedArchiveClient':transport.DedicatedArchiveClient,
    'ArchiveUploader':transport.ArchiveUploader,'load_settings':lambda:dict(transport.DEFAULTS),
    'safe_write_json':lambda *a,**k:None})

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def service(self,root):
        cfg=NS(overflow_recovery=NS(enabled=True,archive_dir=str(root),archive_glob='*.db',marker_suffix='.replayed',min_available_memory_mb=500,max_messages_per_request=25,max_request_bytes=131072),
               outbox=NS(path=str(root/'live.db')),uploader=NS(pause_file=str(root/'pause')))
        broker=NS(close=AsyncMock());lock=asyncio.Lock()
        service=recovery.OverflowRecoveryService(cfg,transport_lock=lock,client=broker)
        return service,broker,lock
    async def test_service_has_independent_lock_and_closes_only_owned_client(self):
        with tempfile.TemporaryDirectory() as d:
            s,broker,live_lock=self.service(Path(d));self.assertIsNot(s.transport_lock,live_lock)
            own=NS(close=AsyncMock());s.client=own;await s.close();await s.close()
            own.close.assert_awaited_once();broker.close.assert_not_awaited()
    async def test_archive_backoff_does_not_block_other_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s,_,_=self.service(root)
            for name,due in [('a.db',time.time()+3600),('b.db',0)]:
                with sqlite3.connect(root/name) as db:
                    db.execute('CREATE TABLE outbox_messages(state TEXT,next_attempt_epoch REAL)')
                    db.execute('INSERT INTO outbox_messages VALUES(?,?)',('retry',due))
            os.utime(root/'a.db',(1,1));os.utime(root/'b.db',(2,2))
            self.assertEqual(s._next_archive(),root/'b.db')
    async def test_retry_after_cools_whole_archive_worker(self):
        with tempfile.TemporaryDirectory() as d:
            s,_,_=self.service(Path(d));start=time.monotonic()
            s._http_outcome(client.TransportResponse(status_code=429,retry_after_sec=120))
            self.assertGreaterEqual(s.policy.cooldown_until,start+120)
            self.assertEqual(s.counters.archive_http_failure_count,1)
    async def test_recovery_status_exposes_transport_and_actual_ack_counters(self):
        with tempfile.TemporaryDirectory() as d:
            s,_,_=self.service(Path(d));status=s.status()
            self.assertEqual(status['transport_mode'],'dedicated_archive_client')
            self.assertEqual(status['auth_mode'],'shared_token_broker')
            self.assertEqual(status['max_archive_concurrency'],1)
            self.assertEqual(status['counters']['archive_accepted_bytes'],0)

if __name__=='__main__':unittest.main()
