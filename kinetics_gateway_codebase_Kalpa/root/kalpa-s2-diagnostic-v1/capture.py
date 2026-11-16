#!/usr/bin/env python3
"""Small current-state capture: live ACKs versus historical S2 retries."""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import json
import os
import re
import signal
import sqlite3
import subprocess
import tarfile
import time

BASE=Path('/opt/ornate-ems-gateway/backend')
def utc():return datetime.now(timezone.utc).isoformat()
def clean(value):
    s=str(value or '')
    s=re.sub(r'(?i)Bearer\s+[^\s"\'<>]+','Bearer [REDACTED]',s)
    s=re.sub(r'''(?ix)(["']?(?:[\w-]*(?:password|secret|token|authorization)[\w-]*)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',r'\1[REDACTED]',s)
    s=re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b','[REDACTED]',s)
    return ''.join(c if c.isprintable() else ' ' for c in s)[:1500]
def resolve(value):
    p=Path(value);return p if p.is_absolute() else BASE/p
def load(path):
    p=Path(path)
    if p.stat().st_size>2*1024*1024:raise RuntimeError('status file exceeds limit')
    return json.loads(p.read_text())
def query(path,sql,args=(),limit=10000,budget=1.5):
    path=Path(path)
    if not path.is_file():raise RuntimeError('database missing')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2);clock=time.monotonic()
    try:
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA temp_store=MEMORY');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-clock>budget),1000)
        rows=[]
        for r in db.execute(sql,args):
            if len(rows)>=limit or time.monotonic()-clock>budget:raise RuntimeError('bounded query limit reached')
            rows.append(dict(r))
        return rows
    finally:db.close()
def status(cfg,section,default,main=False):
    settings=cfg if main else cfg.get(section,{})
    s=load(resolve(settings.get('status_file',default)))
    if main:
        return {'status_generated_utc':s.get('status_generated_utc'),'started_utc':s.get('started_utc'),
          'uploader':{k:(clean(v) if k=='last_error' else v) for k,v in s.get('uploader',{}).items()},
          'stream_backend_ack':{stream:{k:(clean(v) if k=='last_error' else v) for k,v in r.items()} for stream,r in s.get('stream_backend_ack',{}).items()},
          'outbox':s.get('outbox'),'paused':s.get('transport_control',{}).get('paused')}
    counters=s.get('counters',{})
    result={'status_generated_utc':s.get('status_generated_utc'),'started_utc':s.get('started_utc'),
        'running':s.get('running'),'last_error':clean(s.get('last_error')),
        'counters':{k:(clean(v) if k=='last_error' else v) for k,v in counters.items()},'resource_gate':s.get('resource_gate')}
    if section=='recovery_uploader':return status({'status_file':str(resolve(default))},'',default,main=True)
    return result
def samples(live,archives,out,label):
    rows=[];payloads=[]
    for path in [live]+archives:
        for sub in ('bms_extended','ems_system','pcs_extended'):
            try:
                found=query(path,'''SELECT message_id,stream,substream,state,created_at_utc,
                    last_attempt_utc,attempt_count,last_error,backend_detail,payload_bytes
                    FROM outbox_messages WHERE stream='general_asset_telemetry' AND substream=?
                    AND state IN ('pending','retry','inflight') ORDER BY created_epoch ASC LIMIT 1''',(sub,),limit=1)
                for r in found:
                    r['source_db']=str(path);r['last_error']=clean(r.get('last_error'));r['backend_detail']=clean(r.get('backend_detail'))
                    rows.append(r)
                    if len(payloads)<3:
                        body=query(path,'SELECT payload_json FROM outbox_messages WHERE message_id=?',(r['message_id'],),limit=1)
                        if body and len(body[0]['payload_json'])<=150000:
                            payloads.append({'message_id':r['message_id'],'source_db':str(path),'envelope':json.loads(body[0]['payload_json'])})
            except Exception as e:rows.append({'source_db':str(path),'substream':sub,'unavailable':type(e).__name__})
    (out/(label+'_s2_retry_examples.json')).write_text(json.dumps(rows,indent=2)+'\n')
    (out/(label+'_s2_payload_examples.json')).write_text(json.dumps(payloads,indent=2)+'\n')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args();os.umask(0o077)
    cfg=load('/etc/ornate-ems-gateway/central_sync.json');out=Path(a.output);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    live=resolve(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
    archive_dir=resolve(cfg.get('overflow_archive',{}).get('archive_dir','/mnt/ems-logs/ornate-ems-gateway/backlog_archives'))
    archive_files=sorted(archive_dir.glob(cfg.get('overflow_recovery',{}).get('archive_glob','central_sync_overflow_*.db')))
    archives=list(dict.fromkeys(archive_files[:2]+archive_files[-4:]))
    safe_config={name:{k:cfg.get(name,{}).get(k) for k in keys} for name,keys in (
        ('backend',('timeout_sec','connect_timeout_sec','gzip_enabled')),
        ('uploader',('worker_count','max_messages_per_request','max_request_bytes','idle_sleep_sec','coalescing_window_sec')),
        ('outbox',('acked_retention_hours','cleanup_interval_sec','max_db_size_mb')),
        ('overflow_recovery',('enabled','request_pause_sec','max_messages_per_request','max_request_bytes','live_pause_sendable_count','live_pause_oldest_age_sec','max_load1')))}
    start=time.time();clock=time.monotonic();seen=set();error=None;complete=False
    (out/'run.json').write_text(json.dumps({'started_utc':utc(),'duration_sec':300,'settings':safe_config,
       'sampled_archive_files':[str(p) for p in archives],
       'limits':['Current 5-minute observation, not a recreation of overnight backend responses.',
         'Live ACK receipts exclude direct archive ACKs. Rejection counters count outcomes, not distinct messages.',
         'Retry/payload examples are a bounded sample, not a complete backlog inventory.',
         'Full HTTP 200 per-message response bodies are not stored by existing tracing.',
         'Includes up to three raw S2 telemetry envelopes per endpoint sample; no credentials/config/env exports.']},indent=2)+'\n')
    def stop(sig,frame):raise KeyboardInterrupt('signal '+str(sig))
    signal.signal(signal.SIGTERM,stop)
    print('Read-only S2 diagnostic: five minutes; no restart/config/queue changes.',flush=True)
    samples(live,archives,out,'start')
    try:
      with (out/'live_acks.jsonl').open('a') as ackfile,(out/'snapshots.jsonl').open('a') as snapshotfile:
        while True:
          elapsed=time.monotonic()-clock;s={'utc':utc(),'elapsed_sec':round(elapsed,1),'errors':[]}
          tasks=[('live',lambda:status(cfg,'','/var/lib/ornate-central-sync/status.json',main=True)),
            ('recovery',lambda:status(cfg,'overflow_recovery','/var/lib/ornate-central-sync/overflow_recovery_status.json')),
            ('archiver',lambda:status(cfg,'overflow_archive','/var/lib/ornate-central-sync/overflow_archiver_status.json')),
            ('recovery_uploader',lambda:status(cfg,'recovery_uploader',cfg.get('overflow_recovery',{}).get('uploader_status_file','/var/lib/ornate-central-sync/overflow_recovery_uploader_status.json')))]
          for key,read in tasks:
            try:s[key]=read()
            except Exception as e:s['errors'].append(key+':'+type(e).__name__)
          try:
            for r in query(live,'SELECT * FROM delivery_receipts_v1 WHERE ack_epoch>=?',(start,),limit=10000):
                if r['message_id'] in seen:continue
                seen.add(r['message_id']);ackfile.write(json.dumps(r)+'\n')
            ackfile.flush()
          except Exception as e:s['errors'].append('ACK receipts:'+type(e).__name__)
          try:s['generation']=query(live,'SELECT * FROM generation_metrics_v1',limit=256)
          except Exception as e:s['errors'].append('generation:'+type(e).__name__)
          s['live_ACKs_captured']=len(seen);snapshotfile.write(json.dumps(s)+'\n');snapshotfile.flush()
          print(f'{int(elapsed)}s | unique live ACKs captured {len(seen)} | capture errors {s["errors"]}',flush=True)
          if elapsed>=300:complete=True;break
          time.sleep(max(0,min(15,300-elapsed)))
    except (Exception,KeyboardInterrupt) as e:error=type(e).__name__+': '+str(e)
    finally:
        samples(live,archives,out,'end')
        try:
            trace=query('/var/lib/ornate-central-sync/batch_trace.db',
              'SELECT request_id,completed_utc,source_db,http_status,message_count,payload_bytes,groups_json,members_json,detail FROM batch_trace WHERE completed_epoch>=? ORDER BY completed_epoch',(start,),limit=1000)
            for r in trace:r['detail']=clean(r.get('detail'))
            (out/'http_batches.json').write_text(json.dumps(trace,indent=2)+'\n')
        except Exception as e:(out/'http_unavailable.txt').write_text(type(e).__name__+'\n')
        (out/'completion.json').write_text(json.dumps({'completed':complete,'error':error,'finished_utc':utc(),'live_ACKs_captured':len(seen)},indent=2)+'\n')
        with tarfile.open(str(out)+'.tar.gz','w:gz') as tar:
            for p in sorted(out.iterdir()):
                if p.is_file():tar.add(p,arcname=out.name+'/'+p.name)
        print('Share this bundle: '+str(out)+'.tar.gz',flush=True)
    return 0 if complete else 1
if __name__=='__main__':raise SystemExit(main())
