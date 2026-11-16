import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import sys
from datetime import datetime, timezone
import unittest

spec=importlib.util.spec_from_file_location('dashboard',Path(__file__).with_name('dashboard.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

class Tests(unittest.TestCase):
    def test_counter_delta_uses_status_time_and_holds_cached_samples(self):
        r=d.Rates()
        a={'started_utc':'same','status_generated_utc':'2026-10-30T10:00:00Z','uploader':{'request_count':2,'accepted_count':10,'already_processed_count':1,'bytes_sent_uncompressed':1000}}
        self.assertIsNone(r.update(a))
        b={**a,'status_generated_utc':'2026-10-30T10:00:02Z','uploader':{'request_count':4,'accepted_count':13,'already_processed_count':2,'bytes_sent_uncompressed':3048}}
        self.assertEqual(r.update(b),(1,1.5,.5,1024))
        self.assertEqual(r.update(b),(1,1.5,.5,1024))

    def test_restart_and_decreasing_counters_do_not_make_negative_rates(self):
        r=d.Rates();a={'started_utc':'a','status_generated_utc':'2026-10-30T10:00:00Z','uploader':{'request_count':10}}
        r.update(a)
        self.assertIsNone(r.update({**a,'started_utc':'b','status_generated_utc':'2026-10-30T10:00:02Z'}))
        self.assertIsNone(r.update({**a,'started_utc':'b','status_generated_utc':'2026-10-30T10:00:04Z','uploader':{'request_count':1}}))
        self.assertIsNone(r.update({}))

    def test_safe_read_rejects_missing_corrupt_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'status.json'
            self.assertIsNotNone(d.load_json(p)[1]);p.write_text('{')
            self.assertIsNotNone(d.load_json(p)[1]);p.write_text('[]')
            self.assertIsNotNone(d.load_json(p)[1]);p.write_bytes(b' '* (2*1024*1024+1))
            self.assertIsNotNone(d.load_json(p)[1])

    def test_database_retained_counts_no_creation_or_data_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'outbox.db'
            self.assertEqual(d.s2_retained(p)[0],{});self.assertFalse(p.exists())
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE outbox_messages (stream TEXT, substream TEXT, state TEXT, acknowledged_utc TEXT)')
                db.executemany('INSERT INTO outbox_messages VALUES (?,?,?,?)',[
                    ('general_asset_telemetry','combiner_box_meter','acked','2026-10-30T10:00:00Z'),
                    ('general_asset_telemetry','combiner_box_meter','retry',None),
                    ('general_asset_telemetry','ems_system','acked','2026-10-30T10:00:01Z'),
                    ('fast_bess_telemetry','bess_fast','acked','2026-10-30T10:00:00Z')])
            before=p.read_bytes();rows,error=d.s2_retained(p)
            self.assertIsNone(error);self.assertEqual(rows['combiner_box_meter']['acked'],1)
            self.assertEqual(rows['combiner_box_meter']['retry'],1)
            self.assertNotIn('bess_fast',rows);self.assertEqual(before,p.read_bytes())

    def test_query_budget_interrupts_without_modifying_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'outbox.db'
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE outbox_messages (stream TEXT,substream TEXT,state TEXT,acknowledged_utc TEXT)')
                db.executemany('INSERT INTO outbox_messages VALUES (?,?,?,?)',[('general_asset_telemetry','x','pending',None)]*3000)
            rows,error=d.s2_retained(p,budget=-1)
            self.assertEqual(rows,{});self.assertEqual(error,'query budget exceeded')
            with sqlite3.connect(p) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox_messages').fetchone()[0],3000)

    def test_http_200_does_not_hide_per_stream_rejections(self):
        now=1793354400
        status={'started_utc':'2026-10-30T09:00:00Z','status_generated_utc':'2026-10-30T10:00:00Z',
            'uploader':{'request_success_count':1,'last_http_status':200},
            'stream_backend_ack':{'general_asset_telemetry':{'retryable_rejected_count':5,'last_backend_result':'retryable_rejected','last_error':'unsupported_stream'}}}
        text=d.draw({'general_assets':{'enabled':True}},status,{}, {},{},None,'waiting',{},None,{},now,'active',[],220,now)
        self.assertIn('retryable_rejected',text);self.assertIn('BACKEND REJECTION: S2: unsupported_stream',text)
        self.assertIn('HTTP 2xx alone is not a message ACK',text)

    def test_stale_disabled_and_no_evidence_are_distinct(self):
        self.assertEqual(d.stream_state({'solis':{'enabled':False}},'solis',{},False),'DISABLED')
        self.assertEqual(d.stream_state({},'general_assets',{},True),'STALE STATUS')
        self.assertEqual(d.stream_state({},'general_assets',{},False),'no ACK observed')

    def test_sensitive_fields_and_terminal_controls_filtered(self):
        text=d.clean('Bearer my-private-token client_secret="secret-value" password=hidden \x1b[31mred')
        self.assertNotIn('my-private-token',text);self.assertNotIn('secret-value',text)
        self.assertNotIn('hidden',text);self.assertNotIn('\x1b',text)
        self.assertNotIn('a-password',d.clean('https://user:a-password@example.test/ingest'))

    def test_archive_metrics_are_file_sizes_not_fabricated_message_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'central_sync_overflow_20261030T10.db';p.write_bytes(b'1234')
            Path(str(p)+'-wal').write_bytes(b'12');Path(str(p)+'.replayed.json').write_text('{}')
            result=d.archive_summary(folder)
            self.assertEqual((result['files'],result['bytes'],result['markers']),(1,6,1))
            self.assertNotIn('message_count',result)

    def test_once_cli_uses_existing_status_and_leaves_inputs_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);status=root/'status.json';config=root/'config.json'
            stamp=datetime.now(timezone.utc).isoformat()
            status.write_text(json.dumps({'status_generated_utc':stamp,'started_utc':stamp,
                'uploader':{'accepted_count':158,'last_http_status':200,'missing_ack_count':0},
                'stream_backend_ack':{'general_asset_telemetry':{'acked_count':117,'last_backend_result':'accepted'}}}))
            config.write_text(json.dumps({'status_file':str(status),
                'overflow_archive':{'archive_dir':str(root/'absent-archives')},
                'overflow_recovery':{'status_file':str(root/'absent-recovery'),'uploader_status_file':str(root/'absent-upload')},
                'outbox':{'path':str(root/'absent.db')}}))
            before=(config.read_bytes(),status.read_bytes())
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('dashboard.py')),
                '--config',str(config),'--backend',str(root),'--once','--no-logs','--details'],
                capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('accepted 158',result.stdout)
            self.assertIn('RECENT DELTAS  unavailable',result.stdout)
            self.assertIn('combiner_box_meter',result.stdout)
            self.assertEqual(before,(config.read_bytes(),status.read_bytes()))
            self.assertFalse((root/'absent.db').exists())

    def metric_fixture(self, root, rows):
        p=root/'metrics.db'
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE outbox_messages (message_id TEXT,stream TEXT,substream TEXT,ack_status TEXT,acknowledged_utc TEXT,payload_bytes INTEGER,payload_json TEXT,state TEXT)')
            db.executemany('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?,?,?)',rows)
        return p

    def test_minute_counts_use_ack_time_split_duplicates_and_preserve_stream_units(self):
        now=d.epoch('2026-10-30T10:00:00Z')
        rows=[
            ('a','fast_bess_telemetry','bess_fast','accepted','2026-10-30T09:59:55Z',1000000,json.dumps({'record_count':4,'created_at_utc':'2026-10-26T00:00:00Z'}),'acked'),
            ('b','general_asset_telemetry','bms_extended','accepted','2026-10-30T09:59:59Z',2000000,json.dumps({'record_count':6}),'acked'),
            ('c','general_asset_telemetry','combiner_box_meter','accepted','2026-10-30T09:59:00.500000Z',500,json.dumps({'record_count':7}),'acked'),
            ('d','fast_bess_telemetry','bess_fast','already_processed','2026-10-30T09:59:58Z',1000000,json.dumps({'record_count':4}),'acked'),
            ('e','fast_bess_telemetry','bess_fast','accepted','2026-10-30T09:58:59Z',99,json.dumps({'record_count':9}),'acked'),
            ('f','fast_bess_telemetry','bess_fast','accepted','2026-10-30T10:00:00.500000Z',99,json.dumps({'record_count':9}),'acked'),
            ('g','fast_bess_telemetry','bess_fast',None,None,99,json.dumps({'record_count':9}),'retry')]
        with tempfile.TemporaryDirectory() as folder:
            p=self.metric_fixture(Path(folder),rows);before=p.read_bytes()
            result,error=d.ack_window(p,now=now)
            self.assertIsNone(error);self.assertEqual(result['accepted'],[3,17,3000500])
            self.assertEqual(result['duplicate'],[1,4,1000000])
            self.assertEqual(result['streams']['general_asset_telemetry'],[2,13,2000500])
            self.assertEqual(result['substreams']['combiner_box_meter'],[1,7,500])
            self.assertEqual(before,p.read_bytes())
            display='\n'.join(d.minute_lines(result))
            self.assertIn('17 records/min',display);self.assertIn('3.001 MB/min',display)
            self.assertIn('not new ingestion',display)

    def test_minute_incomplete_or_unknown_ack_evidence_is_not_shown_as_zero(self):
        now=d.epoch('2026-10-30T10:00:00Z')
        row=('a','general_asset_telemetry','bms_extended','accepted','2026-10-30T09:59:55Z',100,json.dumps({'record_count':4}),'acked')
        with tempfile.TemporaryDirectory() as folder:
            p=self.metric_fixture(Path(folder),[row])
            result,error=d.ack_window(p,now=now,max_bytes=1)
            self.assertIsNone(result);self.assertIn('size limit',error)
            self.assertIn('unavailable',d.minute_lines(result,error)[0])
            result,error=d.ack_window(p,now=now,budget=-1)
            self.assertIsNone(result);self.assertIsNotNone(error)
            with sqlite3.connect(p) as db:db.execute("UPDATE outbox_messages SET ack_status='unexpected'")
            result,error=d.ack_window(p,now=now)
            self.assertIsNone(result);self.assertIn('unrecognized',error)

    def test_minute_empty_valid_window_and_record_list_fallback(self):
        now=d.epoch('2026-10-30T10:00:00Z')
        with tempfile.TemporaryDirectory() as folder:
            p=self.metric_fixture(Path(folder),[])
            result,error=d.ack_window(p,now=now)
            self.assertIsNone(error);self.assertEqual(result['accepted'],[0,0,0])
            with sqlite3.connect(p) as db:
                db.execute('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?,?,?)',('a','gateway_health','gateway','accepted','2026-10-30T09:59:59Z',100,json.dumps({'records':[{},{}]}),'acked'))
            result,error=d.ack_window(p,now=now)
            self.assertIsNone(error);self.assertEqual(result['accepted'],[1,2,100])

    def test_paths_use_current_configuration_with_fallbacks(self):
        p=d.defaults({'status_file':'relative/status.json','outbox':{'path':'db.sqlite3'}},Path('/backend'))
        self.assertEqual(p['live'],Path('/backend/relative/status.json'))
        self.assertEqual(p['outbox'],Path('/backend/db.sqlite3'))
        self.assertEqual(p['replay_upload'],Path('/var/lib/ornate-central-sync/overflow_recovery_uploader_status.json'))

if __name__=='__main__':unittest.main()
