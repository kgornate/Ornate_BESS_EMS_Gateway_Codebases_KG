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

CONFIG=Path('/etc/ornate-ems-gateway/central_sync.json')

def config_plan():
    raw=CONFIG.read_bytes(); cfg=json.loads(raw)
    recovery=cfg.get('overflow_recovery')
    if not isinstance(recovery,dict) or not recovery.get('enabled'):
        raise RuntimeError('Archive recovery must already be enabled')
    if recovery.get('max_request_bytes',131072) not in (131072,262144) or recovery.get('max_messages_per_request',25) not in (25,10):
        raise RuntimeError('Archive batch settings differ from the reviewed baseline')
    # No other configuration fields are edited.
    recovery['max_request_bytes']=262144
    recovery['max_messages_per_request']=10
    return raw,(json.dumps(cfg,indent=2)+'\n').encode()

def preflight():
    for name,digest in json.loads((ROOT/'checksums.json').read_text()).items():
        if sha((ROOT/name).read_bytes())!=digest:raise RuntimeError('Package checksum differs: '+name)
    expected=json.loads((ROOT/'expected.json').read_text());source_guard(expected)
    for p in ROOT.rglob('*.py'):compile(p.read_bytes(),str(p),'exec')
    env=dict(os.environ,KALPA_TEST_BASE=str(TARGET),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable,'-B','-m','unittest','test_budget','test_install','-v'],cwd=ROOT,env=env,check=True,timeout=60)
    # Validate an optional operator override without changing it.
    sys.path.insert(0,str(ROOT/'payload/app/central_sync'))
    from recovery_budget import load_budget_settings
    load_budget_settings()
    config_plan()
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
            if s.get('running') and s.get('recovery_scheduler')=='byte_budget_v1' and stamp>=since-2:return
        except (OSError,ValueError,KeyError):pass
        time.sleep(1)
    raise RuntimeError('Fresh archive-budget status did not appear; restoring source')

def restore(backup,meta,guard=False):
    if 'config' in meta:
        e=meta['config']; p=CONFIG
        if guard and sha(p.read_bytes()) not in (e['original'],e['installed']):
            raise RuntimeError('Refusing to overwrite a concurrent configuration edit')
        data=(backup/'central_sync.json').read_bytes()
        if sha(data)!=e['original']: raise RuntimeError('Configuration backup checksum differs')
        atomic(p,data,e['mode'],e['uid'],e['gid'])
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
    backup=Path('/root/kalpa-archive-budget-v2-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    old_config,new_config=config_plan(); cs=CONFIG.stat()
    meta={'installed_at_utc':datetime.now(timezone.utc).isoformat(),'files':{},
          'config':{'original':sha(old_config),'installed':sha(new_config),
                    'mode':cs.st_mode&0o777,'uid':cs.st_uid,'gid':cs.st_gid}}
    (backup/'central_sync.json').write_bytes(old_config)
    (backup/'central_sync.json').chmod(0o600)
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
        if CONFIG.read_bytes()!=old_config:raise RuntimeError('Configuration edited during preflight')
        for name,e in meta['files'].items():
            changed=True
            atomic(TARGET/name,(ROOT/'payload'/name).read_bytes(),e['mode'],e['uid'],e['gid'])
        changed=True
        atomic(CONFIG,new_config,cs.st_mode&0o777,cs.st_uid,cs.st_gid)
        since=time.time();action('start');wait_ready(since)
    except BaseException:
        if changed:
            if active():action('stop')
            restore(backup,meta,guard=True)
        action('start')
        raise
    print('PATCH INSTALLED: byte-budget archive scheduler; Central Sync restarted.')
    print('Archive batches: max 10 messages / 256 KiB. Live uploader, generation cadence and queue data preserved.')
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
    if 'config' in meta and sha(CONFIG.read_bytes())!=meta['config']['installed']:
        raise RuntimeError('Rollback refused: configuration edited later')
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
        with open('/run/lock/kalpa-archive-budget-v2.lock','w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);rollback(args.rollback)
        return
    expected=preflight();print('Preflight PASS: source, package, syntax, settings and behavior tests.')
    if args.check_only:print('CHECK ONLY: no production files, services or queue data changed.');return
    if os.geteuid()!=0:raise RuntimeError('Run installation as root')
    with open('/run/lock/kalpa-archive-budget-v2.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);install(expected)

if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
