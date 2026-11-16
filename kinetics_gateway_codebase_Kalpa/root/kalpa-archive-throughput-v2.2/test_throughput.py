import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace as NS
from test_budget import OverflowRecoveryService,RecoveryCounters,OutboxStore,CentralSyncConfig,make_message
from app.central_sync.alarm_wire_format import normalize_body,severity
import repair_alarms as repair

class AlarmTests(unittest.TestCase):
    def test_all_local_severities_and_states(self):
        for old,new in [('alarm','High'),('critical','Critical'),('warning','Warning'),('info','Info'),('High','High')]:self.assertEqual(severity(old),new)
        original={'message_id':'keep','sequence':42,'records':[{'event_id':'event','severity':'warning','state':'cleared','timestamp_utc':'same'}]}
        fixed=normalize_body(original)
        self.assertEqual(fixed['records'][0]['state'],'CLEARED');self.assertEqual(fixed['records'][0]['severity'],'Warning')
        self.assertEqual(fixed['records'][0]['event_id'],'event');self.assertEqual(fixed['message_id'],'keep');self.assertEqual(fixed['sequence'],42)
        self.assertEqual(original['records'][0]['severity'],'warning')
        self.assertEqual(normalize_body(fixed),fixed)
    def test_unknown_enum_fails(self):
        with self.assertRaises(ValueError):severity('urgent')
    def test_historical_repair_preserves_identity_and_retry_evidence(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'archive.db';db=sqlite3.connect(p)
            db.execute('CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,sequence INTEGER,state TEXT,stream TEXT,last_error TEXT,payload_json TEXT,payload_bytes INTEGER,next_attempt_epoch REAL,attempt_count INTEGER)')
            raw=json.dumps({'records':[{'event_id':'unchanged','severity':'alarm','state':'active'}]})
            mid=sorted(repair.KNOWN)[0]
            db.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,1,'retry','alarms_events','invalid_payload: severity',raw,len(raw),1,7));db.commit()
            entries=repair.plan([p]);self.assertEqual(len(entries),1);repair.apply_one(entries[0])
            row=db.execute('SELECT message_id,sequence,state,last_error,payload_json,payload_bytes,attempt_count FROM outbox_messages').fetchone()
            self.assertEqual(row[:4],(mid,1,'retry','invalid_payload: severity'));self.assertEqual(row[-1],7)
            self.assertEqual(row[5],len(row[4].encode()));self.assertEqual(json.loads(row[4])['records'][0]['event_id'],'unchanged')
            self.assertEqual(repair.plan([p]),[]);db.close()
    def test_repair_refuses_concurrent_payload_change(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'archive.db';db=sqlite3.connect(p)
            db.execute('CREATE TABLE outbox_messages(message_id TEXT,state TEXT,last_error TEXT,payload_json TEXT,payload_bytes INTEGER,next_attempt_epoch REAL)')
            db.execute("INSERT INTO outbox_messages VALUES('id','retry','invalid_payload','changed',7,0)");db.commit()
            with self.assertRaises(RuntimeError):repair.apply_one({'database':str(p),'original':{'message_id':'id','state':'retry','last_error':'invalid_payload','payload_json':'original'},'corrected_payload_json':'fixed'})
            self.assertEqual(db.execute('SELECT payload_json FROM outbox_messages').fetchone()[0],'changed');db.close()

class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_and_uploader_reused_and_closed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=CentralSyncConfig();cfg.overflow_recovery.archive_dir=d;cfg.outbox.path=str(Path(d)/'live.db')
            service=OverflowRecoveryService(cfg,transport_lock=asyncio.Lock(),client=NS())
            path=Path(d)/'archive.db';store=OutboxStore(str(path))
            # One retry row remains, so the selected DB can be reused.
            store.conn.execute("""INSERT INTO outbox_messages(message_id,gateway_id,site_id,stream,substream,sequence,priority,priority_rank,created_at_utc,created_epoch,payload_json,payload_bytes,state,next_attempt_epoch)
              VALUES('one','g','s','alarms_events','events',1,'normal',1,'2026-11-12T00:00:00Z',0,'{}',2,'retry',0)""");store.conn.commit();store.close()
            uploader=NS(counters=NS(request_success_count=0),run_once=AsyncMock(return_value=True))
            with patch('app.central_sync.overflow_recovery.ArchiveUploader',return_value=uploader) as factory:
                self.assertTrue(await service._replay_one(path));first=service._session_store
                self.assertTrue(await service._replay_one(path));self.assertIs(service._session_store,first)
                self.assertEqual(factory.call_count,1);self.assertEqual(service.counters.archive_open_count,1)
                self.assertEqual(service.counters.archive_session_reuse_count,1)
                self.assertEqual(service._next_archive(),path)
                service._close_session();self.assertIsNone(service._session_store)
                with self.assertRaises(Exception):first.conn.execute('SELECT 1')
    async def test_no_send_closes_session(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=CentralSyncConfig();cfg.overflow_recovery.archive_dir=d;cfg.outbox.path=str(Path(d)/'live.db')
            service=OverflowRecoveryService(cfg,transport_lock=asyncio.Lock(),client=NS())
            uploader=NS(counters=NS(request_success_count=0),run_once=AsyncMock(return_value=False))
            with patch('app.central_sync.overflow_recovery.ArchiveUploader',return_value=uploader):
                self.assertFalse(await service._replay_one(Path(d)/'empty.db'))
                self.assertIsNone(service._session_store)

class ProducerTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_alarm_output_uses_canonical_enums_and_stable_event_id(self):
        from app.central_sync.alarm_event_producer import AlarmEventProducer
        with tempfile.TemporaryDirectory() as d:
            cfg=CentralSyncConfig();cfg.alarms_events.state_file=str(Path(d)/'cursor.json');cfg.alarms_events.startup_emit_existing=True
            item={'timestamp':'2026-11-12T09:00:00Z','alarm_key':'key','asset_id':'asset','action':'raised','code':'test','severity':'alarm'}
            api=NS(alarms_history=AsyncMock(return_value={'history':[item]}))
            producer=AlarmEventProducer(config=cfg,outbox=NS(),local_api=api);producer._write_status=lambda:None
            with patch('app.central_sync.alarm_event_producer.emit_message',return_value=(True,NS(message_id='new',sequence=1))) as emit:
                await producer.collect_once()
                record=emit.call_args.kwargs['records'][0]
                self.assertEqual(record['severity'],'High');self.assertEqual(record['state'],'ACTIVE')
                self.assertEqual(record['event_id'],producer._event_id(item))
