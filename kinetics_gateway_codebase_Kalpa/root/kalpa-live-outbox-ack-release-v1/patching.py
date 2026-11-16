import ast
MARKER='# KALPA_IMMEDIATE_ACK_RELEASE_V1'

def replace_function(source,name,replacement):
    tree=ast.parse(source)
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    if len(nodes)!=1:raise RuntimeError('Unexpected function layout: '+name)
    node=nodes[0];lines=source.splitlines(keepends=True)
    start=min([node.lineno]+[d.lineno for d in node.decorator_list])-1
    lines[start:node.end_lineno]=[replacement+'\n']
    return ''.join(lines)

def patch_outbox(source):
    if MARKER in source:return source
    imp='from .contracts import LogicalMessage\n'
    init='        self._init_schema()\n'
    accepted='''                    self.conn.executemany(
                        """UPDATE outbox_messages
                           SET state='acked',acknowledged_utc=?,ack_status=?,
                               backend_detail=?,last_error=NULL
                           WHERE message_id=?""",
                        [(acked_utc, status, detail, message_id)
                         for message_id, status, detail in accepted_rows],
                    )'''
    for anchor in (imp,init,accepted):
        if source.count(anchor)!=1:raise RuntimeError('Outbox differs from verified layout; no patch applied')
    source=source.replace(imp,imp+'from .queue_metrics import init_receipts, release_acked, prune_receipts\n'+MARKER+'\n',1)
    source=source.replace(init,init+'        init_receipts(self.conn)\n        self.conn.commit()\n',1)
    source=source.replace(accepted,'''                    for message_id, status, detail in accepted_rows:
                        release_acked(self.conn, message_id, status, time.time())
                    prune_receipts(self.conn)''',1)
    source=replace_function(source,'mark_accepted','''    def mark_accepted(self, message_id: str, ack_status: str, detail: str | None = None) -> None:
        self.apply_batch_updates(accepted=[(message_id, ack_status, detail)])
''')
    # Receipts are also aged out by existing cleanup, without retaining payloads.
    cleanup=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='cleanup')
    lines=source.splitlines(keepends=True);part=''.join(lines[cleanup.lineno-1:cleanup.end_lineno])
    anchor='            self.conn.commit()\n'
    if part.count(anchor)!=1:raise RuntimeError('Cleanup layout differs')
    part=part.replace(anchor,'            prune_receipts(self.conn)\n'+anchor,1)
    old_delete="DELETE FROM outbox_messages WHERE state='acked' AND acknowledged_utc < ?"
    if part.count(old_delete)!=1:raise RuntimeError('ACK cleanup predicate differs')
    part=part.replace(old_delete,old_delete+" AND ack_status IN ('accepted','already_processed')",1)
    lines[cleanup.lineno-1:cleanup.end_lineno]=[part];source=''.join(lines)
    compile(source,'outbox.py','exec');return source

def patch_dashboard(source):
    if '# KALPA_COMPACT_ACK_VIEW_V1' in source:return source
    source=replace_function(source,'ack_window','''def ack_window(path, now=None, seconds=60, budget=1.0, max_rows=10000, max_bytes=64*1024*1024):
    # KALPA_COMPACT_ACK_VIEW_V1
    from queue_metrics import receipt_window
    return receipt_window(path, now=now, seconds=seconds, budget=budget, max_rows=max_rows)
''')
    source=replace_function(source,'s2_retained','''def s2_retained(path, budget=0.75):
    from queue_metrics import s2_queue_receipts
    return s2_queue_receipts(path, budget=budget)
''')
    source=source.replace('(LIVE uploader retained rows only)','(LIVE compact ACK receipts; payloads released)')
    source=source.replace('retained rows; excludes archives; sample age','ACKs: last 5min metadata; queue: current; excludes archives; sample age')
    source=source.replace('ACK time, retained live rows, excludes duplicates/recovery','ACK time, compact 5min receipts, excludes duplicates/recovery')
    source=source.replace('no retained ACK','no recent ACK receipt')
    compile(source,'dashboard.py','exec');return source
