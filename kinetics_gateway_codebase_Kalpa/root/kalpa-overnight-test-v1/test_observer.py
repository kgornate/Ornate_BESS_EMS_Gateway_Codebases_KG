import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('observer', Path(__file__).with_name('test.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Tests(unittest.TestCase):

    def db(self, path, rows):
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE outbox_messages (message_id TEXT,stream TEXT,substream TEXT,state TEXT,payload_bytes INTEGER,created_epoch REAL,ack_status TEXT,acknowledged_utc TEXT,payload_json TEXT)')
            c.executemany('INSERT INTO outbox_messages VALUES (?,?,?,?,?,?,?,?,?)', rows)
        return path

    def row(self, mid, state='retry', ack=None):
        return (mid, 'general_asset_telemetry', 'ems_system', state, 100, 1000, ack, '2026-11-02T10:00:00Z', json.dumps({'record_count': 4}))

    def test_inventory_deduplicates_copies_and_prior_ack(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = root / 'archives'
            a.mkdir()
            live = self.db(root / 'live.db', [self.row('copy'), self.row('accepted', 'acked', 'accepted')])
            archive = self.db(a / 'central_sync_overflow_one.db', [self.row('copy'), self.row('accepted'), self.row('archiveonly'), self.row('dead', 'dead')])
            before = (live.read_bytes(), archive.read_bytes())
            r, ids, acked, details = m.database_inventory(live, a, '*.db', now=1060)
            self.assertEqual(ids, {'copy', 'archiveonly'})
            self.assertEqual(r['unique_unacked'], 2)
            self.assertEqual((r['live_unacked'], r['archive_unacked'], r['logical_bytes']), (1, 2, 200))
            self.assertEqual(r['dead_ids'], 1)
            self.assertEqual(r['oldest_age_sec'], 60)
            self.assertEqual(before, (live.read_bytes(), archive.read_bytes()))

    def test_inventory_limit_fails_instead_of_partial_zero(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = root / 'archives'
            a.mkdir()
            live = self.db(root / 'live.db', [self.row('x')])
            with self.assertRaises(RuntimeError):
                m.database_inventory(live, a, '*.db', max_rows=0)
            with self.assertRaises(RuntimeError):
                m.database_inventory(live, a, '*.db', budget=-1)

    def test_missing_database_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / 'absent.db'
            with self.assertRaises(RuntimeError):
                m.database_inventory(p, root, 'no-matches')
            self.assertFalse(p.exists())

    def test_live_ack_counts_native_records_and_dedups_seen_ids(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.db(Path(d) / 'live.db', [self.row('a', 'acked', 'accepted'), self.row('b', 'acked', 'already_processed')])
            before = p.read_bytes()
            rows = m.recent_acks(p, '2026-11-02T09:00:00Z', {'a'})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['records'], 4)
            self.assertEqual(rows[0]['status'], 'already_processed')
            self.assertEqual(before, p.read_bytes())

    def test_producer_delta_does_not_turn_reset_into_negative_rate(self):
        a = {'producers': {'s': {'started_utc': 'same', 'message_count': 4, 'record_count': 20, 'failure_count': 0}}}
        b = {'producers': {'s': {'started_utc': 'same', 'message_count': 8, 'record_count': 40, 'failure_count': 1}}}
        self.assertEqual(m.producer_deltas(a, b)['s'], {'available': True, 'message_count': 4, 'record_count': 20, 'failure_count': 1})
        b['producers']['s']['started_utc'] = 'new'
        self.assertFalse(m.producer_deltas(a, b)['s']['available'])
if __name__ == '__main__':
    unittest.main()
