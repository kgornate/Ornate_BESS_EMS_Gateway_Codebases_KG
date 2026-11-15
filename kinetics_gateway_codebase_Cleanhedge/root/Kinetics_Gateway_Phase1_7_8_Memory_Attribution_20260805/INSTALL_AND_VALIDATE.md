# Install and validate Phase 1.7 + 1.8

## Deploy

From Windows PowerShell:

```powershell
scp .\Kinetics_Gateway_Phase1_7_8_Memory_Attribution_20260805.tar.gz gw1-ssh.unityess:/root/
```

On FRDM:

```bash
cd /root
tar -xzf Kinetics_Gateway_Phase1_7_8_Memory_Attribution_20260805.tar.gz
cd Kinetics_Gateway_Phase1_7_8_Memory_Attribution_20260805
bash install_phase1_7_8.sh
```

## Validation

First wait for initialization without generating load:

```bash
for N in $(seq 1 36); do
  BODY=$(curl -sS --connect-timeout 1 --max-time 3 http://127.0.0.1:8000/api/health || true)
  printf '%s %s\n' "$N" "$BODY" | grep -o '"ready":[^,}]*' || true
  printf '%s' "$BODY" | grep -q '"ready":true' && break
  sleep 5
done
```

Confirm service and scheduler:

```bash
systemctl show kinetics-gateway.service -p ActiveState -p SubState -p MainPID -p NRestarts -p MemoryCurrent -p MemoryPeak -p TasksCurrent --no-pager
systemctl is-enabled bess-seven-day-v302.service
systemctl is-active bess-seven-day-v302.service
```

Expected scheduler state is `disabled` and `inactive`. Do not start it.

Authenticate and save runtime diagnostics:

```bash
LOGIN_RESPONSE=$(curl -fsS --connect-timeout 3 --max-time 20 -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"internal","password":"Internal@123"}')
TOKEN=$(printf '%s' "$LOGIN_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
export TOKEN
curl -fsS --connect-timeout 2 --max-time 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/diagnostics/runtime -o /tmp/p178_runtime_before.json
python3 -m json.tool /tmp/p178_runtime_before.json
```

Run a bounded five-minute sampler in the background:

```bash
GATEWAY_PID=$(systemctl show kinetics-gateway.service -p MainPID --value)
python3 /opt/kinetics-gateway/backend/tools/phase1_memory_probe.py --pid "$GATEWAY_PID" --samples 60 --interval 5 --output /tmp/p178_memory.jsonl &
PROBE_PID=$!
```

During the sample, validate only normal health and compact control status:

```bash
for N in $(seq 1 30); do
  curl -sS --connect-timeout 1 --max-time 3 -o /dev/null -w "$N HEALTH=%{http_code} TIME=%{time_total}\n" http://127.0.0.1:8000/api/health
  curl -sS --connect-timeout 1 --max-time 5 -o /dev/null -w "$N COMPACT=%{http_code} TIME=%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/control-sequence/status/all/compact
  sleep 10
done | tee /tmp/p178_api_soak.txt
wait "$PROBE_PID"
```

Summarize memory attribution:

```bash
python3 - <<'PY'
import json
rows=[json.loads(x) for x in open('/tmp/p178_memory.jsonl') if x.strip()]
for key,path in {
 'rss':('process','rss_bytes'),'rss_anon':('process','rss_anon_bytes'),
 'cgroup':('cgroup','current_bytes'),'cgroup_anon':('cgroup','anon_bytes'),
 'cgroup_file':('cgroup','file_bytes')}.items():
 vals=[r[path[0]].get(path[1]) for r in rows if r[path[0]].get(path[1]) is not None]
 print(key, 'first=', vals[0] if vals else None, 'last=', vals[-1] if vals else None,
       'delta=', vals[-1]-vals[0] if vals else None, 'max=', max(vals) if vals else None)
PY
```

Final checks:

```bash
curl -fsS --connect-timeout 2 --max-time 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/diagnostics/runtime -o /tmp/p178_runtime_after.json
python3 -m json.tool /tmp/p178_runtime_after.json
systemctl show kinetics-gateway.service -p ActiveState -p SubState -p MainPID -p NRestarts -p MemoryCurrent -p MemoryPeak -p TasksCurrent --no-pager
journalctl -u kinetics-gateway.service --since '-10 minutes' --no-pager -p warning
journalctl -k --since '-10 minutes' --no-pager | grep -Ei 'oom|out of memory|killed process|segfault|watchdog|hung task' || echo 'NO OOM, KILL, OR HANG FOUND'
```

## Phase 2 gate

Pass when all health and compact calls are HTTP 200, `NRestarts=0`, no OOM or
hang occurs, process anonymous RSS does not show sustained growth, and startup
finishes. A high cgroup number dominated by `file_bytes` is cache, not a Python
heap leak, and does not by itself block Phase 2.
