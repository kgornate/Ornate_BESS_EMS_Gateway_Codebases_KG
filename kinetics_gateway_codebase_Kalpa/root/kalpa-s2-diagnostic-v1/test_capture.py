import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import capture

class Tests(unittest.TestCase):
    def test_query_is_read_only_and_missing_source_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.db'
            with sqlite3.connect(p) as db:db.execute('CREATE TABLE x(v)');db.execute('INSERT INTO x VALUES(4)')
            before=p.read_bytes();self.assertEqual(capture.query(p,'SELECT * FROM x'),[{'v':4}]);self.assertEqual(p.read_bytes(),before)
            with self.assertRaises(RuntimeError):capture.query(Path(d)/'missing.db','SELECT 1')
            self.assertFalse((Path(d)/'missing.db').exists())
    def test_query_limit_is_explicit_not_partial_success(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.db'
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE x(v)');db.executemany('INSERT INTO x VALUES(?)',[(1,),(2,)])
            with self.assertRaises(RuntimeError):capture.query(p,'SELECT * FROM x',limit=1)
    def test_sensitive_error_strings_are_filtered(self):
        s=capture.clean('Bearer SECRET client_secret="TOPSECRET" access_token=TOKEN')
        self.assertNotIn('TOPSECRET',s);self.assertNotIn('TOKEN',s);self.assertNotIn('Bearer SECRET',s)
    def test_main_status_does_not_export_backend_credentials(self):
        source={'status_generated_utc':'now','backend':{'client_secret':'PRIVATE'},'uploader':{'accepted_count':3},'stream_backend_ack':{'s2':{'last_error':'client_secret="PRIVATE"'}}}
        with patch.object(capture,'load',return_value=source):result=capture.status({},'','unused',main=True)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('backend',result)
        self.assertEqual(result['uploader']['accepted_count'],3)
if __name__=='__main__':unittest.main()
