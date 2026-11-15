#!/usr/bin/env python3
"""Bounded read-only field capture; no dashboard/archive enumeration."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time

BASE=Path('/opt/kinetics-gateway/backend')

def resolved(path):
    p=Path(path); return p if p.is_absolute() else BASE/p

def read(path): return json.loads(resolved(path).read_text())

def system(unit):
    r=subprocess.run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState'],
                     capture_output=True,text=True,timeout=5)
    return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)

def process(unit):
    v=system(unit); pid=int(v.get('MainPID') or 0)
    if not pid: return v
    try:
        stat=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        v.update(cpu_sec=(int(stat[11])+int(stat[12]))/os.sysconf('SC_CLK_TCK'),
                 start_ticks=int(stat[19]))
    except (OSError,ValueError,IndexError): pass
    return v

def boot_id(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def sample(cfg):
    result={'utc':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),
            'boot_id':boot_id(),'errors':[]}
    for name,fn in (
        ('central',lambda:read(cfg.get('status_file','/var/lib/kinetics-central-sync/status.json'))),
        ('s2',lambda:read(cfg.get('general_assets',{}).get('status_file','/var/lib/kinetics-central-sync/s2_status.json'))),
        ('archiver',lambda:read(cfg.get('overflow_archive',{}).get('status_file','/var/lib/kinetics-central-sync/overflow_archiver_status.json'))),
        ('recovery',lambda:read(cfg.get('overflow_recovery',{}).get('status_file','/var/lib/kinetics-central-sync/overflow_recovery_status.json'))),
        ('main_process',lambda:process('kinetics-gateway.service')),
        ('sync_process',lambda:process('kinetics-central-sync.service')),
    ):
        try: result[name]=fn()
        except Exception as error: result['errors'].append(name+':'+type(error).__name__)
    try:
        path=resolved(cfg.get('outbox',{}).get('path','/var/lib/kinetics-central-sync/central_sync.db'))
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)
        try:
            started=time.monotonic()
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda:int(time.monotonic()-started>1),1000)
            result['generation']=[dict(zip(('stream','substream','messages','payload_bytes'),row)) for row in db.execute(
                'SELECT stream,substream,messages,payload_bytes FROM generation_metrics_v1')]
            result['recent_live_receipts']=[dict(zip(('message_id','stream','substream','ack_status','ack_epoch','payload_bytes'),row))
                for row in db.execute('SELECT message_id,stream,substream,ack_status,ack_epoch,payload_bytes '
                                      'FROM delivery_receipts_v1 WHERE ack_epoch>=? ORDER BY ack_epoch DESC LIMIT 5001',
                                      (time.time()-300,))]
        finally: db.close()
    except Exception as error: result['errors'].append('generation:'+type(error).__name__)
    return result

def freshness(value, stamp):
    try:
        now=datetime.fromisoformat(stamp)
        updated=datetime.fromisoformat(value.replace('Z','+00:00'))
        return round((now-updated).total_seconds(),2)
    except Exception: return None


