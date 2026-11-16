#!/usr/bin/env python3
"""Companion live ACK evidence collector. Does not restart any existing service."""
import argparse
from collections import defaultdict,Counter
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import tarfile
import time

def utc():return datetime.now(timezone.utc).isoformat()
def epoch(value):return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
def readonly(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
    db.execute('PRAGMA query_only=ON');return db
def metadata_ids(path):
    # Do not select payload_json or even trailing record fields here. Only
    # retrieve the small message identity for rows already indexed as ACKed.
    db=readonly(path);clock=time.monotonic()
    try:
        db.set_progress_handler(lambda:int(time.monotonic()-clock>3),1000)
        rows=db.execute("SELECT message_id FROM outbox_messages WHERE state='acked' LIMIT 100001").fetchall()
        if len(rows)>100000:raise RuntimeError('metadata row limit reached')
        return [r[0] for r in rows]
    finally:db.close()
def one_ack(db,mid):
    clock=time.monotonic();db.set_progress_handler(lambda:int(time.monotonic()-clock>1),1000)
    row=db.execute('''SELECT stream,substream,ack_status,acknowledged_utc,payload_bytes,payload_json
        FROM outbox_messages WHERE message_id=? AND state='acked' ''',(mid,)).fetchone()
    if row is None:return None
    stream,sub,status,stamp,size,payload=row
    if status not in ('accepted','already_processed'):raise RuntimeError('unknown ACK status')
    if len(payload)>2*1024*1024:raise RuntimeError('single ACK payload read limit')
    body=json.loads(payload);count=body.get('record_count')
    if not isinstance(count,int) or isinstance(count,bool) or count<0:count=len(body['records'])
    return {'message_id':mid,'stream':stream,'substream':sub,'status':status,'acknowledged_utc':stamp,'records':count,'bytes':int(size)}
def add(row,seen,totals,output):
    mid=row['message_id']
    if mid in seen:return False
    output.write(json.dumps(row,separators=(',',':'))+'\n');output.flush();seen.add(mid)
    key=(row['stream'],row['substream'],row['status'])
    totals[key][0]+=1;totals[key][1]+=row['records'];totals[key][2]+=row['bytes']
    return True
def original_rows(path,offset):
    if not path.is_file():return [],offset
    rows=[]
    with path.open() as f:
        f.seek(offset)
        while True:
            begin=f.tell();line=f.readline()
            if not line:break
            if not line.endswith('\n'):f.seek(begin);break
            rows.append(json.loads(line))
        return rows,f.tell()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--duration',type=float,required=True);a=ap.parse_args()
    if not 60<=a.duration<=86400:ap.error('duration out of range')
    os.umask(0o077)
    original=Path(Path('/root/kalpa-overnight-results-path.txt').read_text().strip())
    run=json.loads((original/'run.json').read_text());cutoff=epoch(run['started_utc'])
    cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
    live=Path(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
    if not live.is_absolute():live=Path('/opt/ornate-ems-gateway/backend')/live
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    (out/'run.json').write_text(json.dumps({'started_utc':utc(),'original_run':str(original),'original_start':run['started_utc'],
        'limits':['ACKs unioned by message ID from original evidence and still-retained live rows.',
          'Cleaned rows missed by both collectors cannot be reconstructed. Direct archive replay ACKs remain excluded.',
          'Original observer continues; its displayed count may remain frozen. Use this combined evidence for analysis.',
          'Source SQLite is read-only; no services, configuration, source or queue state changed.']},indent=2)+'\n')
    seen=set();checked_old=set();totals=defaultdict(lambda:[0,0,0]);errors=Counter();offset=0;completed=False;failure=None
    clock=time.monotonic();last_log=-60
    def stop(sig,frame):raise KeyboardInterrupt('signal '+str(sig))
    signal.signal(signal.SIGTERM,stop)
    print('Read-only companion ACK capture started. Results: '+str(out),flush=True)
    try:
      with (out/'combined_live_ack_evidence.jsonl').open('a') as output:
        while True:
          try:
            rows,offset=original_rows(original/'live_ack_evidence.jsonl',offset)
            for row in rows:add(row,seen,totals,output)
          except Exception as e:errors['original_evidence:'+type(e).__name__]+=1
          try:
            ids=metadata_ids(live);cycle=time.monotonic();size_read=0
            db=readonly(live)
            try:
              for mid in ids:
                if mid in seen or mid in checked_old:continue
                if size_read>=8*1024*1024 or time.monotonic()-cycle>3:break
                try:
                  row=one_ack(db,mid)
                  if row is None:continue
                  size_read+=row['bytes']
                  if epoch(row['acknowledged_utc'])<cutoff:checked_old.add(mid);continue
                  add(row,seen,totals,output)
                except Exception as e:errors['new_ACK:'+type(e).__name__]+=1
            finally:db.close()
          except Exception as e:errors['metadata:'+type(e).__name__]+=1
          elapsed=time.monotonic()-clock
          if elapsed-last_log>=30:
            last_log=elapsed
            status={'utc':utc(),'elapsed_sec':round(elapsed,1),'combined_unique_live_ACKs':len(seen),'errors':dict(errors)}
            (out/'status.json').write_text(json.dumps(status)+'\n')
            print(json.dumps(status),flush=True)
          if elapsed>=a.duration:completed=True;break
          time.sleep(max(0,min(5,a.duration-elapsed)))
    except (Exception,KeyboardInterrupt) as e:failure=type(e).__name__+': '+str(e)
    finally:
        result={'completed':completed,'error':failure,'original_run':str(original),'errors':dict(errors),
          'combined_unique_live_ACKs':len(seen),'by_group':[{'stream':k[0],'substream':k[1],'status':k[2],
          'messages':v[0],'records':v[1],'bytes':v[2]} for k,v in sorted(totals.items())]}
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
        (out/'summary.txt').write_text('Combined retained live ACK evidence: '+str(len(seen))+' unique message IDs\nErrors: '+json.dumps(dict(errors))+'\nDirect archive ACKs excluded; missing cleaned evidence cannot be recovered.\n')
        with tarfile.open(str(out)+'.tar.gz','w:gz') as tar:
            for p in out.iterdir():
                if p.is_file():tar.add(p,arcname=out.name+'/'+p.name)
        print('Combined ACK evidence bundle: '+str(out)+'.tar.gz',flush=True)
    return 0 if completed else 1
if __name__=='__main__':raise SystemExit(main())
