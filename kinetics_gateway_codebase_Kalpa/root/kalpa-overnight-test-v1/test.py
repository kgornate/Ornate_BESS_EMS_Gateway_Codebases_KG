#!/usr/bin/env python3
"""Read-only overnight live generation/delivery observation. No outage simulation."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import tarfile
import evidence
CONFIG=Path('/etc/ornate-ems-gateway/central_sync.json')
BACKEND=Path('/opt/ornate-ems-gateway/backend')
SERVICE='ornate-central-sync.service'
PRODUCERS=[('fast_bess','s1',True),('general_assets','s2',True),('gateway_health','s3',True),('alarms_events','s4',True),('soc_controller','s5',False),('solis','s6',False),('edge_ai','s7',False),('configuration_audit','s8',True),('charge_discharge','s10',True)]
def utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def epoch(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError):return None

def read_json(path):
    path=Path(path)
    if path.stat().st_size>2*1024*1024:raise ValueError('JSON exceeds read limit')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('expected JSON object')
    return value

def safe_json(path,obj):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
    temporary.chmod(0o600);os.replace(temporary,path)

def path_for(value):
    path=Path(value);return path if path.is_absolute() else BACKEND/path

def run(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=3).stdout.strip()

def database_inventory(live,archive_dir,archive_glob,now=None,budget=3,max_rows=100000,max_files=128):
    """Deduplicate pending IDs across live/archive copies and retained ACK evidence.

    Cross-file snapshots are not atomic. Scan failures invalidate aggregate totals.
    Only small row metadata is read; full payloads are not scanned for inventory.
    """
    now=time.time() if now is None else now
    archives=sorted(Path(archive_dir).glob(archive_glob))
    if len(archives)>max_files:raise RuntimeError('archive inventory exceeds file limit')
    pending={};acked=set();dead=set();live_ids=set();archive_ids=set();rows=0
    for path in [Path(live)]+archives:
        if not path.is_file():raise RuntimeError('database missing: '+path.name)
        db=None;started=time.monotonic()
        try:
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.3)
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda:int(time.monotonic()-started>budget),1000)
            for mid,stream,sub,state,size,created in db.execute('''SELECT message_id,stream,substream,state,payload_bytes,created_epoch FROM outbox_messages'''):
                rows+=1
                if rows>max_rows or time.monotonic()-started>budget:raise RuntimeError('inventory read limit reached')
                if state=='acked':acked.add(mid)
                elif state=='dead':dead.add(mid)
                elif state in ('pending','retry','inflight'):
                    pending[mid]={'stream':stream,'substream':sub,'bytes':int(size),'created':float(created),'state':state}
                    (live_ids if path==Path(live) else archive_ids).add(mid)
                else:raise RuntimeError('unknown outbox state')
        finally:
            if db is not None:db.close()
    # A retained ACK can coexist with an earlier copied retry row.
    for mid in acked:pending.pop(mid,None)
    ids=set(pending)
    by_group=Counter((r['stream'],r['substream']) for r in pending.values())
    result={'unique_unacked':len(ids),'logical_bytes':sum(r['bytes'] for r in pending.values()),
       'live_unacked':len(live_ids&ids),'archive_unacked':len(archive_ids&ids),
       'dead_ids':len(dead-acked),'archive_files':len(archives),
       'oldest_age_sec':max((max(0,now-r['created']) for r in pending.values()),default=0),
       'groups':[{'stream':k[0],'substream':k[1],'unacked':v} for k,v in sorted(by_group.items())]}
    return result,ids,acked,pending

def recent_acks(live,cutoff,seen):
    """New retained live ACK evidence, including record/byte volume, deduped by ID."""
    db=None;started=time.monotonic();result=[];size_read=0
    try:
        db=sqlite3.connect(Path(live).resolve().as_uri()+'?mode=ro',uri=True,timeout=.3)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda:int(time.monotonic()-started>1),1000)
        for mid,stream,sub,ack,stamp,size,payload in db.execute('''SELECT message_id,stream,substream,ack_status,acknowledged_utc,payload_bytes,payload_json
                FROM outbox_messages WHERE state='acked' AND acknowledged_utc>=?''',(cutoff,)):
            if mid in seen:continue
            size_read+=len(payload.encode())
            if size_read>32*1024*1024 or len(result)>10000 or time.monotonic()-started>1:raise RuntimeError('ACK read budget exceeded')
            if ack not in ('accepted','already_processed'):raise RuntimeError('unknown ACK status')
            body=json.loads(payload);records=body.get('record_count')
            if not isinstance(records,int) or isinstance(records,bool) or records<0:
                records=len(body['records'])
            result.append({'message_id':mid,'stream':stream,'substream':sub,'status':ack,
                'acknowledged_utc':stamp,'records':records,'bytes':int(size)})
        return result
    finally:
        if db is not None:db.close()

def producer_deltas(a,b):
    result={}
    for key,start in a.get('producers',{}).items():
        end=b.get('producers',{}).get(key)
        if not end or end.get('started_utc')!=start.get('started_utc'):
            result[key]={'available':False,'reason':'producer missing/restarted'};continue
        values={n:int(end.get(n,0))-int(start.get(n,0)) for n in ('message_count','record_count','failure_count')}
        result[key]={'available':all(v>=0 for v in values.values()),**values}
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--duration',type=float,required=True)
    parser.add_argument('--interval',type=float,default=30)
    a=parser.parse_args()
    if not 60<=a.duration<=24*3600:parser.error('duration must be 60 seconds to 24 hours')
    if not 10<=a.interval<=60:parser.error('interval must be 10 to 60 seconds')
    os.umask(0o077)
    cfg=read_json(CONFIG)
    if run(['systemctl','is-active',SERVICE])!='active':raise RuntimeError('Central Sync must already be active')
    pause=path_for(cfg.get('uploader',{}).get('pause_file','/var/lib/ornate-central-sync/uploader.pause'))
    if pause.exists():raise RuntimeError('Uploader is paused; test makes no changes')
    if cfg.get('command_downlink',{}).get('enabled',False):raise RuntimeError('D1 must remain disabled for this monitoring test')
    out=Path(a.output);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    live=path_for(cfg.get('status_file','/var/lib/ornate-central-sync/status.json'))
    dbpath=path_for(cfg.get('outbox',{}).get('path','/var/lib/ornate-central-sync/central_sync.db'))
    arc=path_for(cfg.get('overflow_archive',{}).get('archive_dir','/mnt/ems-logs/ornate-ems-gateway/backlog_archives'))
    rec=cfg.get('overflow_recovery',{})
    recpath=path_for(rec.get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))
    paths={k:path_for(cfg.get(k,{}).get('status_file',f'/var/lib/ornate-central-sync/{s}_status.json'))
      for k,s,default in PRODUCERS if cfg.get(k,{}).get('enabled',default)}
    started=time.time();startstamp=utc();clock=time.monotonic();next_due=clock
    samples=[];seen=set();ack_totals={};observed_groups=set();error=None;complete=False
    http_seen=set();http_totals=Counter();generation_start=None;generation_end=None
    http_last_id=0
    limits=['Generation message/record counts use producer-counter deltas, not retained-row generation estimates.',
      'Live accepted record/MB totals include pre-existing messages accepted during the test; not a matched generation cohort.',
      'Direct archive replay ACK record/byte volume is unavailable; never added to the live totals.',
      'Live/archive queue counts deduplicate copies, but cross-database snapshots are not atomic.',
      'No outage, upload pause, gateway configuration/source changes, service restart or hardware commands are performed.',
      'All-stream delivery requires source/substream evidence; idle/disabled/unmapped streams cannot be certified.',
      'Gateway ACK evidence is not an independent verification of backend database commit.']
    safe_json(out/'run.json',{'started_utc':startstamp,'duration_sec':a.duration,'interval_sec':a.interval,'kind':'OVERNIGHT_LIVE_ONLY','limits':limits,'settings':evidence.config_summary(cfg)})
    def stop(sig,frame):raise KeyboardInterrupt('Test received signal '+str(sig))
    signal.signal(signal.SIGTERM,stop)
    print(f'OVERNIGHT live-only test started; duration {a.duration/3600:.2f} hours. No pause or outage will be introduced.',flush=True)
    print('Results: '+str(out),flush=True)
    try:
      while True:
        elapsed=time.monotonic()-clock
        snap={'utc':utc(),'elapsed_sec':round(elapsed,2),'producers':{},'errors':[]}
        if run(['systemctl','is-active',SERVICE])!='active':raise RuntimeError('Central Sync is no longer active; no restart attempted')
        if pause.exists():raise RuntimeError('External upload pause detected; marker left untouched')
        try:
          s=read_json(live);stamp=epoch(s.get('status_generated_utc'))
          if stamp is None or time.time()-stamp>20:raise RuntimeError('Uploader status stale')
          snap['instance']=s.get('started_utc')
          snap['uploader']={k:s.get('uploader',{}).get(k) for k in ('request_count','request_success_count','request_failure_count',
             'accepted_count','already_processed_count','retryable_rejected_count','permanent_rejected_count','missing_ack_count')}
          snap['streams']={k:{n:r.get(n) for n in ('acked_count','accepted_count','already_processed_count','retryable_rejected_count',
             'permanent_rejected_count','missing_ack_count','last_backend_result')} for k,r in s.get('stream_backend_ack',{}).items()}
          for key,result in s.get('stream_backend_ack',{}).items():
            snap['streams'][key]['last_error']=evidence.clean(result.get('last_error'))
          snap['last_transport_error']=evidence.clean(s.get('uploader',{}).get('last_error'))
        except Exception as e:snap['errors'].append('Status: '+type(e).__name__+': '+str(e))
        for key,path in paths.items():
          try:
            p=read_json(path);snap['producers'][key]={k:p.get(k) for k in ('started_utc','running','message_count','record_count','failure_count','last_poll_utc')}
          except Exception as e:snap['errors'].append('Producer '+key+': '+type(e).__name__)
        try:
          totals=evidence.generation(dbpath);snap['generation_bytes_counters']=totals
          if generation_start is None:generation_start=(elapsed,totals)
          generation_end=(elapsed,totals)
        except Exception as e:snap['errors'].append('Generation bytes: '+type(e).__name__)
        try:
          attempts=evidence.attempts(dbpath,startstamp[:19],http_seen,after_id=http_last_id)
          for item in attempts:
            http_last_id=max(http_last_id,item['id'])
            if (epoch(item['attempted_utc']) or 0)<started:continue
            try:item['batch_trace']=evidence.trace(item['request_id'])
            except Exception as e:item['trace_unavailable']=type(e).__name__
            with (out/'http_batches.jsonl').open('a') as f:f.write(json.dumps(item)+'\n')
            http_seen.add((item['id'],item['request_id']))
            http_totals['requests']+=1;http_totals['attempted_bytes']+=item['payload_bytes']
            code=item['http_status'] or 0;http_totals['http_'+str(code)]+=1
        except Exception as e:snap['errors'].append('HTTP evidence: '+type(e).__name__)
        snap['resources']=evidence.resources()
        snap['resources']['central_service']=run(['systemctl','show',SERVICE,'-p','MemoryCurrent','-p','MemoryPeak','-p','CPUUsageNSec','-p','NRestarts'])
        try:
          inv,ids,acked,pending=database_inventory(dbpath,arc,rec.get('archive_glob','central_sync_overflow_*.db'),max_rows=500000)
          snap['inventory']=inv
          for item in pending.values():
            if item['created']>=started:observed_groups.add((item['stream'],item['substream']))
        except Exception as e:snap['errors'].append('Inventory: '+type(e).__name__+': '+str(e))
        try:
          r=read_json(recpath);snap['recovery']={'running':r.get('running'),'status_generated_utc':r.get('status_generated_utc'),
            'pause_reason':r.get('counters',{}).get('last_pause_reason'),'request_cycles':r.get('counters',{}).get('request_cycle_count')}
        except Exception as e:snap['recovery_unavailable']=type(e).__name__
        try:
          rows=recent_acks(dbpath,startstamp[:19],seen)
          rows=[r for r in rows if (epoch(r['acknowledged_utc']) or 0)>=started]
          with (out/'live_ack_evidence.jsonl').open('a') as f:
            for r in rows:
              f.write(json.dumps(r)+'\n');seen.add(r['message_id'])
              key=(r['stream'],r['substream'],r['status']);v=ack_totals.setdefault(key,[0,0,0]);v[0]+=1;v[1]+=r['records'];v[2]+=r['bytes']
        except Exception as e:snap['errors'].append('ACK evidence: '+type(e).__name__+': '+str(e))
        samples.append(snap)
        with (out/'samples.jsonl').open('a') as f:f.write(json.dumps(snap)+'\n')
        inv=snap.get('inventory',{})
        print(f'{int(elapsed):3}s LIVE | pending live {inv.get("live_unacked","?")} | archived {inv.get("archive_unacked","?")} | total {inv.get("unique_unacked","?")} | live ACK evidence {len(seen)}',flush=True)
        if elapsed>=a.duration:complete=True;break
        next_due+=a.interval;time.sleep(max(0,min(a.interval,next_due-time.monotonic(),a.duration-(time.monotonic()-clock))))
    except (Exception,KeyboardInterrupt) as e:error=type(e).__name__+': '+str(e)
    finally:
      report={'completed_requested_duration':complete,'requested_duration_sec':a.duration,'error':error,'limits':limits,'start_utc':startstamp,'end_utc':utc(),'samples':len(samples)}
      lines=['KALPA OVERNIGHT LIVE UPLOAD / GENERATION TEST',f'Completed requested {a.duration/3600:.2f} hours: {complete}',f'Error: {error or "none"}',
        'No outage or upload pause was introduced.']
      if samples:
        first,last=samples[0],samples[-1];span=max(.001,last['elapsed_sec']-first['elapsed_sec'])
        gen=producer_deltas(first,last);report['generation_by_producer']=gen
        valid=all(r.get('available') for r in gen.values()) and len(gen)==len(paths)
        generated_messages=sum(r.get('message_count',0) for r in gen.values())
        generated_records=sum(r.get('record_count',0) for r in gen.values())
        report['generation_counters_complete']=valid
        report['generated_total']={'messages':generated_messages,'records':generated_records,'minutes':span/60} if valid else None
        lines+=['',f'Generation counters complete: {valid}']
        if valid:lines+=[f'Generated: {generated_messages} messages / {generated_records} records; average {generated_messages*60/span:.1f} msg/min, {generated_records*60/span:.1f} records/min']
        report['initial_inventory']=first.get('inventory');report['final_inventory']=last.get('inventory')
        report['final_stream_results']=last.get('streams',{})
        report['final_recovery']=last.get('recovery',{})
        instances={s.get('instance') for s in samples if s.get('instance')}
        report['uploader_restarted']=len(instances)>1
        deltas={}
        if len(instances)==1 and first.get('uploader') and last.get('uploader'):
          deltas={k:int(last['uploader'].get(k) or 0)-int(first['uploader'].get(k) or 0) for k in first['uploader']}
          report['live_uploader_counter_deltas']=deltas
          lines+=['','Live uploader counter deltas: '+json.dumps(deltas)]
        a=first.get('inventory',{});b=last.get('inventory',{})
        lines += ['',f'Combined unique pending: {a.get("unique_unacked","?")} -> {b.get("unique_unacked","?")}',
          f'Archived pending messages: {a.get("archive_unacked","?")} -> {b.get("archive_unacked","?")}',
          f'Oldest combined pending age: {a.get("oldest_age_sec","?")} -> {b.get("oldest_age_sec","?")} sec']
        errors=[{'utc':s['utc'],'errors':s['errors']} for s in samples if s['errors']]
        report['snapshot_errors']=errors
        flags=[]
        if errors or not valid or len(instances)!=1:flags.append('Incomplete/reset measurement evidence; no pass conclusion.')
        for k in ('request_failure_count','retryable_rejected_count','permanent_rejected_count','missing_ack_count'):
          if deltas.get(k,0)>0:flags.append(k+' increased by '+str(deltas[k]))
        if any(r.get('failure_count',0)>0 for r in gen.values()):flags.append('Producer failures occurred.')
        if a and b and b['archive_unacked']>a['archive_unacked']:flags.append('Archived pending workload grew during the healthy live-only test.')
        accepted_groups={(k[0],k[1]) for k,v in ack_totals.items() if k[2] in ('accepted','already_processed') and v[0]}
        report['observed_new_pending_groups_without_live_ack']=[{'stream':k[0],'substream':k[1]} for k in sorted(observed_groups-accepted_groups)]
        if observed_groups-accepted_groups:flags.append('Some observed generated groups had no retained live ACK evidence; review per-substream coverage.')
        report['review_flags']=flags
        if generation_start is not None and generation_end is not None:
          try:
            byte_groups=evidence.generation_delta(generation_start[1],generation_end[1])
            minutes=(generation_end[0]-generation_start[0])/60
            if minutes<=0:raise RuntimeError('insufficient generation byte samples')
            report['generation_bytes_by_group']=byte_groups
            byte_total=sum(r['bytes'] for r in byte_groups)
            report['generation_logical_MB']=byte_total/1e6
            report['generation_MB_per_min']=byte_total/1e6/minutes
            lines+=['',f'Generation logical JSON: {byte_total/1e6:.3f} MB; average {byte_total/1e6/minutes:.3f} MB/min']
          except Exception as e:flags.append('Generation byte rate unavailable: '+str(e))
        if a and b and b['unique_unacked']>a['unique_unacked']:
          flags.append('Combined live + SD pending count increased; sustained delivery keeping pace is not established.')
        lines+=['','Review flags:']+(['- '+f for f in flags] or ['No listed failure observed; full live+recovery record/byte reconciliation still unavailable.'])
      report['http_totals']=dict(http_totals)
      lines+=['','Recorded live HTTP attempts: '+json.dumps(dict(http_totals))]
      report['observed_live_ack_totals']=[{'stream':k[0],'substream':k[1],'status':k[2],'messages':v[0],'records':v[1],'logical_MB':v[2]/1e6} for k,v in sorted(ack_totals.items())]
      accepted=[r for r in report['observed_live_ack_totals'] if r['status']=='accepted']
      elapsed_minutes=max(.001,(time.monotonic()-clock)/60)
      report['live_accepted_average']={
        'messages_per_min':sum(r['messages'] for r in accepted)/elapsed_minutes,
        'records_per_min':sum(r['records'] for r in accepted)/elapsed_minutes,
        'MB_per_min':sum(r['logical_MB'] for r in accepted)/elapsed_minutes}
      lines+=['Live accepted averages (direct archive ACKs excluded): '+json.dumps(report['live_accepted_average'])]
      lines+=['','Observed live ACK totals (archive replay excluded; may include older queued data):']
      for r in report['observed_live_ack_totals']:lines.append(f'{r["stream"]}/{r["substream"]}: {r["status"]}, {r["messages"]} msg, {r["records"]} records, {r["logical_MB"]:.3f} MB')
      report['conclusion']='Review steady-state counters, ACK continuity, per-stream coverage and total pending trend. No automatic all-stream certification.'
      lines+=['',report['conclusion'],'Files: '+str(out),'Central Sync and all existing gateway settings were left unchanged.']
      safe_json(out/'report.json',report);(out/'summary.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines),flush=True)
      bundle=Path(str(out)+'.tar.gz')
      with tarfile.open(bundle,'w:gz') as tar:
        for path in sorted(out.iterdir()):
          if path.is_file():tar.add(path,arcname=out.name+'/'+path.name)
      print('Share this results bundle: '+str(bundle),flush=True)
    return 0 if complete else 1

if __name__=='__main__':raise SystemExit(main())
