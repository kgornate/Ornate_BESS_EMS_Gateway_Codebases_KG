import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import monitor_evidence as e
import dashboard

SCHEMA='''CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,stream TEXT,substream TEXT,
 priority TEXT,priority_rank INTEGER,created_epoch REAL,payload_bytes INTEGER,payload_json TEXT,state TEXT);
 CREATE INDEX idx_outbox_stats ON outbox_messages(state,priority,created_epoch,payload_bytes);
 CREATE INDEX idx_outbox_stream ON outbox_messages(stream,substream,created_epoch);
 CREATE INDEX idx_outbox_spill ON outbox_messages(state,priority_rank DESC,created_epoch,message_id);
 CREATE TABLE delivery_receipts_v1(message_id TEXT PRIMARY KEY,stream TEXT,substream TEXT,ack_status TEXT,
 ack_epoch REAL,record_count INTEGER,payload_bytes INTEGER,created_epoch REAL,attempt_count INTEGER);
'''
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sd=self.root/'archives';self.sd.mkdir();self.live=self.root/'live.db';self.archive=self.sd/'overflow_1.db'
        self.now=time.time()
        for p in (self.live,self.archive):
            with sqlite3.connect(p) as db:db.executescript(SCHEMA)
    def message(self,p,mid='m1',state='pending'):
        with sqlite3.connect(p) as db:db.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,'general_asset_telemetry','bms_extended','P3',3,self.now-100,123,'x'*10000,state))
    def ack(self,p,mid='a1',stamp=None,status='accepted'):
        with sqlite3.connect(p) as db:db.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',(mid,'general_asset_telemetry','bms_extended',status,self.now-2 if stamp is None else stamp,4,123,self.now-20,1))
    def snapshot(self):return e.Evidence().snapshot(self.live,self.sd,'*.db',self.now)
    def test_live_and_sd_acceptance_separate(self):
        self.ack(self.live,'a');self.ack(self.archive,'b');v=self.snapshot()
        self.assertEqual(v['live']['accepted'],[1,4,123]);self.assertEqual(v['archive']['accepted'],[1,4,123]);self.assertEqual(v['total']['accepted'],[2,8,246])
    def test_id_overlap_deduplicated(self):
        for p in (self.live,self.archive):self.message(p);self.ack(p)
        v=self.snapshot();self.assertEqual(len(v['pending']),1);self.assertEqual(v['pending'][0]['location'],'LIVE+SD')
        self.assertEqual(v['total']['accepted'][0],1)
    def test_duplicate_ack_not_fresh_accepted(self):
        self.ack(self.archive,status='already_processed');v=self.snapshot()
        self.assertEqual(v['archive']['accepted'][0],0);self.assertEqual(v['archive']['duplicate'][0],1)
    def test_unknown_queue_not_rendered_as_pending(self):
        self.assertEqual(e.sub_status(3,0,False,True),'ACKs observed; queue partial')
    def test_success_and_backlog_both_visible(self):
        self.assertEqual(e.sub_status(3,2,True,True),'ACKs observed; backlog present')
    def test_no_window_ack_does_not_claim_rejection(self):
        self.assertEqual(e.sub_status(0,0,True,True),'no ACK in 60s; no pending observed')
    def test_missing_database_not_created(self):
        p=self.root/'missing.db'
        with self.assertRaises(RuntimeError):e.query(p,'SELECT 1')
        self.assertFalse(p.exists())
    def test_read_only_enforced(self):
        with self.assertRaises(sqlite3.OperationalError):e.query(self.live,'DELETE FROM outbox_messages')
    def test_queries_are_covering_indexes(self):
        self.message(self.archive)
        sqls=["SELECT rowid,state,created_epoch,payload_bytes FROM outbox_messages INDEXED BY idx_outbox_stats WHERE state='pending'",
              'SELECT rowid,stream,substream FROM outbox_messages INDEXED BY idx_outbox_stream',
              "SELECT rowid,message_id FROM outbox_messages INDEXED BY idx_outbox_spill WHERE state='pending'"]
        with sqlite3.connect(self.archive) as db:
            for sql in sqls:self.assertIn('COVERING INDEX',str(db.execute('EXPLAIN QUERY PLAN '+sql).fetchall()))
        self.assertEqual(e.indexed_pending(self.archive)[0]['message_id'],'m1')
    def test_more_than_old_12000_row_limit(self):
        with sqlite3.connect(self.archive) as db:
            db.executemany('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?,?)',((str(i),'general_asset_telemetry','bms_extended','P3',3,self.now,1,'','retry') for i in range(12500)))
        self.assertEqual(len(e.indexed_pending(self.archive)),12500)
    def test_receipt_window_excludes_old_and_future(self):
        self.ack(self.archive,'old',self.now-61);self.ack(self.archive,'future',self.now+1);self.ack(self.archive,'valid')
        self.assertEqual(len(e.receipts(self.archive,self.now)[0]),1)
    def test_missing_receipt_table_explicit(self):
        with sqlite3.connect(self.archive) as db:db.execute('DROP TABLE delivery_receipts_v1')
        self.assertEqual(e.receipts(self.archive,self.now),([],False))
    def test_missing_indexes_partial_not_zero(self):
        self.message(self.archive)
        with sqlite3.connect(self.archive) as db:db.execute('DROP INDEX idx_outbox_stats')
        v=self.snapshot();self.assertFalse(v['queue_complete']);self.assertIn(str(self.archive),v['queue_errors'])
    def test_render_labels_both_delivery_paths(self):
        self.message(self.archive);self.ack(self.live);v=self.snapshot()
        text='\n'.join(dashboard.render(v,{'status_generated_utc':'2026-11-03T09:00:00Z'}, {},{},None,'active',self.now,width=220))
        for label in ('SD ACCEPTED','TOTAL ACCEPTED','ACKs observed; backlog present'):self.assertIn(label,text)
        self.assertNotIn('pending unique',text)
    def test_monitor_does_not_alter_queue_rows(self):
        self.message(self.archive)
        def rows():
            with sqlite3.connect(self.archive) as db:return db.execute('SELECT * FROM outbox_messages').fetchall()
        before=rows();self.snapshot();self.assertEqual(before,rows())
    def test_unchanged_archive_uses_cache_but_live_refreshes(self):
        self.message(self.archive)
        monitor=e.Evidence()
        with patch.object(e,'indexed_pending',wraps=e.indexed_pending) as scan:
            monitor.snapshot(self.live,self.sd,'*.db',self.now)
            self.assertEqual(scan.call_count,2)
            monitor.last_scan=-1e20
            monitor.snapshot(self.live,self.sd,'*.db',self.now+20)
            self.assertEqual(scan.call_count,3)
            self.message(self.archive,'new')
            monitor.last_scan=-1e20
            v=monitor.snapshot(self.live,self.sd,'*.db',self.now+40)
            self.assertEqual(scan.call_count,5);self.assertEqual(len(v['pending']),2)
    def test_once_command_end_to_end(self):
        import contextlib,io,sys
        self.message(self.archive);self.ack(self.archive);self.ack(self.live,'live-a')
        config=self.root/'config.json'
        config.write_text(json.dumps({'outbox':{'path':str(self.live)},'overflow_archive':{'archive_dir':str(self.sd)},'overflow_recovery':{'archive_glob':'*.db'}}))
        out=io.StringIO()
        with patch.object(sys,'argv',['dashboard.py','--once','--config',str(config)]),patch.object(dashboard.m,'run_command',return_value='active'),contextlib.redirect_stdout(out):
            self.assertEqual(dashboard.main(),0)
        text=out.getvalue()
        self.assertIn('SD ACCEPTED',text);self.assertIn('ACKs observed; backlog present',text)
        self.assertIn('m1 | SD',text);self.assertNotIn('pending unique',text)
    def test_rate_cap_truncation_is_unavailable(self):
        with sqlite3.connect(self.archive) as db:
            db.executemany('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',((str(i),'s','sub','accepted',self.now-1,1,1,self.now-2,0) for i in range(5000)))
        with self.assertRaisesRegex(RuntimeError,'truncate'):e.receipts(self.archive,self.now)

if __name__=='__main__':unittest.main()
