#!/usr/bin/env python3
"""Isolated local Central Sync producer harness and read-only terminal dashboard."""
import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time

STREAMS = {'s1': 'fast_bess_telemetry', 's2': 'general_asset_telemetry',
           's3': 'gateway_health', 's4': 'alarms_events',
           's8': 'configuration_audit', 's10': 'battery_charge_discharge_control'}


def build_config(backend, source, state):
    sys.path.insert(0, str(backend))
    from app.central_sync.config import CentralSyncConfig
    raw = json.loads(source.read_text())
    config = CentralSyncConfig.model_validate(raw)
    config.enabled = False  # Production/global flag is never armed by this tool.
    config.local_gateway_api.base_url = 'http://127.0.0.1:8000'
    config.command_downlink.enabled = False
    for field in ('overflow_archive', 'overflow_recovery', 'backlog_replay'):
        getattr(config, field).enabled = False
    config.outbox.path = str(state / 'outbox.db')
    config.outbox.required_mount_path = None
    config.outbox.fail_if_mount_missing = False
    config.outbox.max_db_size_mb = 256
    config.outbox.min_free_space_mb = 512
    fields = {'s1': 'fast_bess', 's2': 'general_assets', 's3': 'gateway_health',
              's4': 'alarms_events', 's8': 'configuration_audit', 's10': 'charge_discharge'}
    for key, field in fields.items():
        producer = getattr(config, field)
        producer.status_file = str(state / (key + '.json'))
        if hasattr(producer, 'state_file'):
            producer.state_file = str(state / (key + '-cursor.json'))
    config.alarms_events.startup_emit_existing = False
    config.configuration_audit.gateway_config_path = '/etc/ornate-ems-gateway/config.json'
    config.configuration_audit.central_sync_config_path = str(source)
    catalog = Path(config.general_assets.catalog_dir)
    config.general_assets.catalog_dir = str(catalog if catalog.is_absolute() else backend / catalog)
    manifest = config.general_assets.s2_policy_manifest_path
    if not manifest or not config.general_assets.s2_policy_expected_sha256:
        raise RuntimeError('Frozen S2 manifest and approved SHA256 must be configured')
    manifest = Path(manifest)
    config.general_assets.s2_policy_manifest_path = str(manifest if manifest.is_absolute() else backend / manifest)
    # Credentials are supplied by the existing systemd EnvironmentFile, never copied.
    if not os.environ.get('CENTRAL_SYNC_LOCAL_API_PASSWORD'):
        password = os.environ.get('ORNATE_EMS_INTERNAL_PASSWORD') or os.environ.get('KINETICS_INTERNAL_PASSWORD')
        if password:
            os.environ['CENTRAL_SYNC_LOCAL_API_PASSWORD'] = password
    if not config.local_gateway_api.resolved_password() and not config.local_gateway_api.resolved_token():
        raise RuntimeError('Local API credentials missing from the service environment')
    return config


def make_producers(config, outbox, local_api=None):
    from app.central_sync.fast_bess_producer import FastBESSProducer
    from app.central_sync.general_asset_producer import GeneralAssetProducer
    from app.central_sync.gateway_health_producer import GatewayHealthProducer
    from app.central_sync.alarm_event_producer import AlarmEventProducer
    from app.central_sync.configuration_audit_producer import ConfigurationAuditProducer
    from app.central_sync.charge_discharge_producer import ChargeDischargeProducer
    # Deliberately no uploader, backend HTTP client, command executor or replay service.
    return {
        's1': FastBESSProducer(config=config, outbox=outbox, local_api=local_api),
        's2': GeneralAssetProducer(config=config, outbox=outbox, local_api=local_api),
        's3': GatewayHealthProducer(config=config, outbox=outbox, local_api=local_api),
        's4': AlarmEventProducer(config=config, outbox=outbox, local_api=local_api),
        's8': ConfigurationAuditProducer(config=config, outbox=outbox),
        's10': ChargeDischargeProducer(config=config, outbox=outbox, local_api=local_api),
    }


