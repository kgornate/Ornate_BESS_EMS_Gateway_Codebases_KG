#!/usr/bin/env python3
"""Passive S2 signal cadence observation against master Excel reference."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import tarfile
import time

BASE=Path('/opt/ornate-ems-gateway/backend')
CONFIG=Path('/etc/ornate-ems-gateway/central_sync.json')

def utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def resolve(value):
    p=Path(value);return p if p.is_absolute() else BASE/p
def load(path):return json.loads(Path(path).read_text())
def fingerprint(value,quality):
    return hashlib.sha256(json.dumps([value,quality],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).digest()
def query(path,sql,args=(),limit=100000,budget=1.5):
    if not Path(path).is_file():raise RuntimeError('database missing: '+Path(path).name)
    clock=time.monotonic();db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)
    try:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-clock>budget),1000)
        out=[]
        for r in db.execute(sql,args):
            if len(out)>=limit or time.monotonic()-clock>budget:raise RuntimeError('bounded read limit reached')
            out.append(dict(r))
        return out
    finally:db.close()

def init(db):
    db.executescript('''PRAGMA journal_mode=WAL;
      CREATE TABLE messages(id INTEGER PRIMARY KEY,mid TEXT UNIQUE,stream TEXT,substream TEXT,
        epoch REAL,bytes INTEGER,records INTEGER);
      CREATE TABLE signals(id INTEGER PRIMARY KEY,runtime TEXT UNIQUE,point TEXT,vendor TEXT,substream TEXT);
      CREATE TABLE emissions(signal_id INTEGER,message_id INTEGER,epoch REAL,fp BLOB,
        policy TEXT,interval REAL,reason TEXT,retention TEXT);
      CREATE INDEX emissions_signal_time ON emissions(signal_id,epoch);
    ''')

def capture(db,item,payload,cache):
    body=json.loads(payload);records=body.get('records',[])
    if not isinstance(records,list):raise RuntimeError('Unexpected records container')
    cur=db.execute('INSERT OR IGNORE INTO messages(mid,stream,substream,epoch,bytes,records) VALUES(?,?,?,?,?,?)',
        (item['message_id'],item['stream'],item['substream'],item['created_epoch'],item['payload_bytes'],len(records)))
    if not cur.rowcount:return 0
    mid=cur.lastrowid
    if item['stream']!='general_asset_telemetry':return 0
    rows=[]
    for r in records:
        runtime=r.get('runtime_signal_id')
        if not runtime:raise RuntimeError('S2 runtime identity missing')
        if runtime not in cache:
            db.execute('INSERT OR IGNORE INTO signals(runtime,point,vendor,substream) VALUES(?,?,?,?)',
                (runtime,r.get('point_id'),r.get('vendor'),item['substream']))
            cache[runtime]=db.execute('SELECT id FROM signals WHERE runtime=?',(runtime,)).fetchone()[0]
        try:interval=float(r.get('central_record_sec'))
        except (TypeError,ValueError):interval=-1
        rows.append((cache[runtime],mid,item['created_epoch'],fingerprint(r.get('value'),r.get('quality')),
          r.get('policy'),interval,r.get('trigger_reason'),r.get('retention_class')))
    db.executemany('INSERT INTO emissions VALUES(?,?,?,?,?,?,?,?)',rows)
    return len(rows)

def analyse(db,ref):
    expected={(r['vendor'],r['point_id']):r for r in ref['points']}
    results=[];covered=set();issue_totals=Counter();reasons=Counter()
    for sid,runtime,point,vendor,sub in db.execute('SELECT id,runtime,point,vendor,substream FROM signals ORDER BY id'):
        rule=expected.get((vendor,point));flags=Counter();triggers=Counter();gaps=[];last=None;count=0
        if rule:covered.add((vendor,point))
        else:flags['unknown_excel_point']+=1
        for stamp,fp,mode,interval,reason,retention in db.execute('SELECT epoch,fp,policy,interval,reason,retention FROM emissions WHERE signal_id=? ORDER BY epoch,message_id',(sid,)):
            count+=1;triggers[reason or 'missing']+=1
            if rule:
                if mode!=rule['mode'] or abs(interval-rule['interval_sec'])>.001 or sub!=rule['substream']:flags['excel_policy_mismatch']+=1
                if retention!=rule['retention']:flags['excel_retention_mismatch']+=1
            if reason not in ('startup','policy_change','clock_reset','change','heartbeat','periodic'):flags['unknown_trigger_reason']+=1
            if last and rule:
                gap=stamp-last[0];gaps.append(gap);expected_sec=rule['interval_sec']
                if reason not in ('startup','policy_change','clock_reset'):
                    if rule['mode']=='PERIODIC' and gap<expected_sec-2:flags['early_periodic_candidate']+=1
                    if rule['mode']=='ON_CHANGE_HEARTBEAT':
                        if reason=='change' and fp==last[1]:flags['unchanged_change_candidate']+=1
                        if reason=='heartbeat' and gap<expected_sec-2:flags['early_heartbeat_candidate']+=1
                    # Late gaps need source/capture evidence; they are NOT automatic violations.
                    if gap>expected_sec+max(10,expected_sec*.10):flags['late_gap_review']+=1
            last=(stamp,fp)
        issue_totals.update(flags);reasons.update(triggers)
        results.append({'runtime_signal_id':runtime,'point_id':point,'vendor':vendor,'substream':sub,
          'observed_emissions':count,'expected':rule,'trigger_counts':dict(triggers),'flags':dict(flags),
          'min_gap_sec':min(gaps) if gaps else None,'max_gap_sec':max(gaps) if gaps else None,
          'mean_gap_sec':sum(gaps)/len(gaps) if gaps else None})
    missing=[r for r in ref['points'] if (r['vendor'],r['point_id']) not in covered]
    return results,missing,dict(issue_totals),dict(reasons)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--duration',type=float,required=True);args=ap.parse_args()
    if not 60<=args.duration<=86400:ap.error('duration out of range')
    os.umask(0o077);root=Path(__file__).resolve().parent;ref=load(root/'reference.json');cfg=load(CONFIG)
    out=Path(args.output);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    live=resolve(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
    arc=resolve(cfg.get('overflow_archive',{}).get('archive_dir','/mnt/ems-logs/ornate-ems-gateway/backlog_archives'))
    pattern=cfg.get('overflow_recovery',{}).get('archive_glob','central_sync_overflow_*.db')
    db=sqlite3.connect(out/'audit.db');init(db);cache={};errors=Counter();completed=False;stop_error=None
    policy=cfg.get('general_assets',{});manifest_checks={'available':False}
    try:
        raw=resolve(policy['s2_policy_manifest_path']).read_bytes()
        sha=hashlib.sha256(raw).hexdigest();manifest=json.loads(raw)
        approved=sha==policy.get('s2_policy_expected_sha256')
        exp={(r['vendor'],r['point_id']):r for r in ref['points']};drift=[]
        for p in manifest['points']:
            r=exp.get((p['vendor'],p['point_id']))
            if not r or any(p[k]!=r[k] for k in ('mode','interval_sec','substream')):drift.append(p['point_id'])
        present={(p['vendor'],p['point_id']) for p in manifest['points']}
        manifest_checks={'available':True,'approved_checksum_matches':approved,'sha256':sha,
          'policy_drift':drift,'missing_excel_points':[r['point_id'] for k,r in exp.items() if k not in present]}
    except Exception as e:manifest_checks['error']=type(e).__name__
    start=time.time();clock=time.monotonic();first_totals=None;last_totals=None;archive_scan=0;last_status=0
    (out/'run.json').write_text(json.dumps({'started_utc':utc(),'duration_sec':args.duration,
        'excel_file':ref['workbook'],'excel_sha256':ref['sha256'],'manifest_checks':manifest_checks},indent=2)+'\n')
    def stop(sig,frame):raise KeyboardInterrupt('signal '+str(sig))
    signal.signal(signal.SIGTERM,stop)
    print('Read-only cadence audit started. Excel S2 policy checks; all-stream message coverage. Results: '+str(out),flush=True)
    try:
      while True:
        paths=[(live,max(start,time.time()-120))]
        if time.monotonic()-archive_scan>=60:
            archive_scan=time.monotonic();archives=sorted(arc.glob(pattern))
            if len(archives)>128:errors['archive_file_limit']+=1;archives=archives[-128:]
            paths += [(p,start) for p in archives]
        budget_start=time.monotonic();bytes_read=0
        for path,cutoff in paths:
          if time.monotonic()-budget_start>4:errors['cycle_read_budget']+=1;break
          try:
            meta=query(path,'SELECT message_id,stream,substream,created_epoch,payload_bytes FROM outbox_messages WHERE created_epoch>=?',(cutoff,))
            for item in meta:
              if db.execute('SELECT 1 FROM messages WHERE mid=?',(item['message_id'],)).fetchone():continue
              if bytes_read+item['payload_bytes']>8*1024*1024:errors['cycle_payload_budget']+=1;break
              payload=query(path,'SELECT payload_json FROM outbox_messages WHERE message_id=?',(item['message_id'],),limit=1)
              if not payload:errors['row_moved_before_read']+=1;continue
              bytes_read+=item['payload_bytes']
              try:
                db.execute('SAVEPOINT one_message')
                capture(db,item,payload[0]['payload_json'],cache)
                db.execute('RELEASE one_message')
              except Exception:
                db.execute('ROLLBACK TO one_message');db.execute('RELEASE one_message');cache.clear();raise
          except Exception as e:errors[path.name+':'+type(e).__name__]+=1
        db.commit()
        try:
            totals=query(live,'SELECT stream,substream,messages,payload_bytes,first_epoch FROM generation_metrics_v1',limit=256)
            if first_totals is None:first_totals=totals
            last_totals=totals
        except Exception as e:errors['generation_counter:'+type(e).__name__]+=1
        elapsed=time.monotonic()-clock
        if elapsed-last_status>=60 or not last_status:
            last_status=elapsed
            counts={'utc':utc(),'elapsed_sec':round(elapsed,1),'captured_messages':db.execute('SELECT count(*) FROM messages').fetchone()[0],
              's2_emissions':db.execute('SELECT count(*) FROM emissions').fetchone()[0],'read_errors':dict(errors)}
            with (out/'capture.jsonl').open('a') as f:f.write(json.dumps(counts)+'\n')
            print(json.dumps(counts),flush=True)
        if elapsed>=args.duration:completed=True;break
        time.sleep(max(0,min(5,args.duration-elapsed)))
    except (Exception,KeyboardInterrupt) as e:stop_error=type(e).__name__+': '+str(e)
    finally:
        db.commit();signals,missing,flags,triggers=analyse(db,ref)
        stream_summary=[{'stream':r[0],'substream':r[1],'messages':r[2],'records':r[3],'bytes':r[4]} for r in db.execute('SELECT stream,substream,count(*),sum(records),sum(bytes) FROM messages GROUP BY stream,substream')]
        report={'completed':completed,'error':stop_error,'elapsed_sec':time.monotonic()-clock,'manifest_checks':manifest_checks,
          'canonical_reference_points':len(ref['points']),'runtime_signals_observed':len(signals),'unobserved_canonical_points':missing,
          'signal_flags':flags,'trigger_totals':triggers,'read_errors':dict(errors),'stream_summary':stream_summary,
          'generation_counter_start':first_totals,'generation_counter_end':last_totals,
          'limits':['Only post-start messages still readable in live/SD DBs are captured; cleanup and move races can cause gaps.',
          'Not all hardware polls are observed. Missing source data, offline meters and gaps prevent all-point certification.',
          'Intervals use envelope creation time with 2s early tolerance; scheduling/HTTP collection latency can affect gaps.',
          'Late gaps and unchanged-change flags are review candidates, not automatic failures.',
          'S2 signal-level audit uses bundled master Excel v1.1; other streams have message-level timing/volume evidence only.',
          'No audit of hardware calibration or backend commit. No upload configuration changes or restart.']}
        (out/'signals.json').write_text(json.dumps(signals,separators=(',',':'))+'\n')
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        lines=['KALPA S2 CADENCE AUDIT','Completed: '+str(completed),'Runtime signals observed: '+str(len(signals)),
          'Canonical Excel points not observed: '+str(len(missing)),'Signal flags: '+json.dumps(flags),
          'Trigger totals: '+json.dumps(triggers),'Capture errors: '+json.dumps(dict(errors)),
          'Coverage must be reviewed before cadence certification. See report.json and signals.json.']
        (out/'summary.txt').write_text('\n'.join(lines)+'\n');db.close()
        with tarfile.open(str(out)+'.tar.gz','w:gz') as tar:
            for p in sorted(out.iterdir()):
                if p.is_file():tar.add(p,arcname=out.name+'/'+p.name)
        print('\n'.join(lines)+'\nShare: '+str(out)+'.tar.gz',flush=True)
    return 0 if completed else 1
if __name__=='__main__':raise SystemExit(main())
