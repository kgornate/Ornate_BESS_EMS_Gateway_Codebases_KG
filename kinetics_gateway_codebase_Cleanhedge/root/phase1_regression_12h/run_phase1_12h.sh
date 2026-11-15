#!/bin/bash

set -u

TEST_ROOT="/root/phase1_regression_12h"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${TEST_ROOT}/${RUN_ID}"
INTERVAL_SECONDS=300
TOTAL_SAMPLES=144
SERVICE="kinetics-gateway.service"
SCHEDULER="bess-seven-day-v302.service"
DATABASE="/mnt/ems-logs/kinetics-gateway/kinetics_gateway.db"

mkdir -p "${RUN_DIR}/diagnostics"

METRICS="${RUN_DIR}/metrics.tsv"
EVENTS="${RUN_DIR}/events.log"

printf '%s\n' "${RUN_DIR}" > "${TEST_ROOT}/latest_run"
date --iso-8601=seconds > "${RUN_DIR}/started_at.txt"

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "timestamp" \
  "sample" \
  "active_state" \
  "sub_state" \
  "main_pid" \
  "restarts" \
  "memory_current_bytes" \
  "memory_peak_bytes" \
  "tasks_current" \
  "vmrss_kb" \
  "rssanon_kb" \
  "database_bytes" \
  "health_http" \
  "health_time_s" \
  "compact_http" \
  > "${METRICS}"

{
  echo "Phase 1 12-hour regression started"
  echo "Start: $(date --iso-8601=seconds)"
  echo "Run directory: ${RUN_DIR}"
  echo "Gateway service: ${SERVICE}"
  echo "Scheduler service: ${SCHEDULER}"
} | tee -a "${EVENTS}"

if systemctl is-active --quiet "${SCHEDULER}"; then
  echo "$(date --iso-8601=seconds) FAIL: scheduler is active" |
    tee -a "${EVENTS}"
  exit 1
fi

for SAMPLE in $(seq 1 "${TOTAL_SAMPLES}")
do
  TIMESTAMP="$(date --iso-8601=seconds)"

  ACTIVE_STATE="$(
    systemctl show "${SERVICE}" -p ActiveState --value 2>/dev/null
  )"

  SUB_STATE="$(
    systemctl show "${SERVICE}" -p SubState --value 2>/dev/null
  )"

  MAIN_PID="$(
    systemctl show "${SERVICE}" -p MainPID --value 2>/dev/null
  )"

  RESTARTS="$(
    systemctl show "${SERVICE}" -p NRestarts --value 2>/dev/null
  )"

  MEMORY_CURRENT="$(
    systemctl show "${SERVICE}" -p MemoryCurrent --value 2>/dev/null
  )"

  MEMORY_PEAK="$(
    systemctl show "${SERVICE}" -p MemoryPeak --value 2>/dev/null
  )"

  TASKS_CURRENT="$(
    systemctl show "${SERVICE}" -p TasksCurrent --value 2>/dev/null
  )"

  VMRSS_KB=""
  RSSANON_KB=""

  if [ -r "/proc/${MAIN_PID}/status" ]; then
    VMRSS_KB="$(
      awk '/^VmRSS:/ {print $2}' "/proc/${MAIN_PID}/status"
    )"

    RSSANON_KB="$(
      awk '/^RssAnon:/ {print $2}' "/proc/${MAIN_PID}/status"
    )"
  fi

  DATABASE_BYTES="$(
    stat -c '%s' "${DATABASE}" 2>/dev/null || echo 0
  )"

  HEALTH_RESULT="$(
    curl -sS \
      --connect-timeout 2 \
      --max-time 5 \
      -o "${RUN_DIR}/health_latest.json" \
      -w '%{http_code} %{time_total}' \
      http://127.0.0.1:8000/api/health \
      2>> "${EVENTS}" \
      || echo "000 5"
  )"

  HEALTH_HTTP="$(printf '%s' "${HEALTH_RESULT}" | awk '{print $1}')"
  HEALTH_TIME="$(printf '%s' "${HEALTH_RESULT}" | awk '{print $2}')"

  LOGIN_RESPONSE="$(
    curl -sS \
      --connect-timeout 3 \
      --max-time 20 \
      -X POST \
      http://127.0.0.1:8000/api/auth/login \
      -H 'Content-Type: application/json' \
      -d '{"username":"internal","password":"Internal@123"}' \
      2>> "${EVENTS}" \
      || true
  )"

  TOKEN="$(
    printf '%s' "${LOGIN_RESPONSE}" |
      python3 -c \
      'import json,sys
