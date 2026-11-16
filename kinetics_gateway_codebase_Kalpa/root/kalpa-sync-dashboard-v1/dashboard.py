#!/usr/bin/env python3
"""Read-only terminal dashboard for the existing Ornate Central Sync outbox."""
import argparse
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SERVICE = 'ornate-central-sync.service'
SLOTS = [
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
SUBS = ['ems_system', 'pcs_extended', 'bms_extended', 'io_module',
        'liquid_cooling', 'fire_protection', 'dehumidifier',
        'remote_control', 'utility_meter']


def clean(text):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(text))
    text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', text)
    text = re.sub(r'''(?ix)(["']?(?:[\w-]*(?:password|secret|token|authorization)[\w-]*)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',
                  r'\1[REDACTED]', text)
    return ''.join(c for c in text if c.isprintable())


def command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or r.stderr.strip() or 'unavailable'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'


def aggregate(rows, window):
    groups = defaultdict(list)
    for stream, sub, created, size, records in rows:
        # Aggregate and breakdown rows are intentionally separate views.
        for key in [('TOTAL', '*'), (stream, '*'), (stream, sub or '')]:
            groups[key].append((int(size), int(records or 0)))
    result = {}
    for key, values in groups.items():
        sizes = sorted(x[0] for x in values)
        records = sum(x[1] for x in values)
        total_bytes = sum(sizes)
        result[key] = dict(messages=len(values), records=records,
            bytes=total_bytes, msg_sec=len(values)/window, rec_sec=records/window,
            kib_sec=total_bytes/window/1024, mean_bytes=total_bytes/len(values),
            p95=sizes[math.ceil(len(sizes)*.95)-1], maximum=sizes[-1])
    return result


EMPTY = dict(messages=0, records=0, bytes=0, msg_sec=0, rec_sec=0,
             kib_sec=0, mean_bytes=0, p95=0, maximum=0)


def connect(db):
    # mode=ro prevents accidental creation or writes to the production database.
    con = sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True,
                          timeout=2, isolation_level=None)
    con.execute('PRAGMA query_only=ON')
    return con


def snapshot(con, now, window, limit=10000):
    rows = []
    con.execute('BEGIN')
    try:
        keys = con.execute('SELECT stream,substream FROM sequences').fetchall()
        # Exact stream/substream predicates use the existing idx_outbox_stream.
        # Fetch metadata + record counts only; never export signal values.
        for stream, sub in keys:
            remaining = limit + 1 - len(rows)
            if remaining <= 0:
                break
            rows.extend(con.execute('''SELECT stream,substream,created_epoch,
                payload_bytes,json_extract(payload_json,'$.record_count')
                FROM outbox_messages
                WHERE stream=? AND substream=? AND created_epoch>=? AND created_epoch<?
                ORDER BY created_epoch DESC LIMIT ?''',
                (stream, sub, now-window, now, remaining)).fetchall())
    finally:
        con.rollback()
    return rows[:limit], len(rows) > limit


def queue_stats(con):
    return con.execute('''SELECT state,COUNT(*),COALESCE(SUM(payload_bytes),0)
        FROM outbox_messages GROUP BY state''').fetchall()


def display(cfg, metrics, queue, db, state, logs, warning, window, started, queue_age):
    width = max(80, min(150, shutil.get_terminal_size((125, 50)).columns))
    lines = [
        'ORNATE / KALPA — CENTRAL SYNC LIVE',
        datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')+
        ' | Service: '+clean(state),
        f'Generation: previous {window}s of RETAINED messages | View uptime: {time.monotonic()-started:.0f}s',
        'Read-only view. Poll/display interval does not change producer cadence.',
    ]
    total = metrics.get(('TOTAL', '*'), EMPTY)
    lines += [f'TOTAL {total["msg_sec"]:.3f} msg/s | {total["rec_sec"]:.2f} records/s | '
              f'{total["kib_sec"]:.2f} KiB/s | {total["kib_sec"]*3600/1024:.2f} MiB/hour',
              f'Message bytes: mean {total["mean_bytes"]:.0f} | p95 {total["p95"]} | max {total["maximum"]}', '']
    header = f'{"Stream / S2 substream":43} {"Msg/60s":>8} {"Rec/60s":>9} {"Rec/s":>8} {"KiB/s":>9} {"p95 B":>8}'
    if window != 60:
        header = header.replace('Msg/60s', 'Msg/win').replace('Rec/60s', 'Rec/win')
    lines += [header, '-'*min(width, 100)]
    known = set()
    for slot, section, default in SLOTS:
        sc = cfg.get(section, {})
        stream = sc.get('result_stream' if slot == 'S9' else 'stream', default)
        known.add(stream)
        enabled = sc.get('enabled', False)
        m = metrics.get((stream, '*'), EMPTY)
        label = slot+' '+stream+(' [disabled]' if not enabled else '')
        lines.append(f'{label:43} {m["messages"]:8d} {m["records"]:9d} {m["rec_sec"]:8.2f} {m["kib_sec"]:9.2f} {m["p95"]:8d}')
        if slot == 'S2':
            subs = list(SUBS) + sorted({sub for st, sub in metrics if st == stream and sub not in SUBS and sub != '*'})
            for sub in subs:
                sm = metrics.get((stream, sub), EMPTY)
                lines.append(f'{"  "+sub:43} {sm["messages"]:8d} {sm["records"]:9d} {sm["rec_sec"]:8.2f} {sm["kib_sec"]:9.2f} {sm["p95"]:8d}')
    if any(st not in known and st != 'TOTAL' for st, sub in metrics):
        lines.append('WARNING: Additional unconfigured streams are included in TOTAL.')
    counts = {state: count for state, count, size in queue}
    backlog = sum(counts.get(s, 0) for s in ['pending','retry','inflight'])
    payload = sum(size for state, count, size in queue if state in ['pending','retry','inflight'])
    physical = sum(p.stat().st_size for p in [db, Path(str(db)+'-wal'), Path(str(db)+'-shm')] if p.exists())
    lines += ['', f'LIVE OUTBOX (sample age {queue_age:.0f}s): backlog {backlog} messages / {payload/2**20:.2f} MiB JSON',
        f'pending {counts.get("pending",0)} | retry {counts.get("retry",0)} | inflight {counts.get("inflight",0)} | '
        f'retained ACKed {counts.get("acked",0)} | dead {counts.get("dead",0)}',
        f'Database + WAL/SHM: {physical/2**20:.2f} MiB; external overflow archives are not counted.',
        '', 'RECENT CENTRAL SYNC LOGS (journal; sensitive key/value fields filtered):']
    lines.extend(clean(line) for line in logs.splitlines()[-6:])
    if warning:
        lines += ['', 'WARNING: '+warning]
    lines += ['', 'Rates are logical JSON generation, not HTTP throughput or Excel cadence certification.',
        'Window counts can undercount if messages leave the live DB; this view is not an audited benchmark.',
        'Zeros: idle/disabled/unmapped or no retained emission; inspect logs. S2 records = signal samples.',
        'Ctrl+C closes ONLY this view. Central Sync and uploads keep running.']
    return '\n'.join(clean(line)[:width] for line in lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='/etc/ornate-ems-gateway/central_sync.json')
    p.add_argument('--refresh', type=float, default=10)
    p.add_argument('--window', type=int, default=60)
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    if args.refresh < 5 or not 10 <= args.window <= 300:
        p.error('refresh must be >=5 seconds; window must be 10..300 seconds')
    started = time.monotonic()
    queue, last_queue = [], -1000
    try:
        while True:
            con = None
            try:
                cfg = json.loads(Path(args.config).read_text())
                db = Path(cfg['outbox']['path'])
                con = connect(db)
                rows, capped = snapshot(con, time.time(), args.window)
                if time.monotonic()-last_queue >= 30:
                    queue = queue_stats(con)
                    last_queue = time.monotonic()
                metrics = aggregate(rows, args.window)
                warning = 'Window exceeds 10000 messages: displayed rates are incomplete.' if capped else ''
                if not cfg.get('enabled'):
                    warning += ' Central Sync configuration enabled=false.'
                state = command(['systemctl','is-active',SERVICE])
                logs = command(['journalctl','-u',SERVICE,'-n','6','--no-pager','-o','cat'])
                text = display(cfg, metrics, queue, db, state, logs, warning,
                               args.window, started, time.monotonic()-last_queue)
            except (OSError, ValueError, KeyError, sqlite3.Error) as e:
                text = 'Dashboard waiting / read failed: '+clean(e)+'\nRetrying; Central Sync is unchanged. Ctrl+C exits.'
            finally:
                if con is not None:
                    con.close()
            if sys.stdout.isatty() and not args.once:
                print('\033[2J\033[H', end='')
            print(text, flush=True)
            if args.once:
                return
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print('\nView closed. Central Sync is unchanged.')


if __name__ == '__main__':
    main()
