#!/usr/bin/env python3
"""Add transactional generation byte counters; leave transport unchanged."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

TARGET=Path('/opt/ornate-ems-gateway/backend/app/central_sync/outbox.py')
VIEW=Path('/opt/kalpa-delivery-dashboard/dashboard.py')
MARKER='# KALPA_GENERATION_BYTES_V1'
METHOD='''    # KALPA_GENERATION_BYTES_V1
    def _init_generation_metrics(self):
        self.conn.execute("""CREATE TABLE IF NOT EXISTS generation_metrics_v1 (
            stream TEXT NOT NULL, substream TEXT NOT NULL,
            messages INTEGER NOT NULL, payload_bytes INTEGER NOT NULL,
            first_epoch REAL NOT NULL,
            PRIMARY KEY(stream,substream))""")
        self.conn.commit()

    def _add_generation_metrics(self, stream, substream, messages, payload_bytes):
        # Caller holds the existing lock and transaction. No separate COMMIT.
        self.conn.execute("""INSERT INTO generation_metrics_v1
            (stream,substream,messages,payload_bytes,first_epoch)
            VALUES(?,?,?,?,?) ON CONFLICT(stream,substream) DO UPDATE SET
            messages=messages+excluded.messages,
            payload_bytes=payload_bytes+excluded.payload_bytes""",
            (stream,substream or '',messages,payload_bytes,time.time()))

'''

def patch_source(source):
    if MARKER in source:return source
    init='        self._init_schema()\n'
    method='    def enqueue(self, message: LogicalMessage) -> bool:\n'
    normal='                self.conn.commit()\n                self._invalidate_stats_cache()\n                return True\n'
    s2='                self.conn.commit()\n                self._invalidate_stats_cache()\n                return emitted\n'
    for anchor in (init,method,normal,s2):
        if source.count(anchor)!=1:raise RuntimeError('Outbox source differs; no changes made. Share outbox.py for adaptation.')
    source=source.replace(init,init+'        self._init_generation_metrics()\n',1)
    source=source.replace(method,METHOD+method,1)
    source=source.replace(normal,'                self._add_generation_metrics(message.stream, message.substream, 1, payload_bytes)\n'+normal,1)
    source=source.replace(s2,'                self._add_generation_metrics(stream, substream, len(emitted), total_size)\n'+s2,1)
    compile(source,'outbox.py','exec')
    return source

def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
def write(path,data,mode):
    temp=path.with_name(path.name+'.generation-tmp')
    temp.write_bytes(data);temp.chmod(mode);os.replace(temp,path)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
    root=Path(__file__).resolve().parent
    for line in (root/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split(None,1)
        if hashlib.sha256((root/name.strip()).read_bytes()).hexdigest()!=digest:raise RuntimeError('Package checksum mismatch')
    old=TARGET.read_bytes();new=patch_source(old.decode()).encode()
    if not VIEW.is_file():raise RuntimeError('Existing kalpa-delivery-dashboard required')
    if 'RECENT HTTP BATCHES' not in VIEW.read_text():raise RuntimeError('Unexpected viewer layout')
    compile((root/'dashboard.py').read_text(),'dashboard.py','exec')
    if args.check_only:print('PASS: source anchors, syntax and checksums; nothing changed.');return
    for unit in ('kalpa-live-upload-test.service','kalpa-sync-throughput-test.service'):
        if active(unit):raise RuntimeError('Timed test active; wait for completion before installing.')
    backup=Path('/root')/('kalpa-generation-mb-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(TARGET,backup/'outbox.py');shutil.copy2(VIEW,backup/'dashboard.py')
    running=active('ornate-central-sync.service')
    if running:subprocess.run(['systemctl','stop','ornate-central-sync.service'],check=True)
    try:
        write(TARGET,new,TARGET.stat().st_mode&0o777)
        write(VIEW,(root/'dashboard.py').read_bytes(),VIEW.stat().st_mode&0o777)
        if running:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=True)
    except BaseException:
        shutil.copy2(backup/'outbox.py',TARGET);shutil.copy2(backup/'dashboard.py',VIEW)
        if running:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=False)
        raise
    print('Installed generation MB/min counters. Backup:',backup)
    print('Central Sync restarted.' if running else 'Central Sync remains stopped.')
    print('Reopen kalpa-delivery-dashboard. MB/min warms up for 60 seconds; old data is excluded.')
if __name__=='__main__':main()
