import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import monitor_evidence as e
import dashboard
SCHEMA='''CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,stream TEXT,substream TEXT,priority TEXT,priority_rank INTEGER,created_epoch REAL,payload_bytes INTEGER,payload_json TEXT,state TEXT);
CREATE INDEX idx_outbox_stats ON outbox_messages(state,priority,created_epoch,payload_bytes);
CREATE INDEX idx_outbox_stream ON outbox_messages(stream,substream,created_epoch);
CREATE INDEX idx_outbox_spill ON outbox_messages(state,priority_rank DESC,created_epoch,message_id);
CREATE TABLE delivery_receipts_v1(message_id TEXT PRIMARY KEY,stream TEXT,substream TEXT,ack_status TEXT,ack_epoch REAL,record_count INTEGER,payload_bytes INTEGER,created_epoch REAL,attempt_count INTEGER);'''
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.sd=self.root/'sd';self.sd.mkdir();self.live=self.root/'live.db';self.now=time.time();self.db(self.live)
 def db(self,p,mid=None,ack=None):
  with closing(sqlite3.connect(p))as d,d:
   d.executescript(SCHEMA)
   if mid:d.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,'general_asset_telemetry','bms_extended','P3',3,self.now-100,100,'large payload','retry'))
   if ack:d.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',(ack,'general_asset_telemetry','bms_extended','accepted',self.now-2,4,100,self.now-100,1))
 def snap(self,m=None,now=None):return (m or e.Evidence()).snapshot(self.live,self.sd,'*.db',self.now if now is None else now)
 def test_more_than_127_archives(self):
  for n in range(160):self.db(self.sd/f'{n:03d}.db',str(n))
  m=e.Evidence()
  for _ in range(5):m.last_scan=-1e20;v=self.snap(m)
  self.assertEqual(v['queue_coverage']['total'],161);self.assertTrue(v['queue_complete']);self.assertEqual(v['queue_summary']['sd'],160)
  self.assertIn('v1.6','\n'.join(dashboard.render(v,{}, {},{},None,'active',self.now)))
 def test_copy_count_honest_and_ack_dedup(self):
  p=self.sd/'a.db';self.db(p,'same','ack');
  with closing(sqlite3.connect(self.live))as d,d:
   d.execute('INSERT INTO outbox_messages VALUES(?,?,?,?,?,?,?,?,?)',('same','general_asset_telemetry','bms_extended','P3',3,self.now-10,100,'','pending'))
   d.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',('ack','general_asset_telemetry','bms_extended','accepted',self.now-1,4,100,self.now-100,1))
  v=self.snap();self.assertEqual(v['queue_summary']['live'],1);self.assertEqual(v['queue_summary']['sd'],1);self.assertEqual(v['total']['accepted'][0],1)
  text='\n'.join(dashboard.render(v,{}, {},{},None,'active',self.now));self.assertIn('not deduplicated',text);self.assertNotIn('unique pending',text)
 def test_compact_cache(self):
  rows=[dict(message_id=str(i),stream='s',substream='x',state='pending',payload_bytes=10,created_epoch=i)for i in range(20000)]
  v=e.summarize_pending(rows);self.assertEqual(v['count'],20000);self.assertEqual(len(v['samples']),5);self.assertEqual(v['bytes'],200000)
 def test_expired_cached_ack_disappears(self):
  self.db(self.sd/'a.db',ack='a');m=e.Evidence();self.assertEqual(self.snap(m)['archive']['accepted'][0],1)
  self.assertEqual(self.snap(m,self.now+70)['archive']['accepted'][0],0)
 def test_partial_does_not_kill_live_ack(self):
  self.db(self.sd/'a.db',mid='x')
  with closing(sqlite3.connect(self.live))as d,d:d.execute('INSERT INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',('a','s','x','accepted',self.now-1,1,20,self.now-10,1))
  original=e.indexed_pending
  def scan(p):
   if p!=self.live:raise RuntimeError('read budget exceeded')
   return original(p)
  with patch.object(e,'indexed_pending',side_effect=scan):v=self.snap()
  self.assertFalse(v['queue_complete']);self.assertEqual(v['live']['accepted'][0],1)
  text='\n'.join(dashboard.render(v,{}, {},{},None,'active',self.now));self.assertIn('QUEUE CURRENT COUNTS: unavailable',text)
 def test_read_only(self):
  with self.assertRaises(sqlite3.OperationalError):e.query(self.live,'DELETE FROM outbox_messages')
 def test_missing_does_not_create(self):
  p=self.root/'no.db'
  with self.assertRaises(RuntimeError):e.query(p,'SELECT 1')
  self.assertFalse(p.exists())
 def test_changed_archive_refresh(self):
  p=self.sd/'a.db';self.db(p,'x');m=e.Evidence();self.assertEqual(self.snap(m)['queue_summary']['sd'],1)
  with closing(sqlite3.connect(p))as d,d:d.execute("DELETE FROM outbox_messages")
  m.last_scan=-1e20;self.assertEqual(self.snap(m,self.now+20)['queue_summary']['sd'],0)
if __name__=='__main__':unittest.main(verbosity=2)