async def generate(args):
    config = build_config(args.backend.resolve(), args.config.resolve(), args.state.resolve())
    args.state.mkdir(parents=True, exist_ok=True)
    from app.central_sync.outbox import OutboxStore
    from app.central_sync.status import safe_write_json
    # Refuse duplicate manual/service generators against the same test database.
    import fcntl
    lock = open(args.state / 'generator.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('The monitor generator is already running')
    outbox = OutboxStore(config.outbox.path, max_db_size_mb=256, min_free_space_mb=512)
    producers = {}
    tasks = []
    started = time.time()
    try:
        producers = make_producers(config, outbox)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        for producer in producers.values():
            if producer.producer_config.enabled:
                tasks.append(asyncio.create_task(producer.run_forever()))
        while not stop.is_set():
            safe_write_json(str(args.state / 'status.json'), {
                'running': True, 'started_epoch': started, 'updated_epoch': time.time(),
                'mode': 'LOCAL GENERATION ONLY', 'uploads': False, 'commands': False,
                'outbox': config.outbox.path, 'limit_mb': 256,
                'producers': {key: producer.status() for key, producer in producers.items()},
            })
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
    finally:
        for producer in producers.values():
            await producer.stop()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=20)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for producer in producers.values():
            close = getattr(producer, 'close', None)
            if close:
                await close()
        safe_write_json(str(args.state / 'status.json'), {
            'running': False, 'started_epoch': started, 'updated_epoch': time.time(),
            'uploads': False, 'commands': False,
            'producers': {key: producer.status() for key, producer in producers.items()},
        })
        outbox.close()
        lock.close()


def read_snapshot(state):
    status = json.loads((state / 'status.json').read_text())
    db = state / 'outbox.db'
    with contextlib.closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.execute('PRAGMA query_only=ON')
        rows = conn.execute('SELECT stream, substream, COUNT(*), SUM(payload_bytes) '
                            'FROM outbox_messages GROUP BY stream, substream').fetchall()
    size = sum(p.stat().st_size for p in [db, Path(str(db) + '-wal'), Path(str(db) + '-shm')] if p.exists())
    return status, rows, size


def watch(args):
    previous = {}
    prior_start = None
    prior_time = time.monotonic()
    while True:
        try:
            status, rows, size = read_snapshot(args.state)
            now = time.monotonic()
            dt = max(now - prior_time, 0.001)
            same_run = status.get('started_epoch') == prior_start
            lines = ['KALPA CENTRAL SYNC — LOCAL GENERATION MONITOR',
                     'Uploads: OFF | D1 commands: OFF | Separate test outbox | Ctrl+C exits this view',
                     'Generator: ' + ('RUNNING' if status.get('running') and time.time() - status['updated_epoch'] < 15 else 'STOPPED / STATUS STALE'),
                     f'Test DB files: {size / 1024**2:.1f} MiB | nominal cap: 256 MiB | no automatic deletion',
                     '', f'{"Stream":<8} {"Session records":>16} {"Records/s":>12} {"Session messages":>18} {"Errors":>10}']
            current = {}
            for key in STREAMS:
                p = status.get('producers', {}).get(key, {})
                records = p.get('record_count', 0)
                current[key] = records
                rate = f'{max(0, records - previous.get(key, records)) / dt:.2f}' if same_run else 'warming up'
                lines.append(f'{key.upper():<8} {records:>16} {rate:>12} {p.get("message_count",0):>18} {p.get("failure_count",0):>10}')
                if p.get('last_error'):
                    lines.append('  Error: ' + str(p['last_error']).replace('\n', ' ')[:240])
            lines += ['', 'S5/S6/S7 are application-dependent; S9 needs command results. They are not generated here.',
                      'S4/S8 can remain at zero until a new event/configuration change occurs.',
                      '', f'{"Outbox stream / substream":<63} {"Messages":>10} {"Payload MiB":>14}']
            for stream, substream, messages, payload in rows:
                lines.append(f'{stream + " / " + substream:<63} {messages:>10} {(payload or 0)/1024**2:>14.2f}')
            if size > 240 * 1024**2:
                lines.append('CAPACITY NEAR LIMIT: stop the generator after your observation. Data is retained, not uploaded.')
            print(('\033[2J\033[H' if sys.stdout.isatty() else '') + '\n'.join(lines), flush=True)
            previous, prior_start, prior_time = current, status.get('started_epoch'), now
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f'Waiting for generator status/outbox: {error}', flush=True)
        if args.once:
            return
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--once', action='store_true', help='print one dashboard snapshot')
    parser.add_argument('--backend', type=Path, default=Path('/opt/ornate-ems-gateway/backend'))
    parser.add_argument('--config', type=Path, default=Path('/etc/ornate-ems-gateway/central_sync.json'))
    parser.add_argument('--state', type=Path, default=Path('/var/lib/kalpa-central-monitor'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        if args.generate:
            asyncio.run(generate(args))
        else:
            watch(args)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
