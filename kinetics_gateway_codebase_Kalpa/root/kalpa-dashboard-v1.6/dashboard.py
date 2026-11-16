#!/usr/bin/env python3
"""Live and archive delivery terminal monitor; no service/database mutations."""
import argparse
from collections import deque, Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import textwrap
import time
import metrics as m
from monitor_evidence import Evidence, query, sub_status
LABELS={stream:slot for slot,key,stream in m.STREAMS}
TRACE_PATH='/var/lib/ornate-central-sync/batch_trace.db'
class GenerationBytes:
    def __init__(self):self.samples=deque()
    def update(self, rows, now):
        identity=tuple(sorted((r['stream'],r['substream'],r['first_epoch']) for r in rows))
        total=sum(r['payload_bytes'] for r in rows)
        q=self.samples
        # A newly appearing substream is valid growth, not a counter reset.
        old=dict(((s,sub),stamp) for s,sub,stamp in (q[-1][1] if q else ()))
        new=dict(((s,sub),stamp) for s,sub,stamp in identity)
        if q and (total<q[-1][2] or any(new.get(k)!=v for k,v in old.items())):q.clear()
        q.append((now,identity,total))
        while len(q)>1 and q[1][0]<=now-60:q.popleft()
        dt=now-q[0][0]
        if dt<=0:return None
        return ((total-q[0][2])*60/dt/1e6,dt)

class Generation:
    def __init__(self):self.samples={}
    def update(self,key,status,now):
        q=self.samples.setdefault(key,deque())
        instance=status.get('started_utc');counts=(m.integer(status.get('message_count')),m.integer(status.get('record_count')))
        if q and (q[-1][1]!=instance or any(a<b for a,b in zip(counts,q[-1][2]))):q.clear()
        stamp=m.epoch(status.get('last_poll_utc'))
        if stamp is None or now-stamp>60:return None
        if not q or q[-1][0]!=stamp:q.append((stamp,instance,counts))
        while len(q)>1 and q[1][0]<=stamp-60:q.popleft()
        if len(q)<2 or q[-1][0]<=q[0][0]:return None
        dt=q[-1][0]-q[0][0]
        return ((q[-1][2][0]-q[0][2][0])*60/dt,(q[-1][2][1]-q[0][2][1])*60/dt,dt)

