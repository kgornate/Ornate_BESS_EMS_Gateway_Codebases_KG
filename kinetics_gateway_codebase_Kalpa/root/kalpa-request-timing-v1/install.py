#!/usr/bin/env python3
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from timing_probe import DEADLINE

ROOT=Path('/opt/ornate-ems-gateway/backend/app/central_sync')
MARK='# KALPA_REQUEST_TIMING_V1'
def insert(source,old,new):
    if source.count(old)!=1:raise RuntimeError('Source anchor differs; no changes made: '+old[:70])
    return source.replace(old,new,1)
def patch(name,source):
    if MARK in source:return source
    if name=='batcher.py':
        source=insert(source,'from .outbox import OutboxItem, OutboxStore\n','from .outbox import OutboxItem, OutboxStore\nfrom .timing_probe import timed_prepare\n'+MARK+'\n')
        source=insert(source,'    def prepare(self) -> PreparedBatch | None:\n','    @timed_prepare\n    def prepare(self) -> PreparedBatch | None:\n')
    elif name=='client.py':
        source=insert(source,'import httpx\n','import httpx\nfrom .timing_probe import timed_network, measured_phase, trace_callback\n'+MARK+'\n')
        source=insert(source,'    async def _post_once(\n','    @timed_network\n    async def _post_once(\n')
        source=insert(source,'    async def _obtain_token(\n','    @measured_phase("auth_ms")\n    async def _obtain_token(\n')
        source=insert(source,'                content=payload,\n                headers=headers,\n','                content=payload,\n                headers=headers,\n                extensions={"trace": trace_callback},\n')
    elif name=='uploader.py':
        source=insert(source,'from .batcher import OutboxBatcher, PreparedBatch\n','from .batcher import OutboxBatcher, PreparedBatch\nfrom .timing_probe import timed_batch, measured_phase, lock_started, lock_acquired\n'+MARK+'\n')
        source=insert(source,'    async def _send_prepared(self, prepared: PreparedBatch) -> None:\n','    @timed_batch\n    async def _send_prepared(self, prepared: PreparedBatch) -> None:\n')
        source=insert(source,'            async with self.transport_lock:\n                response = await self.client.post_batch(prepared.json_bytes)\n','            lock_started()\n            async with self.transport_lock:\n                lock_acquired()\n                response = await self.client.post_batch(prepared.json_bytes)\n')
        source=insert(source,'    async def _apply_partial_ack(self, items: list[OutboxItem], response: TransportResponse) -> None:\n','    @measured_phase("ack_apply_ms")\n    async def _apply_partial_ack(self, items: list[OutboxItem], response: TransportResponse) -> None:\n')
    else:raise RuntimeError('Unexpected source file')
    compile(source,name,'exec');return source
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout.strip()
def write(path,data,mode):
    p=path.with_name(path.name+'.timing-tmp');p.write_bytes(data);p.chmod(mode);os.replace(p,path)
def annotate(kind):
    event=json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'event':kind,'reason':'request timing instrumentation; planned Central Sync restart; analyse producer/uploader counters by instance'})+'\n'
    for name in ('kalpa-overnight-results-path.txt','kalpa-cadence-results-path.txt'):
        marker=Path('/root')/name
        if marker.is_file():
            p=Path(marker.read_text().strip())
            if p.is_dir():
                with (p/'external_changes.jsonl').open('a') as f:f.write(event)
def main():
    import httpx
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');a=ap.parse_args()
    root=Path(__file__).resolve().parent
    for line in (root/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split(None,1)
        if hashlib.sha256((root/name.strip()).read_bytes()).hexdigest()!=digest:raise RuntimeError('Package checksum mismatch')
    if time_now()>=DEADLINE:raise RuntimeError('Timing capture deadline has passed; nothing changed.')
    if 'extensions' not in inspect.signature(httpx.AsyncClient.post).parameters:raise RuntimeError('Installed HTTPX lacks trace extension argument')
    versions={n:importlib.metadata.version(n) for n in ('httpx','httpcore')}
    names=('client.py','batcher.py','uploader.py');old={n:(ROOT/n).read_bytes() for n in names}
    updated={n:patch(n,old[n].decode()).encode() for n in names}
    compile((root/'timing_probe.py').read_text(),'timing_probe.py','exec')
    # Real installed HTTPX/httpcore smoke test using a temporary loopback server.
    import asyncio,threading
    from http.server import BaseHTTPRequestHandler,HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    events=[]
    async def local_trace(name,info):events.append(name)
    async def smoke():
        async with httpx.AsyncClient(timeout=3,trust_env=False) as client:
            response=await client.post('http://127.0.0.1:'+str(server.server_port),content=b'{}',extensions={'trace':local_trace})
            if response.status_code!=200 or not any('send_request_body' in e for e in events) or not any('receive_response' in e for e in events):raise RuntimeError('Installed trace API smoke test failed; no changes made')
    try:asyncio.run(smoke())
    finally:server.shutdown();server.server_close();worker.join(timeout=2)
    print('Preflight PASS: source anchors, syntax, checksums and HTTPX extension signature.',versions)
    if a.check_only:return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=Path('/root')/('kalpa-request-timing-backup-'+stamp);backup.mkdir(mode=0o700)
    for n in names:shutil.copy2(ROOT/n,backup/n)
    helper=ROOT/'timing_probe.py';had=helper.exists()
    if had:shutil.copy2(helper,backup/'timing_probe.py')
    (backup/'versions.json').write_text(json.dumps(versions)+'\n')
    active=run('systemctl','show','ornate-central-sync.service','-p','ActiveState','--value')=='active'
    paused=[]
    try:
        # Keep existing observers alive while the measured service briefly restarts.
        # Their monotonic deadlines continue; the gap is explicitly annotated.
        annotate('timing_install_begin')
        for unit in ('kalpa-overnight-test.service','kalpa-cadence-audit.service'):
            status=subprocess.run(['systemctl','show',unit,'-p','MainPID','--value'],capture_output=True,text=True)
            pid=int(status.stdout.strip() or 0) if status.returncode==0 else 0
            if pid>0:os.kill(pid,signal.SIGSTOP);paused.append(pid)
        if active:run('systemctl','stop','ornate-central-sync.service')
        write(helper,(root/'timing_probe.py').read_bytes(),0o644)
        for n in names:write(ROOT/n,updated[n],(ROOT/n).stat().st_mode&0o777)
        if active:run('systemctl','start','ornate-central-sync.service')
        annotate('timing_install_complete')
    except BaseException:
        for n in names:shutil.copy2(backup/n,ROOT/n)
        if had:shutil.copy2(backup/'timing_probe.py',helper)
        else:helper.unlink(missing_ok=True)
        if active:subprocess.run(['systemctl','start','ornate-central-sync.service'],check=False)
        annotate('timing_install_rollback');raise
    finally:
        for pid in paused:
            try:os.kill(pid,signal.SIGCONT)
            except ProcessLookupError:pass
    print('Timing installed. Central Sync restarted.' if active else 'Timing installed; Central Sync remains inactive.')
    print('Backup:',backup)
    print('Observers resumed; planned restart boundary recorded in both result folders.')
    print('Tracing ends at 11 AM IST, 3 November; transport settings are unchanged.')
def time_now():
    import time
    return time.time()
if __name__=='__main__':main()
