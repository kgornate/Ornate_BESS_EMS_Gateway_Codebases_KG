#!/bin/sh
set -eu

BASE=http://127.0.0.1:8000
PY=/opt/kinetics-gateway/venv/bin/python3
STAMP=$(date +%Y%m%d_%H%M%S)
TMP=/tmp/stage3c_verify_${STAMP}
mkdir -p "$TMP"

echo "Stage 3C verification started: $(date -Is)"
echo "Keep Flutter closed. Do not start charge/discharge during this cache-only verification."

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
echo "1) Runtime-monitor cache samples for all four pairs"
for PAIR in pair_1 pair_2 pair_3 pair_4; do
    curl -sS --connect-timeout 3 --max-time 15 \
        -o "$TMP/${PAIR}_cache.json" \
        -w "$PAIR HTTP=%{http_code} elapsed=%{time_total}s\n" \
        -H "$AUTH" \
        "$BASE/api/diagnostics/runtime-monitor/${PAIR}/sample"
    "$PY" - "$TMP/${PAIR}_cache.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
r=p.get("refresh",{})
s=p.get("summary",{})
print({
    "source":r.get("source"),
    "stale":r.get("stale"),
    "freshness_ages":{k:v.get("age_seconds") for k,v in r.get("freshness",{}).items()},
    "precharge_state":s.get("precharge_state"),
    "positive_contactor_closed":s.get("positive_contactor_closed"),
    "negative_contactor_closed":s.get("negative_contactor_closed"),
    "pcs_operating_state":s.get("pcs_operating_state"),
    "pcs_power_setpoint_kw":s.get("pcs_power_setpoint_kw"),
    "pcs_actual_power_kw":s.get("pcs_actual_power_kw"),
    "errors":p.get("errors"),
})
if r.get("source") != "background_poll_cache" or p.get("errors"):
    raise SystemExit("FAIL: runtime-monitor cache sample is unavailable or stale")
PY
done

echo
echo "2) Compare cached safety fields with one sequential live read per pair"
for PAIR in pair_1 pair_2 pair_3 pair_4; do
    curl -fsS --connect-timeout 3 --max-time 15 \
        -H "$AUTH" \
        "$BASE/api/diagnostics/runtime-monitor/${PAIR}/sample" \
        -o "$TMP/${PAIR}_cache_compare.json"
    curl -sS --connect-timeout 3 --max-time 180 \
        -o "$TMP/${PAIR}_live_compare.json" \
        -w "$PAIR live HTTP=%{http_code} elapsed=%{time_total}s\n" \
        -H "$AUTH" \
        "$BASE/api/control-sequence/${PAIR}/status?fresh=true"
    "$PY" - "$PAIR" "$TMP/${PAIR}_cache_compare.json" "$TMP/${PAIR}_live_compare.json" <<'PY'
import json,sys
pair,cache_path,live_path=sys.argv[1:]
c=json.load(open(cache_path)); l=json.load(open(live_path))
cs=c.get("summary",{}); ls=l.get("summary",{})
exact=[
 "precharge_state","positive_contactor_closed","negative_contactor_closed",
 "pcs_operating_state","pcs_dc_breaker_feedback_closed",
]
failed=[]
for key in exact:
    if cs.get(key) != ls.get(key): failed.append((key,cs.get(key),ls.get(key)))
for key,tol in (("pcs_power_setpoint_kw",0.11),("pcs_actual_power_kw",1.0)):
    a,b=cs.get(key),ls.get(key)
    if a is None or b is None or abs(float(a)-float(b))>tol: failed.append((key,a,b))
print(pair,{"cache_source":c.get("refresh",{}).get("source"),"live_source":l.get("refresh",{}).get("source"),"mismatches":failed})
if c.get("errors") or l.get("errors") or failed:
    raise SystemExit(f"FAIL: cache/live safety comparison failed for {pair}")
PY
    sleep 2
done

curl -fsS --connect-timeout 3 --max-time 15 -H "$AUTH" \
    "$BASE/api/diagnostics/control-refresh" > "$TMP/control_refresh_before_stress.json"

echo
echo "3) Five-minute four-pair cache-sampling and memory stability"
I=1
while [ "$I" -le 30 ]; do
    J=1
    while [ "$J" -le 5 ]; do
        for PAIR in pair_1 pair_2 pair_3 pair_4; do
            curl -fsS --connect-timeout 3 --max-time 10 \
                -H "$AUTH" \
                "$BASE/api/diagnostics/runtime-monitor/${PAIR}/sample" \
                >/dev/null
        done
        J=$((J+1))
        sleep 2
    done
    printf '%s ' "$(date -Is)"
    memory_line
    I=$((I+1))
done

curl -fsS --connect-timeout 3 --max-time 15 -H "$AUTH" \
    "$BASE/api/diagnostics/control-refresh" > "$TMP/control_refresh_after_stress.json"

"$PY" - "$TMP/control_refresh_before_stress.json" "$TMP/control_refresh_after_stress.json" <<'PY'
import json,sys
before=json.load(open(sys.argv[1]))["pairs"]
after=json.load(open(sys.argv[2]))["pairs"]
changes={p:after[p]["started"]-before[p]["started"] for p in before}
print("Live-refresh starts during cache-only stress:",changes)
if any(changes.values()):
    raise SystemExit("FAIL: cache-only runtime samples initiated live hardware refreshes")
PY

echo
echo "4) Runtime-monitor diagnostics"
curl -fsS --connect-timeout 3 --max-time 20 \
    -H "$AUTH" "$BASE/api/diagnostics/runtime-monitor" \
    | "$PY" -m json.tool

echo
echo "Stage 3C cache-path verification completed."
echo "This does not energize any pair. The next validation is one-pair low-power active runtime monitoring."
echo "Temporary response files: $TMP"
