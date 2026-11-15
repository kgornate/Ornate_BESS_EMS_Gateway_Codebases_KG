#!/bin/sh
set -eu

BASE=http://127.0.0.1:8000
PY=/opt/kinetics-gateway/venv/bin/python3
STAMP=$(date +%Y%m%d_%H%M%S)
TMP=/tmp/stage3b_verify_${STAMP}
mkdir -p "$TMP"

echo "Stage 3B verification started: $(date -Is)"
echo "Keep Flutter closed. Do not start charge/discharge during this test."

TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

AUTH="Authorization: Bearer $TOKEN"

memory_line() {
    PID=$(systemctl show kinetics-gateway.service -p MainPID --value)
    CG=/sys/fs/cgroup/system.slice/kinetics-gateway.service
    TOTAL=$(cat "$CG/memory.current")
    ANON=$(awk '$1=="anon" {print $2}' "$CG/memory.stat")
    FILE=$(awk '$1=="file" {print $2}' "$CG/memory.stat")
    RSS=$(ps -p "$PID" -o rss= | tr -d ' ')
    CPU=$(ps -p "$PID" -o %cpu= | tr -d ' ')
    "$PY" - "$TOTAL" "$ANON" "$FILE" "$RSS" "$CPU" <<'PY'
import sys
v=list(map(float,sys.argv[1:]))
print(f"Memory Total={v[0]/1048576:.1f}MB Anon={v[1]/1048576:.1f}MB File={v[2]/1048576:.1f}MB RSS={v[3]/1024:.1f}MB CPU={v[4]:.1f}%")
PY
}

memory_line

echo
echo "1) Default cached-status latency (fresh omitted)"
for PAIR in pair_1 pair_2 pair_3 pair_4; do
    curl -sS --connect-timeout 3 --max-time 15 \
        -o "$TMP/${PAIR}_cached.json" \
        -w "$PAIR HTTP=%{http_code} elapsed=%{time_total}s\n" \
        -H "$AUTH" \
        "$BASE/api/control-sequence/${PAIR}/status"
done

echo
echo "2) Seed one live engineering result per pair, sequentially"
for PAIR in pair_1 pair_2 pair_3 pair_4; do
    curl -sS --connect-timeout 3 --max-time 180 \
        -o "$TMP/${PAIR}_live.json" \
        -w "$PAIR HTTP=%{http_code} elapsed=%{time_total}s\n" \
        -H "$AUTH" \
        "$BASE/api/control-sequence/${PAIR}/status?fresh=true"
    "$PY" - "$TMP/${PAIR}_live.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
r=p.get("refresh",{})
s=p.get("summary",{})
print({
 "source":r.get("source"),
 "busy":r.get("busy"),
 "duration_ms":r.get("duration_ms"),
 "precharge_state":s.get("precharge_state"),
 "positive_contactor_closed":s.get("positive_contactor_closed"),
 "negative_contactor_closed":s.get("negative_contactor_closed"),
 "pcs_operating_state":s.get("pcs_operating_state"),
 "pcs_power_setpoint_kw":s.get("pcs_power_setpoint_kw"),
 "pcs_actual_power_kw":s.get("pcs_actual_power_kw"),
 "errors":p.get("errors"),
})
PY
    sleep 3
done

echo
echo "3) Concurrent fresh=true protection test on pair_1"
for I in 1 2 3 4 5 6 7 8; do
    (
        curl -sS --connect-timeout 3 --max-time 180 \
            -o "$TMP/concurrent_${I}.json" \
            -w "%{http_code} %{time_total}\n" \
            -H "$AUTH" \
            "$BASE/api/control-sequence/pair_1/status?fresh=true" \
            > "$TMP/concurrent_${I}.meta"
    ) &
done
wait

"$PY" - "$TMP" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
rows=[]
for meta in sorted(root.glob("concurrent_*.meta")):
    idx=meta.stem.split("_")[-1]
    code,elapsed=meta.read_text().strip().split()
    body_path=root/f"concurrent_{idx}.json"
    try:
        body=json.load(open(body_path))
        refresh=body.get("refresh",{})
        source=refresh.get("source") or body.get("detail",{}).get("code")
        busy=refresh.get("busy")
    except Exception as exc:
        source=f"parse_error:{exc}"
        busy=None
    rows.append((int(idx),code,float(elapsed),source,busy))
for row in sorted(rows):
    print(f"request={row[0]} HTTP={row[1]} elapsed={row[2]:.3f}s source={row[3]} busy={row[4]}")
live=sum(1 for row in rows if row[3]=="live_hardware")
cached=sum(1 for row in rows if row[3]=="cached_while_live_refresh_busy")
conflict=sum(1 for row in rows if row[3]=="live_status_busy")
print(f"SUMMARY live_hardware={live} cached_busy={cached} conflict={conflict}")
if live > 1:
    raise SystemExit("FAIL: more than one concurrent request performed live hardware reads")
if live + cached + conflict != len(rows):
    raise SystemExit("FAIL: unexpected concurrent response")
PY

echo
echo "4) Refresh diagnostics"
curl -fsS --connect-timeout 3 --max-time 20 \
    -H "$AUTH" "$BASE/api/diagnostics/control-refresh" \
    | "$PY" -m json.tool

echo
echo "5) Five-minute cached polling and memory stability"
I=1
while [ "$I" -le 30 ]; do
    for PAIR in pair_1 pair_2 pair_3 pair_4; do
        curl -fsS --connect-timeout 3 --max-time 10 \
            -H "$AUTH" \
            "$BASE/api/control-sequence/${PAIR}/status?fresh=false" \
            >/dev/null
    done
    printf '%s ' "$(date -Is)"
    memory_line
    I=$((I+1))
    sleep 10
done

echo
echo "Stage 3B verification completed."
echo "Temporary response files: $TMP"
