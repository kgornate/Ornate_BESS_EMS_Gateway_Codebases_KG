import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('benchmark', Path(__file__).with_name('benchmark.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BenchmarkTests(unittest.TestCase):
    def test_sizes_rates_and_bins(self):
        m = b.Meter()
        for sec, size in [(0, 10), (0, 30), (2, 100)]:
            m.add(sec, 4, size, 8)
        r = m.result(10)
        self.assertEqual(r['messages'], 3)
        self.assertEqual(r['average_records_per_sec'], 1.2)
        self.assertEqual(r['p95_message_bytes'], 100)
        self.assertEqual(r['peak_fixed_1s_messages'], 2)
        self.assertEqual(r['logical_json_bytes'], 140)

    def run_synthetic(self, missing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root/'outbox.db'
            conn = sqlite3.connect(db)
            conn.executescript('''CREATE TABLE outbox_messages (
                stream TEXT, substream TEXT, created_epoch REAL,
                payload_json TEXT, payload_bytes INTEGER, state TEXT);
                CREATE TABLE sequences (stream TEXT, substream TEXT, last_sequence INTEGER);''')
            conn.execute('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?)',
                         ('fast_bess_telemetry', 'bess_fast', time.time()-100, '{"record_count":99}', 19, 'pending'))
            conn.execute('INSERT INTO sequences VALUES (?,?,?)', ('fast_bess_telemetry', 'bess_fast', 1))
            conn.commit()
            cfg = root/'config.json'
            cfg.write_text(json.dumps(dict(enabled=True, outbox=dict(path=str(db)),
                fast_bess=dict(enabled=True), general_assets=dict(enabled=True),
                gateway_health=dict(enabled=True))))
            calls = []
            def service(args, **kwargs):
                calls.append(args)
                if args[1] == 'start':
                    now = time.time()
                    for stream, sub, records, state in [
                        ('fast_bess_telemetry', 'bess_fast', 4, 'acked'),
                        ('general_asset_telemetry', 'bms_extended', 12, 'pending'),
                        ('gateway_health', 'gateway', 1, 'retry')]:
                        payload = json.dumps({'record_count': records})
                        conn.execute('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?)',
                                     (stream, sub, now, payload, len(payload.encode()), state))
                        old = conn.execute('SELECT last_sequence FROM sequences WHERE stream=? AND substream=?', (stream, sub)).fetchone()
                        if old:
                            conn.execute('UPDATE sequences SET last_sequence=last_sequence+1 WHERE stream=? AND substream=?', (stream, sub))
                        else:
                            conn.execute('INSERT INTO sequences VALUES (?,?,1)', (stream, sub))
                    if missing:
                        conn.execute('UPDATE sequences SET last_sequence=last_sequence+1 WHERE stream=?', ('gateway_health',))
                    conn.commit()
                return type('Result', (), {'returncode': 0})()
            argv = ['benchmark.py', '--config', str(cfg), '--output', str(root/'results'),
                    '--minutes', '0.005', '--startup-seconds', '0.1']
            with patch.object(sys, 'argv', argv), patch.object(b.subprocess, 'run', service):
                if missing:
                    with self.assertRaises(SystemExit):
                        b.main()
                else:
                    b.main()
            report = json.loads((root/'results/report.json').read_text())
            total = next(r for r in report['results'] if r['phase']=='all' and r['slot']=='TOTAL')
            self.assertEqual(total['messages'], 3)
            self.assertEqual(total['records'], 17)
            self.assertEqual(report['capture_counts_reconciled'], not missing)
            self.assertEqual({r['slot'] for r in report['results']} - {'TOTAL'},
                             {'S'+str(i) for i in range(1,11)})
            self.assertEqual([a[1] for a in calls if a[1] != 'is-active'], ['stop','start','stop','stop'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM outbox_messages').fetchone()[0], 4)
            conn.close()

    def test_all_stream_capture_preserves_queue_and_counts_acked(self):
        self.run_synthetic()

    def test_sequence_mismatch_marks_incomplete(self):
        self.run_synthetic(missing=True)


if __name__ == '__main__':
    unittest.main()
