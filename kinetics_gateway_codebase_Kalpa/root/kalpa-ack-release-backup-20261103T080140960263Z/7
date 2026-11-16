#!/usr/bin/env python3
"""Single terminal view of real generation, ACKs, messages and HTTP evidence."""
import argparse
from collections import deque,Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import textwrap
import time
import metrics as m

LABELS={stream:slot for slot,key,stream in m.STREAMS}
TRACE_PATH='/var/lib/ornate-central-sync/batch_trace.db'
def query(path,sql,args=(),budget=.7,limit=12000):
    if not Path(path).is_file():raise RuntimeError('database missing')
    start=time.monotonic();db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
    try:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-start>budget),1000)
        rows=[]
        for row in db.execute(sql,args):
            if len(rows)>=limit or time.monotonic()-start>budget:raise RuntimeError('query budget exceeded')
            rows.append(dict(row))
        return rows
    finally:db.close()

def inventory(path,root,pattern):
    paths=[Path(path)]+sorted(Path(root).glob(pattern))
    if len(paths)>65:raise RuntimeError('archive file limit exceeded')
    items={};where={};priority={'acked':4,'dead':3,'inflight':2,'retry':1,'pending':0}
    for p in paths:
        for row in query(p,'''SELECT message_id,stream,substream,state,payload_bytes,created_epoch,
          acknowledged_utc,ack_status,attempt_count,last_attempt_utc,last_error,backend_detail
          FROM outbox_messages''',budget=1):
            mid=row['message_id'];location='LIVE' if p==Path(path) else 'SD'
            where.setdefault(mid,set()).add(location)
            if mid not in items or priority.get(row['state'],-1)>priority.get(items[mid]['state'],-1):items[mid]=row
    for mid,row in items.items():row['location']='+'.join(sorted(where[mid]))
    return list(items.values())

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

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',default='/etc/ornate-ems-gateway/central_sync.json')
    p.add_argument('--interval',type=float,default=5);p.add_argument('--once',action='store_true')
    p.add_argument('--message',help='show full retained evidence for a message UUID')
    p.add_argument('--request',help='show recorded batch membership by request UUID')
    a=p.parse_args()
    if a.interval<3:p.error('--interval must be >=3 seconds')
    gen=Generation();genbytes=GenerationBytes();history=deque(maxlen=120);started=time.time()
    try:
      while True:
        now=time.time();cfg,error=m.load_json(a.config)
        if error:print('Config unavailable: '+error);return 1
        paths=m.defaults(cfg,Path('/opt/ornate-ems-gateway/backend'))
        if a.request:
            try:
                traced=query(TRACE_PATH,'SELECT * FROM batch_trace WHERE request_id=?',(a.request,))
                if not traced:print('Request membership not retained (old batch or outside trace retention).');return 0
                for r in traced:
                    print('Request:',r['request_id'],'HTTP:',r['http_status'] or 'NO RESPONSE','Completed:',r['completed_utc'])
                    print('Reason:',m.clean(r.get('detail') or 'none; inspect message-level ACK separately'))
                    for item in json.loads(r['members_json']):
                        print(item['message_id'],LABELS.get(item['stream'],item['stream']),item['substream'],item['payload_bytes'],'bytes')
                return 0
            except Exception as e:print('Batch trace unavailable:',type(e).__name__);return 1
        live,err=m.load_json(paths['live']);u=live.get('uploader',{})
        service=m.run_command(['systemctl','is-active',m.SERVICE]);status_age=m.age(live.get('status_generated_utc'),now)
        fresh=service=='active' and status_age is not None and status_age<=15
        rows=[];db_error=None
        try:rows=inventory(paths['outbox'],paths['archives'],cfg.get('overflow_recovery',{}).get('archive_glob','central_sync_overflow_*.db'))
        except Exception as e:db_error=type(e).__name__+': '+str(e)
        if a.message:
            if db_error:print('Evidence unavailable: '+db_error);return 1
            selected=[r for r in rows if r['message_id']==a.message]
            if not selected:print('Not retained in live/archives. This does not prove missing generation or failed delivery.');return 0
            for r in selected:
                for k,v in r.items():print(k+': '+m.clean(v))
            print('Request-to-message linkage and complete attempt history are not persisted by current gateway schema.')
            return 0
        ack,ackerr=m.ack_window(paths['outbox'],now=now)
        req,reqerr=m.request_window(paths['outbox'],now=now)
        generation={}
        for slot,key,stream in m.STREAMS:
            default=slot not in ('S5','S6','S7','S9')
            if slot=='S9' or cfg.get(key,{}).get('enabled',default) is False:continue
            producer,_=m.load_json(m.resolve(cfg.get(key,{}).get('status_file',f'/var/lib/ornate-central-sync/{slot.lower()}_status.json'),Path('/opt/ornate-ems-gateway/backend')))
            generation[stream]=gen.update(stream,producer,now)
        byte_rate=None;byte_error=None
        try:
            totals=query(paths['outbox'],'SELECT stream,substream,messages,payload_bytes,first_epoch FROM generation_metrics_v1')
            byte_rate=genbytes.update(totals,now)
        except Exception as e:
            byte_error=type(e).__name__;genbytes.samples.clear()
        pending=[r for r in rows if r['state'] in ('pending','retry','inflight')]
        counts=Counter(r['state'] for r in rows)
        lines=[];width=max(85,shutil.get_terminal_size((140,60)).columns)
        color=sys.stdout.isatty() and not a.once
        def put(s=''):
            lines.extend(textwrap.wrap(m.clean(s),width=width,replace_whitespace=False,drop_whitespace=False) or [''])
        def heading(s):
            lines.append(('\033[1;36m'+s+'\033[0m') if color else s)
        heading('KALPA  /  CENTRAL SYNC DELIVERY MONITOR')
        put(datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')+f'   Service: {service}   Status age: {m.age_text(live.get("status_generated_utc"),now)}   Refresh: {a.interval:g}s')
        if not fresh:put('STATUS STALE / SERVICE INACTIVE: saved counters are historical.')
        if db_error:put('QUEUE EVIDENCE UNAVAILABLE: '+db_error)
        complete_generation=all(generation.get(s) for s in generation) and bool(generation)
        gm=sum(v[0] for v in generation.values() if v);gr=sum(v[1] for v in generation.values() if v)
        byte_text=f'{byte_rate[0]:.3f} MB/min' if byte_rate and fresh else '-- MB/min'
        if byte_rate and byte_rate[1]<60:byte_text+=' [warming]'
        put(f'GENERATING   {gm:.1f} msg/min   {gr:,.0f} records/min   {byte_text}'+('   [warming / incomplete producer evidence]' if not complete_generation else '   [producer counters / durable JSON bytes]'))
        if byte_error:put('Generation bytes unavailable: counters not installed or database query failed ('+byte_error+').')
        if ack:
            am,ar,ab=ack['accepted'];put(f'LIVE ACCEPTED   {am} msg/min   {ar:,} records/min   {ab/1e6:.3f} MB/min   | duplicate ACKs: {ack["duplicate"][0]}')
        else:put('LIVE ACCEPTED   unavailable: '+str(ackerr))
        if not db_error:
            oldest=max((max(0,now-r['created_epoch']) for r in pending),default=0)
            put(f'WAITING   {len(pending)} unique messages   {sum(r["payload_bytes"] for r in pending)/1e6:.3f} MB   oldest {oldest:.0f}s   | retry {counts["retry"]}   dead {counts["dead"]}')
            live_n=sum('LIVE' in r['location'] for r in pending);sd_n=sum('SD' in r['location'] for r in pending)
            put(f'LOCATION   live {live_n}   SD {sd_n}   (copy overlap deduplicated in WAITING total)')
        if req:put(f'HTTP / LAST 60s   {req["requests"]} completed   2xx {req["http_2xx"]}   HTTP errors {req["http_errors"]}   no response {req["no_response"]}   attempted {req["bytes"]/1e6:.3f} MB')
        else:put('HTTP WINDOW unavailable: '+str(reqerr))
        put('Transport: '+str(u.get('last_error') or 'no last transport error')+'   | Per-message results can differ from HTTP status.')
        put();heading('STREAM DELIVERY   generation = producer deltas; accepted = retained LIVE ACKs in last 60s')
        put(f'{"Stream":34} {"Gen msg/m":>9} {"Gen rec/m":>9} {"ACK msg/m":>9} {"ACK rec/m":>9} {"Wait L/SD":>10}  Result')
        summaries=live.get('stream_backend_ack',{})
        for slot,key,stream in m.STREAMS:
            subrows=[r for r in pending if r['stream']==stream]
            g=generation.get(stream);vals=(ack or {}).get('streams',{}).get(stream,[0,0,0])
            state=m.stream_state(cfg,key,summaries.get(stream,{}),not fresh)
            if slot=='S9' and not cfg.get('command_downlink',{}).get('enabled',False):state='DISABLED'
            gmsg=f'{g[0]:.1f}' if g else '--';grec=f'{g[1]:.0f}' if g else '--'
            av=f'{vals[0]}' if ack else '--';rv=f'{vals[1]}' if ack else '--'
            waits=f'{sum("LIVE" in r["location"] for r in subrows)}/{sum("SD" in r["location"] for r in subrows)}' if not db_error else '?'
            put(f'{(slot+" "+stream):34} {gmsg:>9} {grec:>9} {av:>9} {rv:>9} {waits:>10}  {state}')
            if slot=='S2':
              for sub in m.SUBSTREAMS:
                sr=[r for r in subrows if r['substream']==sub];sv=(ack or {}).get('substreams',{}).get(sub,[0,0,0])
                put(f'  {sub:32} {"--":>9} {"--":>9} {str(sv[0]) if ack else "--":>9} {str(sv[1]) if ack else "--":>9} {str(len(sr)) if not db_error else "?":>10}  pending unique')
        put();heading('RECENT MESSAGES   newest generated IDs; state and reason are message-level evidence')
        recent=sorted(rows,key=lambda r:r['created_epoch'],reverse=True)[:5]
        for r in recent:
            state=r.get('ack_status') if r['state']=='acked' else r['state']
            put(f'{r["message_id"]}  {LABELS.get(r["stream"],r["stream"])} / {r["substream"]}  {state}  {r["location"]}  attempts {r["attempt_count"]}')
            reason=r.get('last_error') or r.get('backend_detail')
            if reason:put('  Reason: '+str(reason))
        put();heading('RECENT HTTP BATCHES   exact request IDs and recorded HTTP outcomes')
        try:
            batches=query(paths['outbox'],'''SELECT request_id,attempted_utc,message_count,payload_bytes,http_status,outcome,detail FROM transport_attempts ORDER BY id DESC LIMIT 4''')
            for r in batches:
                code=str(r['http_status']) if r['http_status'] else 'NO RESPONSE'
                put(f'{r["request_id"]}  HTTP {code}  {r["message_count"]} msg  {r["payload_bytes"]/1000:.1f} KB  {r["outcome"]}')
                try:
                    traced=query(TRACE_PATH,'SELECT groups_json FROM batch_trace WHERE request_id=?',(r['request_id'],))
                    if traced:
                        groups=json.loads(traced[0]['groups_json'])
                        put('  Streams: '+'; '.join(f'{LABELS.get(g["stream"],g["stream"])}/{g["substream"] or "(none)"} x{g["messages"]}' for g in groups))
                    else:put('  Streams: not recorded for this older batch')
                except Exception:put('  Streams: trace unavailable (older batch or tracing not installed)')
                if r['detail']:put('  Reason: '+str(r['detail']))
        except Exception as e:put('HTTP detail unavailable: '+type(e).__name__)
        for stream,result in summaries.items():
            if result.get('last_error'):put('MESSAGE RESULT '+stream+': '+str(result.get('last_error')))
        put();put('Batch stream membership is captured prospectively by the tracing patch. Old batches cannot be reconstructed.')
        put('Queue cleanup removes old rows. This viewer does not claim a complete historical ledger. S2 subgroup generation rates: unavailable.')
        put('Generation MB/min: committed new logical JSON bytes, decimal MB; excludes retries/replay; survives archival and ACK cleanup.')
        put('Gen rates warm up for 60s; accepted rates may include older queued data and exclude direct archive ACKs. No automatic all-data-delivered claim.')
        put('Read-only. Ctrl+C closes this view only. Inspect ID: kalpa-delivery-dashboard --message UUID')
        if color:sys.stdout.write('\033[2J\033[H')
        print('\n'.join(lines),flush=True)
        if a.once:return 0
        time.sleep(a.interval)
    except KeyboardInterrupt:print('\nView closed; Central Sync unchanged.');return 0

if __name__=='__main__':raise SystemExit(main())
