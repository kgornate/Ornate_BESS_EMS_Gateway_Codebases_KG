import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from collections import defaultdict
import capture

class Tests(unittest.TestCase):
    def test_metadata_and_single_ack_are_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.db'
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,stream,substream,state,ack_status,acknowledged_utc,payload_bytes,payload_json)')
                body=json.dumps({'records':[{'value':'not-to-save'},{'value':55}]})
                db.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?)',('id','s2','bms','acked','accepted','2026-11-02T18:00:00Z',100,body))
            before=p.read_bytes()
            self.assertEqual(capture.metadata_ids(p),['id'])
            db=capture.readonly(p)
            try:row=capture.one_ack(db,'id')
            finally:db.close()
            self.assertEqual(row['records'],2)
            self.assertNotIn('not-to-save',json.dumps(row))
            self.assertEqual(before,p.read_bytes())
    def test_union_deduplicates_original_and_recovered(self):
        row={'message_id':'id','stream':'s2','substream':'bms','status':'accepted','records':4,'bytes':100}
        seen=set();totals=defaultdict(lambda:[0,0,0]);out=io.StringIO()
        self.assertTrue(capture.add(row,seen,totals,out))
        self.assertFalse(capture.add(row,seen,totals,out))
        self.assertEqual(totals[('s2','bms','accepted')],[1,4,100])
        self.assertEqual(len(out.getvalue().splitlines()),1)
    def test_partial_original_line_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rows.jsonl';p.write_text('{"message_id":"one"}\n{"message_id":')
            rows,offset=capture.original_rows(p,0)
            self.assertEqual(len(rows),1)
            with p.open('a') as f:f.write('"two"}\n')
            rows,_=capture.original_rows(p,offset)
            self.assertEqual(rows,[{'message_id':'two'}])
    def test_missing_source_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing.db'
            with self.assertRaises(sqlite3.OperationalError):capture.metadata_ids(p)
            self.assertFalse(p.exists())
if __name__=='__main__':unittest.main()
