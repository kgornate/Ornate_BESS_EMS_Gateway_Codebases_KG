import sqlite3
from pathlib import Path
import tempfile
import unittest
import evidence

class EvidenceTests(unittest.TestCase):
    def test_generation_new_group_and_reset(self):
        a=[dict(stream='s2',substream='bms',messages=10,payload_bytes=100,first_epoch=1)]
        b=[dict(stream='s2',substream='bms',messages=15,payload_bytes=180,first_epoch=1),dict(stream='s2',substream='pcs',messages=2,payload_bytes=40,first_epoch=2)]
        rows=evidence.generation_delta(a,b)
        self.assertEqual(sum(r['bytes'] for r in rows),120)
        b[0]['first_epoch']=9
        with self.assertRaises(RuntimeError):evidence.generation_delta(a,b)

    def test_http_readonly_dedup_and_reason(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'outbox.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE transport_attempts(id,request_id,attempted_utc,message_count,payload_bytes,http_status,outcome,detail)')
                db.execute("INSERT INTO transport_attempts VALUES(1,'req','2026-11-02T11:00:00Z',2,500,0,'transport_failure','WriteTimeout')")
            before=path.read_bytes()
            rows=evidence.attempts(path,'2026-11-02',set())
            self.assertEqual(rows[0]['detail'],'WriteTimeout')
            self.assertEqual(evidence.attempts(path,'2026-11-02',{(1,'req')}),[])
            self.assertEqual(evidence.attempts(path,'2026-11-02',set(),after_id=1),[])
            self.assertEqual(path.read_bytes(),before)

    def test_config_omits_credentials(self):
        cfg={'backend':{'timeout_sec':30,'client_secret':'PRIVATE','base_url':'PRIVATE'},'uploader':{'max_messages_per_request':10}}
        text=str(evidence.config_summary(cfg))
        self.assertNotIn('PRIVATE',text)
        self.assertNotIn('secret',text)

if __name__=='__main__':unittest.main()
