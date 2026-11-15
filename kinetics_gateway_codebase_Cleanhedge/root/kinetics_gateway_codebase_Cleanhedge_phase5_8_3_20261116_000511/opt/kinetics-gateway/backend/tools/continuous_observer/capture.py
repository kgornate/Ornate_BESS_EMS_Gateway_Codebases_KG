#!/usr/bin/env python3
"""Continuous read-only observation; bounded deduplication and restart-aware metrics."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import tarfile
import time
from probe import sample,read,freshness

LIVE_KEYS=('accepted_count','already_processed_count','retryable_rejected_count','permanent_rejected_count','missing_ack_count','request_count','request_failure_count','bytes_sent_uncompressed')
ARCHIVE_KEYS=('archive_accepted_count','archive_accepted_bytes','archive_already_processed_count','archive_duplicate_bytes','archive_retryable_rejected_count','archive_permanent_rejected_count','archive_missing_ack_count','archive_http_attempt_count','archive_http_failure_count','archive_http_duration_total_ms','archive_selection_total_ms','archive_store_open_total_ms','archive_housekeeping_total_ms','archive_session_reuse_count','archive_open_count')
SPILL_KEYS=('archived_message_count','archived_payload_bytes','source_deleted_count')
STOP=False

def utc():return datetime.now(timezone.utc).isoformat()
def atomic(path,value):
    temp=path.with_suffix(path.suffix+'.new')
    with temp.open('w') as f:
        os.chmod(temp,0o600);json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def fresh_status(status,now):
    age=freshness(status.get('status_generated_utc'),now)
    return status.get('running') and age is not None and 0<=age<=15

class Segments:
    def __init__(self,keys):
        self.keys=keys;self.current=None;self.segments=[];self.resets=0;self.discarded=0
    def observe(self,instance,counters,stamp):
        if not instance:return
        vals={k:v for k,v in counters.items() if k in self.keys and isinstance(v,(int,float)) and not isinstance(v,bool)}
        if not vals:return
        current=self.current
        reset=bool(current and any(k in current['last'] and v<current['last'][k] for k,v in vals.items()))
        if current is None or current['instance']!=instance or reset:
            if reset:self.resets+=1
            if len(self.segments)>=1024:
                self.discarded+=1;self.current=None;return
            current={'instance':instance,'first_utc':stamp,'last_utc':stamp,'first':dict(vals),'last':dict(vals),'samples':0}
            self.current=current;self.segments.append(current)
        for k,v in vals.items():
            if k not in current['first']:current['first'][k]=v
            current['last'][k]=v
        current['last_utc']=stamp;current['samples']+=1
    def report(self):
        totals=Counter();seconds=0
        for s in self.segments:
            for k,v in s['last'].items():totals[k]+=max(0,v-s['first'][k])
            seconds+=max(0,datetime.fromisoformat(s['last_utc'].replace('Z','+00:00')).timestamp()-datetime.fromisoformat(s['first_utc'].replace('Z','+00:00')).timestamp())
        return {'observed_counter_deltas':dict(totals),'covered_seconds':seconds,'segments':self.segments,
                'counter_resets':self.resets,'segment_bound_reached':self.discarded}

class Receipts:
    def __init__(self):self.seen={};self.messages=0;self.bytes=0;self.by_stream={};self.bounds=0
    def observe(self,rows,now,start):
        self.seen={k:v for k,v in self.seen.items() if v>=now-600}
        if len(rows)>=5001:self.bounds+=1
        for r in rows:
            if r['ack_epoch']<start or r['ack_epoch']<now-300 or r['message_id'] in self.seen:continue
            if len(self.seen)>=10000:self.bounds+=1;break
            self.seen[r['message_id']]=r['ack_epoch']
            if r['ack_status']=='accepted':
                self.messages+=1;self.bytes+=r['payload_bytes']
                k=r['stream']+'/'+str(r.get('substream') or '')
                if k not in self.by_stream and len(self.by_stream)>=128:self.bounds+=1;continue
                c=self.by_stream.setdefault(k,{'messages':0,'bytes':0});c['messages']+=1;c['bytes']+=r['payload_bytes']

class Generation:
    def __init__(self):self.last={};self.bytes=0;self.messages=0;self.resets=0
    def observe(self,rows):
        for row in rows:
            key=(row['stream'],row['substream']);current=(row['messages'],row['payload_bytes']);old=self.last.get(key)
            if old:
                if current[0]>=old[0] and current[1]>=old[1]:self.messages+=current[0]-old[0];self.bytes+=current[1]-old[1]
                else:self.resets+=1
            if key in self.last or len(self.last)<128:self.last[key]=current

def main():
    global STOP
    parser=argparse.ArgumentParser();parser.add_argument('--duration',type=int,default=86400);parser.add_argument('--interval',type=int,default=15);parser.add_argument('--output-root',type=Path,default=Path('/root'))
    args=parser.parse_args()
    if not 10<=args.duration<=172800 or not 5<=args.interval<=300:raise SystemExit('Duration 10..172800; interval 5..300 seconds')
    cfg=read('/etc/kinetics-gateway/central_sync.json')
    output=args.output_root/('cleanedge-central-sync-observation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(mode=0o700)
    atomic(args.output_root/'cleanedge-central-sync-capture-path.json',{'output':str(output),'started_utc':utc()})
    for name in ('archive_recovery_budget.json','archive_transport.json'):
        p=Path('/etc/kinetics-gateway')/name
        if p.is_file():
            q=output/name;q.write_bytes(p.read_bytes());q.chmod(0o600)
    live=Segments(LIVE_KEYS);archive=Segments(ARCHIVE_KEYS);spill=Segments(SPILL_KEYS)
    receipts=Receipts();generation=Generation();decisions=Counter();errors=0;count=0;queue_max=0;age_max=0.;stale=Counter()
    started=time.monotonic();start_epoch=time.time();last_checkpoint=0;failure=None;completed=False
    def stop(signum,frame):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def summary():
        elapsed=max(.001,time.monotonic()-started)
        ar=archive.report();lr=live.report();sr=spill.report()
        covered=ar['covered_seconds'];accepted=ar['observed_counter_deltas'].get('archive_accepted_bytes',0)
        return {'completed':completed,'error':failure,'started_utc':datetime.fromtimestamp(start_epoch,timezone.utc).isoformat(),
          'updated_utc':utc(),'elapsed_sec':elapsed,'requested_duration_sec':args.duration,'samples':count,'samples_with_errors':errors,
          'stale_or_unavailable_status_samples':dict(stale),'generation_observed_MB_per_min':generation.bytes/1e6*60/elapsed,
          'generation_counter_resets':generation.resets,'live_receipts_observed_MB_per_min':receipts.bytes/1e6*60/elapsed,
          'live_receipts_observed_messages':receipts.messages,'receipt_bounds_reached':receipts.bounds,'live_receipts_by_stream':receipts.by_stream,
          'archive_accepted_MB_per_min_covered':accepted/1e6*60/covered if covered else None,
          'max_sampled_live_waiting':queue_max,'max_sampled_live_oldest_age_sec':age_max,
          'decisions':dict(decisions),'live':lr,'archive':ar,'archiver':sr,
          'limits':['Rates describe observed evidence; no complete per-message delivery certification.',
          'Counter deltas are calculated only within matching service instances and reset segments.',
          'Intervals between segments and before first fresh sample are excluded from covered rates.',
          'Fresh status samples may bridge observation gaps; inspect raw samples for coverage.',
          'Receipt deduplication retains at most 10000 IDs for 10 minutes; observations older than 5 minutes are skipped.',
          'SD individual message receipts and global unique remaining backlog are not scanned.',
          'Raw samples remain available if interrupted; summary.partial.json is checkpointed every minute.']}
    print('Continuous read-only v2.2 capture; SSH may disconnect. Duration:',args.duration,flush=True)
    print('Results:',output,flush=True)
    try:
        with (output/'samples.jsonl').open('w') as handle:
            os.chmod(handle.name,0o600)
            while not STOP:
                s=sample(cfg);rows=s.pop('recent_live_receipts',[])
                receipts.observe(rows,time.time(),start_epoch);generation.observe(s.get('generation',[]));count+=1
                for key,tracker,counters in [('central',live,'uploader'),('recovery',archive,'counters'),('archiver',spill,'counters')]:
                    status=s.get(key,{})
                    if status.get('started_utc') and fresh_status(status,s['utc']):tracker.observe(s['boot_id']+':'+str(status.get('started_utc') or ''),status.get(counters,{}),status['status_generated_utc'])
                    else:stale[key]+=1
                q=s.get('central',{}).get('outbox',{});rc=s.get('recovery',{}).get('counters',{})
                queue_max=max(queue_max,int(q.get('unacked_count') or 0));age_max=max(age_max,float(q.get('oldest_sendable_age_sec') or 0))
                reason=rc.get('last_pause_reason') or 'admitted';decisions[reason]+=1
                errors+=bool(s['errors']);handle.write(json.dumps(s,separators=(',',':'))+'\n');handle.flush()
                elapsed=time.monotonic()-started
                print(f"{elapsed:.0f}s | live={q.get('unacked_count')} | SD accepted={rc.get('archive_accepted_count')} | archive bytes={rc.get('archive_accepted_bytes')} | recovery={reason} | errors={s['errors']}",flush=True)
                if elapsed-last_checkpoint>=60 or count==1:atomic(output/'summary.partial.json',summary());last_checkpoint=elapsed
                remaining=args.duration-elapsed
                if remaining<=0:completed=True;break
                deadline=time.monotonic()+min(args.interval,remaining)
                while not STOP and time.monotonic()<deadline:time.sleep(min(1,max(0,deadline-time.monotonic())))
    except Exception as exc:
        failure=type(exc).__name__+': '+str(exc)
    finally:
        atomic(output/'summary.json',summary())
        atomic(output/'completion.json',{'completed':completed,'error':failure,'finished_utc':utc(),'samples':count,'stopped_by_signal':STOP})
        bundle=Path(str(output)+'.tar.gz')
        with tarfile.open(bundle,'w:gz') as tar:tar.add(output,arcname=output.name)
        bundle.chmod(0o600);print('Share:',bundle,flush=True)
    if failure:raise SystemExit(failure)

if __name__=='__main__':main()

