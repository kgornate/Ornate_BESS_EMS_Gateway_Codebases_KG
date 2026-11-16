#!/usr/bin/env python3
"""Read-only installed patch and optional existing meter API verification."""
import argparse,getpass,hashlib,json,subprocess,urllib.request
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--backend',type=Path,default=Path('/opt/ornate-ems-gateway/backend'));p.add_argument('--config',type=Path,default=Path('/etc/ornate-ems-gateway/central_sync.json'));p.add_argument('--check-meter',action='store_true');p.add_argument('--url',default='http://127.0.0.1:8000');p.add_argument('--username',default='internal');a=p.parse_args()
 package=Path(__file__).resolve().parent;meta=json.loads((package/'patch.json').read_text())
 for name,entry in meta['files'].items():
  if hashlib.sha256((a.backend/name).read_bytes()).hexdigest()!=entry['after_sha256']:raise RuntimeError('Installed file mismatch: '+name)
 config=json.loads(a.config.read_text());g=config['general_assets'];path=Path(g['s2_policy_manifest_path']);path=path if path.is_absolute() else a.backend/path
 assert hashlib.sha256(path.read_bytes()).hexdigest()==g['s2_policy_expected_sha256']==meta['manifest_sha256']
 manifest=json.loads(path.read_text());assert len(manifest['points'])==2506
 assert config['backend']['ingest_path']=='/api/v1/gateway/ingest'
 assert 'utility_meter' not in manifest['substreams']
 assert {'bess_aux_consumption_meter','combiner_box_meter'}<=set(manifest['substreams'])
 meter=[x for x in manifest['points'] if x['vendor']=='acrel'];assert len(meter)==34
 assert all(x['substream']=='combiner_box_meter' for x in meter)
 print('Installed source, catalog, S2 manifest checksum and substream names: PASS')
 print('ACREL combiner_box_meter: 34 signals; total active power 5s, other 26 measurements 30s, seven energies 60s')
 print('S2 contract: 2506 canonical points; 9529 expected four-pair + meter instances')
 print('BESS auxiliary consumption: renamed reserved substream; zero mapped signals')
 status=subprocess.run(['systemctl','is-active','ornate-central-sync.service'],capture_output=True,text=True)
 print('Central Sync:',status.stdout.strip() or 'unknown','(keep inactive until backend is ready)')
 dashboard=Path('/opt/kalpa-sync-dashboard/dashboard.py')
 if dashboard.exists():
  assert hashlib.sha256(dashboard.read_bytes()).hexdigest()==meta['dashboard_after_sha256']
  print('Dashboard substream labels: PASS')
 if not a.check_meter:
  print('No producer started. Existing meter source can be checked with --check-meter.');return
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def request(path,data=None,token=None):
  headers={'Content-Type':'application/json'}
  if token:headers['Authorization']='Bearer '+token
  req=urllib.request.Request(a.url.rstrip('/')+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
  with opener.open(req,timeout=15) as response:return json.load(response)
 token=request('/api/auth/login',{'username':a.username,'password':getpass.getpass('Gateway API password: ')})['access_token']
 snapshot=request('/api/telemetry/snapshot',token=token)
 asset=(snapshot.get('meters') or {}).get('acrel_meter_1')
 if not asset:
  print('No acrel_meter_1 in gateway snapshot. No readings will be invented.');return
 print('Existing meter source:',asset.get('source'),'status:',asset.get('status'),'online:',asset.get('online'),'disabled:',asset.get('disabled'))
 expected={x['key'] for x in meter};available=expected&set((asset.get('telemetry') or {}))
 print('Available mapped fields:',len(available),'/ 34')
 print('CT/PT scaling validated:',asset.get('scaling_validated',False))
 if asset.get('source')=='hardware' and asset.get('online') and len(available)==34 and not asset.get('disabled'):
  print('Hardware meter cache availability: PASS (S2 generation remains stopped)')
 else:print('Hardware availability is not fully verified; inspect the existing meter API/driver.')
 print('No producer started, no serial polling added, no outbox writes made.')

if __name__=='__main__':main()
