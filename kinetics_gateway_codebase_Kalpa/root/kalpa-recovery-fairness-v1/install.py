#!/usr/bin/env python3
"""Exact-source-guarded recovery-only installation; never opens a queue DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
TARGET=Path('/opt/ornate-ems-gateway/backend/app/central_sync')
POLICY=Path('/etc/ornate-ems-gateway/recovery_policy.json')
UNIT='ornate-central-sync.service'
BACKUP_ROOT=Path('/root')
CONFIG=Path('/etc/ornate-ems-gateway/central_sync.json')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def active():return subprocess.run(['systemctl','is-active','--quiet',UNIT]).returncode==0
def ctl(verb):subprocess.run(['systemctl',verb,UNIT],check=True,timeout=60)
def atomic(path,data,mode=0o644):
    temp=path.with_name(path.name+'.kalpa-tmp')
    try:
        with temp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        temp.chmod(mode);os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()

def checks():
    manifest=json.loads((ROOT/'checksums.json').read_text())
    for name,digest in manifest.items():
        if sha(ROOT/name)!=digest:raise RuntimeError('Package checksum mismatch: '+name)
    expected=json.loads((ROOT/'expected_source.json').read_text())['overflow_recovery.py']
    if sha(TARGET/'overflow_recovery.py')!=expected:
        raise RuntimeError('Deployed recovery source differs from the analysed version; nothing changed.')
    for p in (TARGET/'recovery_policy.py',POLICY):
        if p.exists():raise RuntimeError('Existing policy/patch found; nothing changed: '+str(p))
    for p in ROOT.glob('*.py'):compile(p.read_text(),str(p),'exec')
    subprocess.run([sys.executable,'-B','-m','unittest','-v','test_policy','test_install'],cwd=ROOT,check=True,timeout=60)
    from recovery_policy import load_policy
    load_policy(ROOT/'recovery_policy.json')
    cfg=json.loads(CONFIG.read_text())
    if not cfg.get('overflow_recovery',{}).get('enabled',False):
        raise RuntimeError('Archive recovery is not enabled; nothing changed.')
    print('Preflight PASS: exact deployed source, package checksums, syntax and policy tests.',flush=True)

def restore(backup, guard=True):
    meta=json.loads((backup/'manifest.json').read_text())
    for row in meta['files']:
        p=Path(row['target'])
        if guard and (not p.exists() or sha(p)!=row['installed_sha256']):
            raise RuntimeError('Rollback refused: target changed since installation: '+str(p))
    was=active()
    if was:ctl('stop')
    try:
        for row in meta['files']:
            p=Path(row['target'])
            if row['existed']:atomic(p,(backup/row['backup']).read_bytes(),row['mode'])
            elif p.exists():p.unlink()
    finally:
        if was:ctl('start')
    print('Source/policy restored. Queues, SD archives and main configuration were not modified.')

def install():
    checks()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=BACKUP_ROOT/('kalpa-recovery-fairness-backup-'+stamp)
    backup.mkdir(mode=0o700)
    targets=[(TARGET/'overflow_recovery.py','overflow_recovery.py'),
             (TARGET/'recovery_policy.py','recovery_policy.py'),(POLICY,'recovery_policy.json')]
    rows=[]
    for i,(p,name) in enumerate(targets):
        row={'target':str(p),'existed':p.exists(),'backup':str(i)+'.original',
             'mode':(p.stat().st_mode & 0o777) if p.exists() else 0o644,
             'installed_sha256':sha(ROOT/name)}
        if p.exists():shutil.copy2(p,backup/row['backup'])
        rows.append(row)
    was=active()
    (backup/'manifest.json').write_text(json.dumps({'files':rows,'was_active':was},indent=2))
    if was:ctl('stop')
    writes_started=False
    try:
        # Detect concurrent changes after stopping, before writing.
        original=json.loads((ROOT/'expected_source.json').read_text())['overflow_recovery.py']
        if sha(TARGET/'overflow_recovery.py')!=original:raise RuntimeError('Source changed during install')
        writes_started=True
        for (p,name),row in zip(targets,rows):atomic(p,(ROOT/name).read_bytes(),row['mode'])
        if was:
            ctl('start')
            healthy=False
            for _ in range(10):
                time.sleep(2)
                if not active():break
                cfg=json.loads(CONFIG.read_text())
                status=Path(cfg['overflow_recovery'].get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))
                if not status.is_absolute():status=TARGET.parents[1]/status
                try:
                    s=json.loads(status.read_text())
                    if s.get('running') and 'bounded_opportunity_count' in s.get('counters',{}):healthy=True;break
                except (OSError,ValueError):pass
            if not healthy:raise RuntimeError('New recovery runtime status was not confirmed')
    except Exception:
        if active():ctl('stop')
        if writes_started:
            for row in rows:
                p=Path(row['target'])
                if row['existed']:atomic(p,(backup/row['backup']).read_bytes(),row['mode'])
                elif p.exists():p.unlink()
        if was:ctl('start')
        print('Installation failed; source/policy restored.',file=sys.stderr)
        raise
    print('PATCH INSTALLED. Backup:',backup)
    print('Central Sync restarted.' if was else 'Central Sync was inactive and remains inactive.')
    print('No database or archive rows changed by installer. Main config, cadence and transport settings preserved.')
    print('Verify: python3 verify.py')
    print('Rollback: python3 install.py --rollback '+str(backup))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');ap.add_argument('--rollback',type=Path)
    args=ap.parse_args()
    try:
        if args.rollback:restore(args.rollback)
        elif args.check_only:checks();print('CHECK ONLY: no files, services or queue contents changed.')
        else:
            if os.geteuid()!=0:raise RuntimeError('Run installer as root')
            install()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
