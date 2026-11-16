#!/usr/bin/env python3
"""Repair only six known S4 validation failures, preserving envelope/event IDs."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'payload/app/central_sync'))
from alarm_wire_format import normalize_body
KNOWN=set('c7503e95-f767-4f12-aee1-c3d827fecc03 76af4940-19c9-48a4-82f0-3dd5fd7339d3 3985041b-8fa6-40ac-af23-c52f3cc12b7c e8d0e87a-af43-4669-aecf-44dad15aa754 26e42fc1-7494-4f08-9477-0c267d147d90 afac332a-78ff-486f-904c-4b3c5642d8aa'.split())
UNIT='ornate-central-sync.service'
BASE=Path('/opt/ornate-ems-gateway/backend')
def resolve(p):
    p=Path(p);return p if p.is_absolute() else BASE/p

def plan(paths):
    result=[]
    for path in paths:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            db.row_factory=sqlite3.Row
            marks=','.join('?' for _ in KNOWN)
            rows=db.execute(f'SELECT * FROM outbox_messages WHERE message_id IN ({marks})',tuple(sorted(KNOWN))).fetchall()
            for row in rows:
                original=dict(row)
                if row['state'] in ('acked','dead'): continue
                if row['stream']!='alarms_events':raise RuntimeError('Unexpected stream: '+row['message_id'])
                body=json.loads(row['payload_json']);fixed=normalize_body(body)
                if fixed==body:continue
                if row['state'] not in ('pending','retry'):raise RuntimeError('Alarm still inflight; retry after its lease expires: '+row['message_id'])
                if 'invalid_payload' not in (row['last_error'] or ''):raise RuntimeError('Missing saved validation rejection: '+row['message_id'])
                result.append({'database':str(path),'original':original,
                    'corrected_payload_json':json.dumps(fixed,separators=(',',':'),ensure_ascii=False)})
    return result

def apply_one(entry):
    row=entry['original'];raw=entry['corrected_payload_json']
    with closing(sqlite3.connect(entry['database'],timeout=3)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            cur=db.execute("""UPDATE outbox_messages SET payload_json=?,payload_bytes=?,next_attempt_epoch=?
                WHERE message_id=? AND state=? AND payload_json=? AND last_error=?""",
                (raw,len(raw.encode('utf-8')),time.time(),row['message_id'],row['state'],row['payload_json'],row['last_error']))
            if cur.rowcount!=1:raise RuntimeError('Row changed since audit; no update: '+row['message_id'])

def durable_json(path,body):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(body,f,indent=2);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
    archive=cfg.get('overflow_recovery',{})
    paths=[resolve(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))]
    paths+=sorted(resolve(archive.get('archive_dir','/mnt/ems-logs/ornate-ems-gateway/backlog_archives')).glob(archive.get('archive_glob','central_sync_overflow_*.db')))
    paths=list(dict.fromkeys(p.resolve() for p in paths if p.is_file()))
    if not args.apply:
        entries=plan(paths);print('Would repair:',len(entries),'stored copies; no changes made.')
        for e in entries:print(Path(e['database']).name,e['original']['message_id'])
        return
    if os.geteuid()!=0:raise RuntimeError('Run as root')
    with open('/run/lock/kalpa-alarm-repair.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=subprocess.run(['systemctl','is-active','--quiet',UNIT]).returncode==0
        if active:subprocess.run(['systemctl','stop',UNIT],check=True,timeout=120)
        try:
            entries=plan(paths)
            if not entries:print('No eligible alarm repairs remain.');return
            output=Path('/root/kalpa-alarm-format-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            output.mkdir(mode=0o700)
            durable_json(output/'originals-and-plan.json',entries)
            print('Original payloads and complete rows backed up:',output,flush=True)
            done=[]
            try:
                for e in entries:apply_one(e);done.append({'database':e['database'],'message_id':e['original']['message_id']})
            finally:durable_json(output/'applied.json',done)
            print('Repaired stored alarm copies:',len(done))
            print('Event IDs, message IDs, sequences, attempt counts and saved rejection evidence preserved.')
            print('Still pending/retry: backend ACK is required. Future runs skip already corrected rows.')
        finally:
            if active:subprocess.run(['systemctl','start',UNIT],check=True,timeout=120)

if __name__=='__main__':main()
