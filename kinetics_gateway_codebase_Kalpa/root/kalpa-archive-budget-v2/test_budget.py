import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('KALPA_TEST_BASE','/opt/ornate-ems-gateway/backend'))
sys.path.insert(0,str(BASE))
import app.central_sync
app.central_sync.__path__.insert(0,str(ROOT/'payload/app/central_sync'))
from app.central_sync.recovery_budget import ByteBudget,BudgetPolicy,DEFAULTS,load_budget_settings
from app.central_sync.archive_transport import DEFAULTS as LEGACY,ArchiveUploader,DedicatedArchiveClient,DeferredBeforeHTTP,release_unsent_claims
from app.central_sync.overflow_recovery import RecoveryCounters,OverflowRecoveryService
from app.central_sync.config import CentralSyncConfig
from app.central_sync.outbox import OutboxStore
from app.central_sync.contracts import make_message
from app.central_sync.client import TransportResponse,CentralBackendClient

class Clock:
    def __init__(self):self.t=0.0
    def __call__(self):return self.t
    def advance(self,n):self.t+=n

def health(count=0,age=None):
    return {'live':{'available':True,'unacked_count':count,'oldest_age_sec':age},
            'live_transport':{'available':True,'last_error':None,'request_failure_count':0,'started_utc':'one'},
            'mem_available_mb':1000,'load1':2.0,'cpu_count':2,'transport_paused':False}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.budget=ByteBudget(DEFAULTS['burst_bytes'],self.clock)
        self.policy=BudgetPolicy(LEGACY,DEFAULTS,self.budget,self.clock)
        self.config=NS(min_available_memory_mb=500)
    def decide(self,h):return self.policy.decide(h,self.config)
    def warm(self,h):
        self.decide(h);self.clock.advance(1);return self.decide(h)
    def test_two_fresh_observations_required(self):
        h=health();self.assertFalse(self.decide(h)[0]);self.assertFalse(self.decide(h)[0])
        self.clock.advance(1);self.assertTrue(self.decide(h)[0])
    def test_normal_live_burst_limits_rate_instead_of_starving(self):
        self.assertTrue(self.warm(health(35,35))[0])
        self.assertEqual(self.policy.mode,'limited')
        self.assertEqual(self.budget.status()['MB_per_min'],DEFAULTS['limited_MB_per_min'])
    def test_manual_pause_memory_load_and_unavailable_probes_block(self):
        for key,value,reason in [('transport_paused',True,'transport_paused'),
                                 ('mem_available_mb',499,'memory_pressure'),('load1',4.1,'load_pressure')]:
            h=health();h[key]=value;self.assertEqual(self.decide(h),(False,reason))
        for key in ('live','live_transport'):
            h=health();h[key]['available']=False;self.assertFalse(self.decide(h)[0])
    def test_hard_live_count_and_age_not_overridden(self):
        for h in (health(101,10),health(1,76)):
            self.assertFalse(self.warm(h)[0]);self.assertEqual(self.budget.status()['MB_per_min'],0)
    def test_nonfinite_health_and_missing_age_fail_closed(self):
        for key in ('load1','mem_available_mb','cpu_count'):
            h=health();h[key]=float('nan');self.assertFalse(self.decide(h)[0])
        self.assertFalse(self.decide(health(1,None))[0])
    def test_sustained_growth_yields(self):
        for count in (25,31,38):
            answer=self.decide(health(count,20));self.clock.advance(5)
        self.assertEqual(answer,(False,'live_queue_growing'))
    def test_live_failure_even_if_already_recovered_forces_cooldown(self):
        h=health();self.warm(h);h['live_transport']['request_failure_count']=1
        self.assertEqual(self.decide(h),(False,'backend_cooldown'))
        self.assertEqual(self.budget.status()['MB_per_min'],0)
    def test_active_live_error_blocks(self):
        h=health();h['live_transport']['last_error']='ReadTimeout'
        self.assertEqual(self.decide(h),(False,'live_transport_failure'))
    def test_gradual_ramp_is_bounded(self):
        h=health();self.warm(h)
        for _ in range(20):self.clock.advance(60);self.decide(h)
        self.assertEqual(self.budget.status()['MB_per_min'],DEFAULTS['max_MB_per_min'])
    def test_budget_charges_bytes_and_no_idle_burst_accumulates(self):
        self.budget.set_rate(.5);self.clock.advance(60)
        self.assertTrue(self.budget.consume(200000));self.assertFalse(self.budget.consume(100000))
        self.budget.set_rate(0);self.clock.advance(3600);self.budget.set_rate(.5)
        self.assertFalse(self.budget.ready(100000))
    def test_attempted_byte_rate_bound_for_normal_requests(self):
        self.budget.set_rate(.5)
        for i in range(1200):
            self.clock.advance(1)
            self.budget.consume(98000)
            self.assertLessEqual(self.budget.charged_bytes,self.clock.t*.5e6/60+1)
    def test_oversized_first_request_repays_debt(self):
        self.budget.set_rate(.5);self.clock.advance(60)
        self.assertTrue(self.budget.consume(400000));self.assertFalse(self.budget.consume(1000))
        self.clock.advance(60);self.assertTrue(self.budget.consume(1000))
    def test_settings_reject_unknown_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            for obj in ({'unknown':1},{'hard_live_count':1},{'initial_MB_per_min':float('nan')},{'healthy_samples':1}):
                p.write_text(json.dumps(obj))
                with self.assertRaises(ValueError):load_budget_settings(p)

