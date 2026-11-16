#!/usr/bin/env python3
"""Kalpa DEBIX validation: isolated tests + read-only live API + simulated upload.

Never starts production Central Sync, changes production configuration, or sends
control commands. All Central Sync state and output live under --output.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app-root', default='/opt/ornate-ems-gateway/backend')
    p.add_argument('--config-dir', default='/etc/ornate-ems-gateway')
    p.add_argument('--api-url', default='http://127.0.0.1:8000')
    p.add_argument('--output', default='/root/kalpa-validation-' + time.strftime('%Y%m%d-%H%M%S'))
    args = p.parse_args()
    # Validation uses loopback API calls and in-memory HTTP only. Ignore shell
    # proxies so these cannot redirect or interfere with the local test lane.
    for key in list(os.environ):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy'):
            os.environ.pop(key, None)
    app, conf, out = Path(args.app_root).resolve(), Path(args.config_dir).resolve(), Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'checks': [], 'limitations': [
        'Mock/SIL validation, not physical BMS/PCS commissioning.',
        'Simulated backend ACKs, not production ingestion or backend authentication.',
        'Short functional check, not a long-duration performance/retention soak.',
        'SOC/Solis/Edge AI streams are configuration-dependent; disabled streams are not treated as failures.'
    ]}

    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'pass': bool(condition), 'detail': detail})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name + ': ' + str(detail))

    try:
        gateway = json.loads((conf / 'config.json').read_text())
        central = json.loads((conf / 'central_sync.json').read_text())
        originals = {str(x): hashlib.sha256(x.read_bytes()).hexdigest()
                     for x in (conf / 'config.json', conf / 'central_sync.json')}
        check('Gateway is in mock mode', gateway.get('mode') == 'mock')
        check('Hardware write gates disabled', not gateway['bms']['write_enabled'] and not gateway['pcs']['write_enabled'])
        check('Production uploads disabled', central.get('enabled') is False)
        import pytest  # noqa: F401

        # A separate source tree prevents test imports/database writes affecting
        # the running gateway. Tests deliberately use their mock configuration.
        suite = out / 'isolated-source'
        shutil.copytree(app, suite, ignore=shutil.ignore_patterns(
            'data', '__pycache__', '.git', '*.log', '*.db', '*.db-wal', '*.db-shm',
            'protocol_sources', 'docs', 'venv', '.venv'))
        isolated_mock = suite / 'configs/kinetics_mock.json'
        test_config = json.loads(isolated_mock.read_text())
        test_config['storage']['preferred_mount_point'] = str(suite / 'not-a-mount')
        test_config['storage']['preferred_root'] = str(suite / 'data')
        test_config['storage']['fallback_root'] = str(suite / 'data')
        isolated_mock.write_text(json.dumps(test_config))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('ORNATE_', 'KINETICS_', 'CENTRAL_SYNC_'))}
        env['KINETICS_CONFIG'] = str(suite / 'configs/kinetics_mock.json')
        env['PYTHONPATH'] = str(suite)
        completed = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=suite,
                                   env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / 'pytest.log').write_text(completed.stdout)
        print(completed.stdout, flush=True)
        check('Packaged regression suite', completed.returncode == 0, completed.stdout[-1800:])

        # Parse only the local API password; do not source shell code or copy
        # production backend credentials into the validation client.
        values = {}
        for line in (conf / 'ornate-ems-gateway.env').read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        password = values.get('ORNATE_EMS_INTERNAL_PASSWORD')
        check('Local API password available', bool(password))
        sys.path.insert(0, str(app))
        asyncio.run(live_checks(args, app, conf, out, password, gateway, central, report, check))
        check('Production configurations unchanged', all(
            hashlib.sha256(Path(k).read_bytes()).hexdigest() == v for k, v in originals.items()))
        report['result'] = 'PASS'
    except Exception as exc:
        report['result'] = 'FAIL'
        report['error'] = str(exc)
        (out / 'error.log').write_text(traceback.format_exc())
        print('FAILED: ' + str(exc), flush=True)
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2, default=str))
        print('\nResult: ' + report['result'] + '\nResults directory: ' + str(out), flush=True)
    return 0 if report['result'] == 'PASS' else 1


async def live_checks(args, app, conf, out, password, gateway, central, report, check):
    import httpx
    from app.central_sync.config import CentralSyncConfig
    from app.central_sync.local_gateway_api import LocalGatewayApiClient
    from app.central_sync.outbox import OutboxStore
    from app.central_sync.general_asset_producer import GeneralAssetProducer
    from app.central_sync.fast_bess_producer import FastBESSProducer
    from app.central_sync.gateway_health_producer import GatewayHealthProducer
    from app.central_sync.charge_discharge_producer import ChargeDischargeProducer
    from app.central_sync.alarm_event_producer import AlarmEventProducer
    from app.central_sync.configuration_audit_producer import ConfigurationAuditProducer
    from app.central_sync.client import CentralBackendClient
    from app.central_sync.uploader import CentralSyncUploader
    from app.central_sync.contracts import LogicalMessage, make_message

    bodies = {}
    async with httpx.AsyncClient(base_url=args.api_url, timeout=90, trust_env=False) as h:
        r = await h.post('/api/auth/login', json={'username': 'internal', 'password': password})
        check('Live API authentication', r.status_code == 200)
        h.headers['Authorization'] = 'Bearer ' + r.json()['access_token']
        endpoints = [
            '/api/health', '/api/assets', '/api/telemetry/snapshot', '/api/telemetry/compact',
            '/api/normalized/snapshot', '/api/normalized/pairs', '/api/normalized/safety',
            '/api/bms/bank', '/api/bms/racks', '/api/bms/racks/summary',
            '/api/bms/environment', '/api/pcs/all', '/api/alarms', '/api/alarms/history',
            '/api/events', '/api/storage/status', '/api/diagnostics/polling',
            '/api/diagnostics/runtime', '/api/diagnostics/data-rate',
            '/api/control-sequence/status/all', '/api/control/capabilities',
            '/api/platform/readiness', '/api/commissioning/status',
        ]
        for route in endpoints:
            started = time.monotonic()
            response = await h.get(route)
            check('GET ' + route, response.status_code == 200,
                  {'http_status': response.status_code, 'seconds': round(time.monotonic()-started, 3)})
            bodies[route] = response.json()
        initial_health = bodies['/api/health']
        await asyncio.sleep(3)
        later = (await h.get('/api/health')).json()
        before = initial_health.get('polling', {}).get('fast', {}).get('count', 0)
        after = later.get('polling', {}).get('fast', {}).get('count', 0)
        check('Live fast polling advances', after > before, {'before': before, 'after': after})

    snapshot = bodies['/api/telemetry/snapshot']
    normalized = bodies['/api/normalized/snapshot']
    check('Four normalized battery/PCS pairs', len(normalized.get('pairs', {})) == 4)
    assets = [snapshot.get('bank', {})] + list(snapshot.get('racks', []))
    assets += list(snapshot.get('pcs_devices', {}).values())
    assets += list(snapshot.get('environment', {}).values())
    counts = []
    for asset in assets:
        telemetry = asset.get('telemetry') or {}
        qualities = Counter(str(v.get('quality')) for v in telemetry.values() if isinstance(v, dict))
        counts.append({'asset_id': asset.get('asset_id'), 'online': asset.get('online'),
                       'signal_count': len(telemetry), 'quality_counts': dict(qualities)})
        check('Telemetry present: ' + str(asset.get('asset_id')), bool(telemetry), counts[-1])
        check('Mock telemetry quality: ' + str(asset.get('asset_id')),
              asset.get('online') is True and qualities.get('good', 0) == len(telemetry), counts[-1])
    check('Four racks and four PCS exposed', len(snapshot.get('racks', [])) == 4 and len(snapshot.get('pcs_devices', {})) == 4)
    report['asset_telemetry'] = counts

    # Use the actual deployed Central Sync config, but redirect ALL state paths
    # and transport. This does not enable the production service/configuration.
    cfg = CentralSyncConfig.model_validate(central)
    cfg.enabled = True
    cfg.identity.gateway_id = 'debix-local-validation'
    cfg.identity.site_id = 'local-validation'
    cfg.backend.base_url = 'http://validation.invalid'
    cfg.backend.gateway_token = 'validation-only'
    cfg.backend.source_ip = None
    cfg.local_gateway_api.base_url = args.api_url
    cfg.local_gateway_api.password = password
    cfg.local_gateway_api.password_env = ''
    cfg.local_gateway_api.token = None
    cfg.local_gateway_api.token_env = ''
    cfg.general_assets.catalog_dir = str(app / 'generated_protocols')
    cfg.general_assets.s2_policy_manifest_path = str(app / 'configs/kalpa_s2_policy_frozen_v1_0.json')
    cfg.outbox.path = str(out / 'validation-outbox.db')
    cfg.outbox.required_mount_path = None
    cfg.outbox.fail_if_mount_missing = False
    cfg.outbox.min_free_space_mb = 0
    cfg.status_file = str(out / 'central-status.json')
    cfg.uploader.pause_file = str(out / 'uploader.pause')
    cfg.uploader.coalescing_window_sec = 0
    for name in ('fast_bess', 'general_assets', 'gateway_health', 'alarms_events',
                 'configuration_audit', 'charge_discharge', 'soc_controller', 'solis', 'edge_ai', 'command_downlink',
                 'overflow_archive', 'overflow_recovery', 'backlog_replay'):
        item = getattr(cfg, name)
        if hasattr(item, 'status_file'):
            item.status_file = str(out / (name + '-status.json'))
        if hasattr(item, 'state_file'):
            item.state_file = str(out / (name + '-state.json'))
    cfg.command_downlink.enabled = False
    cfg.command_downlink.ledger_path = str(out / 'commands.db')
    gw_copy, cs_copy = out / 'audit-gateway.json', out / 'audit-central.json'
    gw_copy.write_text(json.dumps(gateway))
    cs_copy.write_text(json.dumps(central))
    cfg.configuration_audit.gateway_config_path = str(gw_copy)
    cfg.configuration_audit.central_sync_config_path = str(cs_copy)

    api = LocalGatewayApiClient(cfg.local_gateway_api)
    class FrozenAPI:
        async def telemetry_snapshot(self):
            return snapshot
        def __getattr__(self, key):
            return getattr(api, key)

    box = OutboxStore(cfg.outbox.path, min_free_space_mb=0)
    try:
        s1 = FastBESSProducer(config=cfg, outbox=box, local_api=api)
        s2 = GeneralAssetProducer(config=cfg, outbox=box, local_api=FrozenAPI())
        s3 = GatewayHealthProducer(config=cfg, outbox=box, local_api=api)
        s4 = AlarmEventProducer(config=cfg, outbox=box, local_api=api)
        s8 = ConfigurationAuditProducer(config=cfg, outbox=box)
        s10 = ChargeDischargeProducer(config=cfg, outbox=box, local_api=api)
        results = {}
        for name, producer, kwargs in [
            ('S1', s1, {}), ('S2', s2, {}), ('S3', s3, {'force_heartbeat': True}),
            ('S4', s4, {}), ('S8', s8, {}), ('S10', s10, {'force_emit': True})]:
            results[name] = await producer.collect_once(**kwargs)
        check('S1 emits four BESS records', results['S1'].get('record_count') == 4, results['S1'])
        check('Optimized S2 emits all 9494 frozen runtime signals', results['S2'].get('record_count') == 9494,
              {'record_count': results['S2'].get('record_count'), 'message_count': results['S2'].get('message_count')})
        check('S3 gateway health emitted', results['S3'].get('emitted'), results['S3'])
        check('S10 charge/discharge status emitted', results['S10'].get('emitted'), results['S10'])
        check('S4 establishes history cursor', results['S4'].get('reason') == 'baseline_established', results['S4'])
        check('S8 establishes configuration baseline', results['S8'].get('reason') == 'baseline_established', results['S8'])
        # Generate an audit event by changing ONLY the isolated copy.
        changed = dict(gateway); changed['mode'] = 'validation-audit-change'
        gw_copy.write_text(json.dumps(changed))
        audit = await s8.collect_once()
        check('S8 detects isolated configuration change', audit.get('emitted'), audit)
        restarted = GeneralAssetProducer(config=cfg, outbox=box, local_api=FrozenAPI())
        repeat = await restarted.collect_once()
        check('Optimized S2 checkpoints survive producer restart', not repeat.get('emitted'), repeat)
        rows = box.conn.execute('SELECT payload_json,payload_bytes,stream FROM outbox_messages').fetchall()
        for row in rows:
            LogicalMessage.model_validate_json(row['payload_json'])
        check('All generated message envelopes validate', bool(rows), {'messages': len(rows)})
        s2rows = [r for r in rows if r['stream'] == cfg.general_assets.stream]
        check('S2 logical messages bounded to 96 KiB', bool(s2rows) and all(r['payload_bytes'] <= 98304 for r in s2rows))
        report['central_producers'] = results
        report['s2_substreams'] = dict(Counter(json.loads(r['payload_json'])['substream'] for r in s2rows))
        report['s2_records_by_substream'] = dict(Counter())
        for row in s2rows:
            body = json.loads(row['payload_json'])
            sub = body['substream']
            report['s2_records_by_substream'][sub] = report['s2_records_by_substream'].get(sub, 0) + body['record_count']
        report['disabled_streams'] = [name for name in ('soc_controller', 'solis', 'edge_ai') if not getattr(cfg, name).enabled]

        # Real batching/client/ACK code with HTTP intercepted in memory. No
        # socket is opened to validation.invalid or any production backend.
        backend = CentralBackendClient(cfg.backend, cfg.identity.gateway_id)
        backend.client_id = backend.client_secret = None
        await backend.http.aclose()
        mode = {'value': 'ack'}
        wire = []
        def handler(request):
            raw = request.content
            compressed = request.headers.get('content-encoding') == 'gzip'
            body = json.loads(gzip.decompress(raw) if compressed else raw)
            wire.append({'messages': len(body['messages']), 'wire_bytes': len(raw), 'gzip': compressed})
            if mode['value'] == 'fail':
                return httpx.Response(503, json={'error': 'injected validation outage'})
            if mode['value'] == 'rate':
                return httpx.Response(429, headers={'Retry-After': '2'}, json={'error': 'injected throttle'})
            if mode['value'] == 'split' and len(body['messages']) > 1:
                return httpx.Response(413, json={'error': 'injected size limit'})
            if mode['value'] == 'partial':
                return httpx.Response(200, json={'accepted': [body['messages'][0]['message_id']]})
            if mode['value'] == 'duplicate':
                return httpx.Response(200, json={'accepted': [
                    {'message_id': m['message_id'], 'status': 'already_processed'} for m in body['messages']]})
            return httpx.Response(200, json={'accepted': [m['message_id'] for m in body['messages']], 'rejected': []})
        backend.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        uploader = CentralSyncUploader(config=cfg, outbox=box, client=backend)
        async def drain():
            for _ in range(250):
                if not await uploader.run_once():
                    break
            check('Isolated outbox drains after ACK', box.stats()['unacked_count'] == 0, box.stats())
        try:
            await drain()
            cfg.backend.gzip_enabled = True
            backend.gzip_enabled = True
            def sample(sequence):
                return make_message(schema_version=cfg.identity.schema_version,
                    gateway_id=cfg.identity.gateway_id, site_id=cfg.identity.site_id,
                    organization_id=cfg.identity.organization_id,
                    gateway_software_version=cfg.identity.software_version,
                    stream='gateway_health', substream='validation', sequence=sequence,
                    priority='P2', records=[{'value': sequence}], compression='gzip')
            box.enqueue(sample(900001))
            await drain()
            check('Gzip transport exercised', any(x['gzip'] for x in wire))
            box.enqueue(sample(900002))
            mode['value'] = 'fail'
            await uploader.run_once()
            check('Backend outage retains message for retry', box.stats()['retry_count'] == 1, box.stats())
            box.conn.execute("UPDATE outbox_messages SET next_attempt_epoch=0 WHERE state='retry'")
            box.conn.commit()
            mode['value'] = 'ack'
            await drain()
            box.enqueue(sample(900003))
            mode['value'] = 'rate'
            await uploader.run_once()
            retry_row = box.conn.execute("SELECT next_attempt_epoch FROM outbox_messages WHERE state='retry'").fetchone()
            check('HTTP 429 honors Retry-After', retry_row is not None and retry_row[0] > time.time(), box.stats())
            box.conn.execute("UPDATE outbox_messages SET next_attempt_epoch=0 WHERE state='retry'")
            box.conn.commit()
            mode['value'] = 'ack'
            await drain()
            box.enqueue(sample(900004)); box.enqueue(sample(900005))
            mode['value'] = 'partial'
            await uploader.run_once()
            check('Partial ACK retries only unacknowledged message', box.stats()['retry_count'] == 1 and uploader.counters.missing_ack_count >= 1)
            box.conn.execute("UPDATE outbox_messages SET next_attempt_epoch=0 WHERE state='retry'")
            box.conn.commit()
            mode['value'] = 'ack'
            await drain()
            box.enqueue(sample(900006))
            mode['value'] = 'duplicate'
            await drain()
            check('Duplicate ACK treated as success', uploader.counters.already_processed_count >= 1)
            box.enqueue(sample(900007)); box.enqueue(sample(900008))
            mode['value'] = 'split'
            await drain()
            check('HTTP 413 splits multi-message request', bool(box.conn.execute("SELECT 1 FROM transport_attempts WHERE outcome='split_413'").fetchone()))
            mode['value'] = 'ack'
            box.enqueue(sample(900009))
            Path(cfg.uploader.pause_file).write_text('validation pause')
            paused = await uploader.run_once()
            check('Uploader pause retains pending data', not paused and box.stats()['pending_count'] == 1)
            Path(cfg.uploader.pause_file).unlink()
            await drain()

            # Exercise overflow copy-before-delete and replay using only our
            # validation DB/archive directory, including the pause gate.
            from app.central_sync.overflow_archiver import OverflowArchiver
            from app.central_sync.overflow_recovery import OverflowRecoveryService
            cfg.overflow_archive.archive_dir = str(out / 'overflow-archives')
            cfg.overflow_archive.min_archive_free_space_mb = 0
            cfg.overflow_archive.spill_min_age_sec = 1
            cfg.overflow_recovery.archive_dir = cfg.overflow_archive.archive_dir
            cfg.overflow_recovery.status_file = str(out / 'overflow-recovery.json')
            cfg.overflow_recovery.uploader_status_file = str(out / 'overflow-uploader.json')
            box.enqueue(sample(900010))
            box.conn.execute("UPDATE outbox_messages SET created_epoch=? WHERE state='pending'", (time.time()-120,))
            box.conn.commit()
            archiver = OverflowArchiver(config=cfg, outbox=box)
            spill = archiver.run_once()
            report['overflow_spill'] = spill
            archive_path = archiver.counters.last_archive_path
            check('Overflow archives old data before deleting live copy', archiver.counters.archived_message_count == 1 and box.stats()['unacked_count'] == 0, spill)
            recovery = OverflowRecoveryService(cfg, transport_lock=uploader.transport_lock, client=backend)
            Path(cfg.uploader.pause_file).write_text('validation pause')
            permitted, reason, _ = recovery._resource_gate()
            check('Overflow recovery obeys transport pause', not permitted and reason == 'transport_paused')
            Path(cfg.uploader.pause_file).unlink()
            await recovery._replay_one(Path(archive_path))
            check('Overflow replay ACKs and retains archive evidence', recovery.counters.archive_drained_count == 1 and Path(archive_path).exists())
            check('Outbox database integrity', box.conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok')
            report['simulated_transport'] = {'requests': wire, 'uploader': uploader.status()}
        finally:
            await uploader.close()
    finally:
        await api.close()
        box.close()


if __name__ == '__main__':
    raise SystemExit(main())
