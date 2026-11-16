#!/usr/bin/env python3
"""Exact-source guarded connection cleanup; no queue or settings changes."""
import argparse,hashlib,json,os,subprocess,sys,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
TARGET=Path('/opt/ornate-ems-gateway/backend/app/central_sync/overflow_recovery.py')
SERVICE='ornate-central-sync.service'
def digest(data):return hashlib.sha256(data).hexdigest()
def call(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=45)
def atomic(path,data,mode):
    fd,name=tempfile.mkstemp(prefix='.kalpa-cleanup-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(name,mode);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
def ready(restarted_at):
    cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
    p=Path(cfg.get('overflow_recovery',{}).get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))
    if not p.is_absolute():p=Path('/opt/ornate-ems-gateway/backend')/p
    for _ in range(25):
        try:
            s=json.loads(p.read_text());stamp=datetime.fromisoformat(s['status_generated_utc'].replace('Z','+00:00'))
            instance=datetime.fromisoformat(s['started_utc'].replace('Z','+00:00'))
            if instance.timestamp()>=restarted_at-1 and s.get('running') and s.get('transport_mode')=='dedicated_archive_client' and (datetime.now(timezone.utc)-stamp).total_seconds()<8:
                call('systemctl','is-active','--quiet',SERVICE);return
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):pass
        time.sleep(1)
    raise RuntimeError('Fresh dedicated archive status was not observed after restart')
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
    manifest=json.loads((HERE/'checksums.json').read_text())
    for name,expected in manifest.items():
        if digest((HERE/name).read_bytes())!=expected:raise RuntimeError('Package checksum mismatch: '+name)
    expected=json.loads((HERE/'expected.json').read_text());old=TARGET.read_bytes();new=(HERE/'overflow_recovery.py').read_bytes()
    if digest(old) not in (expected['before'],expected['after']):raise RuntimeError('Deployed recovery differs from verified v2.0.1; nothing changed')
    if digest(new)!=expected['after']:raise RuntimeError('Replacement checksum mismatch')
    compile(new,str(TARGET),'exec');call('systemctl','is-active','--quiet',SERVICE)
    if args.check_only:print('CHECK PASS: exact recovery source and package verified; nothing changed.');return
    if digest(old)==expected['after']:print('Connection cleanup already installed.');return
    backup=Path('/root/kalpa-sqlite-cleanup-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(mode=0o700)
    (backup/'overflow_recovery.py').write_bytes(old)
    mode=TARGET.stat().st_mode & 0o777
    call('systemctl','stop',SERVICE)
    installed=False
    try:
        if TARGET.read_bytes()!=old:raise RuntimeError('Concurrent source edit detected')
        atomic(TARGET,new,mode);installed=True
        restarted_at=time.time();call('systemctl','start',SERVICE);ready(restarted_at)
    except Exception:
        if installed:
            call('systemctl','stop',SERVICE)
            if TARGET.read_bytes()==new:atomic(TARGET,old,mode)
        call('systemctl','start',SERVICE)
        raise
    print('SQLite connections now close on normal and exceptional archive-selection paths.')
    print('Central Sync active; fresh dedicated archive status verified. Backup:',backup)
if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
