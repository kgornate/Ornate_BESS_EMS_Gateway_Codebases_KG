#!/usr/bin/env python3
"""Export all retained timing rows plus mean/p95 summaries; never writes source DB."""
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import statistics
import tarfile
import argparse

def stats(values):
    values=sorted(v for v in values if isinstance(v,(int,float)))
    if not values:return {'count':0,'mean_ms':None,'p95_ms':None,'max_ms':None}
    import math
    return {'count':len(values),'mean_ms':statistics.mean(values),'p95_ms':values[max(0,math.ceil(.95*len(values))-1)],'max_ms':max(values)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output');a=ap.parse_args()
    path=Path('/var/lib/ornate-central-sync/request_timing.db')
    if not path.is_file():raise SystemExit('Timing database not created yet; check Central Sync log for instrumentation problems.')
    out=Path(a.output or '/root/kalpa-request-timing-results-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);db.execute('PRAGMA query_only=ON')
    phases=defaultdict(list);codes=defaultdict(int);count=0;first=None;last=None
    with (out/'requests.jsonl').open('w') as f:
        for _,stamp,body in db.execute('SELECT request_id,completed_utc,data FROM request_timing ORDER BY id'):
            r=json.loads(body);count+=1;first=first or stamp;last=stamp;f.write(json.dumps(r)+'\n')
            attempts=r.get('network_attempts',[])
            code=str(attempts[-1].get('http_status')) if attempts else 'no_network_attempt'
            codes[code]+=1
            for key in ('prepare_ms','lock_wait_ms','auth_ms','ack_apply_ms','send_and_ack_total_ms'):
                if key in r and r[key] is not None:phases[code+'/'+key].append(r[key])
            for network in attempts:
                netcode=str(network.get('http_status'))
                for key in ('network_total_ms','connect_tcp_ms','tls_ms','send_headers_ms','send_body_ms','wait_headers_ms','read_body_ms'):
                    if network.get(key) is not None:phases[netcode+'/'+key].append(network[key])
    db.close()
    result={'requests':count,'first_completed_utc':first,'last_completed_utc':last,'http_status_counts':dict(codes),
        'phase_summary':{k:stats(v) for k,v in sorted(phases.items())},
        'limits':['Response header wait includes network, proxy and backend delay; not isolated backend CPU/database time.',
          'TCP timing includes name resolution and connection setup when traced; reused connections have no new TCP/TLS event.',
          'Missing phase means unobserved/not applicable, not zero. Failed operations can have partial timings.',
          'Auth/client HTTP/total timings overlap; do not sum all phase fields.',
          '413 parent totals can include recursive split children; inspect rows before aggregating those.',
          'Request completion time excludes post-recording overhead and worker idle sleep; consecutive completion gaps are not pure HTTP latency.',
          'Counters before/after the planned Central Sync restart must be analysed separately.']}
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['KALPA REQUEST TIMING',f'Completed logical batch operations: {count}','HTTP status counts: '+json.dumps(dict(codes)),
      'Phase | samples | mean ms | p95 ms | max ms']
    for key,r in result['phase_summary'].items():lines.append(f'{key} | {r["count"]} | {r["mean_ms"]:.2f} | {r["p95_ms"]:.2f} | {r["max_ms"]:.2f}')
    lines+=['','Response wait is network + proxy + backend, not backend database time alone.']
    (out/'summary.txt').write_text('\n'.join(lines)+'\n')
    with tarfile.open(str(out)+'.tar.gz','w:gz') as tar:
        for p in out.iterdir():
            if p.is_file():tar.add(p,arcname=out.name+'/'+p.name)
    print('\n'.join(lines));print('Results: '+str(out)+'.tar.gz')
if __name__=='__main__':main()
