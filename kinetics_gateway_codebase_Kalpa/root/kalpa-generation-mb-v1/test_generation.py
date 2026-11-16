import ast
from collections import deque
import json
from pathlib import Path
import sqlite3
import time
import unittest
from install import patch_source

ROOT=Path(__file__).resolve().parent

class Tests(unittest.TestCase):
    def counter_class(self):
        from install import METHOD
        source='class Box:\n'+METHOD
        ns={'time':time};exec(source,ns)
        box=ns['Box']();box.conn=sqlite3.connect(':memory:');box._init_generation_metrics()
        return box

    def test_durable_totals_not_removed_with_message_cleanup(self):
        box=self.counter_class()
        box.conn.execute('CREATE TABLE messages(id TEXT)')
        box.conn.execute("INSERT INTO messages VALUES('one')")
        box._add_generation_metrics('s1','fast',1,400)
        box.conn.commit()
        box.conn.execute('DELETE FROM messages');box.conn.commit()
        self.assertEqual(box.conn.execute('SELECT messages,payload_bytes FROM generation_metrics_v1').fetchone(),(1,400))

    def test_s2_multi_message_sum_and_transaction_rollback(self):
        box=self.counter_class()
        box._add_generation_metrics('s2','bms',3,2400);box.conn.commit()
        box._add_generation_metrics('s2','bms',2,1800);box.conn.rollback()
        self.assertEqual(box.conn.execute('SELECT messages,payload_bytes FROM generation_metrics_v1').fetchone(),(3,2400))

    def test_rate_window_growth_and_reset(self):
        tree=ast.parse((ROOT/'dashboard.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GenerationBytes')
        ns={'deque':deque};exec(compile(ast.Module(body=[cls],type_ignores=[]),'rate','exec'),ns)
        rate=ns['GenerationBytes']()
        def row(b,sub='bms',stamp=1):return {'stream':'s2','substream':sub,'payload_bytes':b,'first_epoch':stamp}
        self.assertIsNone(rate.update([row(100)],0))
        self.assertAlmostEqual(rate.update([row(1000100)],60)[0],1.0)
        # New substream adds real generation without resetting existing history.
        self.assertAlmostEqual(rate.update([row(1000100),row(1000000,'pcs')],65)[0],2e6*60/65/1e6)
        self.assertIsNone(rate.update([row(1,stamp=2)],70))

    def test_guarded_source_patch(self):
        fixture='''import time
class Box:
    def init(self):
        self._init_schema()
    def enqueue(self, message: LogicalMessage) -> bool:
        with self.lock:
            try:
                self.conn.commit()
                self._invalidate_stats_cache()
                return True
            except Exception:
                return False
    def enqueue_s2_atomic(self):
        with self.lock:
            try:
                self.conn.commit()
                self._invalidate_stats_cache()
                return emitted
            except Exception:
                raise
'''
        patched=patch_source(fixture)
        self.assertEqual(patch_source(patched),patched)
        self.assertIn('self._add_generation_metrics(stream, substream, len(emitted), total_size)\n                self.conn.commit()',patched)
        with self.assertRaises(RuntimeError):patch_source('unknown layout')

    def test_normal_enqueue_duplicate_not_counted(self):
        # Execute the real baseline enqueue method with the installed counter hook.
        baseline=ROOT.parent.parent/'dashboard-update/gateway/outbox.py'
        if not baseline.exists():self.skipTest('Full baseline source not bundled')
        tree=ast.parse(patch_source(baseline.read_text()))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef) and m.name=='enqueue' for m in n.body))
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='enqueue')
        ns={'LogicalMessage':object,'json':json,'sqlite3':sqlite3,'_iso_to_epoch':lambda x:1}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'enqueue','exec'),ns)
        box=self.counter_class()
        import threading
        box._lock=threading.Lock();box.capacity_status=lambda:{'can_accept':True}
        box._invalidate_stats_cache=lambda:None
        box.conn.execute('''CREATE TABLE outbox_messages(message_id TEXT PRIMARY KEY,gateway_id,site_id,stream,substream,sequence,priority,priority_rank,created_at_utc,created_epoch,payload_json,payload_bytes,state,next_attempt_epoch)''')
        class Message:
            message_id='id';gateway_id='gw';site_id='site';stream='s1';substream='fast';sequence=1;priority='P1';priority_rank=1;created_at_utc='now'
            def model_dump(self,mode):return {'value':'µ'}
        self.assertTrue(ns['enqueue'](box,Message()))
        self.assertFalse(ns['enqueue'](box,Message()))
        self.assertEqual(box.conn.execute('SELECT messages,payload_bytes FROM generation_metrics_v1').fetchone(),(1,len('{"value":"µ"}'.encode())))

if __name__=='__main__':unittest.main()
