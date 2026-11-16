import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('dashboard', Path(__file__).with_name('dashboard.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class DashboardTests(unittest.TestCase):
    def test_aggregate_counts_once_and_percentile(self):
        rows = [('general_asset_telemetry','bms_extended', 995, 100, 4),
                ('general_asset_telemetry','pcs_extended', 998, 300, 6),
                ('fast_bess_telemetry','bess_fast', 999, 200, 4)]
        m = d.aggregate(rows,60)
        self.assertEqual(m['TOTAL','*']['messages'],3)
        self.assertEqual(m['TOTAL','*']['records'],14)
        self.assertEqual(m['TOTAL','*']['p95'],300)
        self.assertEqual(m['general_asset_telemetry','*']['bytes'],400)
        self.assertEqual(m['general_asset_telemetry','bms_extended']['records'],4)

    def test_readonly_window_includes_acked_excludes_old_and_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.db'
            c=sqlite3.connect(db)
            c.executescript('''CREATE TABLE sequences(stream TEXT,substream TEXT);
                CREATE TABLE outbox_messages(stream TEXT,substream TEXT,created_epoch REAL,
                payload_json TEXT,payload_bytes INTEGER,state TEXT);
                CREATE INDEX idx_outbox_stream ON outbox_messages(stream,substream,created_epoch);''')
            c.execute('INSERT INTO sequences VALUES (?,?)',('fast_bess_telemetry','bess_fast'))
            for stamp,state in [(900,'pending'),(990,'acked'),(995,'retry'),(1001,'pending')]:
                c.execute('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?)',
                    ('fast_bess_telemetry','bess_fast',stamp,'{"record_count":4}',18,state))
            c.commit()
            before=db.read_bytes()
            ro=d.connect(db)
            rows,capped=d.snapshot(ro,1000,60)
            self.assertEqual(len(rows),2)
            self.assertFalse(capped)
            self.assertEqual(sum(r[4] for r in rows),8)
            short,capped=d.snapshot(ro,1000,60,limit=1)
            self.assertTrue(capped)
            self.assertEqual(len(short),1)
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute('DELETE FROM outbox_messages')
            ro.close()
            c.close()
            self.assertEqual(before,db.read_bytes())

    def test_logs_filter_secrets_and_terminal_escape(self):
        text=d.clean('client_secret="very secret" token=abc Bearer xyz\x1b[2J\x00')
        for secret in ['very secret','abc','xyz','\x1b','\x00']:
            self.assertNotIn(secret,text)
        self.assertIn('[REDACTED]',text)

    def test_all_slots_and_substreams_visible_when_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'general_assets':{'enabled':True}}
            output=d.display(cfg,{},[],Path(tmp)/'absent.db','inactive','No entries','',60,d.time.monotonic(),0)
            for slot,section,stream in d.SLOTS:
                self.assertIn(slot+' '+stream,output)
            for sub in d.SUBS:
                self.assertIn(sub,output)
            self.assertIn('inactive',output)


if __name__=='__main__':
    unittest.main()
