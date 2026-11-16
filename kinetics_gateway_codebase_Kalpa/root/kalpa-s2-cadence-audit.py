#!/usr/bin/env python3
"""Read-only deployed S2 policy/record audit. No config/service writes."""
import argparse
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

APPROVED_POLICY_SHA = '97f59c18ec78698ace04438725d3a6a68c4f65fd4be32f62cd5c8dba3284e336'
REFERENCE_CODE = {'general_asset_producer.py': '539ec99bbe17be862a343841183a138f3b75ecae8711176b11099b6b793e6cf1', 's2_policy.py': 'c051b57f2a551abbcf732fa08ba911233f2bb6f0f0ed52dcea89e04c07105bf3', 's2_chunks.py': 'cf87377343784e8869108ade19a5b72e65793cf79e90fb6da911a2720fe82f15', 'producer_common.py': '6cecb1d92a284ee7310bd262e43d443f5ab5f8d0ec286d0778747a33c0fd5476', 'outbox.py': 'fa0ac10563175d371fbfd928fd6480ff90e713e33d839a62d6556f10b01b3f0f', 'service.py': '612ad6c7f7a526e09f48af58a87ca8ac60007dfa1b8644cfe6ff0b6dbc8f51e4', 'config.py': 'd0cdd209f80046cc624922528e6e712dc8ab1099eaa3c38bb3bfd93429862414', 'contracts.py': '7d4ed66d665e6be8648e8f924be9b74b58789c8b0d50286407de19ebb8b0074b'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_records(messages, policies):
    stats = defaultdict(lambda: dict(records=0, trigger_counts=Counter(), observed_signals=set(),
                                    metadata_mismatches=0, unexpected_triggers=0))
    issues = Counter()
    mismatch_examples = []
    for sub, payload in messages:
        body = json.loads(payload)
        for record in body.get('records', []):
            r = stats[sub]
            r['records'] += 1
            r['observed_signals'].add(record.get('runtime_signal_id', 'MISSING'))
            reason = record.get('trigger_reason', 'MISSING')
            r['trigger_counts'][reason] += 1
            policy = policies.get((record.get('vendor'), record.get('point_id')))
            bad = []
            if policy is None:
                bad.append('unknown point')
            else:
                for field, actual, expected in [
                    ('substream',sub,policy['substream']),
                    ('record_substream',record.get('substream'),policy['substream']),
                    ('policy',record.get('policy'),policy['mode']),
                    ('central_record_sec',record.get('central_record_sec'),policy['interval_sec'])]:
                    if actual != expected:
                        bad.append(field)
                allowed = {'startup','policy_change','clock_reset'} | (
                    {'change','heartbeat'} if policy['mode']=='ON_CHANGE_HEARTBEAT' else {'periodic'})
                if reason not in allowed:
                    r['unexpected_triggers'] += 1
                    issues['unexpected_trigger'] += 1
            if bad:
                r['metadata_mismatches'] += 1
                issues['metadata_mismatch'] += 1
                if len(mismatch_examples) < 10:
                    mismatch_examples.append(dict(point_id=record.get('point_id'),fields=bad))
    return stats, issues, mismatch_examples


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=float, default=35)
    parser.add_argument('--config', default='/etc/ornate-ems-gateway/central_sync.json')
    parser.add_argument('--backend', default='/opt/ornate-ems-gateway/backend')
    args=parser.parse_args()
    if not 0 < args.minutes <= 120:
        parser.error('minutes must be >0 and <=120')
    cfgpath=Path(args.config)
    cfg=json.loads(cfgpath.read_text())
    section=cfg.get('general_assets',{})
    policy_path=Path(section.get('s2_policy_manifest_path') or '')
    if not section.get('s2_policy_manifest_path'):
        raise SystemExit('No optimized S2 manifest configured')
    if not policy_path.is_absolute():
        policy_path=Path(args.backend)/policy_path
    manifest=json.loads(policy_path.read_text())
    actual_sha=sha(policy_path)
    policies={(p['vendor'],p['point_id']):p for p in manifest['points']}
    expected_signals=Counter()
    for point in manifest['points']:
        expected_signals[point['substream']]+=point['expected_instances']
    db=Path(cfg['outbox']['path']).resolve()
    con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=5,isolation_level=None)
    con.execute('PRAGMA query_only=ON')
    end=time.time()
    start=end-args.minutes*60
    stream=section.get('stream','general_asset_telemetry')
    keys=con.execute('SELECT substream FROM sequences WHERE stream=?',(stream,)).fetchall()
    def messages():
        # Each bounded fetch releases its read snapshot before JSON processing.
        # New rows after the capture boundary are excluded.
        high=con.execute('SELECT COALESCE(MAX(rowid),0) FROM outbox_messages').fetchone()[0]
        for (sub,) in keys:
            last=0
            while True:
                rows=con.execute('''SELECT rowid,payload_json FROM outbox_messages
                    WHERE stream=? AND substream=? AND created_epoch>=? AND created_epoch<?
                      AND rowid>? AND rowid<=? ORDER BY rowid LIMIT 64''',
                    (stream,sub,start,end,last,high)).fetchall()
                if not rows:
                    break
                last=rows[-1][0]
                for _,payload in rows:
                    yield sub,payload
    try:
        stats,issues,examples=check_records(messages(),policies)
    finally:
        con.close()
    code={}
    for name, expected in REFERENCE_CODE.items():
        path=Path(args.backend)/'app/central_sync'/name
        actual=sha(path) if path.exists() else None
        code[name]=dict(matches_reviewed_reference=actual==expected,actual_sha256=actual)
    rows=[]
    for sub in manifest['substreams']:
        row=stats[sub]
        rows.append(dict(substream=sub,contract_runtime_signals=expected_signals[sub],
            observed_unique_signals=len(row['observed_signals']),records=row['records'],
            trigger_counts=dict(row['trigger_counts']),metadata_mismatches=row['metadata_mismatches'],
            unexpected_triggers=row['unexpected_triggers']))
    output=dict(audit='read-only retained S2 record metadata and trigger audit',
        window_minutes=args.minutes,window_start_epoch=start,window_end_epoch=end,
        policy_matches_excel_reviewed_manifest=(actual_sha==APPROVED_POLICY_SHA),
        policy_matches_configured_expected_checksum=(actual_sha==section.get('s2_policy_expected_sha256')),
        actual_policy_sha256=actual_sha,central_sync_config_sha256=sha(cfgpath),
        reviewed_code_comparison=code,records_checked=sum(x['records'] for x in rows),
        issues=dict(issues),mismatch_examples=examples,substreams=rows,
        limitations=[
            'Only retained live-outbox records are checked; archived/deleted rows are absent.',
            'Fewer observed signals than contract total is not proof of loss: six-hour heartbeat points may not emit in this window.',
            'Trigger metadata identifies change-driven traffic but does not independently prove changes occurred.',
            'This checks emitted policy metadata and code identity, not exact emission timing, suppressed/missing emissions or signal value correctness.',
            'A full cadence timing test requires controlled source changes and producer emission/checkpoint timestamps.',
        ])
    print(json.dumps(output,indent=2))


if __name__=='__main__':
    main()
