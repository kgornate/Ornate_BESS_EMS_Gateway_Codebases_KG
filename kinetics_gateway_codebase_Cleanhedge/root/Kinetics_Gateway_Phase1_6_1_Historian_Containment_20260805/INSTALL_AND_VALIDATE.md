# Phase 1.6.1 FRDM Install and Safe Validation

The board must already contain the cumulative Phase 1.6 source. Keep `bess-seven-day-v302.service` disabled and inactive.

## Copy from Windows

```powershell
scp .\Kinetics_Gateway_Phase1_6_1_Historian_Containment_20260805.tar.gz gw1-ssh.unityess:/root/
```

## Install on FRDM

```bash
cd /root
tar -xzf Kinetics_Gateway_Phase1_6_1_Historian_Containment_20260805.tar.gz
cd Kinetics_Gateway_Phase1_6_1_Historian_Containment_20260805
bash install_phase1_6_1.sh
```

The installer creates a timestamped backup, overlays four source/test files, compiles Python, runs the targeted test and restarts only `kinetics-gateway.service`.

## Authenticate

```bash
LOGIN_RESPONSE=$(curl -fsS --max-time 20 -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"internal","password":"Internal@123"}')
TOKEN=$(printf '%s' "$LOGIN_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
export TOKEN
```

## Baseline checks

```bash
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health
curl -fsS --max-time 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/historian/status
systemctl is-enabled bess-seven-day-v302.service
systemctl is-active bess-seven-day-v302.service
```

Expected scheduler state: `disabled`, `inactive`.

## Safer staged concurrency test

Replace `bms_bank` only if that asset ID is unavailable. This test sends eight requests, not forty.

```bash
rm -f /tmp/p161_hist_*.txt
for N in $(seq 1 8); do
  (curl -sS --connect-timeout 2 --max-time 30 -o /tmp/p161_hist_${N}.json -w '%{http_code} %{time_total}\n' -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8000/api/historian/bms_bank?limit=5000' > /tmp/p161_hist_${N}.txt) &
done
wait
sort /tmp/p161_hist_*.txt | uniq -c
```

Expected: one request is admitted (normally HTTP 200); competing requests return fast HTTP 503. No request should return `000`.

## Protection evidence

```bash
for N in $(seq 1 30); do
  curl -fsS --connect-timeout 1 --max-time 3 -o /dev/null -w "$N HTTP=%{http_code} TIME=%{time_total}\n" http://127.0.0.1:8000/api/health
done
curl -fsS --max-time 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/historian/status
curl -fsS --max-time 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/diagnostics/runtime -o /tmp/p161_runtime.json
systemctl show kinetics-gateway.service -p ActiveState -p SubState -p NRestarts -p MemoryCurrent -p MemoryPeak -p TasksCurrent --no-pager
systemctl show cloudflared.service -p ActiveState -p SubState -p NRestarts --no-pager
journalctl -k --since '30 minutes ago' --no-pager | grep -Ei 'oom|out of memory|killed process|segfault|watchdog|hung task' || echo 'NO OOM OR KERNEL HANG FOUND'
```

Do not repeat the former 40-request stress test until this staged test is approved.
