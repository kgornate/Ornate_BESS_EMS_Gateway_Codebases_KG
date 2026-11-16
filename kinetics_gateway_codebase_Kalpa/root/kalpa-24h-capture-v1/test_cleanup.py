#!/usr/bin/env python3
import ast,hashlib,json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import time
HERE=Path(__file__).resolve().parent
class CleanupTests(unittest.TestCase):
    def run_case(self,due=True,fail_at=None):
        source=(HERE/'overflow_recovery.py').read_text();tree=ast.parse(source)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef)and m.name=='_next_archive'for m in n.body))
        method=next(m for m in cls.body if isinstance(m,ast.FunctionDef)and m.name=='_next_archive')
        opened=[]
        class Connection(sqlite3.Connection):
            closed=False
            def execute(self,sql,*args):
                if fail_at=='count' and 'COUNT(*)' in sql:raise sqlite3.OperationalError('injected count failure')
                if fail_at=='due' and 'SELECT 1' in sql:raise sqlite3.OperationalError('injected due failure')
                return super().execute(sql,*args)
            def close(self):self.closed=True;return super().close()
        def connect(*a,**kw):
            c=sqlite3.connect(*a,**kw,factory=Connection);opened.append(c);return c
        scope={'Path':Path,'closing':closing,'sqlite3':SimpleNamespace(connect=connect,Error=sqlite3.Error),'time':time}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'method','exec'),scope)
        with tempfile.TemporaryDirectory()as folder:
            p=Path(folder)/'archive.db'
            with closing(sqlite3.connect(p))as db:
                db.execute('CREATE TABLE outbox_messages(state TEXT,next_attempt_epoch REAL)')
                db.execute('INSERT INTO outbox_messages VALUES (?,?)',('pending',time.time()+(-100 if due else 1000)));db.commit()
            obj=SimpleNamespace(recovery_config=SimpleNamespace(archive_dir=folder,archive_glob='*.db'),_read_replayed_marker=lambda p:None,_archive_signature=lambda p:{},_write_replayed_marker=lambda *a,**kw:None)
            result=scope['_next_archive'](obj)
            self.assertEqual(result,p if due and fail_at is None else None)
            self.assertTrue(opened);self.assertTrue(all(c.closed for c in opened))
    def test_due_connections_closed(self):self.run_case()
    def test_future_connections_closed(self):self.run_case(False)
    def test_count_exception_closed(self):self.run_case(fail_at='count')
    def test_due_exception_closed(self):self.run_case(fail_at='due')
    def test_replacement_hash_and_syntax(self):
        b=(HERE/'overflow_recovery.py').read_bytes();self.assertEqual(hashlib.sha256(b).hexdigest(),json.loads((HERE/'expected.json').read_text())['after']);compile(b,'recovery','exec')
if __name__=='__main__':unittest.main(verbosity=2)
