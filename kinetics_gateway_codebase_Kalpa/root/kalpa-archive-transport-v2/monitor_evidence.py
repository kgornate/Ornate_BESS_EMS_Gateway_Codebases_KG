"""Read-only indexed queue/receipt evidence for the terminal monitor."""
from pathlib import Path
import sqlite3
import time

PENDING={'pending','retry','inflight'}
PRIORITY={'pending':0,'retry':1,'inflight':2,'dead':3}

def query(path,sql,args=(),budget=.5,limit=150000,connection=None):
    p=Path(path)
    if not p.is_file():raise RuntimeError('database missing')
    db=connection or sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
    start=time.monotonic()
    try:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()-start>budget),1000)
        rows=[]
        for r in db.execute(sql,args):
            if len(rows)>=limit or time.monotonic()-start>budget:raise RuntimeError('read budget exceeded')
            rows.append(dict(r))
        return rows
    finally:
        db.set_progress_handler(None,0)
        if connection is None:db.close()

def indexed_pending(path):
    p=Path(path)
    if not p.is_file():raise RuntimeError('database missing')
    db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        return _indexed_pending(path,db)
    finally:db.close()

def _indexed_pending(path,db):
    def query_pending(path,sql,args=()):
        return query(path,sql,args,connection=db)
    # All three queries are covering-index reads: never fetch payload_json or
    # cross its overflow pages to reach state/error columns in the main table.
    a=query_pending(path,"""SELECT rowid AS rid,state,created_epoch,payload_bytes
        FROM outbox_messages INDEXED BY idx_outbox_stats
        WHERE state IN ('pending','retry','inflight','dead')""")
    if not a:return []
    b={r['rid']:r for r in query_pending(path,"""SELECT rowid AS rid,stream,substream
        FROM outbox_messages INDEXED BY idx_outbox_stream""")}
    c={r['rid']:r['message_id'] for r in query_pending(path,"""SELECT rowid AS rid,message_id
        FROM outbox_messages INDEXED BY idx_outbox_spill
        WHERE state IN ('pending','retry','inflight','dead')""")}
    result=[]
    for r in a:
        if r['rid'] not in b or r['rid'] not in c:
            raise RuntimeError('queue changed during indexed sample; retry next refresh')
        r.update(stream=b[r['rid']]['stream'],substream=b[r['rid']]['substream'],message_id=c[r['rid']])
        del r['rid'];result.append(r)
    return result

def receipts(path,now):
    tables=query(path,"SELECT name FROM sqlite_master WHERE type='table' AND name='delivery_receipts_v1'")
    if not tables:return [],False
    cap=query(path,'SELECT COUNT(*) AS n,MIN(ack_epoch) AS oldest FROM delivery_receipts_v1')[0]
    if cap['n']>=5000 and cap['oldest'] is not None and cap['oldest']>now-60:
        raise RuntimeError('receipt retention count may truncate 60s window')
    rows=query(path,"""SELECT message_id,stream,substream,ack_status,ack_epoch,
        record_count,payload_bytes,created_epoch,attempt_count
        FROM delivery_receipts_v1 WHERE ack_epoch>=? AND ack_epoch<=?""",(now-60,now),limit=10000)
    if any(r['ack_status'] not in ('accepted','already_processed') for r in rows):
        raise RuntimeError('unknown ACK status')
    return rows,True

def ack_stats(rows):
    result={'accepted':[0,0,0],'duplicate':[0,0,0],'streams':{},'substreams':{}}
    for r in rows:
        delta=[1,int(r['record_count']),int(r['payload_bytes'])]
        key='accepted' if r['ack_status']=='accepted' else 'duplicate'
        result[key]=[a+b for a,b in zip(result[key],delta)]
        if key=='accepted':
            for group,value in [('streams',r['stream']),('substreams',(r['stream'],r['substream']))]:
                row=result[group].setdefault(value,[0,0,0])
                result[group][value]=[a+b for a,b in zip(row,delta)]
    return result

