"""Additive batch membership observation. Never participates in delivery policy."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sqlite3
import time

TRACE_PATH='/var/lib/kinetics-central-sync/batch_trace.db'
def safe_detail(value):
    s=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',str(value or ''))
    s=re.sub(r'(?i)Bearer\s+[^\s"\'<>]+','Bearer [REDACTED]',s)
    s=re.sub(r'''(?ix)(["']?(?:[\w-]*(?:password|secret|token|authorization)[\w-]*)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',r'\1[REDACTED]',s)
    s=re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b','[REDACTED JWT]',s)
    return ''.join(c for c in s if c.isprintable())[:600]

def record_batch_trace(prepared,response,source_db,trace_path=TRACE_PATH):
    """Completed request membership only; successful HTTP is not per-message ACK.

    Caller contains errors so tracing cannot change the uploader outcome. No
    telemetry values, access tokens or request headers are stored.
    """
    members=[{'message_id':str(i.message_id),'stream':str(i.stream),
              'substream':str(i.substream or ''),'payload_bytes':int(i.payload_bytes)} for i in prepared.items]
    counts=Counter((i['stream'],i['substream']) for i in members)
    groups=[{'stream':k[0],'substream':k[1],'messages':v} for k,v in sorted(counts.items())]
    path=Path(trace_path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=.05)
    try:
        db.execute('PRAGMA busy_timeout=50')
        db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        db.execute('''CREATE TABLE IF NOT EXISTS batch_trace (
          request_id TEXT PRIMARY KEY,completed_utc TEXT NOT NULL,completed_epoch REAL NOT NULL,
          source_db TEXT NOT NULL,http_status INTEGER,message_count INTEGER,payload_bytes INTEGER,
          groups_json TEXT NOT NULL,members_json TEXT NOT NULL,detail TEXT)''')
        now=time.time();code=int(response.status_code or 0)
        detail=(getattr(response,'exception',None) or getattr(response,'text','')) if not 200<=code<300 else ''
        with db:
            db.execute('INSERT OR REPLACE INTO batch_trace VALUES (?,?,?,?,?,?,?,?,?,?)',
              (str(prepared.request_id),datetime.now(timezone.utc).isoformat(),now,str(source_db),code,
               len(members),len(prepared.json_bytes),json.dumps(groups,separators=(',',':')),
               json.dumps(members,separators=(',',':')),safe_detail(detail)))
            db.execute('DELETE FROM batch_trace WHERE completed_epoch<?',(now-86400,))
            db.execute('''DELETE FROM batch_trace WHERE request_id NOT IN
              (SELECT request_id FROM batch_trace ORDER BY completed_epoch DESC LIMIT 1000)''')
        db.execute('PRAGMA incremental_vacuum(8)')
    finally:db.close()

