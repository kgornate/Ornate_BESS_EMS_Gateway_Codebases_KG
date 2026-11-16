import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
import install as installer

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.pkg=self.root/'package';self.pkg.mkdir()
        self.target=self.root/'app';self.target.mkdir()
        self.dash=self.root/'dashboard';self.dash.mkdir()
        self.backups=self.root/'backups';self.backups.mkdir()
        self.policy=self.root/'archive_transport.json'
        self.original=b'# verified recovery original\n'
        (self.target/'overflow_recovery.py').write_bytes(self.original)
        for n in ('dashboard.py','monitor_evidence.py'):(self.dash/n).write_text('# original dashboard\n')
        for n in ('overflow_recovery.py','archive_transport.py','dashboard.py','monitor_evidence.py'):(self.pkg/n).write_text('# new patch\n')
        (self.pkg/'archive_transport.json').write_text('{}')
        originals=[self.target/'overflow_recovery.py',self.dash/'dashboard.py',self.dash/'monitor_evidence.py']
        (self.pkg/'expected_source.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in originals}))
        patches=[patch.object(installer,'ROOT',self.pkg),patch.object(installer,'TARGET',self.target),patch.object(installer,'DASHBOARD',self.dash),
                 patch.object(installer,'POLICY',self.policy),patch.object(installer,'BACKUP_ROOT',self.backups),patch.object(installer,'checks',lambda:None),
                 patch.object(installer,'active',lambda:False),patch.object(installer.subprocess,'run',return_value=NS(returncode=1))]
        for p in patches:p.start();self.addCleanup(p.stop)
    def quiet(self,fn,*args):
        with contextlib.redirect_stdout(io.StringIO()):return fn(*args)
    def test_install_rollback_restores_five_targets(self):
        self.quiet(installer.install)
        self.assertTrue((self.target/'archive_transport.py').exists());self.assertTrue(self.policy.exists())
        self.quiet(installer.restore,next(self.backups.iterdir()))
        self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),self.original)
        self.assertFalse((self.target/'archive_transport.py').exists());self.assertFalse(self.policy.exists())
        self.assertEqual((self.dash/'dashboard.py').read_text(),'# original dashboard\n')
    def test_partial_install_failure_rolls_back(self):
        atomic=installer.atomic
        def fail(p,data,mode=0o644):
            if p==self.policy:raise OSError('simulated failure')
            atomic(p,data,mode)
        with patch.object(installer,'atomic',fail),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):self.quiet(installer.install)
        self.assertFalse((self.target/'archive_transport.py').exists());self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),self.original)
    def test_later_edits_protected_on_rollback(self):
        self.quiet(installer.install);(self.dash/'dashboard.py').write_text('# later edit')
        with self.assertRaisesRegex(RuntimeError,'Rollback refused'):self.quiet(installer.restore,next(self.backups.iterdir()))
        self.assertEqual((self.dash/'dashboard.py').read_text(),'# later edit')
    def test_concurrent_edit_preserved_when_no_install_writes_started(self):
        digest=installer.sha
        def changed(p):
            if p==self.target/'overflow_recovery.py':p.write_text('# concurrent edit')
            return digest(p)
        with patch.object(installer,'sha',changed),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'Source changed'):self.quiet(installer.install)
        self.assertEqual((self.target/'overflow_recovery.py').read_text(),'# concurrent edit')
        self.assertFalse(self.policy.exists())

if __name__=='__main__':unittest.main()