try:
    print(json.load(sys.stdin).get("access_token", ""))
except Exception:
    print("")'
  )"

  COMPACT_HTTP="000"
  COMPACT_TIME="0"

  if [ -n "${TOKEN}" ]; then
    COMPACT_RESULT="$(
      curl -sS \
        --connect-timeout 2 \
        --max-time 8 \
        -o "${RUN_DIR}/compact_latest.json" \
        -w '%{http_code} %{time_total}' \
        -H "Authorization: Bearer ${TOKEN}" \
        http://127.0.0.1:8000/api/control-sequence/status/all/compact \
        2>> "${EVENTS}" \
        || echo "000 8"
    )"

    COMPACT_HTTP="$(
      printf '%s' "${COMPACT_RESULT}" |
        awk '{print $1}'
    )"

    COMPACT_TIME="$(
      printf '%s' "${COMPACT_RESULT}" |
        awk '{print $2}'
    )"

    curl -sS \
      --connect-timeout 2 \
      --max-time 10 \
      -H "Authorization: Bearer ${TOKEN}" \
      http://127.0.0.1:8000/api/diagnostics/runtime \
      -o "${RUN_DIR}/diagnostics/runtime_${SAMPLE}.json" \
      2>> "${EVENTS}" \
      || true
  else
    echo "${TIMESTAMP} authentication failed at sample ${SAMPLE}" \
      >> "${EVENTS}"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${TIMESTAMP}" \
    "${SAMPLE}" \
    "${ACTIVE_STATE}" \
    "${SUB_STATE}" \
    "${MAIN_PID}" \
    "${RESTARTS}" \
    "${MEMORY_CURRENT}" \
    "${MEMORY_PEAK}" \
    "${TASKS_CURRENT}" \
    "${VMRSS_KB}" \
    "${RSSANON_KB}" \
    "${DATABASE_BYTES}" \
    "${HEALTH_HTTP}" \
    "${HEALTH_TIME}" \
    "${COMPACT_HTTP}" \
    >> "${METRICS}"

  printf '%s sample=%s/144 service=%s/%s restarts=%s rssanon_kb=%s health=%s compact=%s\n' \
    "${TIMESTAMP}" \
    "${SAMPLE}" \
    "${ACTIVE_STATE}" \
    "${SUB_STATE}" \
    "${RESTARTS}" \
    "${RSSANON_KB}" \
    "${HEALTH_HTTP}" \
    "${COMPACT_HTTP}" |
    tee -a "${EVENTS}"

  if [ "${ACTIVE_STATE}" != "active" ] ||
     [ "${SUB_STATE}" != "running" ]; then
    echo "${TIMESTAMP} FAILURE: gateway service not running" \
      >> "${EVENTS}"
  fi

  if systemctl is-active --quiet "${SCHEDULER}"; then
    echo "${TIMESTAMP} FAILURE: legacy scheduler became active" \
      >> "${EVENTS}"
  fi

  if [ "${SAMPLE}" -lt "${TOTAL_SAMPLES}" ]; then
    sleep "${INTERVAL_SECONDS}"
  fi
done

date --iso-8601=seconds > "${RUN_DIR}/completed_at.txt"

journalctl -u "${SERVICE}" \
  --since "$(cat "${RUN_DIR}/started_at.txt")" \
  --no-pager \
  > "${RUN_DIR}/gateway_journal.log"

journalctl -k \
  --since "$(cat "${RUN_DIR}/started_at.txt")" \
  --no-pager \
  > "${RUN_DIR}/kernel_journal.log"

df -h \
  / \
  /mnt/ems-logs \
  > "${RUN_DIR}/filesystem_final.txt" 2>&1

systemctl show "${SERVICE}" \
  -p ActiveState \
  -p SubState \
  -p MainPID \
  -p NRestarts \
  -p MemoryCurrent \
  -p MemoryPeak \
  -p TasksCurrent \
  --no-pager \
  > "${RUN_DIR}/service_final.txt"

echo "Phase 1 12-hour regression completed at $(date --iso-8601=seconds)" |
  tee -a "${EVENTS}"