def render(e,live,recovery,generation,byte_rate,service,now,width=150,cfg=None):
    cfg=cfg or {}
    lines=[]
    def put(s=''):lines.extend(textwrap.wrap(m.clean(s),width=width,replace_whitespace=False,drop_whitespace=False) or [''])
    age=m.age(live.get('status_generated_utc'),now)
    fresh=service=='active' and age is not None and age<=15
    put('KALPA / CENTRAL SYNC DELIVERY MONITOR -- LIVE + SD v1.6')
    put(datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')+f' | Service {service} | Status age {m.age_text(live.get("status_generated_utc"),now)}')
    if not fresh:put('LIVE STATUS STALE: saved backend results are historical.')
    gm=sum(v[0] for v in generation.values() if v);gr=sum(v[1] for v in generation.values() if v)
    bt=f'{byte_rate[0]:.3f}' if byte_rate else '--'
    put(f'GENERATING       {gm:.1f} msg/min | {gr:,.0f} records/min | {bt} MB/min'+(' [warming: wait 60s]' if not byte_rate or byte_rate[1]<60 else ''))
    def rate(name,data):
        n,r,b=data['accepted'];d=data['duplicate'][0]
        put(f'{name:16} {n} msg/min | {r:,} records/min | {b/1e6:.3f} MB/min | duplicate ACKs {d}')
    if e['ack_errors']:put('ACK WINDOW PARTIAL: values below are observed lower bounds; some DB reads failed.')
    rate('LIVE ACCEPTED',e['live']);rate('SD ACCEPTED',e['archive']);rate('TOTAL ACCEPTED',e['total'])
    put('ACK window: last 60s, per-message receipts; TOTAL deduplicates IDs across live/SD.')
    put('Receipt tables LIVE %s/%s; SD %s/%s. Absent tables have no compact receipt evidence.'%(*e['formats']['LIVE'],*e['formats']['SD']))
    pending=[r for r in e['pending'] if r['state'] in ('pending','retry','inflight')]
    q=e['queue_summary'];coverage=e['queue_coverage']
    if e['queue_complete']:
        oldest=max(0,now-q['oldest']) if q['oldest'] is not None else 0
        put(f'QUEUE COPY COUNTS: LIVE {q["live"]} | SD {q["sd"]} | {q["bytes"]/1e6:.3f} MB | oldest {oldest:.0f}s | oldest sample {e["queue_age"]:.0f}s')
    else:put('QUEUE CURRENT COUNTS: unavailable until archive coverage catches up; partial counts withheld.')
    put(f'Queue coverage: {coverage["sampled"]}/{coverage["total"]} DBs sampled | {coverage["verified"]} verified this sweep | cache age {e["queue_age"]:.0f}s')
    put('Queue counts are stored message copies; LIVE/SD overlap and archive duplicates are not deduplicated. Reads are not one cross-DB transaction.')
    for label,errors in [('Queue refresh',e['queue_errors']),('ACK refresh',e['ack_errors'])]:
        for reason,count in Counter(str(v) for v in errors.values()).items():put(f'{label}: {count} DB(s): {reason}')
    c=recovery.get('counters',{});h=recovery.get('resource_gate',{})
    put(f'RECOVERY: running={recovery.get("running")} | cycles {c.get("request_cycle_count")} | opportunities {c.get("bounded_opportunity_count")} | latest decision {c.get("last_pause_reason") or "admitted"}')
    put(f'TRANSPORT: {recovery.get("transport_mode","legacy/shared")} | archive mode {recovery.get("adaptive_mode","--")} | interval {recovery.get("archive_interval_sec","--")}s | archive active {c.get("archive_active_requests","--")}/{recovery.get("max_archive_concurrency","--")}')
    if 'archive_accepted_bytes' in c:
        put(f'ARCHIVE ACK TOTALS / THIS RECOVERY INSTANCE: accepted {c.get("archive_accepted_count")} msg | {c.get("archive_accepted_records")} records | {c.get("archive_accepted_bytes",0)/1e6:.3f} MB | already processed {c.get("archive_already_processed_count")} | retry rejected {c.get("archive_retryable_rejected_count")} | missing ACK {c.get("archive_missing_ack_count")} | byte-metric errors {c.get("archive_ack_metric_error_count")}')
    put(f'Recovery status age {m.age_text(recovery.get("status_generated_utc"),now)} | load {h.get("load1")} | RAM available {h.get("mem_available_mb")} MB')
    put();put('STREAM DELIVERY / LAST 60s -- acceptance and backlog are separate facts')
    put(f'{"Stream / substream":36} {"Gen msg/m":>9} {"Gen rec/m":>9} {"Live ACK/m":>10} {"SD ACK/m":>9} {"ACK rec/m":>10} {"Wait L/SD":>12}  Evidence')
    def row(label,stream,sub=None,disabled=False):
        key=stream if sub is None else (stream,sub);group='streams' if sub is None else 'substreams'
        lv=e['live'][group].get(key,[0,0,0]);sd=e['archive'][group].get(key,[0,0,0]);tot=e['total'][group].get(key,[0,0,0])
        counts={'LIVE':0,'SD':0}
        for (st,ss),value in e['queue_summary']['groups'].items():
            if st==stream and (sub is None or ss==sub):
                for loc in counts:counts[loc]+=value[loc]
        wait=f'{counts["LIVE"]}/{counts["SD"]}'
        rr=range(sum(counts.values()))
        if not e['queue_complete']:wait='?/?'
        st=sub_status(tot[0],len(rr),e['queue_complete'],not e['ack_errors'])
        if disabled:st='DISABLED | '+st
        g=generation.get(stream) if sub is None else None
        gm=f'{g[0]:.1f}' if g else '--';gr=f'{g[1]:.0f}' if g else '--'
        put(f'{label:36} {gm:>9} {gr:>9} {lv[0]:>10} {sd[0]:>9} {tot[1]:>10} {wait:>12}  {st}')
    for slot,key,stream in m.STREAMS:
        disabled=cfg.get(key,{}).get('enabled',slot not in ('S5','S6','S7','S9')) is False
        if slot=='S9':disabled=not cfg.get('command_downlink',{}).get('enabled',False)
        row(slot+' '+stream,stream,disabled=disabled)
        if slot=='S2':
            for sub in m.SUBSTREAMS:row('  '+sub,stream,sub)
    summaries=live.get('stream_backend_ack',{})
    for stream,v in summaries.items():
        if v.get('last_error'):put('LAST SAVED LIVE RESULT '+stream+': '+str(v.get('last_error'))+' | '+str(v.get('last_backend_result_utc') or v.get('last_ack_utc') or 'timestamp unavailable'))
    put();put('RECENT MESSAGE ACKs -- location identifies sending DB; retained receipts only')
    for r in sorted(e['ack_rows'],key=lambda r:r['ack_epoch'],reverse=True)[:6]:
        put(f'{r["message_id"]} | {r["location"]} | {LABELS.get(r["stream"],r["stream"])}/{r["substream"]} | {r["ack_status"]} | {r["payload_bytes"]/1000:.1f} KB')
    put();put('RECENT PENDING IDs -- newest outstanding sampled messages')
    if not e['queue_complete']:put('Queue coverage incomplete: pending ID samples below can be cached; verify individual IDs before drawing conclusions.')
    for r in sorted(pending,key=lambda r:r['created_epoch'],reverse=True)[:5]:
        put(f'{r["message_id"]} | {r["location"]} | {LABELS.get(r["stream"],r["stream"])}/{r["substream"]} | {r["state"]} | age {max(0,now-r["created_epoch"]):.0f}s')
    put();put('Per-minute ACKs are observed receipts, not generation-cohort delivery percentages.')
    put('SD backlog includes older pending data even when fresh data is accepted. No ACK in 60s does not prove rejection.')
    put('Queue reads use metadata indexes, refresh every 15s, and are not one cross-DB transaction. Incomplete counts and per-stream waits are withheld; pending counts represent copies, not unique IDs.')
    put('Receipt retention is bounded; missing cleaned history cannot be reconstructed. Files retained after replay are not pending-message counts.')
    put('Read-only. Ctrl+C closes this view. --message UUID / --request UUID inspect retained evidence.')
    return lines

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',default='/etc/ornate-ems-gateway/central_sync.json');p.add_argument('--interval',type=float,default=5)
    p.add_argument('--once',action='store_true');p.add_argument('--message');p.add_argument('--request');a=p.parse_args()
    if a.interval<3:p.error('--interval must be >=3')
    evidence=Evidence();gen=Generation();gb=GenerationBytes()
    try:
        while True:
            cfg,err=m.load_json(a.config)
            if err:print('Config unavailable:',err);return 1
            paths=m.defaults(cfg,Path('/opt/ornate-ems-gateway/backend'));now=time.time()
            if a.request:
                rows=query(TRACE_PATH,'SELECT * FROM batch_trace WHERE request_id=?',(a.request,))
                print(json.dumps(rows,indent=2));return 0
            if a.message:
                for path in [Path(paths['outbox'])]+sorted(Path(paths['archives']).glob(cfg.get('overflow_recovery',{}).get('archive_glob','central_sync_overflow_*.db'))):
                    for table,sql in [('queue','SELECT message_id,stream,substream,state,attempt_count,last_error,backend_detail FROM outbox_messages WHERE message_id=?'),('receipt','SELECT * FROM delivery_receipts_v1 WHERE message_id=?')]:
                        try:
                            for r in query(path,sql,(a.message,)):print(str(path),table,m.clean(json.dumps(r)))
                        except Exception as exc:print(str(path),table,'unavailable:',type(exc).__name__)
                print('Only retained evidence searched; absence does not prove nondelivery.');return 0
            live,_=m.load_json(paths['live']);recovery,_=m.load_json(paths['recovery'])
            e=evidence.snapshot(paths['outbox'],paths['archives'],cfg.get('overflow_recovery',{}).get('archive_glob','central_sync_overflow_*.db'),now)
            generation={}
            for slot,key,stream in m.STREAMS:
                if slot in ('S5','S6','S7','S9') or cfg.get(key,{}).get('enabled',True) is False:continue
                status,_=m.load_json(m.resolve(cfg.get(key,{}).get('status_file',f'/var/lib/ornate-central-sync/{slot.lower()}_status.json'),Path('/opt/ornate-ems-gateway/backend')))
                generation[stream]=gen.update(stream,status,now)
            try:byte_rate=gb.update(query(paths['outbox'],'SELECT stream,substream,messages,payload_bytes,first_epoch FROM generation_metrics_v1'),now)
            except Exception:byte_rate=None;gb.samples.clear()
            service=m.run_command(['systemctl','is-active',m.SERVICE])
            lines=render(e,live,recovery,generation,byte_rate,service,now,max(90,shutil.get_terminal_size((180,60)).columns),cfg=cfg)
            try:
                http=query(TRACE_PATH,'SELECT source_db,http_status,payload_bytes FROM batch_trace WHERE completed_epoch>=? AND completed_epoch<=?',(now-60,now),limit=1000)
                for loc in ('LIVE','SD'):
                    rr=[r for r in http if ('LIVE' if Path(r['source_db']).resolve()==Path(paths['outbox']).resolve() else 'SD')==loc]
                    ok=sum(200<=int(r['http_status'] or 0)<300 for r in rr)
                    no=sum(not r['http_status'] for r in rr)
                    lines.append(f'HTTP LAST 60s {loc}: completed {len(rr)} | 2xx {ok} | errors {len(rr)-ok-no} | no response {no} | attempted {sum(r["payload_bytes"] for r in rr)/1e6:.3f} MB')
            except Exception as exc:lines.append('HTTP window unavailable: '+type(exc).__name__)
            # Trace stores live AND archive requests with source_db membership.
            lines+=['','RECENT HTTP BATCHES -- LIVE / SD; HTTP 200 is not per-message acceptance']
            try:
                for r in query(TRACE_PATH,'SELECT * FROM batch_trace ORDER BY completed_epoch DESC LIMIT 6'):
                    loc='LIVE' if Path(r['source_db']).resolve()==Path(paths['outbox']).resolve() else 'SD'
                    lines.append(m.clean(f'{r["request_id"]} | {loc} | HTTP {r["http_status"] or "NO RESPONSE"} | {r["message_count"]} msg | {r["payload_bytes"]/1000:.1f} KB | {r["completed_utc"]}'))
                    groups=json.loads(r['groups_json']);lines.append('  '+m.clean('; '.join(f'{LABELS.get(g["stream"],g["stream"])}/{g["substream"]} x{g["messages"]}' for g in groups)))
                    if r.get('detail'):lines.append('  Reason: '+m.clean(r['detail']))
            except Exception as exc:lines.append('HTTP trace unavailable: '+type(exc).__name__)
            if sys.stdout.isatty() and not a.once:sys.stdout.write('\033[2J\033[H')
            print('\n'.join(lines),flush=True)
            if a.once:return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:print('\nView closed; Central Sync unchanged.');return 0
    except Exception as exc:print('Evidence unavailable:',m.clean(str(exc)));return 1

if __name__=='__main__':raise SystemExit(main())
