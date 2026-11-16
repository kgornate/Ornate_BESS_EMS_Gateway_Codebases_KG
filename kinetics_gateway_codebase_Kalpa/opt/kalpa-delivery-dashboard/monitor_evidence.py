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

def summarize_pending(rows):
    groups={};pending=[];oldest=None;total_bytes=0;count=0
    for row in rows:
        if row['state'] not in PENDING:continue
        count+=1;total_bytes+=int(row['payload_bytes'])
        oldest=row['created_epoch'] if oldest is None else min(oldest,row['created_epoch'])
        key=(row['stream'],row['substream']);v=groups.setdefault(key,[0,0])
        v[0]+=1;v[1]+=int(row['payload_bytes'])
        if len(groups)>256:raise RuntimeError('per-database group limit exceeded')
        pending.append(row)
    return {'count':count,'bytes':total_bytes,'oldest':oldest,'groups':groups,
            'samples':sorted(pending,key=lambda r:r['created_epoch'],reverse=True)[:5]}

class Evidence:
    """Retain compact per-DB counts, not all backlog message IDs."""
    def __init__(self):
        self.pending_cache={};self.pending_errors={};self.signatures={}
        self.last_scan=-1e20;self.last_attempt={};self.ack_cache={};self.ack_attempt={}
    def snapshot(self,live,root,pattern,now):
        live=Path(live);archives=sorted(p for p in Path(root).glob(pattern) if p.is_file() and p!=live)
        # A bounded metadata catalog may be exceeded without killing live monitoring.
        truncated=len(archives)>4096;inventory_count=len(archives)+1
        archives=archives[:4096];paths=[live]+archives;keys={str(p) for p in paths}
        for mapping in (self.pending_cache,self.signatures,self.last_attempt,self.ack_cache,self.ack_attempt):
            for key in set(mapping)-keys:del mapping[key]
        if time.monotonic()-self.last_scan>=15:
            self.last_scan=time.monotonic();self.pending_errors={};deadline=time.monotonic()+2
            ordered=[live]+sorted(archives,key=lambda p:self.last_attempt.get(str(p),-1))
            for p in ordered:
                k=str(p)
                try:
                    before=signature(p)
                    if p!=live and k in self.pending_cache and before==self.signatures.get(k):
                        self.pending_cache[k]=(now,self.pending_cache[k][1]);continue
                    if time.monotonic()>deadline:
                        self.pending_errors[k]='refresh work budget reached; will retry';continue
                    self.last_attempt[k]=now
                    sample=summarize_pending(indexed_pending(p));after=signature(p)
                    self.pending_cache[k]=(now,sample);self.signatures[k]=after if before==after else None
                except Exception as exc:self.pending_errors[k]=str(exc)
        complete=not truncated and not self.pending_errors and all(str(p) in self.pending_cache for p in paths)
        q={'live':0,'sd':0,'bytes':0,'oldest':None,'groups':{}};samples=[]
        for p in paths:
            k=str(p)
            if k not in self.pending_cache:continue
            loc='LIVE' if p==live else 'SD';v=self.pending_cache[k][1]
            q[loc.lower()]+=v['count'];q['bytes']+=v['bytes']
            if v['oldest'] is not None:q['oldest']=v['oldest'] if q['oldest'] is None else min(q['oldest'],v['oldest'])
            for group,counts in v['groups'].items():
                bucket=q['groups'].setdefault(group,{'LIVE':0,'SD':0});bucket[loc]+=counts[0]
            samples.extend(dict(row,location=loc) for row in v['samples'])
        # No claim of cross-DB ID deduplication: pending counts are stored copies.
        samples=sorted(samples,key=lambda r:r['created_epoch'],reverse=True)[:30]
        ack_rows={};ack_errors={};formats={'LIVE':[0,1],'SD':[0,len(archives)]};deadline=time.monotonic()+2
        # Fair refresh; unchanged DB receipt rows can be reused and window-filtered.
        ordered=[live]+sorted(archives,key=lambda p:self.ack_attempt.get(str(p),-1))
        for p in ordered:
            k=str(p);loc='LIVE' if p==live else 'SD'
            try:
                before=signature(p);cached=self.ack_cache.get(k)
                if cached and cached[0]==before:rr,has_table=cached[1:]
                elif time.monotonic()>deadline:
                    ack_errors[k]='ACK refresh work budget reached';continue
                else:
                    self.ack_attempt[k]=now;rr,has_table=receipts(p,now);after=signature(p)
                    rr=[r for r in rr if now-60<=r['ack_epoch']<=now]
                    # Bound retained receipt memory across all databases.
                    if sum(len(v[1]) for key,v in self.ack_cache.items() if key!=k)+len(rr)>20000:
                        self.ack_cache.clear()
                    self.ack_cache[k]=(after if before==after else None,rr,has_table)
                if has_table:formats[loc][0]+=1
                for row in rr:
                    if not now-60<=row['ack_epoch']<=now:continue
                    if len(ack_rows)>=20000 and row['message_id'] not in ack_rows:
                        ack_errors[k]='global ACK row limit exceeded';break
                    row=dict(row,location=loc,source_db=k);old=ack_rows.get(row['message_id'])
                    if old:
                        locations=set(old['location'].split('+'))|{loc}
                        chosen=row if row['ack_epoch']>=old['ack_epoch'] else dict(old)
                        chosen['location']='+'.join(sorted(locations));ack_rows[row['message_id']]=chosen
                    else:ack_rows[row['message_id']]=row
            except Exception as exc:ack_errors[k]=str(exc)
        if truncated:
            self.pending_errors['inventory']='more than 4096 archive files; catalog partial'
            ack_errors['inventory']='archive catalog partial'
        live_rows=[r for r in ack_rows.values() if 'LIVE' in r['location']]
        sd_rows=[r for r in ack_rows.values() if 'SD' in r['location']]
        return {'pending':samples,'queue_summary':q,'queue_complete':complete,
                'queue_coverage':{'sampled':len(self.pending_cache),'verified':sum(str(p) in self.pending_cache and str(p) not in self.pending_errors for p in paths),'total':inventory_count},
                'queue_errors':dict(self.pending_errors),'queue_age':max([max(0,now-v[0]) for v in self.pending_cache.values()]or[0]),
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
