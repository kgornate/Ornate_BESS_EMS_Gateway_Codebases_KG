#!/usr/bin/env python3
"""Compact read-only service/counter capture, independent of dashboard scans."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time

BASE=Path('/opt/ornate-ems-gateway/backend')

def load(p):
    p=Path(p)
    return json.loads((p if p.is_absolute() else BASE/p).read_text())

def record(cfg):
    result={'utc':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),'errors':[]}
    pairs={'live':cfg.get('status_file','/var/lib/ornate-central-sync/status.json'),
           'recovery':cfg.get('overflow_recovery',{}).get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'),
           'archiver':cfg.get('overflow_archive',{}).get('status_file','/var/lib/ornate-central-sync/overflow_archiver_status.json')}
    for key,path in pairs.items():
        try:
            data=load(path)
            result[key]={k:data.get(k) for k in ('status_generated_utc','started_utc','running','transport_mode','adaptive_mode','archive_interval_sec','counters','resource_gate','uploader','outbox','stream_backend_ack')}
        except Exception as exc:result['errors'].append(key+':'+type(exc).__name__)
    db=None
    try:
        p=Path(cfg['outbox']['path']);p=p if p.is_absolute()else BASE/p
        db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+1
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        result['generation']=[dict(r)for r in db.execute('SELECT stream,substream,messages,payload_bytes,first_epoch FROM generation_metrics_v1')]
    except Exception as exc:result['errors'].append('generation:'+type(exc).__name__)
    finally:
        if db is not None:db.close()
    result['load1']=os.getloadavg()[0]
    result['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    result['resources']={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.split(':',1)[0] in ('MemAvailable','MemTotal','SwapFree'):result['resources'][line.split(':',1)[0]]=line.split(':',1)[1].strip()
    r=subprocess.run(['systemctl','show','ornate-central-sync.service','-p','MainPID','-p','NRestarts','-p','MemoryCurrent','-p','CPUUsageNSec','-p','ActiveState'],capture_output=True,text=True,timeout=5)
    result['service']=r.stdout
    return result

def safe(value):
    # Exclude credential-bearing fields. Keep ACK error codes/text for diagnosis.
    if isinstance(value,dict):return {k:safe(v)for k,v in value.items() if not any(t in k.lower()for t in ('token','password','secret','authorization'))}
    if isinstance(value,list):return [safe(v)for v in value]
    if isinstance(value,str):
        import re
        return re.sub(r'(?i)Bearer\s+\S+','Bearer [redacted]',value)[:2500]
    return value

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--duration',type=int,default=1800);a=ap.parse_args()
    if not 60<=a.duration<=86400:ap.error('duration must be 60..86400 seconds')
    out=Path(a.output);out.mkdir(parents=True,mode=0o700,exist_ok=False)
    cfg=load('/etc/ornate-ems-gateway/central_sync.json')
    settings={'started_utc':datetime.now(timezone.utc).isoformat(),'duration_sec':a.duration,
              'archive_policy':load('/etc/ornate-ems-gateway/archive_transport.json'),
              'uploader_settings':cfg.get('uploader',{}),'outbox_settings':cfg.get('outbox',{}),'recovery_settings':cfg.get('overflow_recovery',{})}
    (out/'run.json').write_text(json.dumps(safe(settings),indent=2))
    first=None;last=None;started=time.monotonic();count=0;error=None
    print('Read-only validation capture started:',out,flush=True)
    try:
        with (out/'samples.jsonl').open('a',buffering=1) as f:
            while True:
                row=record(cfg);row['elapsed_sec']=round(time.monotonic()-started,2);f.write(json.dumps(safe(row))+'\n');f.flush()
                if first is None:first=row
                last=row;count+=1
                c=row.get('recovery',{}).get('counters')or{};q=row.get('live',{}).get('outbox')or{}
                print(f'{row["elapsed_sec"]:.0f}s | live {q.get("unacked_count")} | archive accepted {c.get("archive_accepted_count")} | archive MB {c.get("archive_accepted_bytes",0)/1e6:.3f} | mode {row.get("recovery",{}).get("adaptive_mode")} | errors {row["errors"]}',flush=True)
                if time.monotonic()-started>=a.duration:break
                time.sleep(min(15,max(0,a.duration-(time.monotonic()-started))))
    except Exception as exc:error=type(exc).__name__+': '+str(exc)
    report={'completed':error is None and time.monotonic()-started>=a.duration,'error':error,'samples':count,
            'finished_utc':datetime.now(timezone.utc).isoformat(),
            'limits':['Counter deltas require same service instance; segment any reset.','Live outbox stats may be cached up to 30 seconds.','No complete per-message ledger or cadence certification.','Unexpected observer stop/reboot may leave partial samples without this completion file.']}
    (out/'completion.json').write_text(json.dumps(report,indent=2))
    with tarfile.open(str(out)+'.tar.gz','w:gz') as tar:tar.add(out,arcname=out.name)
    print('Share:',str(out)+'.tar.gz',flush=True)
if __name__=='__main__':main()
