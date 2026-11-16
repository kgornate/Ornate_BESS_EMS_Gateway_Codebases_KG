import ast
import asyncio
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import ModuleType,SimpleNamespace
import unittest
from patching import patch_outbox,patch_dashboard
import queue_metrics as q
from install import protected_snapshot

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'dashboard-update/gateway'
if not BASE.is_dir():BASE=Path('/opt/ornate-ems-gateway/backend/app/central_sync')

def source_module():
    source=(BASE/'outbox.py').read_text()
    # Include the previously delivered generation-byte patch where available.
    gen=ROOT/'deliverables/kalpa-generation-mb-v1/install.py'
    if gen.is_file() and '# KALPA_GENERATION_BYTES_V1' not in source:
        spec=importlib.util.spec_from_file_location('generation_fixture',gen);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        source=mod.patch_source(source)
    source=patch_outbox(source)
    source=source.replace('from .contracts import LogicalMessage','LogicalMessage = object')
    source=source.replace('from .queue_metrics import','from queue_metrics import')
    module=ModuleType('ack_release_outbox_fixture');sys.modules[module.__name__]=module
    exec(compile(source,'outbox.py','exec'),module.__dict__)
    return module

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.module=source_module()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'outbox.db'
        self.box=self.module.OutboxStore(str(self.path),max_db_size_mb=1024)
        self.seq=0
    def tearDown(self):self.box.close();self.tmp.cleanup()
    def seed(self,mid,state='inflight',status=None,stamp=None,huge=False):
        self.seq+=1
        body={'records':[{'value':'SENSOR_VALUE_MUST_NOT_SURVIVE_ACK'+('x'*1200000 if huge else '')}]}
        msg=SimpleNamespace(message_id=mid,gateway_id='gw',site_id='site',stream='general_asset_telemetry',
            substream='bms_extended',sequence=self.seq,priority='P3',priority_rank=3,
            created_at_utc=datetime.now(timezone.utc).isoformat(),model_dump=lambda **kwargs:body)
        self.assertTrue(self.box.enqueue(msg))
        self.box.conn.execute('UPDATE outbox_messages SET state=?,ack_status=?,acknowledged_utc=? WHERE message_id=?',
            (state,status,stamp,mid));self.box.conn.commit()
    def states(self):return dict(self.box.conn.execute('SELECT message_id,state FROM outbox_messages'))
    def test_explicit_ack_releases_payload_but_keeps_compact_metrics(self):
        self.seed('a');self.box.apply_batch_updates(accepted=[('a','accepted',None)])
        self.assertNotIn('a',self.states())
        row=self.box.conn.execute('SELECT message_id,record_count FROM delivery_receipts_v1').fetchone()
        self.assertEqual(tuple(row),('a',1))
        self.assertNotIn('SENSOR_VALUE_MUST_NOT_SURVIVE_ACK','\n'.join(self.box.conn.iterdump()))
        window,error=q.receipt_window(self.path)
        self.assertIsNone(error);self.assertEqual(window['accepted'][0:2],[1,1])
    def test_already_processed_ack_releases_payload_and_is_separate(self):
        self.seed('a');self.box.mark_accepted('a','already_processed')
        self.assertEqual(self.states(),{})
        window,_=q.receipt_window(self.path)
        self.assertEqual(window['duplicate'][:2],[1,1]);self.assertEqual(window['accepted'],[0,0,0])
    def test_retry_dead_and_unselected_payloads_are_preserved(self):
        for mid in ('a','b','c','d'):self.seed(mid)
        self.box.apply_batch_updates(accepted=[('a','accepted',None)],retries=[('b',time.time()+30,'backend deferred',None)],dead=[('c','permanent rejection',None)])
        self.assertEqual(self.states(),{'b':'retry','c':'dead','d':'inflight'})
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM outbox_messages WHERE payload_json LIKE ?',('%SENSOR_VALUE_MUST_NOT_SURVIVE_ACK%',)).fetchone()[0],3)
    def test_missing_ack_keeps_payload_using_real_uploader_parser(self):
        self.seed('a')
        source=ast.parse((BASE/'uploader.py').read_text())
        method=next(n for n in ast.walk(source) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_apply_partial_ack');method.decorator_list=[]
        ns={'asyncio':asyncio,'Any':object,'OutboxItem':object,'TransportResponse':object,
          'utc_now_iso':lambda:datetime.now(timezone.utc).isoformat(),'_string_or_none':lambda value:None if value is None else str(value)}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'uploader_ack_parser','exec'),ns)
        counters=SimpleNamespace(accepted_count=0,already_processed_count=0,retryable_rejected_count=0,permanent_rejected_count=0,missing_ack_count=0)
        uploader=SimpleNamespace(outbox=self.box,counters=counters,_note_stream_ack=lambda *a,**k:None,_note_stream_result=lambda *a,**k:None,
          _retry_row=lambda item,error,**kwargs:(item.message_id,time.time()+30,error,kwargs.get('attempted_utc')))
        asyncio.run(ns['_apply_partial_ack'](uploader,[SimpleNamespace(message_id='a')],SimpleNamespace(body={})))
        self.assertEqual(self.states(),{'a':'retry'});self.assertEqual(counters.missing_ack_count,1)
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM delivery_receipts_v1').fetchone()[0],0)
    def test_bad_ack_status_rolls_back_and_retains_payload(self):
        self.seed('a')
        with self.assertRaises(ValueError):self.box.apply_batch_updates(accepted=[('a','unknown',None)])
        self.assertEqual(self.states(),{'a':'inflight'})
    def test_ack_commit_failure_rolls_back_receipt_and_deletion(self):
        self.seed('a');self.seed('b')
        self.box.conn.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON outbox_messages WHEN OLD.message_id='b' BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.box.apply_batch_updates(accepted=[('a','accepted',None),('b','accepted',None)])
        self.assertEqual(self.states(),{'a':'inflight','b':'inflight'})
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM delivery_receipts_v1').fetchone()[0],0)
    def test_duplicate_ack_does_not_double_count(self):
        self.seed('a')
        for _ in range(2):self.box.apply_batch_updates(accepted=[('a','accepted',None)])
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM delivery_receipts_v1').fetchone()[0],1)
    def test_migration_preserves_unsent_and_cadence_state(self):
        for mid,state in [('pending','pending'),('retry','retry'),('flight','inflight'),('dead','dead')]:self.seed(mid,state)
        self.seed('delivered','acked','accepted',datetime.now(timezone.utc).isoformat())
        self.seed('unverified','acked','unknown',datetime.now(timezone.utc).isoformat())
        before=protected_snapshot(self.box.conn)
        self.box.conn.execute('BEGIN IMMEDIATE');removed=q.compact_existing(self.box.conn)
        self.assertEqual(removed,1);self.assertEqual(protected_snapshot(self.box.conn),before);self.box.conn.commit()
        self.assertIn('unverified',self.states());self.assertNotIn('delivered',self.states())
    def test_old_ack_migration_avoids_payload_history(self):
        self.seed('old','acked','accepted','2026-01-01T00:00:00Z')
        self.assertEqual(q.compact_existing(self.box.conn),1);self.box.conn.commit()
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM delivery_receipts_v1').fetchone()[0],0)
    def test_cleanup_does_not_purge_unverified_acked_state(self):
        self.seed('unverified','acked','unknown','2026-01-01T00:00:00Z')
        self.box.cleanup(acked_retention_hours=0,dead_letter_retention_days=30,transport_attempt_retention_days=3)
        self.assertEqual(self.states(),{'unverified':'acked'})
    def test_logical_capacity_recovers_without_vacuum_or_larger_cap(self):
        self.seed('big','acked','accepted','2026-01-01T00:00:00Z',huge=True)
        self.box.max_db_size_mb=1
        self.assertFalse(self.box.capacity_status()['can_accept'])
        q.compact_existing(self.box.conn);self.box.conn.commit()
        self.assertTrue(self.box.capacity_status()['can_accept'])
        self.assertGreater(self.box.sqlite_space_status()['reusable_page_bytes'],1000000)
        self.seed('new','pending')
    def test_receipt_time_and_count_bounds(self):
        now=time.time()
        self.box.conn.executemany('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',
            [(str(i),'s2','bms','accepted',now,1,100,now,0) for i in range(q.MAX_RECEIPTS+2)])
        self.box.conn.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',('old','s2','bms','accepted',now-301,1,100,now-301,0))
        q.prune_receipts(self.box.conn,now);self.box.conn.commit()
        self.assertEqual(self.box.conn.execute('SELECT count(*) FROM delivery_receipts_v1').fetchone()[0],q.MAX_RECEIPTS)
        self.assertIsNone(self.box.conn.execute("SELECT 1 FROM delivery_receipts_v1 WHERE message_id='old'").fetchone())
        result,error=q.receipt_window(self.path,now=now+1)
        self.assertIsNone(result);self.assertIn('truncate',error)
    def test_checkpoint_generation_and_sequences_unchanged_by_ack(self):
        self.seed('a');self.box.next_sequence('test','sub')
        before=protected_snapshot(self.box.conn)
        self.box.apply_batch_updates(accepted=[('a','accepted',None)])
        after=protected_snapshot(self.box.conn)
        for key in ('sequences','s2_recording_checkpoint','generation_metrics_v1'):
            if key in before:self.assertEqual(before[key],after[key])
    def test_source_patching_idempotent_and_preserves_generation_patch(self):
        source=(BASE/'outbox.py').read_text();updated=patch_outbox(source)
        self.assertEqual(patch_outbox(updated),updated)
        self.assertIn('def enqueue_s2_atomic',updated)
        with self.assertRaises(RuntimeError):patch_outbox('unexpected layout')
    def test_viewer_patch_and_recent_acked_metadata(self):
        view=ROOT/'deliverables/kalpa-delivery-dashboard-v1/metrics.py'
        if not view.is_file():self.skipTest('Viewer fixture not bundled')
        changed=patch_dashboard(view.read_text());self.assertEqual(patch_dashboard(changed),changed)
        self.seed('a');self.box.mark_accepted('a','accepted')
        row=q.receipt_rows(self.path)['a'];self.assertEqual(row['state'],'acked')
        self.assertNotIn('payload_json',row)
if __name__=='__main__':unittest.main()
