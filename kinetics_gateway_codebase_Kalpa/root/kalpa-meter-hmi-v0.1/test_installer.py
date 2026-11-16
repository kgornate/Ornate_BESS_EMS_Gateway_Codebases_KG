import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

PACKAGE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("KALPA_HMI_BASELINE", str(PACKAGE / "tests/baseline")))


def module():
    spec = importlib.util.spec_from_file_location("hmi_installer_test", PACKAGE / "install.py")
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def fixture(tmp_path):
    folder = tmp_path / "app/local_hmi"
    folder.mkdir(parents=True)
    (folder.parent / "assets").mkdir()
    (folder.parent / "assets/acrel_meter.py").write_text("# backend installed\n")
    for name in ("index.html", "app.js", "styles.css"):
        shutil.copy2(BASE / name, folder / name)
    return folder


def test_apply_idempotence_and_exact_rollback(tmp_path, monkeypatch):
    hmi = fixture(tmp_path)
    installer = module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--hmi-dir", str(hmi), "--backup-root", str(tmp_path)])
    installer.main()
    for name in installer.NAMES:
        assert (hmi / name).read_bytes() == (PACKAGE / "hmi" / name).read_bytes()
    installer.main()
    backups = list(tmp_path.glob("kalpa-meter-hmi-backup-*"))
    assert len(backups) == 1
    installer.restore(backups[0])
    for name in installer.NAMES:
        assert (hmi / name).read_bytes() == (BASE / name).read_bytes()


def test_conflicting_hmi_change_aborts_without_overwrite(tmp_path):
    hmi = fixture(tmp_path)
    (hmi / "app.js").write_text("// a separate HMI update\n")
    html = (hmi / "index.html").read_bytes()
    with pytest.raises(RuntimeError, match="separate changes"):
        module().plan(hmi)
    assert (hmi / "index.html").read_bytes() == html
    assert (hmi / "app.js").read_text() == "// a separate HMI update\n"


def test_missing_backend_blocks_installation(tmp_path):
    hmi = fixture(tmp_path)
    (hmi.parent / "assets/acrel_meter.py").unlink()
    with pytest.raises(RuntimeError, match="backend"):
        module().plan(hmi)


def test_rollback_preserves_post_install_edits(tmp_path, monkeypatch):
    hmi = fixture(tmp_path)
    installer = module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--hmi-dir", str(hmi), "--backup-root", str(tmp_path)])
    installer.main()
    backup = next(tmp_path.glob("kalpa-meter-hmi-backup-*"))
    (hmi / "app.js").write_text("// later update\n")
    with pytest.raises(RuntimeError, match="later changes"):
        installer.restore(backup)
    assert (hmi / "app.js").read_text() == "// later update\n"


def test_check_only_no_file_changes(tmp_path, monkeypatch):
    hmi = fixture(tmp_path)
    installer = module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--hmi-dir", str(hmi), "--backup-root", str(tmp_path), "--check-only"])
    installer.main()
    assert (hmi / "index.html").read_bytes() == (BASE / "index.html").read_bytes()
    assert not list(tmp_path.glob("kalpa-meter-hmi-backup-*"))
