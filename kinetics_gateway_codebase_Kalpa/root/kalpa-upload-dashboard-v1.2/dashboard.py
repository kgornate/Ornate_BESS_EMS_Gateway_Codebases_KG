#!/usr/bin/env python3
"""Read-only terminal upload/ACK view. No API calls, producer or uploader work."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import math
import statistics
import textwrap

SERVICE = 'ornate-central-sync.service'
STREAMS = [
    ('S1', 'fast_bess', 'fast_bess_telemetry'),
    ('S2', 'general_assets', 'general_asset_telemetry'),
    ('S3', 'gateway_health', 'gateway_health'),
    ('S4', 'alarms_events', 'alarms_events'),
    ('S5', 'soc_controller', 'soc_controller'),
    ('S6', 'solis', 'solis'),
    ('S7', 'edge_ai', 'edge_ai'),
    ('S8', 'configuration_audit', 'configuration_audit'),
    ('S9', 'command_downlink', 'command_results'),
    ('S10', 'charge_discharge', 'battery_charge_discharge_control'),
]
SUBSTREAMS = [
    'ems_system', 'pcs_extended', 'bms_extended', 'io_module', 'liquid_cooling',
    'fire_protection', 'dehumidifier', 'remote_control',
    'bess_aux_consumption_meter', 'combiner_box_meter',
]
COUNTERS = ('request_count', 'accepted_count', 'already_processed_count',
            'bytes_sent_uncompressed')


def clean(value):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    text = re.sub(r'(?i)Bearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'''(?ix)(["']?(?:[\w-]*(?:password|secret|token|authorization)[\w-]*)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED JWT]', text)
    text = re.sub(r'(https?://)[^/\s@]+@', r'\1[REDACTED]@', text)
    return ''.join(c for c in text if c.isprintable())


def number(value):
    try:
        result = float(value)
        return result if result == result and abs(result) != float('inf') else 0.0
    except (TypeError, ValueError):
        return 0.0


def integer(value):
    return int(number(value))


def epoch(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def age(value, now=None):
    stamp = epoch(value)
    if stamp is None:
        return None
    return max(0.0, (time.time() if now is None else now) - stamp)


def age_text(value, now=None):
    elapsed = age(value, now)
    if elapsed is None:
        return 'unknown'
    if elapsed < 120:
        return f'{elapsed:.0f}s'
    if elapsed < 7200:
        return f'{elapsed/60:.1f}m'
    return f'{elapsed/3600:.1f}h'


def load_json(path):
    try:
        path = Path(path)
        if path.stat().st_size > 2 * 1024 * 1024:
            return {}, 'file exceeds 2 MiB read limit'
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return {}, 'expected JSON object'
        return value, None
    except (OSError, ValueError) as error:
        return {}, type(error).__name__


def resolve(path, base):
    path = Path(path)
    return path if path.is_absolute() else base / path


class Rates:
    """Derive deltas from status timestamps, never from repeated screen draws."""
    def __init__(self):
        self.previous = None
        self.latest = None
        self.note = 'waiting for next fresh status sample'

    def update(self, status):
        stamp = epoch(status.get('status_generated_utc'))
        if stamp is None:
            self.latest = None
            self.note = 'status unavailable; rates unavailable'
            return None
        current = (status.get('started_utc'), stamp,
                   tuple(number(status.get('uploader', {}).get(k)) for k in COUNTERS))
        before = self.previous
        if before == current:
            return self.latest
        self.previous = current
        if before is None or before[0] != current[0] or current[1] <= before[1] or any(c < b for c, b in zip(current[2], before[2])):
            self.latest = None
            self.note = 'new baseline / counter reset; waiting for next sample'
            return None
        dt = current[1] - before[1]
        self.latest = tuple((c-b)/dt for c, b in zip(current[2], before[2]))
        self.note = f'deltas over {dt:.1f}s between status samples'
        return self.latest


def s2_retained(path, budget=0.75):
    path = Path(path)
    if not path.is_file():
        return {}, 'database not found'
    db = None
    started = time.monotonic()
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.3)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic()-started > budget), 1000)
        rows = db.execute('''SELECT substream,state,COUNT(*),MAX(acknowledged_utc)
            FROM outbox_messages WHERE stream=? GROUP BY substream,state''',
            ('general_asset_telemetry',)).fetchall()
        result = {}
        for sub, state, count, latest in rows:
            item = result.setdefault(sub or '(none)', {})
            item[state] = count
            if state == 'acked':
                item['latest_ack'] = latest
        return result, None
    except sqlite3.Error as error:
        return {}, 'query budget exceeded' if 'interrupted' in str(error) else type(error).__name__
    finally:
        if db is not None:
            db.close()


def archive_summary(directory, max_files=4096):
    result = {'files': 0, 'bytes': 0, 'markers': 0, 'truncated': False, 'error': None}
    root = Path(directory)
    if not root.is_dir():
        result['error'] = 'directory not present'
        return result
    try:
        for path in root.glob('central_sync_overflow_*.db'):
            if result['files'] >= max_files:
                result['truncated'] = True
                break
            result['files'] += 1
            for suffix in ('', '-wal', '-shm'):
                try: result['bytes'] += Path(str(path) + suffix).stat().st_size
                except FileNotFoundError: pass
            result['markers'] += int(Path(str(path) + '.replayed.json').exists())
        result['free'] = shutil.disk_usage(root).free
    except OSError as error:
        result['error'] = type(error).__name__
    return result



def ack_window(path, now=None, seconds=60, budget=1.0, max_rows=10000, max_bytes=64*1024*1024):
    """Count actual retained message ACKs by ACK time, never generation time.

    Payload scans are bounded; incomplete evidence is unavailable, not a zero.
    Original per-message JSON bytes exclude transport wrapper, compression/TLS.
    """
    end = time.time() if now is None else now
    result = {'start': end-seconds, 'end': end, 'streams': {}, 'substreams': {},
              'accepted': [0, 0, 0], 'duplicate': [0, 0, 0]}
    path = Path(path)
    if not path.is_file(): return None, 'live database not found'
    connection = None
    started = time.monotonic()
    seen = set()
    byte_count = 0
    cutoff = datetime.fromtimestamp(end-seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    upper = datetime.fromtimestamp(end+1, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=0.3)
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic()-started > budget), 1000)
        rows = connection.execute("""SELECT message_id,stream,substream,ack_status,
            acknowledged_utc,payload_bytes,payload_json FROM outbox_messages
            WHERE state='acked' AND acknowledged_utc>=? AND acknowledged_utc<=?""", (cutoff, upper))
        for mid, stream, sub, status, timestamp, size, payload in rows:
            if time.monotonic()-started > budget: return None, 'ACK query time budget exceeded'
            ack = epoch(timestamp)
            if ack is None: return None, 'invalid acknowledgement timestamp'
            if not end-seconds <= ack <= end: continue
            if mid in seen: continue
            seen.add(mid)
            byte_count += len(payload.encode('utf-8'))
            if len(seen)>max_rows or byte_count>max_bytes:
                return None, 'ACK scan size limit reached; totals not shown'
            body = json.loads(payload)
            count = body.get('record_count')
            if not isinstance(count, int) or isinstance(count, bool) or count<0:
                values = body.get('records')
                if not isinstance(values, list): return None, 'record count unavailable in retained message'
                count = len(values)
            if status not in {'accepted','already_processed'}:
                return None, 'unrecognized retained ACK status'
            category = 'duplicate' if status=='already_processed' else 'accepted'
            delta = [1, count, integer(size)]
            total = result[category]
            for i in range(3): total[i] += delta[i]
            if category == 'accepted':
                totals = result['streams'].setdefault(stream,[0,0,0])
                for i in range(3):totals[i] += delta[i]
                if stream=='general_asset_telemetry':
                    totals=result['substreams'].setdefault(sub or '(none)',[0,0,0])
                    for i in range(3):totals[i] += delta[i]
        return result, None
    except (sqlite3.Error, ValueError, TypeError) as error:
        return None, 'ACK query time budget exceeded' if 'interrupted' in str(error) else type(error).__name__
    finally:
        if connection is not None: connection.close()


def minute_lines(window, error=None):
    lines = []
    if window is None:
        return ['BACKEND ACCEPTED PER MINUTE: unavailable - '+str(error or 'not sampled yet')]
    end = datetime.fromtimestamp(window['end'], timezone.utc).strftime('%H:%M:%S UTC')
    lines.append('BACKEND ACCEPTED: actual 60s ACK window ending '+end+' (LIVE uploader retained rows only)')
    msgs, records, amount = window['accepted']
    lines.append(f'TOTAL {msgs} messages/min | {records} records/min | {amount/1e6:.3f} MB/min logical JSON | {records/60:.2f} records/s')
    dm, dr, db = window['duplicate']
    lines.append(f'DUPLICATE ACKs separately: {dm} messages/min | {dr} records/min | {db/1e6:.3f} MB/min (already_processed; not new ingestion)')
    lines.append(f'{"Accepted stream / S2 substream":46} {"Msg/min":>8} {"Rec/min":>10} {"MB/min":>10}')
    for slot, key, stream in STREAMS:
        values=window['streams'].get(stream,[0,0,0])
        lines.append(f'{(slot+" "+stream):46} {values[0]:8} {values[1]:10} {values[2]/1e6:10.3f}')
        if slot=='S2':
            for sub in SUBSTREAMS+sorted(set(window['substreams'])-set(SUBSTREAMS)):
                values=window['substreams'].get(sub,[0,0,0])
                lines.append(f'{("  "+sub):46} {values[0]:8} {values[1]:10} {values[2]/1e6:10.3f}')
    lines.append('Archive-recovery records/MB per minute: unavailable (ACKed archive rows are immediately cleaned).')
    return lines


def request_window(path, now=None, seconds=60, budget=0.5, max_rows=10000):
    """Completed live transport attempts by persisted completion time; no HTTP calls.

    record_attempt writes attempted_utc after post_batch returns. Sizes are
    uncompressed batch JSON, including retries and failed attempts, not wire bytes.
    """
    end = time.time() if now is None else now
    path = Path(path)
    if not path.is_file(): return None, 'live database not found'
    conn = None
    started = time.monotonic()
    low = datetime.fromtimestamp(end-seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    high = datetime.fromtimestamp(end+1, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    counts = {'end':end, 'requests':0, 'http_2xx':0, 'http_errors':0,
              'no_response':0, 'bytes':0, 'sizes':[]}
    try:
        conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=0.3)
        conn.execute('PRAGMA query_only=ON')
        conn.set_progress_handler(lambda: int(time.monotonic()-started > budget), 1000)
        for stamp, size, http in conn.execute('''SELECT attempted_utc,payload_bytes,http_status
                FROM transport_attempts WHERE attempted_utc>=? AND attempted_utc<=?''', (low,high)):
            if time.monotonic()-started > budget: return None, 'HTTP query time budget exceeded'
            ts = epoch(stamp)
            if ts is None: return None, 'invalid HTTP attempt timestamp'
            if not end-seconds <= ts <= end: continue
            if not isinstance(size,int) or size<0: return None, 'invalid request byte count'
            counts['requests'] += 1
            if counts['requests']>max_rows: return None, 'HTTP scan size limit reached'
            counts['sizes'].append(size)
            counts['bytes'] += size
            code = integer(http)
            counts['http_2xx' if 200<=code<300 else ('http_errors' if code else 'no_response')] += 1
        sizes = sorted(counts.pop('sizes'))
        counts['median_bytes'] = statistics.median(sizes) if sizes else None
        counts['p95_bytes'] = sizes[math.ceil(len(sizes)*.95)-1] if sizes else None
        counts['max_bytes'] = max(sizes) if sizes else None
        return counts, None
    except sqlite3.Error as error:
        return None, 'HTTP query time budget exceeded' if 'interrupted' in str(error) else type(error).__name__
    finally:
        if conn is not None: conn.close()


def performance_lines(minute, requests, request_error, now, service, stale):
    lines = ['UPLOAD SPEED - rolling 60s; LIVE uploader only']
    if service != 'active' or stale:
        lines.append('WARNING: service inactive/unavailable or status stale; saved counters are historical.')
    if minute is None:
        lines.append('ACKed delivery: UNAVAILABLE (see evidence note below)')
    else:
        msgs, records, size = minute['accepted']
        lines.append(f'ACCEPTED: {size/1e6:.3f} MB/min | {msgs} msg/min | {records:,} records/min')
        lines.append(f'          {size/1e6/60:.4f} MB/s | {records/60:.2f} records/s')
        lines.append(f'ACK window sample age: {max(0,now-minute["end"]):.0f}s; duplicates excluded')
    if requests is None:
        lines.append('Completed HTTP requests: UNAVAILABLE - '+str(request_error or 'not sampled'))
    else:
        lines.append(f'HTTP: {requests["requests"]} req/min | 2xx {requests["http_2xx"]} | HTTP errors {requests["http_errors"]} | no response {requests["no_response"]}')
        lines.append(f'Attempted batch JSON: {requests["bytes"]/1e6:.3f} MB/min (retries/failures included)')
        if requests['requests']:
            lines.append(f'Request size: median {requests["median_bytes"]/1000:.1f} KB | p95 {requests["p95_bytes"]/1000:.1f} KB | max {requests["max_bytes"]/1e6:.3f} MB')
        else:
            lines.append('Request size: no completed requests in this window')
        lines.append(f'HTTP window sample age: {max(0,now-requests["end"]):.0f}s; active requests excluded')
    lines.extend([
        'Response time mean/p95: UNAVAILABLE - duration not recorded by current uploader',
        'REFERENCE PLAN (your image; not a measured capacity test):',
        'Metric                  Gateway workload        Backend design target',
        'JSON throughput         ~5.5-8 MB/min           >=15-16 MB/min',
        'Messages                ~140-150 msg/min        >=300 msg/min',
        'Requests                ~45-50 req/min          >=90-100 req/min',
        'Typical request         ~100-200 KB             handle this size',
        'Largest request         ~1.14 MB observed       allow >=2 MB body',
        'Response time           ~1s average needed      ideally p95 <1.5s',
        'Accepted throughput measures delivery at current load; it does not prove backend capacity.',
    ])
    return lines


def defaults(config, backend):
    archive = config.get('overflow_archive', {})
    recovery = config.get('overflow_recovery', {})
    return {
        'live': resolve(config.get('status_file', '/var/lib/ornate-central-sync/status.json'), backend),
        'recovery': resolve(recovery.get('status_file', '/var/lib/ornate-central-sync/overflow_recovery_status.json'), backend),
        'replay_upload': resolve(recovery.get('uploader_status_file', '/var/lib/ornate-central-sync/overflow_recovery_uploader_status.json'), backend),
        'archiver': resolve(archive.get('status_file', '/var/lib/ornate-central-sync/overflow_archiver_status.json'), backend),
        'outbox': resolve(config.get('outbox', {}).get('path', '/var/lib/ornate-central-sync/central_sync.db'), backend),
        'archives': resolve(archive.get('archive_dir', '/mnt/ems-logs/ornate-ems-gateway/backlog_archives'), backend),
    }


def run_command(args):
    try:
        value = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return value.stdout.strip() or 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'


def recent_backend_logs(previous):
    text = run_command(['journalctl', '-u', SERVICE, '-n', '160', '--no-pager', '-o', 'cat'])
    entries = list(previous)
    for line in text.splitlines():
        if ('POST https://' in line or ('app.central_sync.' in line and any(x in line.lower() for x in ('failed', 'rejected', 'timeout')))):
            line = clean(line)
            if line not in entries:
                entries.append(line)
    return entries[-3:]


def stream_state(config, key, result, stale):
    if config.get(key, {}).get('enabled') is False:
        return 'DISABLED'
    if stale:
        return 'STALE STATUS'
    last = result.get('last_backend_result')
    if last:
        return str(last)
    if integer(result.get('acked_count')):
        return 'ACK observed'
    return 'no ACK observed'


def draw(config, live, recovery, replay, archiver, rate, rate_note, dbrows,
         db_error, archives, cached_at, service, logs, width=140, now=None, minute=None, minute_error=None, details=False,
         requests=None, request_error=None):
    now = time.time() if now is None else now
    lines = []
    def add(value=''):
        value = clean(value)
        lines.extend(textwrap.wrap(value, width=max(70,width), replace_whitespace=False,
                                   drop_whitespace=False) or [''])
    elapsed = age(live.get('status_generated_utc'), now)
    stale = elapsed is None or elapsed > 15
    u, b, out = (live.get(k, {}) for k in ('uploader', 'backend', 'outbox'))
    add('ORNATE / KALPA - UPLOADER AND BACKEND ACKs')
    add(datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') +
        f' | Service: {service} | Status age: {age_text(live.get("status_generated_utc"), now)}' + (' [STALE / UNAVAILABLE]' if stale else ''))
    add('Endpoint: ' + str(b.get('ingest_url') or config.get('backend', {}).get('base_url', 'unknown')))
    add()
    for line in performance_lines(minute, requests, request_error, now, service, stale):add(line)
    add()
    if details:
        add(f'LIVE HTTP requests {integer(u.get("request_count"))} | 2xx {integer(u.get("request_success_count"))} | failed {integer(u.get("request_failure_count"))} | last HTTP {u.get("last_http_status") or "none"} | paused {live.get("transport_control", {}).get("paused", "unknown")}')
        add(f'MESSAGE RESULTS  accepted {integer(u.get("accepted_count"))} | already processed {integer(u.get("already_processed_count"))} | retry rejects {integer(u.get("retryable_rejected_count"))} | permanent rejects {integer(u.get("permanent_rejected_count"))} | missing ACK {integer(u.get("missing_ack_count"))}')
        if rate is not None and not stale:
            add(f'ATTEMPT RATE estimate {rate[0]*60:.2f} req/min | {(rate[1]+rate[2])*60:.2f} ACK msg/min | {rate[3]*60/1e6:.3f} MB/min attempted JSON (status deltas; retries included)')
        else:
            add('RECENT DELTAS  unavailable - ' + ('status stale' if stale else rate_note))
        add(f'Auth successes/failures {integer(b.get("auth_success_count"))}/{integer(b.get("auth_failure_count"))} | workers {integer(u.get("worker_count"))} | active requests {integer(u.get("active_request_count"))} | last success {u.get("last_success_utc") or "none"}')
    add('Last transport error: ' + str(u.get('last_error') or 'none (check per-stream results below)'))
    add()
    for line in minute_lines(minute, minute_error):add(line)
    add()
    if details:add(f'{"Stream":46} {"ACKed":>7} {"RetryRej":>8} {"PermRej":>7} {"MissACK":>7}  Last backend result')
    summaries = live.get('stream_backend_ack', {})
    errors = []
    for slot, key, default_stream in STREAMS:
        stream = config.get(key, {}).get('stream', default_stream)
        item = summaries.get(stream, {})
        result = stream_state(config, key, item, stale)
        if details:add(f'{(slot+" "+stream):46} {integer(item.get("acked_count")):7} {integer(item.get("retryable_rejected_count")):8} {integer(item.get("permanent_rejected_count")):7} {integer(item.get("missing_ack_count")):7}  {result}')
        if item.get('last_error'):
            label = 'TRANSPORT FAILURE' if item.get('last_backend_result')=='transport_failure' else 'BACKEND REJECTION'
            errors.append(label + ': ' + slot + ': ' + str(item['last_error']) + ' | ' + str(item.get('last_backend_result')))
    for error in errors[:3]:add(error)
    if len(errors)>3:add(f'Additional stream errors: {len(errors)-3}; inspect the status file for details.')
    if details:
        add()
        add(f'S2 LIVE-DB EVIDENCE (retained rows; excludes archives; sample age {max(0, now-cached_at):.0f}s)')
        add(f'{"Substream":34} {"ACKed":>7} {"Pending":>8} {"Retry":>7} {"Flight":>7} {"Dead":>6}  Latest ACK UTC')
        for sub in SUBSTREAMS + sorted(set(dbrows)-set(SUBSTREAMS)):
            row = dbrows.get(sub, {})
            add(f'{sub:34} {integer(row.get("acked")):7} {integer(row.get("pending")):8} {integer(row.get("retry")):7} {integer(row.get("inflight")):7} {integer(row.get("dead")):6}  {row.get("latest_ack") or "no retained ACK"}')
        if db_error:add('DB evidence unavailable: ' + db_error)
    add()
    add(f'LIVE QUEUE (status may lag up to 30s) pending {integer(out.get("pending_count"))} | retry {integer(out.get("retry_count"))} | inflight {integer(out.get("inflight_count"))} | dead {integer(out.get("dead_count"))} | unACKed {integer(out.get("unacked_count"))} | JSON {number(out.get("sendable_bytes"))/1024**2:.2f} MiB')
    add('Oldest live sendable age: ' + str(out.get('oldest_sendable_age_sec', 'unknown')) + 's; archives excluded')
    rc = recovery.get('counters', {})
    ac = archiver.get('counters', {})
    add(f'SD ARCHIVES: {integer(archives.get("files"))} files | {number(archives.get("bytes"))/1024**2:.2f} MiB DB+WAL/SHM | {integer(archives.get("markers"))} replay markers (not verified drained counts)')
    if archives.get('error'):add('Archive scan: ' + str(archives['error']))
    if archives.get('truncated'):add('Archive scan capped at 4096 files; totals are partial.')
    if 'free' in archives:add(f'Archive filesystem free: {archives["free"]/1024**3:.2f} GiB')
    def worker_state(status):
        freshness=age(status.get('status_generated_utc'),now)
        if service!='active' or freshness is None or freshness>60:return 'STALE / UNAVAILABLE'
        return 'running' if status.get('running') else 'not running'
    add(f'Archiver: {worker_state(archiver)} | status age {age_text(archiver.get("status_generated_utc"), now)} | copied {integer(ac.get("archived_message_count"))} | live removed {integer(ac.get("source_deleted_count"))} | errors {integer(ac.get("failure_count"))}')
    add(f'Recovery: {worker_state(recovery)} | status age {age_text(recovery.get("status_generated_utc"), now)} | reason {rc.get("last_pause_reason") or "none / unavailable"} | drained archives {integer(rc.get("archive_drained_count"))} | failures {integer(rc.get("failure_count"))}')
    if rc.get('last_error'):add('Recovery error: ' + str(rc['last_error']))
    if details:
        if replay:
            ru = replay.get('uploader', {})
            add(f'RECOVERY LAST uploader snapshot only ({replay.get("started_utc", "unknown")}; age {age_text(replay.get("status_generated_utc"), now)}): accepted {integer(ru.get("accepted_count"))}, duplicate {integer(ru.get("already_processed_count"))}, missing {integer(ru.get("missing_ack_count"))}, last HTTP {ru.get("last_http_status") or "none"}')
            for stream, item in replay.get('stream_backend_ack', {}).items():
                add(f'  Recovery {stream}: ACKed {integer(item.get("acked_count"))}; last {item.get("last_backend_result")}; error {item.get("last_error") or "none"}')
        else:add('Recovery uploader: no snapshot yet; may be gated or no request made. Not proof of failure.')
    if logs and details:
        add('RECENT BACKEND LOGS (filtered):')
        for line in logs:add(line)
    add()
    add('Totals: uploader-instance counters. Accepted/min: ACK time, retained live rows, excludes duplicates/recovery; refresh every DB interval.')
    add("MB = 1,000,000 logical JSON bytes, not wire/compressed bytes. Records mean each stream's record objects. HTTP 2xx alone is not a message ACK.")
    add('Zero/no retained ACK is not proof of rejection. Disabled/idle/unmapped streams may have no emissions. Backend DB/CPU: not exposed.')
    add('Read-only. Ctrl+C closes ONLY this view; it does not change gateway service state.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/ornate-ems-gateway/central_sync.json'))
    parser.add_argument('--backend', type=Path, default=Path('/opt/ornate-ems-gateway/backend'))
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--db-interval', type=float, default=10.0)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--no-logs', action='store_true')
    parser.add_argument('--details', action='store_true', help='also show cumulative counters, S2 states, replay snapshots and logs')
    args = parser.parse_args()
    if args.interval < 1 or args.db_interval < 5:
        parser.error('Use --interval >= 1 and --db-interval >= 5')
    rate = Rates()
    paths = None
    config = {}
    dbrows, db_error, archives = {}, 'not sampled yet', {}
    logs = []
    minute, minute_error = None, 'not sampled yet'
    requests, request_error = None, 'not sampled yet'
    last_details = float('-inf')
    service = 'unknown'
    cached_at = time.time()
    try:
        while True:
            now = time.time()
            fresh, error = load_json(args.config)
            if not error:
                config = fresh
                paths = defaults(config, args.backend)
            if paths is None:
                print('Cannot read configuration: ' + str(error), file=sys.stderr)
                return 1
            live, status_error = load_json(paths['live'])
            recovery, _ = load_json(paths['recovery'])
            replay, _ = load_json(paths['replay_upload'])
            archiver, _ = load_json(paths['archiver'])
            rates = rate.update(live)
            if time.monotonic() - last_details >= args.db_interval:
                if args.details:dbrows, db_error = s2_retained(paths['outbox'])
                window_end=time.time()
                minute, minute_error = ack_window(paths['outbox'],now=window_end)
                requests, request_error = request_window(paths['outbox'],now=window_end)
                archives = archive_summary(paths['archives'])
                service = run_command(['systemctl', 'is-active', SERVICE])
                if not args.no_logs:
                    logs = recent_backend_logs(logs)
                cached_at = time.time()
                last_details = time.monotonic()
            width = shutil.get_terminal_size((150, 60)).columns
            screen = draw(config, live, recovery, replay, archiver, rates, rate.note,
                          dbrows, db_error, archives, cached_at, service, logs, width, now, minute, minute_error, args.details,
                          requests, request_error)
            if error:screen += '\nConfiguration read warning: ' + clean(error)
            if status_error:screen += '\nLive status read warning: ' + clean(status_error)
            if not args.once and sys.stdout.isatty():sys.stdout.write('\033[2J\033[H')
            print(screen, flush=True)
            if args.once:return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nView closed. Central Sync was not stopped.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
