#!/usr/bin/env python3
"""Measure actual Central Sync outbox inserts; standard library only, no credentials exported.

Restarts ONLY ornate-central-sync, observes new durable messages, stops it at the end.
Does not change gateway configuration, delete queued data, or trigger control commands.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import signal
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
SUBSTREAMS = ['ems_system', 'pcs_extended', 'bms_extended', 'io_module',
              'liquid_cooling', 'fire_protection', 'dehumidifier',
              'remote_control', 'utility_meter']


def utc():
    return datetime.now(timezone.utc).isoformat()


def percentile(hist, fraction):
    target = math.ceil(sum(hist.values()) * fraction)
    count = 0
    for size, frequency in sorted(hist.items()):
        count += frequency
        if count >= target:
            return size
    return 0


class Meter:
    def __init__(self):
        self.count = self.records = self.bytes = self.gzip_bytes = 0
        self.sizes = Counter()
        self.bins = defaultdict(lambda: [0, 0, 0])

    def add(self, second, records, size, compressed):
        self.count += 1
        self.records += records
        self.bytes += size
        self.gzip_bytes += compressed
        self.sizes[size] += 1
        b = self.bins[second]
        b[0] += 1
        b[1] += records
        b[2] += size

    def result(self, duration):
        d = max(duration, 0.000001)
        return dict(messages=self.count, records=self.records,
                    logical_json_bytes=self.bytes,
                    average_messages_per_sec=self.count/d,
                    average_records_per_sec=self.records/d,
                    average_kib_per_sec=self.bytes/d/1024,
                    projected_mib_per_hour=self.bytes/d*3600/2**20,
                    projected_gib_per_day=self.bytes/d*86400/2**30,
                    mean_message_bytes=self.bytes/self.count if self.count else 0,
                    p95_message_bytes=percentile(self.sizes, .95),
                    max_message_bytes=max(self.sizes, default=0),
                    peak_fixed_1s_messages=max((b[0] for b in self.bins.values()), default=0),
                    peak_fixed_1s_records=max((b[1] for b in self.bins.values()), default=0),
                    peak_fixed_1s_bytes=max((b[2] for b in self.bins.values()), default=0),
                    gzip_level1_per_message_bytes=self.gzip_bytes)


def observe(conn, after, through):
    # Bounded fetch: release the SQLite read snapshot before parsing/compressing.
    return conn.execute('''SELECT rowid,stream,substream,created_epoch,
        payload_json,payload_bytes FROM outbox_messages
        WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 128''',
                        (after, through)).fetchall()


def sequences(conn):
    return {(s, sub): n for s, sub, n in conn.execute(
        "SELECT stream,substream,last_sequence FROM sequences")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=float, default=30)
    parser.add_argument('--startup-seconds', type=float, default=60)
    parser.add_argument('--config', default='/etc/ornate-ems-gateway/central_sync.json')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    duration = args.minutes * 60
    if duration <= args.startup_seconds or args.startup_seconds < 0:
        parser.error('Run duration must exceed startup-seconds, which must be >= 0')
    config_path = Path(args.config)
    raw = config_path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        raise SystemExit('Central Sync config enabled=false. Enable it before this benchmark.')
    db = Path(cfg['outbox']['path']).resolve()
    if not db.is_file():
        raise SystemExit('Outbox does not exist. Start Central Sync once before benchmarking.')
    out = Path(args.output or '/root/kalpa-workload-' +
               datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    subprocess.run(['systemctl', 'stop', SERVICE], check=True)
    conn = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=5,
                           isolation_level=None)
    conn.execute('PRAGMA query_only=ON')
    last = conn.execute('SELECT COALESCE(MAX(rowid),0) FROM outbox_messages').fetchone()[0]
    baseline = last
    baseline_sequences = sequences(conn)
    captured_sequences = Counter()
    final_sequences = baseline_sequences
    capture_complete = False
    stats = defaultdict(Meter)
    stream_slots = {}
    for slot, section, default in SLOTS:
        sc = cfg.get(section, {})
        stream = sc.get('result_stream' if slot == 'S9' else 'stream', default)
        stream_slots[stream] = (slot, bool(sc.get('enabled', False)))
        for phase in ('all', 'startup', 'steady'):
            stats[(phase, stream, '*')]
    s2 = cfg.get('general_assets', {}).get('stream', 'general_asset_telemetry')
    for sub in SUBSTREAMS:
        for phase in ('all', 'startup', 'steady'):
            stats[(phase, s2, sub)]
    issues = []
    stop = False
    def interrupt(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    started_epoch, started_mono = time.time(), time.monotonic()
    processed = 0
    reason = 'duration_complete'
    last_refresh = -100
    started_at = utc()
    print('Measuring existing producers. Central Sync will STOP when finished.', flush=True)
    print('Results:', out, flush=True)
    start_ok = False
    fatal = None
    generation_elapsed = None
    measurement_stopped = False
    try:
        subprocess.run(['systemctl', 'start', SERVICE], check=True)
        start_ok = True
        while True:
            elapsed = time.monotonic()-started_mono
            if (elapsed >= duration or stop) and not measurement_stopped:
                generation_elapsed = min(elapsed, duration)
                subprocess.run(['systemctl', 'stop', SERVICE], check=True)
                measurement_stopped = True
                if stop:
                    reason = 'interrupted_partial'
            high = conn.execute('SELECT COALESCE(MAX(rowid),0) FROM outbox_messages').fetchone()[0]
            if high < last:
                raise RuntimeError('Outbox rowid moved backwards; database reset/cleanup detected')
            while last < high:
                rows = observe(conn, last, high)
                if not rows:
                    raise RuntimeError('Rows disappeared before observation; benchmark incomplete')
                for rowid, stream, sub, created, payload, size in rows:
                    last = rowid
                    captured_sequences[(stream, sub or '')] += 1
                    relative = created-started_epoch
                    if relative < 0:
                        issues.append('New row had creation time before capture start')
                        continue
                    if relative >= duration:
                        continue
                    obj = json.loads(payload)
                    records = int(obj['record_count'])
                    encoded = payload.encode('utf-8')
                    if len(encoded) != size:
                        issues.append('Stored payload byte count mismatch')
                    compressed = len(gzip.compress(encoded, compresslevel=1, mtime=0))
                    second = int(relative)
                    phase = 'startup' if relative < args.startup_seconds else 'steady'
                    for ph in ('all', phase):
                        for st, ss in ((stream, sub or ''), (stream, '*'), ('TOTAL', '*')):
                            stats[(ph, st, ss)].add(second, records, size, compressed)
                    processed += 1
            elapsed = time.monotonic()-started_mono
            if abs((time.time()-started_epoch)-elapsed) > 2:
                issues.append('Wall clock moved relative to monotonic clock; timestamp bins need review')
            if elapsed-last_refresh >= 10:
                last_refresh = elapsed
                if not measurement_stopped and subprocess.run(
                        ['systemctl', 'is-active', '--quiet', SERVICE], check=False).returncode:
                    raise RuntimeError('Central Sync is not active; measurement is partial')
                print('\033[2J\033[H', end='')
                print(f'KALPA ALL-STREAM BENCHMARK | {elapsed:.0f}/{duration:.0f}s | new messages {processed}')
                print(f'{"Slot / stream":49} {"Messages":>9} {"Records":>10} {"KiB/s":>9} {"p95 B":>9}')
                for stream, (slot, enabled) in stream_slots.items():
                    r = stats[('all', stream, '*')].result(min(elapsed, duration))
                    label = f'{slot} {stream}' + (' [disabled]' if not enabled else '')
                    print(f'{label:49} {r["messages"]:9d} {r["records"]:10d} {r["average_kib_per_sec"]:9.2f} {r["p95_message_bytes"]:9d}')
                print('First 60s = restart observation; steady results exclude that period.')
                print('No network needed. Ctrl+C saves a partial report and stops Central Sync.', flush=True)
            if measurement_stopped:
                break
            time.sleep(max(0, min(1, duration-elapsed)))
    except Exception as exc:
        fatal = str(exc)
        issues.append(fatal)
        reason = 'error_partial'
    finally:
        # Preserve queued data. No other application/network service is stopped.
        stopped = subprocess.run(['systemctl', 'stop', SERVICE], check=False).returncode == 0
        final_sequences = sequences(conn)
        conn.close()
    sequence_checks = []
    for key in sorted(set(baseline_sequences) | set(final_sequences) | set(captured_sequences)):
        allocated = final_sequences.get(key, 0) - baseline_sequences.get(key, 0)
        captured = captured_sequences[key]
        sequence_checks.append(dict(stream=key[0], substream=key[1],
                                    sequence_delta=allocated, captured_messages=captured,
                                    matched=(allocated == captured)))
    capture_complete = bool(captured_sequences) and all(x['matched'] for x in sequence_checks)
    if not captured_sequences:
        issues.append('No messages captured: this run does not establish a generation rate')
    if not capture_complete:
        issues.append('Sequence allocation and captured counts differ: generation failed or rows were missed; do not use as complete capacity evidence')
    elapsed = generation_elapsed if generation_elapsed is not None else min(time.monotonic()-started_mono, duration)
    durations = {'all': elapsed, 'startup': min(elapsed, args.startup_seconds),
                 'steady': max(0, elapsed-args.startup_seconds)}
    results = []
    for (phase, stream, sub), meter in sorted(stats.items()):
        slot, enabled = stream_slots.get(stream, ('TOTAL' if stream == 'TOTAL' else 'OTHER', True))
        status = 'disabled' if not enabled else ('observed' if meter.count else 'enabled_no_emission_observed')
        results.append(dict(phase=phase, slot=slot, stream=stream, substream=sub,
                            configured_enabled=enabled, observation=status,
                            duration_sec=durations[phase], **meter.result(durations[phase])))
    if config_path.read_bytes() != raw:
        issues.append('Central Sync configuration changed during measurement')
    gateway_cfg = Path('/etc/ornate-ems-gateway/config.json')
    mode = json.loads(gateway_cfg.read_text()).get('mode') if gateway_cfg.exists() else 'unknown'
    report = dict(started_utc=started_at, finished_utc=utc(), mode=mode,
                  reason=reason, requested_duration_sec=duration, elapsed_sec=elapsed,
                  configuration_sha256=hashlib.sha256(raw).hexdigest(),
                  baseline_rowid=baseline, final_observed_rowid=last,
                  new_messages_observed=processed, central_sync_stopped=stopped,
                  capture_counts_reconciled=capture_complete, sequence_checks=sequence_checks,
                  issues=sorted(set(issues)), results=results,
                  method=[
                      'Only durable outbox inserts after a controlled producer restart are counted.',
                      'First minute is restart behaviour, NOT a clean first-install baseline: checkpoints/cursors are preserved.',
                      'All ten configured slots are listed. S9 is command results; no commands are injected.',
                      'S2 substream rows are breakdowns, not additional traffic. TOTAL uses each message once.',
                      'Peaks are fixed one-second bins based on producer created_epoch, NOT sliding windows or receipt times.',
                      'Rates and daily projections describe this run, not guaranteed production maxima.',
                      'Gzip values are independent level-1 compression per logical message, NOT measured transport batches.',
                      'No HTTP throughput/ACK measurement; no hardware/control/configuration events are injected.',
                      'Disabled/idle stream zeros cannot size rare-event storm capacity.',
                      'Uploader retries remain part of the running service. Observer JSON/gzip work adds some overhead.',
                  ])
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    with (out/'streams.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    with (out/'seconds.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['second','phase','messages','records','logical_json_bytes'])
        for second in range(math.ceil(elapsed)):
            b = stats[('all', 'TOTAL', '*')].bins.get(second, [0, 0, 0])
            writer.writerow([second, 'startup' if second < args.startup_seconds else 'steady', *b])
    steady = stats[('steady', 'TOTAL', '*')].result(durations['steady'])
    summary = (f'KALPA generated workload benchmark\nMode: {mode}\nResult: {reason}\n'
               f'Duration: {elapsed:.1f}s; steady window: {durations["steady"]:.1f}s\n'
               f'Steady messages/s: {steady["average_messages_per_sec"]:.3f}\n'
               f'Steady records/s: {steady["average_records_per_sec"]:.3f}\n'
               f'Steady logical JSON KiB/s: {steady["average_kib_per_sec"]:.3f}\n'
               f'Projected logical JSON GiB/day: {steady["projected_gib_per_day"]:.3f}\n'
               f'Message mean/p95/max bytes: {steady["mean_message_bytes"]:.1f}/'
               f'{steady["p95_message_bytes"]}/{steady["max_message_bytes"]}\n'
               f'Peak fixed-1s messages/records/bytes: {steady["peak_fixed_1s_messages"]}/'
               f'{steady["peak_fixed_1s_records"]}/{steady["peak_fixed_1s_bytes"]}\n'
               f'Capture counts reconciled: {capture_complete}\n'
               f'Central Sync stopped: {stopped}\nIssues: {sorted(set(issues))}\n'
               'Read report.json method notes before using these figures for capacity planning.\n')
    (out/'summary.txt').write_text(summary)
    print('\n'+summary+'\nResults: '+str(out), flush=True)
    if fatal or not start_ok or not stopped or not capture_complete:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
