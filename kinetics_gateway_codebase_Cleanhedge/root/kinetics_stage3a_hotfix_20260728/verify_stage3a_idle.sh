#!/bin/sh
set -eu
SERVICE=kinetics-gateway.service
CG=/sys/fs/cgroup/system.slice/kinetics-gateway.service
OUT=/tmp/kinetics_stage3a_idle_$(date +%Y%m%d_%H%M%S).log

printf 'Stage 3A idle verification started: %s\n' "$(date -Is)" | tee "$OUT"
printf 'Keep Flutter closed and do not call fresh=true during this test.\n' | tee -a "$OUT"

I=1
while [ "$I" -le 20 ]; do
    PID=$(systemctl show "$SERVICE" -p MainPID --value)
    MEMORY=$(cat "$CG/memory.current")
    ANON=$(awk '$1=="anon" {print $2}' "$CG/memory.stat")
    FILEMEM=$(awk '$1=="file" {print $2}' "$CG/memory.stat")
    RSS=$(ps -p "$PID" -o rss= | tr -d ' ')
    CPU=$(ps -p "$PID" -o %cpu= | tr -d ' ')
    DB=$(curl -sS --connect-timeout 5 --max-time 10 http://127.0.0.1:8000/api/health | \
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("storage",{}).get("database_bytes",0))')
    awk \
        -v now="$(date -Is)" \
        -v total="$MEMORY" \
        -v anon="$ANON" \
        -v filemem="$FILEMEM" \
        -v rss="$RSS" \
        -v cpu="$CPU" \
        -v db="$DB" \
        'BEGIN {printf "%s Total=%.1fMB Anon=%.1fMB File=%.1fMB RSS=%.1fMB CPU=%s%% DB=%.1fMB\n", now,total/1048576,anon/1048576,filemem/1048576,rss/1024,cpu,db/1048576}' | tee -a "$OUT"
    I=$((I + 1))
    sleep 30
done

printf '\nSaved verification log: %s\n' "$OUT"
