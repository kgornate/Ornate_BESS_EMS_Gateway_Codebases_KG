import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install as installer

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.pkg=self.root/'package';self.pkg.mkdir()
        self.target=self.root/'app';self.target.mkdir()
        self.backups=self.root/'backups';self.backups.mkdir()
        self.policy=self.root/'recovery_policy.json'
        self.original=b'# original deployed recovery\n';(self.target/'overflow_recovery.py').write_bytes(self.original)
        for name in ('overflow_recovery.py','recovery_policy.py','recovery_policy.json'):
            (self.pkg/name).write_text('# patched\n' if name.endswith('.py') else '{}')
        (self.pkg/'expected_source.json').write_text(json.dumps({'overflow_recovery.py':hashlib.sha256(self.original).hexdigest()}))
        self.patches=[patch.object(installer,'ROOT',self.pkg),patch.object(installer,'TARGET',self.target),
                      patch.object(installer,'POLICY',self.policy),patch.object(installer,'BACKUP_ROOT',self.backups),
                      patch.object(installer,'checks',lambda:None),patch.object(installer,'active',lambda:False)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):return fn(*args)
    def test_install_and_rollback_restore_exact_source(self):
        self.quiet(installer.install)
        self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),b'# patched\n')
        backup=next(self.backups.iterdir())
        self.quiet(installer.restore,backup)
        self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),self.original)
        self.assertFalse((self.target/'recovery_policy.py').exists());self.assertFalse(self.policy.exists())
    def test_rollback_refuses_later_edits(self):
        self.quiet(installer.install)
        (self.target/'overflow_recovery.py').write_text('# newer edits')
        with self.assertRaisesRegex(RuntimeError,'Rollback refused'):installer.restore(next(self.backups.iterdir()))
        self.assertEqual((self.target/'overflow_recovery.py').read_text(),'# newer edits')
    def test_partial_install_failure_restores_source(self):
        original_atomic=installer.atomic
        def fail_helper(path,data,mode=0o644):
            if path.name=='recovery_policy.py':raise OSError('simulated write failure')
            return original_atomic(path,data,mode)
        with patch.object(installer,'atomic',fail_helper),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):self.quiet(installer.install)
        self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),self.original)
        self.assertFalse(self.policy.exists())
    def test_concurrent_source_change_aborts_and_restores_backup(self):
        # Installer checks again immediately before mutation.
        with patch.object(installer,'sha',return_value='unexpected'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(RuntimeError):self.quiet(installer.install)
        self.assertEqual((self.target/'overflow_recovery.py').read_bytes(),self.original)

if __name__=='__main__':unittest.main()
