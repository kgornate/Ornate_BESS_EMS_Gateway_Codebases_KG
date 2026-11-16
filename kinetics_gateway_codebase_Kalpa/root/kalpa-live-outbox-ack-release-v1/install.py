#!/usr/bin/env python3
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from patching import patch_outbox,patch_dashboard
from queue_metrics import compact_existing

BASE=Path('/opt/ornate-ems-gateway/backend')
APP=BASE/'app/central_sync'
CONFIG=Path('/etc/ornate-ems-gateway/central_sync.json')

def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit],capture_output=True).returncode==0
def put(path,data,mode=0o644):
    tmp=path.with_name(path.name+'.ack-release-tmp');tmp.write_bytes(data);tmp.chmod(mode);os.replace(tmp,path)
def protected_snapshot(db):
    result={}
    selections={'unsent_and_dead':"SELECT * FROM outbox_messages WHERE state!='acked' ORDER BY message_id"}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('sequences','s2_recording_checkpoint','generation_metrics_v1'):
        if table in tables:selections[table]='SELECT * FROM '+table+' ORDER BY 1,2'
    for name,sql in selections.items():
        h=hashlib.sha256();n=0
        for row in db.execute(sql):h.update(json.dumps(tuple(row),separators=(',',':'),default=repr).encode());n+=1
        result[name]={'rows':n,'sha256':h.hexdigest()}
    return result
def restore(backup):
    entries=json.loads((backup/'files.json').read_text())
    for e in entries:
        target=Path(e['target'])
        if e['exists']:shutil.copy2(backup/e['copy'],target)
        else:target.unlink(missing_ok=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');ap.add_argument('--rollback');a=ap.parse_args()
    if a.rollback:
        backup=Path(a.rollback);was=active('ornate-central-sync.service')
        if was:subprocess.run(['systemctl','stop','ornate-central-sync.service'],check=True)
        restore(backup)
        if was:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=True)
        print('Source/config/viewer rollback complete. Removed ACKed payloads are not restored.');return
    root=Path(__file__).resolve().parent
    for line in (root/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split(None,1)
        if hashlib.sha256((root/name.strip()).read_bytes()).hexdigest()!=digest:raise RuntimeError('Package checksum mismatch: '+name)
    config=json.loads(CONFIG.read_text());updated=json.loads(CONFIG.read_text())
    updated.setdefault('outbox',{}).update(acked_retention_hours=0,cleanup_interval_sec=60.0)
    sys.path.insert(0,str(BASE))
    from app.central_sync.config import CentralSyncConfig
    CentralSyncConfig.model_validate(updated)
    changes={APP/'outbox.py':patch_outbox((APP/'outbox.py').read_text()).encode(),APP/'queue_metrics.py':(root/'queue_metrics.py').read_bytes(),
       CONFIG:(json.dumps(updated,indent=2)+'\n').encode()}
    for viewer in (Path('/opt/kalpa-delivery-dashboard/metrics.py'),Path('/opt/kalpa-upload-dashboard/dashboard.py')):
        if viewer.is_file():
            changes[viewer]=patch_dashboard(viewer.read_text()).encode()
            changes[viewer.parent/'queue_metrics.py']=(root/'queue_metrics.py').read_bytes()
    combined=Path('/opt/kalpa-delivery-dashboard/dashboard.py')
    if combined.is_file():
        text=combined.read_text()
        if '# KALPA_RECENT_ACK_METADATA_V1' not in text:
            anchor='        if a.message:\n'
            if text.count(anchor)!=1:raise RuntimeError('Combined dashboard layout differs; no changes made')
            text=text.replace(anchor,'''        # KALPA_RECENT_ACK_METADATA_V1
        try:
            from queue_metrics import receipt_rows
            receipts=receipt_rows(paths['outbox'])
            known={r['message_id'] for r in rows}
            rows=[receipts.get(r['message_id'],r) for r in rows]
            rows.extend(r for mid,r in receipts.items() if mid not in known)
        except Exception as e:
            db_error='ACK receipt metadata unavailable: '+type(e).__name__
'''+anchor,1)
        text=text.replace('accepted = retained LIVE ACKs in last 60s','accepted = compact LIVE ACK receipts in last 60s')
        text=text.replace('accepted rates may include older queued data and exclude direct archive ACKs','accepted rates use short-lived metadata, may include older queued data and exclude direct archive ACKs')
        changes[combined]=text.encode()
    for p,data in changes.items():
        if p.suffix=='.py':compile(data,str(p),'exec')
    path=Path(config.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
    if not path.is_absolute():path=BASE/path
    if not path.is_file():raise RuntimeError('Live outbox database missing; nothing changed')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        before=db.execute('SELECT state,count(*),sum(payload_bytes) FROM outbox_messages GROUP BY state').fetchall()
    print('Preflight PASS: source anchors, syntax, config and package checksums.')
    print('Live message rows before install:',before)
    if a.check_only:print('CHECK ONLY: no files, services or database contents changed.');return
    for unit in ('kalpa-overnight-test.service','kalpa-live-upload-test.service','kalpa-cadence-audit.service','kalpa-ack-capture.service'):
        if active(unit):raise RuntimeError('Measurement still running: '+unit+'; wait for completion before installing')
    backup=Path('/root')/('kalpa-ack-release-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700);entries=[]
    for i,p in enumerate(changes):
        e={'target':str(p),'exists':p.exists(),'copy':str(i)};entries.append(e)
        if p.exists():shutil.copy2(p,backup/str(i))
    (backup/'files.json').write_text(json.dumps(entries,indent=2)+'\n')
    was=active('ornate-central-sync.service')
    if was:subprocess.run(['systemctl','stop','ornate-central-sync.service'],check=True)
    committed=False
    try:
        for p,data in changes.items():put(p,data,(p.stat().st_mode&0o777) if p.exists() else 0o644)
        db=sqlite3.connect(path,timeout=30)
        try:
            db.execute('BEGIN IMMEDIATE');protected=protected_snapshot(db)
            removed=compact_existing(db)
            if protected_snapshot(db)!=protected:raise RuntimeError('Protected message/cadence state changed; database migration rolled back')
            db.commit();committed=True
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            page=db.execute('PRAGMA page_size').fetchone()[0];pages=db.execute('PRAGMA page_count').fetchone()[0];free=db.execute('PRAGMA freelist_count').fetchone()[0]
            report={'removed_valid_acked_rows':removed,'protected_state':protected,
                    'logical_used_MiB':page*(pages-free)/1048576,'reusable_MiB':page*free/1048576,
                    'remaining_states':db.execute('SELECT state,count(*) FROM outbox_messages GROUP BY state').fetchall()}
            (backup/'migration.json').write_text(json.dumps(report,indent=2)+'\n')
        except BaseException:
            db.rollback();raise
        finally:db.close()
        if was:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=True)
    except BaseException:
        restore(backup)
        if was:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=False)
        print('Code/config restored after install failure. ACK removal committed:',committed)
        raise
    print('PATCH INSTALLED: valid ACKed payloads released immediately.')
    print(json.dumps(report,indent=2))
    print('Pending/retry/inflight/dead rows, sequences, checkpoints and generation counters: preserved.')
    print('SD archives untouched. Backup:',backup)
    print('Central Sync restarted.' if was else 'Central Sync remains stopped.')
    print('Physical file size may remain large; freed pages are reusable. Run verify_gateway.py.')
    print('Rollback restores source/config only; deleted ACKed payloads are not recreated.')
if __name__=='__main__':main()