class IntegrationTests(unittest.TestCase):
    def store(self,path):
        store=OutboxStore(str(path)); messages=[]
        for _ in range(2):
            msg=make_message(schema_version='1.0',gateway_id='test',site_id='test',organization_id='test',
                 gateway_software_version='test',stream='general_asset_telemetry',substream='bms_extended',
                 sequence=len(messages)+1,priority='P3',records=[{'value':1}],compression='none')
            store.enqueue(msg);messages.append(msg)
        import time
        items=store.eligible(now_epoch=time.time()+2,coalescing_window_sec=0,max_messages=10,max_bytes=131072)
        return store,items
    def owner(self):
        return NS(counters=RecoveryCounters(),policy=NS(cooldown=lambda n:None),settings=LEGACY,
                  byte_budget=ByteBudget(131328),_send_guard=lambda:None,_write_status=lambda:None)
    def test_partial_ack_releases_only_accepted_and_preserves_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store,items=self.store(Path(d)/'a.db');owner=self.owner();calls=[]
            owner.policy.cooldown=lambda n:calls.append(n)
            uploader=ArchiveUploader(owner=owner,config=CentralSyncConfig(),outbox=store,client=NS())
            response=TransportResponse(200,body={'accepted':[items[0].message_id],
                'rejected':[{'message_id':items[1].message_id,'retryable':True,'error_code':'invalid_payload','detail':'test'}]})
            asyncio.run(uploader._apply_partial_ack(items,response))
            self.assertIsNone(store.conn.execute('SELECT state FROM outbox_messages WHERE message_id=?',(items[0].message_id,)).fetchone())
            self.assertEqual(store.conn.execute('SELECT state FROM outbox_messages WHERE message_id=?',(items[1].message_id,)).fetchone()[0],'retry')
            self.assertEqual(owner.counters.archive_accepted_count,1)
            self.assertEqual(owner.counters.archive_retryable_rejected_count,1)
            self.assertTrue(owner._recent_archive_rejections)
            self.assertEqual(calls,[]);store.close()
    def test_budget_deferral_sends_no_http(self):
        owner=self.owner();client=DedicatedArchiveClient.__new__(DedicatedArchiveClient);client.owner=owner
        with patch.object(CentralBackendClient,'_post_once',new_callable=AsyncMock) as post:
            with self.assertRaises(DeferredBeforeHTTP):asyncio.run(client._post_once(b'x'*1000,'test'))
            post.assert_not_awaited()
        self.assertEqual(owner.counters.archive_http_attempt_count,0)
    def test_deferred_claims_are_retained_without_incrementing_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            store,items=self.store(Path(d)/'a.db');release_unsent_claims(store,items)
            rows=store.conn.execute('SELECT state,attempt_count FROM outbox_messages').fetchall()
            self.assertEqual([(r[0],r[1]) for r in rows],[('retry',0),('retry',0)])
            store.close()
    def test_http_429_keeps_retry_after_cooldown(self):
        owner=self.owner();calls=[];owner.policy.cooldown=lambda n:calls.append(n)
        OverflowRecoveryService._http_outcome(owner,TransportResponse(429,retry_after_sec=123))
        self.assertEqual(calls,[123]);self.assertEqual(owner.counters.archive_http_failure_count,1)

if __name__=='__main__':unittest.main(verbosity=2)
