"""Bounded read-only evidence collectors; never retain secrets or sensor values."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time

def clean(value):
    s=str(value or '')
    s=re.sub(r'(?i)(bearer\s+)[^\s"\']+',r'\1[REDACTED]',s)
    s=re.sub(r'(?i)((?:client_secret|access_token|password|authorization|api_key)["\s:=]+)[^\s,"}]+',r'\1[REDACTED]',s)
    s=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[REDACTED]',s)
    return ''.join(c if c.isprintable() else ' ' for c in s)[:1200]

def query(path,sql,args=(),limit=10000):
    path=Path(path)
    if not path.is_file():raise RuntimeError('database missing')
    started=time.monotonic()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
    try:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-started>1),1000)
        rows=[]
        for r in db.execute(sql,args):
            if len(rows)>=limit or time.monotonic()-started>1:raise RuntimeError('query read budget exceeded')
            rows.append(dict(r))
        return rows
    finally:db.close()

def generation(path):
    return query(path,'SELECT stream,substream,messages,payload_bytes,first_epoch FROM generation_metrics_v1',limit=256)

def generation_delta(first,last):
    a={(r['stream'],r['substream']):r for r in first}
    b={(r['stream'],r['substream']):r for r in last}
    if any(k not in b or b[k]['first_epoch']!=v['first_epoch'] or b[k]['payload_bytes']<v['payload_bytes'] or b[k]['messages']<v['messages'] for k,v in a.items()):
        raise RuntimeError('generation counters reset')
    return [{'stream':k[0],'substream':k[1],
             'messages':r['messages']-a.get(k,{}).get('messages',0),
             'bytes':r['payload_bytes']-a.get(k,{}).get('payload_bytes',0)} for k,r in sorted(b.items())]

def attempts(path,cutoff,seen,after_id=0):
    rows=query(path,'SELECT id,request_id,attempted_utc,message_count,payload_bytes,http_status,outcome,detail FROM transport_attempts WHERE id>? AND attempted_utc>=? ORDER BY id',(after_id,cutoff))
    result=[]
    for r in rows:
        key=(r['id'],r['request_id'])
        if key in seen:continue
        r['detail']=clean(r['detail']);result.append(r)
    return result

def trace(request_id):
    rows=query('/var/lib/ornate-central-sync/batch_trace.db',
       'SELECT request_id,completed_utc,source_db,http_status,message_count,payload_bytes,groups_json,members_json,detail FROM batch_trace WHERE request_id=?',(request_id,))
    if not rows:return None
    r=rows[0];r['groups']=json.loads(r.pop('groups_json'));r['members']=json.loads(r.pop('members_json'))
    r['detail']=clean(r['detail']);return r

def resources():
    result={}
    try:
        values={line.split(':')[0]:line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines()}
        result['memory']={k:values[k] for k in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
        result['loadavg']=Path('/proc/loadavg').read_text().split()[:3]
        for line in Path('/proc/net/dev').read_text().splitlines():
            if ':' not in line:continue
            interface,fields=line.split(':',1);v=fields.split()
            if interface.strip()=='mlan0':result['wifi_cumulative']={k:int(v[i]) for k,i in (('rx_bytes',0),('rx_errors',2),('rx_dropped',3),('tx_bytes',8),('tx_errors',10),('tx_dropped',11))}
        for name,path in (('sd','/mnt/ems-logs'),('internal','/var/lib/ornate-central-sync')):
            u=shutil.disk_usage(path);result[name]={'total':u.total,'free':u.free}
    except Exception as e:result['unavailable']=type(e).__name__
    return result

def config_summary(cfg):
    return {section:{key:cfg.get(section,{}).get(key) for key in keys} for section,keys in (
      ('backend',('timeout_sec','connect_timeout_sec','gzip_enabled')),
      ('uploader',('worker_count','max_messages_per_request','max_request_bytes')),
      ('overflow_archive',('enabled',)),('overflow_recovery',('enabled',)))}
