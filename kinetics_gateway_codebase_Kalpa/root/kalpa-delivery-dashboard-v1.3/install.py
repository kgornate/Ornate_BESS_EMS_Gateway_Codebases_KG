#!/usr/bin/env python3
"""Dashboard-only update. Never restarts or imports Central Sync."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
TARGET=Path('/opt/kalpa-delivery-dashboard')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(p,data,mode=0o644):
    t=p.with_name(p.name+'.dashboard-tmp')
    try:
        with t.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        t.chmod(mode);os.replace(t,p)
    finally:
        if t.exists():t.unlink()
def check():
    for n,d in json.loads((ROOT/'checksums.json').read_text()).items():
        if sha(ROOT/n)!=d:raise RuntimeError('Package checksum mismatch: '+n)
    for n,d in json.loads((ROOT/'expected_source.json').read_text()).items():
        if not (TARGET/n).is_file() or sha(TARGET/n)!=d:raise RuntimeError('Deployed dashboard differs from supplied source: '+n)
    if (TARGET/'monitor_evidence.py').exists():raise RuntimeError('monitor_evidence.py already exists; refusing overwrite')
    for p in ROOT.glob('*.py'):compile(p.read_text(),str(p),'exec')
    subprocess.run([sys.executable,'-B','-m','unittest','-v','test_dashboard'],cwd=ROOT,check=True,timeout=60)
    print('Preflight PASS: dashboard source, checksums, syntax and 17 tests. No services/queues changed.')
def restore(backup):
    meta=json.loads((backup/'manifest.json').read_text())
    for r in meta:
        p=Path(r['target'])
        if not p.exists() or sha(p)!=r['installed_sha']:raise RuntimeError('Later edit detected; rollback refused: '+str(p))
    for r in meta:
        p=Path(r['target'])
        if r['existed']:atomic(p,(backup/r['backup']).read_bytes(),r['mode'])
        else:p.unlink()
    print('Dashboard restored. Reopen its terminal view. Central Sync unchanged.')
def install():
    check()
    backup=Path('/root')/('kalpa-delivery-dashboard-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700);meta=[]
    for name in ('dashboard.py','monitor_evidence.py'):
        p=TARGET/name;exists=p.exists()
        row={'target':str(p),'existed':exists,'backup':name,'mode':p.stat().st_mode & 0o777 if exists else 0o644,'installed_sha':sha(ROOT/name)}
        if exists:shutil.copy2(p,backup/name)
        meta.append(row)
    (backup/'manifest.json').write_text(json.dumps(meta,indent=2))
    try:
        for r in meta:atomic(Path(r['target']),(ROOT/Path(r['target']).name).read_bytes(),r['mode'])
    except Exception:
        for r in meta:
            p=Path(r['target'])
            if r['existed']:atomic(p,(backup/r['backup']).read_bytes(),r['mode'])
            elif p.exists():p.unlink()
        raise
    print('DASHBOARD PATCH INSTALLED. No service restart or database writes.')
    print('Backup:',backup)
    print('Run: kalpa-delivery-dashboard --interval 10')
    print('Rollback: python3 install.py --rollback '+str(backup))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');ap.add_argument('--rollback',type=Path);a=ap.parse_args()
    try:
        if a.rollback:restore(a.rollback)
        elif a.check_only:check()
        else:install()
    except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