def signature(path):
    values=[]
    for p in (Path(path),Path(str(path)+'-wal')):
        try:
            st=p.stat();values.append((st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_ino))
        except FileNotFoundError:values.append(None)
    return tuple(values)

class Evidence:
    def __init__(self):self.pending_cache={};self.pending_errors={};self.signatures={};self.last_scan=-1e20
    def snapshot(self,live,root,pattern,now):
        paths=[Path(live)]+sorted(Path(root).glob(pattern))
        if len(paths)>128:raise RuntimeError('more than 127 archives; evidence scope exceeded')
        if time.monotonic()-self.last_scan>=15:
            self.last_scan=time.monotonic()
            # Reset the errors each cycle; retain good cached values with explicit age.
            self.pending_errors={}
            deadline=time.monotonic()+2
            ordered=paths[:1]+sorted(paths[1:],key=lambda p:self.pending_cache.get(str(p),(-1,[]))[0])
            for p in ordered:
                before=signature(p)
                if p!=Path(live) and str(p) in self.pending_cache and before==self.signatures.get(str(p)):
                    # No DB/WAL modification: revalidate unchanged cached metadata.
                    self.pending_cache[str(p)]=(now,self.pending_cache[str(p)][1])
                    continue
                if time.monotonic()>deadline:
                    self.pending_errors[str(p)]='refresh work budget reached; will retry'
                    continue
                try:
                    sample=indexed_pending(p)
                    after=signature(p)
                    self.pending_cache[str(p)]=(now,sample)
                    self.signatures[str(p)]=after if before==after else None
                except Exception as e:self.pending_errors[str(p)]=str(e)
            for old in set(self.pending_cache)-{str(p) for p in paths}:del self.pending_cache[old]
        items={};locations={};queue_complete=not self.pending_errors
        for p in paths:
            value=self.pending_cache.get(str(p))
            if value is None:queue_complete=False;continue
            loc='LIVE' if p==Path(live) else 'SD'
            for r in value[1]:
                mid=r['message_id'];locations.setdefault(mid,set()).add(loc)
                if mid not in items or PRIORITY[r['state']]>PRIORITY[items[mid]['state']]:items[mid]=dict(r)
        for mid,r in items.items():r['location']='+'.join(sorted(locations[mid]))
        ack_rows={};ack_errors={};formats={'LIVE':[0,1],'SD':[0,max(0,len(paths)-1)]}
        deadline=time.monotonic()+2
        for p in paths:
            loc='LIVE' if p==Path(live) else 'SD'
            if time.monotonic()>deadline:
                ack_errors[str(p)]='ACK refresh work budget reached'
                continue
            try:
                rr,has_table=receipts(p,now)
                if has_table:formats[loc][0]+=1
                for r in rr:
                    r=dict(r,location=loc,source_db=str(p))
                    old=ack_rows.get(r['message_id'])
                    if old and old['location']!=loc:r['location']='LIVE+SD'
                    if old is None or r['ack_epoch']>=old['ack_epoch']:ack_rows[r['message_id']]=r
            except Exception as e:ack_errors[str(p)]=str(e)
        live_rows=[r for r in ack_rows.values() if 'LIVE' in r['location']]
        sd_rows=[r for r in ack_rows.values() if 'SD' in r['location']]
        return {'pending':list(items.values()),'queue_complete':queue_complete,
            'queue_errors':dict(self.pending_errors),
            'queue_age':max([max(0,now-v[0]) for v in self.pending_cache.values()] or [0]),
            'live':ack_stats(live_rows),'archive':ack_stats(sd_rows),'total':ack_stats(ack_rows.values()),
            'ack_rows':list(ack_rows.values()),'ack_errors':ack_errors,'formats':formats,'paths':paths}

def sub_status(accepted,pending,queue_complete,ack_complete):
    parts=[]
    if accepted and ack_complete:parts.append('ACKs observed')
    elif not ack_complete:parts.append('ACK evidence partial')
    else:parts.append('no ACK in 60s')
    if not queue_complete:parts.append('queue partial')
    elif pending:parts.append('backlog present')
    else:parts.append('no pending observed')
    return '; '.join(parts)
