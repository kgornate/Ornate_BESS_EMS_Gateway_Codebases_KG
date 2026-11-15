"""Atomic ACK release plus small, bounded operational receipts; never payload history."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import time

RETENTION_SECONDS=300
MAX_RECEIPTS=5000
VALID_ACKS=frozenset(('accepted','already_processed'))

def init_receipts(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS delivery_receipts_v1 (
        message_id TEXT PRIMARY KEY,stream TEXT NOT NULL,substream TEXT NOT NULL,
        ack_status TEXT NOT NULL,ack_epoch REAL NOT NULL,record_count INTEGER NOT NULL,
        payload_bytes INTEGER NOT NULL,created_epoch REAL NOT NULL,attempt_count INTEGER NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS delivery_receipts_time_v1 ON delivery_receipts_v1(ack_epoch)')

def release_acked(conn,message_id,status,ack_epoch):
    """Called inside the SAME transaction as ACK processing. No COMMIT here."""
    if status not in VALID_ACKS:raise ValueError('Only explicit accepted/already_processed ACKs may release payloads')
    row=conn.execute('SELECT stream,substream,payload_bytes,payload_json,created_epoch,attempt_count FROM outbox_messages WHERE message_id=?',(message_id,)).fetchone()
    if row is None:return False
    stream,sub,size,payload,created,attempts=row
    body=json.loads(payload);count=body.get('record_count')
    if not isinstance(count,int) or isinstance(count,bool) or count<0:
        records=body.get('records')
        if not isinstance(records,list):raise ValueError('Cannot count records in acknowledged message')
        count=len(records)
    conn.execute('INSERT OR IGNORE INTO delivery_receipts_v1 VALUES(?,?,?,?,?,?,?,?,?)',
        (message_id,stream,sub or '',status,float(ack_epoch),count,int(size),float(created),int(attempts)))
    conn.execute('DELETE FROM outbox_messages WHERE message_id=?',(message_id,))
    return True

def prune_receipts(conn,now=None):
    now=time.time() if now is None else now
    conn.execute('DELETE FROM delivery_receipts_v1 WHERE ack_epoch<?',(now-RETENTION_SECONDS,))
    conn.execute('''DELETE FROM delivery_receipts_v1 WHERE message_id IN (
        SELECT message_id FROM delivery_receipts_v1 ORDER BY ack_epoch DESC,message_id DESC
        LIMIT -1 OFFSET ?)''',(MAX_RECEIPTS,))

def compact_existing(conn,now=None):
    """Remove only existing valid ACKed rows. Keep all unsent/dead rows intact.

    Preserve recent operational receipts, not the old payload history. Caller
    runs this with Central Sync stopped and owns commit/rollback.
    """
    now=time.time() if now is None else now
    init_receipts(conn);recent=[]
    for mid,status,stamp in conn.execute("SELECT message_id,ack_status,acknowledged_utc FROM outbox_messages WHERE state='acked' AND ack_status IN ('accepted','already_processed') AND acknowledged_utc IS NOT NULL"):
        try:epoch=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
        except (ValueError,TypeError):continue
        if now-RETENTION_SECONDS<=epoch<=now:recent.append((mid,status,epoch))
    for mid,status,stamp in recent:release_acked(conn,mid,status,stamp)
    deleted=conn.execute("DELETE FROM outbox_messages WHERE state='acked' AND ack_status IN ('accepted','already_processed') AND acknowledged_utc IS NOT NULL").rowcount
    prune_receipts(conn,now)
    return deleted+len(recent)

def receipt_window(path,now=None,seconds=60,budget=1.0,max_rows=10000,**unused):
    end=time.time() if now is None else now
    result={'start':end-seconds,'end':end,'streams':{},'substreams':{},'accepted':[0,0,0],'duplicate':[0,0,0]}
    if not 0<seconds<=RETENTION_SECONDS:return None,'Requested window exceeds compact receipt retention'
    path=Path(path)
    if not path.is_file():return None,'live database not found'
    db=None;clock=time.monotonic()
    try:
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.3)
        db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda:int(time.monotonic()-clock>budget),1000)
        count,oldest=db.execute('SELECT count(*),min(ack_epoch) FROM delivery_receipts_v1').fetchone()
        if count>=MAX_RECEIPTS and oldest is not None and oldest>end-seconds:return None,'Receipt count bound may truncate this window; totals unavailable'
        seen=0
        for stream,sub,status,records,size in db.execute('SELECT stream,substream,ack_status,record_count,payload_bytes FROM delivery_receipts_v1 WHERE ack_epoch>=? AND ack_epoch<=?',(end-seconds,end)):
            seen+=1
            if seen>max_rows or time.monotonic()-clock>budget:return None,'Receipt query read budget exceeded'
            if status not in VALID_ACKS:return None,'Unknown receipt ACK status'
            category='duplicate' if status=='already_processed' else 'accepted';delta=(1,int(records),int(size))
            for i in range(3):result[category][i]+=delta[i]
            if category=='accepted':
                values=result['streams'].setdefault(stream,[0,0,0])
                for i in range(3):values[i]+=delta[i]
                if stream=='general_asset_telemetry':
                    values=result['substreams'].setdefault(sub or '(none)',[0,0,0])
                    for i in range(3):values[i]+=delta[i]
        return result,None
    except sqlite3.Error as e:return None,'Receipt query interrupted' if 'interrupted' in str(e) else type(e).__name__
    finally:
        if db is not None:db.close()

def s2_queue_receipts(path,budget=.75):
    db=None;clock=time.monotonic()
    try:
        db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.3)
        db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda:int(time.monotonic()-clock>budget),1000)
        result={}
        for sub,state,count in db.execute("SELECT substream,state,count(*) FROM outbox_messages WHERE stream='general_asset_telemetry' GROUP BY substream,state"):
            result.setdefault(sub or '(none)',{})[state]=count
        for sub,count,stamp in db.execute("SELECT substream,count(*),max(ack_epoch) FROM delivery_receipts_v1 WHERE stream='general_asset_telemetry' AND ack_epoch>=? GROUP BY substream",(time.time()-RETENTION_SECONDS,)):
            row=result.setdefault(sub or '(none)',{});row['acked']=count;row['latest_ack']=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
        return result,None
    except sqlite3.Error as e:return {},'Receipt/queue query interrupted' if 'interrupted' in str(e) else type(e).__name__
    finally:
        if db is not None:db.close()

def receipt_rows(path,budget=.75):
    """Viewer-compatible recent metadata; no payload fields exist in this table."""
    db=None;clock=time.monotonic()
    try:
        db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.3)
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-clock>budget),1000)
        result={}
        for r in db.execute('SELECT * FROM delivery_receipts_v1 WHERE ack_epoch>=?',(time.time()-RETENTION_SECONDS,)):
            row=dict(r);row.update(state='acked',location='LIVE',last_error=None,backend_detail=None,
              acknowledged_utc=datetime.fromtimestamp(row['ack_epoch'],timezone.utc).isoformat())
            result[row['message_id']]=row
        return result
    finally:
        if db is not None:db.close()

