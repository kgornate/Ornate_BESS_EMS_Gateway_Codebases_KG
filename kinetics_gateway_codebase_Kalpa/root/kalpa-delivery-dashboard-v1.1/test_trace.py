from types import SimpleNamespace as N
import importlib.util
from pathlib import Path
import sqlite3
import json
import tempfile
import unittest
import batch_trace as t
import install as installer

class Tests(unittest.TestCase):
 def fixture(self):
  items=[N(message_id='a',stream='fast_bess_telemetry',substream='bess_fast',payload_bytes=100),
         N(message_id='b',stream='general_asset_telemetry',substream='bms_extended',payload_bytes=200),
         N(message_id='c',stream='general_asset_telemetry',substream='bms_extended',payload_bytes=200)]
  return N(request_id='req1',items=items,json_bytes=b'example-body')
 def test_mixed_membership_exact_ids_and_counts(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'trace.db';t.record_batch_trace(self.fixture(),N(status_code=200,text='',exception=None),'live.db',p)
   with sqlite3.connect(p) as db:r=db.execute('SELECT groups_json,members_json,http_status,message_count,payload_bytes FROM batch_trace').fetchone()
   self.assertEqual(json.loads(r[0]),[{'stream':'fast_bess_telemetry','substream':'bess_fast','messages':1},{'stream':'general_asset_telemetry','substream':'bms_extended','messages':2}])
   self.assertEqual([v['message_id'] for v in json.loads(r[1])],['a','b','c']);self.assertEqual(r[2:],(200,3,12))
 def test_timeout_is_not_invented_http_status_and_reason_is_filtered(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'trace.db';t.record_batch_trace(self.fixture(),N(status_code=0,text='',exception='WriteTimeout: client_secret=hidden Bearer private-token'),'archive.db',p)
   with sqlite3.connect(p) as db:code,detail=db.execute('SELECT http_status,detail FROM batch_trace').fetchone()
   self.assertEqual(code,0);self.assertIn('WriteTimeout',detail);self.assertNotIn('hidden',detail);self.assertNotIn('private-token',detail)
 def test_patch_preserves_existing_code_and_is_idempotent(self):
  path=Path('/workspace/scratch/a5b1af15f50d/dashboard-update/gateway/uploader.py')
  # A real copied source when available, otherwise a minimal structurally valid fixture.
  source=path.read_text() if path.exists() else 'from .batcher import OutboxBatcher, PreparedBatch\nclass U:\n    async def run(self):\n        self.counters.last_http_status = response.status_code\n'
  updated=installer.patch_source(source)
  self.assertEqual(installer.patch_source(updated),updated)
  self.assertEqual(updated.replace('from .batch_trace import record_batch_trace\n','').replace(installer.HOOK,''),source)
 def test_unknown_uploader_fails_without_patch(self):
  with self.assertRaises(RuntimeError):installer.patch_source('unknown source')
if __name__=='__main__':unittest.main()
