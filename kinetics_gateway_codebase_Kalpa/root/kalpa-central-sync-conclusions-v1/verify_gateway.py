#!/usr/bin/env python3
"""Read-only installation and recent retained S2 evidence; never starts a producer."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',type=Path,default=Path('/opt/ornate-ems-gateway/backend'))
    parser.add_argument('--config',type=Path,default=Path('/etc/ornate-ems-gateway/central_sync.json'))
    parser.add_argument('--minutes',type=float,default=2)
    args=parser.parse_args()
    package=Path(__file__).resolve().parent
    metadata=json.loads((package/'patch.json').read_text())
    for name,entry in metadata['files'].items():
        if hashlib.sha256((args.backend/name).read_bytes()).hexdigest()!=entry['after_sha256']:
            raise RuntimeError('Installed file differs: '+name)
    config=json.loads(args.config.read_text())
    general=config['general_assets']
    assert config['backend']['ingest_path']=='/api/v1/gateway/ingest'
    manifest=Path(general['s2_policy_manifest_path'])
    if not manifest.is_absolute():manifest=args.backend/manifest
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==general['s2_policy_expected_sha256']==metadata['manifest_sha256']
    data=json.loads(manifest.read_text())
    expected={p['point_id'] for p in data['points'] if p.get('retention_class')=='T3_3Y'}
    print('Installed source, endpoint path and frozen S2 checksum: PASS')
    print('Canonical points: 2472; four-pair expected instances: 9495; system SoC: S2/ems_system PERIODIC 30s')
    print('Energy/capacity policies: 12 canonical fields / 30 instances, T3_3Y')
    result=subprocess.run(['systemctl','is-active','ornate-central-sync.service'],capture_output=True,text=True)
    print('Central Sync service:',result.stdout.strip() or 'unknown')
    dbpath=Path(config['outbox']['path'])
    if not dbpath.is_absolute():dbpath=args.backend/dbpath
    if not dbpath.is_file():
        print('No outbox found; generation not verified.');return
    db=sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db.execute('PRAGMA query_only=ON')
    soc=[];energy=set();old_energy=set();messages=0
    try:
        for payload, in db.execute('SELECT payload_json FROM outbox_messages WHERE stream=? AND created_epoch>=? ORDER BY created_epoch DESC LIMIT 1000',('general_asset_telemetry',time.time()-args.minutes*60)):
            messages+=1
            for r in json.loads(payload).get('records',[]):
                if r.get('point_id')=='bank.system_data.system_soc':soc.append(r)
                if r.get('point_id') in expected:
                    (energy if r.get('retention_class')=='T3_3Y' else old_energy).add(r['point_id'])
    finally:db.close()
    print('Recent retained S2 messages inspected (limit 1000):',messages)
    if soc:
        valid=all(r.get('substream')=='ems_system' and r.get('policy')=='PERIODIC' and float(r.get('central_record_sec',0))==30 and r.get('unit')=='%' for r in soc)
        print('System SoC recent emission:', 'PASS' if valid else 'CHECK RECORD METADATA', '; retained samples:',len(soc))
        print('Latest retained SoC value/quality:',soc[0].get('value'),soc[0].get('quality'))
    else:print('System SoC: no recent retained sample. Check source data, service logs and outbox capacity; this alone does not prove failure.')
    print('Energy fields recently seen with T3_3Y:',len(energy),'/ 12')
    print('Recent retained energy fields with older tags:',len(old_energy),'(pre-patch queued records retain their tags)')
    print('Some unchanged capacity fields emit only every 1800 seconds. A short window cannot certify all 12.')
    print('This checks local generation only. Successful HTTP responses plus valid ACK handling are required to confirm backend delivery.')

if __name__=='__main__':main()
