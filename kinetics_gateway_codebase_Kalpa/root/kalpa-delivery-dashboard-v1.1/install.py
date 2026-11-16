#!/usr/bin/env python3
"""Guarded additive source insertion; preserve deployed uploader changes."""
import argparse
from datetime import datetime,timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path('/opt/ornate-ems-gateway/backend/app/central_sync')
VIEW=Path('/opt/kalpa-delivery-dashboard/dashboard.py')
MARKER='# KALPA_BATCH_STREAM_TRACE_V1'
HOOK='''        # KALPA_BATCH_STREAM_TRACE_V1
        try:
            await asyncio.to_thread(record_batch_trace, prepared, response, str(self.outbox.path))
        except Exception as trace_error:
            log.warning("Batch membership trace unavailable: %s", type(trace_error).__name__)
'''
def patch_source(source):
    if MARKER in source:return source
    anchor='        self.counters.last_http_status = response.status_code\n'
    imp='from .batcher import OutboxBatcher, PreparedBatch\n'
    if source.count(anchor)!=1 or source.count(imp)!=1:
        raise RuntimeError('Uploader layout differs; no patch applied. Share uploader.py for adaptation.')
    source=source.replace(imp,imp+'from .batch_trace import record_batch_trace\n',1)
    source=source.replace(anchor,anchor+HOOK,1)
    compile(source,'uploader.py','exec')
    return source
def active(unit):
    return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
def write(path,data,mode):
    temp=path.with_name(path.name+'.trace-tmp');temp.write_bytes(data);temp.chmod(mode);os.replace(temp,path)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');a=ap.parse_args()
    src=Path(__file__).resolve().parent
    for line in (src/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split(None,1)
        if hashlib.sha256((src/name.strip()).read_bytes()).hexdigest()!=digest:raise RuntimeError('Package checksum failed: '+name)
    uploader=ROOT/'uploader.py';old=uploader.read_bytes();updated=patch_source(old.decode()).encode()
    if not VIEW.is_file():raise RuntimeError('Install terminal kalpa-delivery-dashboard v1 first')
    for name in ('batch_trace.py','dashboard.py'):compile((src/name).read_text(),name,'exec')
    if a.check_only:print('PASS: patch anchors, syntax and package checksums. No changes made.');return
    if active('kalpa-live-upload-test.service') or active('kalpa-sync-throughput-test.service'):
        raise RuntimeError('A timed test is running. Wait for it to finish before installing; no changes made.')
    was_active=active('ornate-central-sync.service')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path('/root')/('kalpa-batch-trace-backup-'+stamp);backup.mkdir(mode=0o700)
    shutil.copy2(uploader,backup/'uploader.py');shutil.copy2(VIEW,backup/'dashboard.py')
    helper=ROOT/'batch_trace.py';had_helper=helper.exists()
    if had_helper:shutil.copy2(helper,backup/'batch_trace.py')
    if was_active:subprocess.run(['systemctl','stop','ornate-central-sync.service'],check=True)
    try:
        write(helper,(src/'batch_trace.py').read_bytes(),0o644)
        write(uploader,updated,uploader.stat().st_mode&0o777)
        write(VIEW,(src/'dashboard.py').read_bytes(),VIEW.stat().st_mode&0o777)
        if was_active:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=True)
    except Exception:
        shutil.copy2(backup/'uploader.py',uploader);shutil.copy2(backup/'dashboard.py',VIEW)
        if had_helper:shutil.copy2(backup/'batch_trace.py',helper)
        else:helper.unlink(missing_ok=True)
        if was_active:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=False)
        raise
    print('Installed batch stream tracing and terminal update. Backup:',backup)
    print('Central Sync restarted.' if was_active else 'Central Sync remains stopped.')
    print('Reopen kalpa-delivery-dashboard. Stream breakdown appears for NEW completed batches.')
if __name__=='__main__':main()
