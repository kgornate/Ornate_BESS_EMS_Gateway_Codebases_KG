#!/usr/bin/env python3
"""Source-guarded Central Sync archive scheduler install/rollback."""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
TARGET=Path('/opt/ornate-ems-gateway/backend')
UNIT='ornate-central-sync.service'

def sha(data):return hashlib.sha256(data).hexdigest()
def payload():return sorted(str(p.relative_to(ROOT/'payload')) for p in (ROOT/'payload').rglob('*.py'))
def active():return subprocess.run(['systemctl','is-active','--quiet',UNIT],timeout=10).returncode==0
def action(verb):subprocess.run(['systemctl',verb,UNIT],check=True,timeout=120)
def atomic(path,data,mode=0o644,uid=None,gid=None):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.kalpa-archive-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(name,mode)
        if uid is not None:os.chown(name,uid,gid)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def source_guard(expected):
    for name,digest in expected.items():
        p=TARGET/name
        if p.is_symlink() or not p.is_file() or sha(p.read_bytes())!=digest:
            raise RuntimeError('Deployed source differs from uploaded source: '+name)
    for name in payload():
        if name not in expected and (TARGET/name).exists():
            raise RuntimeError('New helper already exists: '+name)

def preflight():
    for name,digest in json.loads((ROOT/'checksums.json').read_text()).items():
        if sha((ROOT/name).read_bytes())!=digest:raise RuntimeError('Package checksum differs: '+name)
    expected=json.loads((ROOT/'expected.json').read_text());source_guard(expected)
    for p in ROOT.rglob('*.py'):compile(p.read_bytes(),str(p),'exec')
    env=dict(os.environ,KALPA_TEST_BASE=str(TARGET),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable,'-B','-m','unittest','test_budget','test_install','test_throughput','-v'],cwd=ROOT,env=env,check=True,timeout=60)
    # Validate an optional operator override without changing it.
    sys.path.insert(0,str(ROOT/'payload/app/central_sync'))
    from recovery_budget import load_budget_settings
    load_budget_settings()
    return expected

def status_path():
    cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
    p=Path(cfg.get('overflow_recovery',{}).get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))
    return p if p.is_absolute() else TARGET/p

def wait_ready(since):
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        if not active():raise RuntimeError('Central Sync exited after startup')
        try:
            s=json.loads(status_path().read_text())
            stamp=datetime.fromisoformat(s['started_utc'].replace('Z','+00:00')).timestamp()
            if s.get('running') and s.get('recovery_scheduler')=='archive_throughput_v2_2' and stamp>=since-2:return
        except (OSError,ValueError,KeyError):pass
        time.sleep(1)
    raise RuntimeError('Fresh archive-budget status did not appear; restoring source')

def restore(backup,meta,guard=False):
    for name,entry in meta['files'].items():
        p=TARGET/name
        if guard and p.exists() and sha(p.read_bytes()) not in {entry['installed'],entry.get('original')}:
            raise RuntimeError('Refusing to overwrite a later edit during restore: '+name)
        if entry['existed']:
            data=(backup/'files'/name).read_bytes()
            if sha(data)!=entry['original']:raise RuntimeError('Backup checksum differs: '+name)
            atomic(p,data,entry['mode'],entry['uid'],entry['gid'])
        elif p.exists():p.unlink()

def install(expected):
    if not active():raise RuntimeError('Central Sync must be active before installation')
    backup=Path('/root/kalpa-archive-throughput-v2_2-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    meta={'installed_at_utc':datetime.now(timezone.utc).isoformat(),'files':{}}
    for name in payload():
        p=TARGET/name;st=p.stat() if p.exists() else None
        e={'existed':st is not None,'mode':st.st_mode&0o777 if st else 0o644,
           'uid':st.st_uid if st else 0,'gid':st.st_gid if st else 0,
           'installed':sha((ROOT/'payload'/name).read_bytes())}
        if st:
            data=p.read_bytes();e['original']=sha(data)
            b=backup/'files'/name;b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(data)
        meta['files'][name]=e
    (backup/'manifest.json').write_text(json.dumps(meta,indent=2))
    try:
        (backup/'recovery-before.json').write_bytes(status_path().read_bytes())
    except OSError:pass
    print('Backup:',backup,flush=True)
    changed=False
    try:
        action('stop');source_guard(expected)
        for name,e in meta['files'].items():
            changed=True
            atomic(TARGET/name,(ROOT/'payload'/name).read_bytes(),e['mode'],e['uid'],e['gid'])
        since=time.time();action('start');wait_ready(since)
    except BaseException:
        if changed:
            if active():action('stop')
            restore(backup,meta,guard=True)
        action('start')
        raise
    print('PATCH INSTALLED: archive throughput v2.2 and future alarm wire format; Central Sync restarted.')
    print('Main gateway, live uploader settings, generation cadence, config and database contents unchanged by installer.')
    print('Existing captures continue. Analyse this service restart as a new counter segment.')
    print('Verify: '+sys.executable+' -B verify.py --duration 300')
    print('Rollback: '+sys.executable+' -B install.py --rollback '+str(backup))

def rollback(backup):
    meta=json.loads((backup/'manifest.json').read_text())
    for name,e in meta['files'].items():
        if not (TARGET/name).is_file() or sha((TARGET/name).read_bytes())!=e['installed']:
            raise RuntimeError('Rollback refused: source edited later: '+name)
        if e['existed'] and sha((backup/'files'/name).read_bytes())!=e['original']:
            raise RuntimeError('Backup checksum differs: '+name)
    was_active=active()
    if was_active:action('stop')
    restore(backup,meta)
    if was_active:action('start')
    print('Original archive scheduler restored; no queue rows changed.')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');ap.add_argument('--rollback',type=Path)
    args=ap.parse_args()
    if args.rollback:
        if os.geteuid()!=0:raise RuntimeError('Run rollback as root')
        with open('/run/lock/kalpa-archive-throughput-v2_2.lock','w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);rollback(args.rollback)
        return
    expected=preflight();print('Preflight PASS: source, package, syntax, settings and behavior tests.')
    if args.check_only:print('CHECK ONLY: no production files, services or queue data changed.');return
    if os.geteuid()!=0:raise RuntimeError('Run installation as root')
    with open('/run/lock/kalpa-archive-throughput-v2_2.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);install(expected)

if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
