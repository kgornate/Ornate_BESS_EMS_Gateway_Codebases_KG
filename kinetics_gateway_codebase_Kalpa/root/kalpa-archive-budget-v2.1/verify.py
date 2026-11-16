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

BASE=Path('/opt/ornate-ems-gateway/backend')

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
        ('central',lambda:read(cfg.get('status_file','/var/lib/ornate-central-sync/status.json'))),
        ('s2',lambda:read(cfg.get('general_assets',{}).get('status_file','/var/lib/ornate-central-sync/s2_status.json'))),
        ('archiver',lambda:read(cfg.get('overflow_archive',{}).get('status_file','/var/lib/ornate-central-sync/overflow_archiver_status.json'))),
        ('recovery',lambda:read(cfg.get('overflow_recovery',{}).get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))),
        ('main_process',lambda:process('ornate-ems-gateway.service')),
        ('sync_process',lambda:process('ornate-central-sync.service')),
    ):
        try: result[name]=fn()
        except Exception as error: result['errors'].append(name+':'+type(error).__name__)
    try:
        path=resolved(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--duration',type=int,default=300)
    args=ap.parse_args()
    if not 10<=args.duration<=3600: raise SystemExit('Duration must be 10..3600 seconds')
    cfg=read('/etc/ornate-ems-gateway/central_sync.json')
    output=Path('/root/kalpa-archive-budget-check-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(mode=0o700); first=None; last=None; receipts={}; capture_errors=0; decisions={}; resets=False; previous=None
    settings=Path('/etc/ornate-ems-gateway/archive_transport.json')
    if settings.is_file():
        (output/'archive_transport.json').write_bytes(settings.read_bytes())
        (output/'archive_transport.json').chmod(0o600)
    start_epoch=time.time()
    print('Read-only verification; keep this terminal open for',args.duration,'seconds.',flush=True)
    print('Results:',output,flush=True)
    started=time.monotonic()
    with (output/'samples.jsonl').open('w') as handle:
        os.chmod(handle.name,0o600)
        while True:
            s=sample(cfg); first=first or s; last=s
            capture_errors+=bool(s['errors'])
            recovery=s.get('recovery',{}); rc=recovery.get('counters',{})
            decision=rc.get('last_pause_reason') or 'admitted'
            decisions[decision]=decisions.get(decision,0)+1
            if previous:
                for key in ('main_process','sync_process'):
                    if (s.get(key,{}).get('MainPID'),s.get(key,{}).get('start_ticks')) != (previous.get(key,{}).get('MainPID'),previous.get(key,{}).get('start_ticks')): resets=True
            previous=s
            for row in s.get('recent_live_receipts',[]):
                if row['ack_epoch']>=start_epoch: receipts[row['message_id']]=row
            handle.write(json.dumps(s,separators=(',',':'))+'\n'); handle.flush()
            status=s.get('central',{}); q=status.get('outbox',{}); u=status.get('uploader',{})
            print(f"{time.monotonic()-started:.0f}s | live={q.get('unacked_count')} | accepted={u.get('accepted_count')}"
                  f" | rejected={u.get('retryable_rejected_count')} | failures={u.get('request_failure_count')}"
                  f" | status age={freshness(status.get('status_generated_utc'),s['utc'])}s"
                  f" | SD accepted={rc.get('archive_accepted_count')} | recovery={decision}"
                  f" | budget={recovery.get('archive_bandwidth_budget',{}).get('MB_per_min')} MB/min"
                  f" | errors={s['errors']}",flush=True)
            remaining=args.duration-(time.monotonic()-started)
            if remaining<=0: break
            time.sleep(min(15,remaining))
    elapsed=last['monotonic']-first['monotonic']
    same=all(first.get(k,{}).get('start_ticks') is not None and
             first[k].get('start_ticks')==last.get(k,{}).get('start_ticks') and
             first[k].get('MainPID')==last.get(k,{}).get('MainPID')
             for k in ('main_process','sync_process')) and first['boot_id']==last['boot_id']
    summary={'elapsed_sec':elapsed,'same_processes_and_boot':same and not resets, 'recovery_decision_samples':decisions,
             'samples_with_errors':capture_errors,
             'limits':['CPU figures are percent of one core.',
                       'Accepted counters can include queued older messages.',
                       'No generation-cohort delivery percentage or cadence certification.',
                       'Status outbox counts may be cached; inspect status ages and errors.',
                       'Archive counters are recovery-instance totals, not remaining backlog counts.']}
    if same and not resets:
        summary['cpu_percent_one_core']={key:round(100*(last[key]['cpu_sec']-first[key]['cpu_sec'])/elapsed,2)
                                        for key in ('main_process','sync_process') if 'cpu_sec' in last[key] and 'cpu_sec' in first[key]}
        a=first.get('central',{}).get('uploader',{}); b=last.get('central',{}).get('uploader',{})
        summary['live_uploader_deltas']={k:b[k]-a[k] for k in ('accepted_count','already_processed_count','retryable_rejected_count','request_count','request_failure_count') if k in a and k in b}
    a={(r['stream'],r['substream']):r for r in first.get('generation',[])}
    b={(r['stream'],r['substream']):r for r in last.get('generation',[])}
    if a and a.keys()==b.keys() and all(b[k]['payload_bytes']>=a[k]['payload_bytes'] for k in a):
        summary['durable_generation_MB_per_min']=round(sum(b[k]['payload_bytes']-a[k]['payload_bytes'] for k in a)/1e6*60/elapsed,3)
    accepted=[r for r in receipts.values() if r['ack_status']=='accepted']
    summary['observed_live_accepted_MB_per_min']=round(sum(r['payload_bytes'] for r in accepted)/1e6*60/elapsed,3)
    summary['observed_live_accepted_messages']=len(accepted)
    summary['limits'].append('Receipt capture is sampled and bounded; cleaned or missed receipts cannot be reconstructed.')
    ra=first.get('recovery',{}); rb=last.get('recovery',{})
    if ra.get('started_utc') and ra.get('started_utc')==rb.get('started_utc'):
        a=ra.get('counters',{}); b=rb.get('counters',{})
        keys=('archive_accepted_count','archive_accepted_bytes','archive_retryable_rejected_count','archive_http_attempt_count','archive_http_failure_count','archive_http_duration_total_ms','archive_http_duration_max_ms')
        summary['archive_deltas']={k:b[k]-a[k] for k in keys if k in a and k in b}
        if 'archive_accepted_bytes' in a and 'archive_accepted_bytes' in b:
            summary['archive_accepted_MB_per_min']=(b['archive_accepted_bytes']-a['archive_accepted_bytes'])/1e6*60/elapsed
    a=first.get('archiver',{}); b=last.get('archiver',{})
    if a.get('started_utc') and a.get('started_utc')==b.get('started_utc'):
        ac=a.get('counters',{}); bc=b.get('counters',{})
        summary['archiver_deltas']={k:bc[k]-ac[k] for k in ('archived_message_count','archived_payload_bytes','source_deleted_count') if k in ac and k in bc}
    summary['limits'].append('No cross-database unique backlog count: source deletions and archive receipts are separate observations.')
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    (output/'summary.json').chmod(0o600)
    bundle=Path(str(output)+'.tar.gz')
    with tarfile.open(bundle,'w:gz') as archive: archive.add(output,arcname=output.name)
    bundle.chmod(0o600)
    print(json.dumps(summary,indent=2)); print('Share:',bundle)

if __name__=='__main__': main()
