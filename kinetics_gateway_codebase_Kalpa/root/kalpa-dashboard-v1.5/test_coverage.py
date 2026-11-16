import unittest,time,sqlite3
from unittest.mock import patch
from test_dashboard import Tests
import monitor_evidence as e
import dashboard
class CoverageTests(Tests):
    def render(self,v):return '\n'.join(dashboard.render(v,{}, {},{},None,'active',self.now,width=220))
    def test_partial_never_publishes_current_numeric_total(self):
        self.message(self.archive)
        with patch.object(e,'indexed_pending',side_effect=RuntimeError('read budget exceeded')):v=self.snapshot()
        text=self.render(v)
        self.assertIn('QUEUE CURRENT TOTAL: unavailable',text);self.assertNotIn('unique pending 0',text);self.assertIn('?/?',text)
    def test_last_complete_retained_after_archive_failure(self):
        self.message(self.archive);m=e.Evidence();first=m.snapshot(self.live,self.sd,'*.db',self.now)
        self.assertTrue(first['queue_complete']);m.last_scan=-1e20;self.message(self.archive,'new')
        original=e.indexed_pending
        def scan(p):
            if p==self.archive:raise RuntimeError('read budget exceeded')
            return original(p)
        with patch.object(e,'indexed_pending',side_effect=scan):v=m.snapshot(self.live,self.sd,'*.db',self.now+20)
        text=self.render(v);self.assertIn('LAST COMPLETE QUEUE: 1 unique',text);self.assertIn('historical, not current',text)
        self.assertEqual(v['last_complete_queue']['utc_epoch'],self.now)
    def test_failed_archive_attempt_is_recorded_for_fair_rotation(self):
        m=e.Evidence()
        with patch.object(e,'indexed_pending',side_effect=RuntimeError('read budget exceeded')):m.snapshot(self.live,self.sd,'*.db',self.now)
        self.assertEqual(m.last_attempt[str(self.archive)],self.now)
    def test_recovery_publishes_new_complete_count(self):
        self.message(self.archive);m=e.Evidence();m.snapshot(self.live,self.sd,'*.db',self.now);self.message(self.archive,'new');m.last_scan=-1e20
        v=m.snapshot(self.live,self.sd,'*.db',self.now+20);self.assertTrue(v['queue_complete']);self.assertEqual(v['last_complete_queue']['count'],2)
if __name__=='__main__':unittest.main()
