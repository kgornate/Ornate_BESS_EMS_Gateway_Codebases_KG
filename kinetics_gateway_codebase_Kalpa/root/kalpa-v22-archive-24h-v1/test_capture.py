import unittest
from capture import Segments,Receipts,Generation,fresh_status
class CaptureTests(unittest.TestCase):
    def test_restart_segments_do_not_subtract_reset_counters(self):
        s=Segments(('n',));s.observe('a',{'n':100},'2026-11-12T10:00:00+00:00');s.observe('a',{'n':120},'2026-11-12T10:01:00+00:00')
        s.observe('b',{'n':1},'2026-11-12T10:02:00+00:00');s.observe('b',{'n':6},'2026-11-12T10:03:00+00:00')
        r=s.report();self.assertEqual(r['observed_counter_deltas']['n'],25);self.assertEqual(r['covered_seconds'],120)
    def test_counter_reset_without_instance_change_starts_segment(self):
        s=Segments(('n',));s.observe('a',{'n':100},'2026-11-12T10:00:00+00:00');s.observe('a',{'n':0},'2026-11-12T10:01:00+00:00');s.observe('a',{'n':2},'2026-11-12T10:02:00+00:00')
        self.assertEqual(s.report()['observed_counter_deltas']['n'],2);self.assertEqual(s.resets,1)
    def test_receipt_dedup_expiry_and_old_rows(self):
        r=Receipts();row={'message_id':'id','ack_epoch':1000,'ack_status':'accepted','payload_bytes':42,'stream':'s','substream':'x'}
        r.observe([row,row],1001,0);self.assertEqual(r.bytes,42)
        r.observe([row],1700,0);self.assertEqual(r.bytes,42);self.assertEqual(len(r.seen),0)
    def test_receipt_memory_bound(self):
        r=Receipts()
        for i in range(3):
            r.observe([{'message_id':str(i*5000+j),'ack_epoch':1000,'ack_status':'accepted','payload_bytes':1,'stream':'s','substream':'x'} for j in range(5000)],1001,0)
        self.assertEqual(len(r.seen),10000);self.assertGreater(r.bounds,0)
    def test_generation_reset(self):
        g=Generation()
        for n in (100,120,0,5):g.observe([{'stream':'s','substream':'x','messages':n,'payload_bytes':n*10}])
        self.assertEqual(g.bytes,250);self.assertEqual(g.resets,1)
    def test_stale_status_not_fresh(self):
        self.assertFalse(fresh_status({'running':True,'status_generated_utc':'2026-11-12T10:00:00Z'},'2026-11-12T10:00:20+00:00'))
        self.assertTrue(fresh_status({'running':True,'status_generated_utc':'2026-11-12T10:00:15Z'},'2026-11-12T10:00:20+00:00'))
if __name__=='__main__':unittest.main()
