import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('archive_budget_installer',Path(__file__).with_name('install.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class InstallerTests(unittest.TestCase):
    def test_config_changes_only_archive_batch_limits(self):
        original={'backend':{'timeout_sec':30},'general_assets':{'poll_interval_sec':1},
                  'overflow_recovery':{'enabled':True,'max_messages_per_request':25,'max_request_bytes':131072,'request_pause_sec':10}}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json'; p.write_text(json.dumps(original))
            with patch.object(m,'CONFIG',p):old,new=m.config_plan()
            after=json.loads(new); expected=json.loads(json.dumps(original))
            expected['overflow_recovery'].update(max_messages_per_request=10,max_request_bytes=262144)
            self.assertEqual(after,expected);self.assertEqual(json.loads(old),original)

    def test_source_change_refused(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'TARGET',Path(d)):
            (Path(d)/'file.py').write_bytes(b'later')
            with self.assertRaises(RuntimeError):m.source_guard({'file.py':m.sha(b'original')})
    def test_restore_keeps_unrelated_files_and_restores_exact_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            import os
            root=Path(d);target=root/'target';backup=root/'backup';target.mkdir();(backup/'files').mkdir(parents=True)
            (target/'a.py').write_bytes(b'patch');(target/'new.py').write_bytes(b'helper');(target/'other').write_bytes(b'keep')
            (backup/'files/a.py').write_bytes(b'original')
            meta={'files':{'a.py':{'existed':True,'mode':0o640,'uid':os.getuid(),'gid':os.getgid(),
                                 'original':m.sha(b'original'),'installed':m.sha(b'patch')},
                           'new.py':{'existed':False,'installed':m.sha(b'helper')}}}
            with patch.object(m,'TARGET',target):m.restore(backup,meta,guard=True)
            self.assertEqual((target/'a.py').read_bytes(),b'original');self.assertFalse((target/'new.py').exists())
            self.assertEqual((target/'other').read_bytes(),b'keep')
    def test_rollback_refuses_later_edit_before_stopping_service(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a.py').write_bytes(b'later')
            (root/'manifest.json').write_text(json.dumps({'files':{'a.py':{'existed':False,'installed':m.sha(b'patch')}}}))
            with patch.object(m,'TARGET',root),patch.object(m,'action') as action:
                with self.assertRaises(RuntimeError):m.rollback(root)
                action.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
