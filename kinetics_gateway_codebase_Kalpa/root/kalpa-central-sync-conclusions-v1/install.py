#!/usr/bin/env python3
"""Targeted offline Central Sync patch with preflight, backup and rollback."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

PACKAGE=Path(__file__).resolve().parent
SERVICE='ornate-central-sync.service'

def digest(data):return hashlib.sha256(data).hexdigest()

def atomic(path,data,record):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.kalpa-sync-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(name,record['mode']);os.chown(name,record['uid'],record['gid'])
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

def systemctl(*args,check=True):
    return subprocess.run(['systemctl',*args,SERVICE],check=check,timeout=45,stdout=subprocess.DEVNULL)

def restore(backup,guard=True):
    data=json.loads((backup/'restore.json').read_text())
    for name,record in data['files'].items():
        path=Path(name)
        if path.is_symlink():raise RuntimeError('Refusing symbolic-link rollback target: '+name)
        if guard:
            actual=digest(path.read_bytes()) if path.exists() else None
            if actual != record['installed_sha256']:raise RuntimeError('File changed since installation: '+name)
        if record['existed'] and digest((backup/record['saved']).read_bytes())!=record['before_sha256']:
            raise RuntimeError('Backup checksum mismatch: '+name)
    systemctl('stop')
    for name,record in data['files'].items():
        path=Path(name)
        if record['existed']:atomic(path,(backup/record['saved']).read_bytes(),record)
        else:path.unlink(missing_ok=True)
    if data['was_active']:systemctl('start')
    print('Original source/configuration restored. Previous service activity restored. Outbox untouched.')

def plan(backend,config):
    metadata=json.loads((PACKAGE/'patch.json').read_text())
    planned={}
    observed={}
    for name,entry in metadata['files'].items():
        if not (PACKAGE/'payload'/name).resolve().is_relative_to(PACKAGE/'payload'):
            raise RuntimeError('Invalid payload path')
        replacement=(PACKAGE/'payload'/name).read_bytes()
        if digest(replacement)!=entry['after_sha256']:raise RuntimeError('Package checksum mismatch: '+name)
        path=backend/name
        if path.is_symlink():raise RuntimeError('Refusing symbolic link: '+str(path))
        current=path.read_bytes() if path.exists() else None
        actual=digest(current) if current is not None else None
        if actual not in {entry['before_sha256'],entry['after_sha256']}:
            raise RuntimeError('Source differs from reviewed gateway export: '+name+'; no files changed')
        if current!=replacement:
            planned[path]=replacement;observed[path]=current
    if config.is_symlink():raise RuntimeError('Refusing symbolic-link configuration')
    current=config.read_bytes();data=json.loads(current)
    general=data['general_assets']
    oldpath=str(general.get('s2_policy_manifest_path',''))
    if Path(oldpath).name not in {'kalpa_s2_policy_frozen_v1_0.json','kalpa_s2_policy_frozen_v1_1.json'}:
        raise RuntimeError('Unexpected active S2 policy; reconcile before updating')
    manifest_path=Path(oldpath) if Path(oldpath).is_absolute() else backend/oldpath
    expected=general.get('s2_policy_expected_sha256')
    if not expected or digest(manifest_path.read_bytes())!=expected:
        raise RuntimeError('Current S2 manifest/config checksum does not match')
    data['backend']['ingest_path']='/api/v1/gateway/ingest'
    general['s2_policy_manifest_path']=metadata['manifest_path']
    general['s2_policy_expected_sha256']=metadata['manifest_sha256']
    candidate=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode()
    if json.loads(current)!=data:
        planned[config]=candidate;observed[config]=current
    return planned,candidate,observed

def preflight(backend,planned,candidate):
    with tempfile.TemporaryDirectory(prefix='kalpa-sync-stage-') as directory:
        stage=Path(directory)
        for folder in ('app','configs'):
            shutil.copytree(backend/folder,stage/folder,ignore=shutil.ignore_patterns('__pycache__'))
        for path,content in planned.items():
            if path.is_relative_to(backend):
                target=stage/path.relative_to(backend);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
                if target.suffix=='.py':compile(content,str(target),'exec')
        conf=stage/'candidate.json';conf.write_bytes(candidate);conf.chmod(0o600)
        script='''import sys
from app.central_sync.config import load_central_sync_config
from app.central_sync.s2_policy import S2PolicyManifest
from app.central_sync.general_asset_producer import GeneralAssetProducer
from app.central_sync.client import CentralBackendClient
from app.central_sync.configuration_audit_producer import ConfigurationAuditProducer
cfg=load_central_sync_config(sys.argv[1])
p=S2PolicyManifest(cfg.general_assets.s2_policy_manifest_path,expected_sha256=cfg.general_assets.s2_policy_expected_sha256)
assert len(p.by_key)==2472
assert sum(x.retention_class=='T3_3Y' for x in p.by_key.values())==12
assert cfg.backend.ingest_path=='/api/v1/gateway/ingest'
print('Preflight imports, configuration, policy checksum and 2472-point mapping: PASS')
'''
        env=dict(os.environ,PYTHONPATH=str(stage)+os.pathsep+os.environ.get('PYTHONPATH',''),PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-c',script,str(conf)],cwd=stage,env=env,capture_output=True,text=True,timeout=60)
        if result.returncode:
            # Configuration validation errors may contain credentials; do not echo them.
            raise RuntimeError('Staged import/config validation failed; deployed files unchanged. Use gateway venv Python.')
        print(result.stdout.strip())

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--rollback',type=Path)
    parser.add_argument('--backend',type=Path,default=Path('/opt/ornate-ems-gateway/backend'))
    parser.add_argument('--config',type=Path,default=Path('/etc/ornate-ems-gateway/central_sync.json'))
    parser.add_argument('--backup-root',type=Path,default=Path('/root'))
    args=parser.parse_args()
    if os.geteuid()!=0 and not args.check_only:parser.error('Run as root on DEBIX')
    if args.rollback:
        if args.check_only:parser.error('--check-only cannot be combined with --rollback')
        restore(args.rollback.resolve());return
    backend=args.backend.absolute();config=args.config.absolute()
    planned,candidate,observed=plan(backend,config)
    preflight(backend,planned,candidate)
    if args.check_only:
        print('CHECK ONLY: clean patch verified; no deployed files changed.');return
    if not planned:print('Patch already installed; no changes made.');return
    was_active=systemctl('is-active','--quiet',check=False).returncode==0
    backup=args.backup_root/('kalpa-sync-conclusions-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700,parents=True)
    records={}
    for i,(path,replacement) in enumerate(planned.items()):
        existed=path.exists();st=path.stat() if existed else None
        current=path.read_bytes() if existed else None
        if current!=observed[path]:raise RuntimeError('File changed during preflight: '+str(path))
        saved='original-'+str(i)
        if existed:
            (backup/saved).write_bytes(current);(backup/saved).chmod(0o600)
        records[str(path)]=dict(existed=existed,saved=saved,mode=(st.st_mode&0o777) if st else 0o644,uid=st.st_uid if st else 0,gid=st.st_gid if st else 0,before_sha256=digest(current) if existed else None,installed_sha256=digest(replacement))
    (backup/'restore.json').write_text(json.dumps(dict(was_active=was_active,files=records),indent=2));(backup/'restore.json').chmod(0o600)
    shutil.copy2(PACKAGE/'install.py',backup/'install.py')
    changed=[]
    if was_active:systemctl('stop')
    try:
        # Check all originals before writing the first replacement.
        for name,record in records.items():
            path=Path(name)
            if path.is_symlink() or (digest(path.read_bytes()) if path.exists() else None)!=record['before_sha256']:
                raise RuntimeError('Source/configuration changed during preflight: '+name)
        for path,replacement in planned.items():
            changed.append(path);atomic(path,replacement,records[str(path)])
        if was_active:
            systemctl('start')
            for _ in range(5):
                time.sleep(1)
                if systemctl('is-active','--quiet',check=False).returncode:raise RuntimeError('Central Sync did not remain active')
    except BaseException:
        systemctl('stop',check=False)
        for path in reversed(changed):
            record=records[str(path)]
            if record['existed']:atomic(path,(backup/record['saved']).read_bytes(),record)
            else:path.unlink(missing_ok=True)
        if was_active:systemctl('start',check=False)
        print('Installation failed; applied file changes restored. Backup:',backup)
        raise
    print('PATCH INSTALLED. Central Sync restarted.' if was_active else 'PATCH INSTALLED. Previously inactive Central Sync remains inactive.')
    print('Backup:',backup)
    print('Rollback: '+sys.executable+' '+str(backup/'install.py')+' --rollback '+str(backup))
    print('Run verify_gateway.py after at least 60 seconds. Backend acceptance still needs a successful upload/ACK.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('INSTALL FAILED:',str(error),file=sys.stderr);sys.exit(1)
