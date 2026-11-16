#!/usr/bin/env python3
import json
from pathlib import Path
import sqlite3
import subprocess
import time

BASE=Path('/opt/ornate-ems-gateway/backend')
cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
path=Path(cfg['outbox']['path'])
if not path.is_absolute():path=BASE/path
def totals():
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute('SELECT sum(messages),sum(payload_bytes) FROM generation_metrics_v1').fetchone()
before=totals();print('Generation counters:',before,flush=True)
time.sleep(10)
after=totals();print('After 10s:',after)
print('Service:',subprocess.run(['systemctl','is-active','ornate-central-sync.service'],capture_output=True,text=True).stdout.strip())
with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    db.execute('PRAGMA query_only=ON')
    page=db.execute('PRAGMA page_size').fetchone()[0];pages=db.execute('PRAGMA page_count').fetchone()[0];free=db.execute('PRAGMA freelist_count').fetchone()[0]
    print('Logical used MiB:',round(page*(pages-free)/1048576,2),'Reusable MiB:',round(page*free/1048576,2))
    print('Message states:',db.execute('SELECT state,count(*) FROM outbox_messages GROUP BY state').fetchall())
    print('Compact ACK receipts:',db.execute('SELECT count(*),max(ack_epoch) FROM delivery_receipts_v1').fetchone())
print('New durable generation observed:',bool(after[0] is not None and before[0] is not None and after[0]>before[0]))
print('A short zero-generation window alone is not proof of failure; inspect producer status if needed.')
print('This verifies local release/recovery; it does not certify backend throughput or S2 acceptance.')
