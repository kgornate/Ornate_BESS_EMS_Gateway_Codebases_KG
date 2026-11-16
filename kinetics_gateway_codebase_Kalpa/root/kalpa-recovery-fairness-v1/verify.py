import json
import subprocess
import time
from pathlib import Path
base=Path('/opt/ornate-ems-gateway/backend')
cfg=json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text())
def read(p):
    p=Path(p);return json.loads((p if p.is_absolute() else base/p).read_text())
def snapshot():
    s=read(cfg['overflow_recovery'].get('status_file','/var/lib/ornate-central-sync/overflow_recovery_status.json'))
    live=read(cfg.get('status_file','/var/lib/ornate-central-sync/status.json'))
    c=s.get('counters',{});u=live.get('uploader',{});q=live.get('outbox',{})
    return {'utc':s.get('status_generated_utc'),'recovery_running':s.get('running'),
        'archive_requests':c.get('request_cycle_count'),'bounded_opportunities':c.get('bounded_opportunity_count'),
        'pause_reason':c.get('last_pause_reason'),'health':s.get('resource_gate'),
        'live_unacked':q.get('unacked_count'),'live_capacity_ok':q.get('capacity',{}).get('can_accept'),
        'live_accepted':u.get('accepted_count'),'live_retry_rejected':u.get('retryable_rejected_count')}
subprocess.run(['systemctl','is-active','ornate-central-sync.service'])
a=snapshot();print('START',json.dumps(a,indent=2),flush=True)
print('Observing for 45 seconds; a hard resource/live guard may legitimately block recovery.',flush=True)
time.sleep(45)
b=snapshot();print('END',json.dumps(b,indent=2))
print('Archive request cycles mean attempts, not proof of message acceptance or backlog drain.')
print('For progress inspect archive ACK outcomes and remaining unacknowledged counts; a retained file alone is not an undelivered queue.')
