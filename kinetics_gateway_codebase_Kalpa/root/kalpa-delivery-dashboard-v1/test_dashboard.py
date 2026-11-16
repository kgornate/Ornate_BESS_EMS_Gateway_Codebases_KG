import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).parent))
import dashboard as d

class Tests(unittest.TestCase):
 def test_generation_real_counters_hold_reset_and_rates(self):
  g=d.Generation();a={'started_utc':'one','last_poll_utc':'2026-11-02T10:00:00Z','message_count':10,'record_count':100}
  t=d.m.epoch(a['last_poll_utc']);self.assertIsNone(g.update('s',a,t))
  b={**a,'last_poll_utc':'2026-11-02T10:00:10Z','message_count':20,'record_count':300}
  self.assertEqual(g.update('s',b,t+10),(60,1200,10))
  self.assertIsNone(g.update('s',{**b,'started_utc':'new'},t+10))
 def test_query_does_not_create_or_modify_db(self):
  with tempfile.TemporaryDirectory() as f:
   p=Path(f)/'a.db'
   with self.assertRaises(RuntimeError):d.query(p,'SELECT 1')
   self.assertFalse(p.exists())
   with sqlite3.connect(p) as c:c.execute('CREATE TABLE x (v INTEGER)');c.execute('INSERT INTO x VALUES (1)')
   before=p.read_bytes();self.assertEqual(d.query(p,'SELECT v FROM x'),[{'v':1}]);self.assertEqual(before,p.read_bytes())
 def test_display_filters_sensitive_errors(self):
  text=d.m.clean('client_secret=hidden Bearer private-token HTTP 503')
  self.assertNotIn('hidden',text);self.assertNotIn('private-token',text);self.assertIn('HTTP 503',text)
if __name__=='__main__':unittest.main()
