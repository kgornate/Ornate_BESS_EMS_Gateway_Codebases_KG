import asyncio,hashlib,json,time
from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import pytest
from app.central_sync.general_asset_producer import GeneralAssetProducer, CatalogIndex, S2_SUBSTREAMS
from app.central_sync.s2_policy import S2PolicyManifest
from app.central_sync.outbox import OutboxCapacityError
from app.assets.acrel_meter import AcrelMeterDriver,MEASUREMENTS,ENERGIES
from app.core.acrel_config import AcrelMeterConfig
import test_s2_frozen_optimized_phase4 as old

NEW=old.ROOT/'configs/kalpa_s2_policy_frozen_v1_2.json'

def cfg(tmp):
 c=old.cfg_for(tmp);c.general_assets.s2_policy_manifest_path=str(NEW);c.general_assets.s2_policy_expected_sha256=hashlib.sha256(NEW.read_bytes()).hexdigest();return c

def meter():
 d=AcrelMeterDriver(AcrelMeterConfig(enabled=True,mode='mock'))
 try:return d.poll()
 finally:d.close()

def recs(db):return [r for row in old.query_messages(db) for r in json.loads(row['payload_json'])['records']]

def test_manifest_and_exact_driver_registers_and_unchanged_bess():
 new=S2PolicyManifest(NEW);before=S2PolicyManifest(old.ROOT/'configs/kalpa_s2_policy_frozen_v1_1.json')
 assert len(new.by_key)==2506
 for key,p in before.by_key.items():assert new.by_key[key]==p
 assert 'utility_meter' not in S2_SUBSTREAMS and 'bess_aux_consumption_meter' in S2_SUBSTREAMS
 assert 'combiner_box_meter' in S2_SUBSTREAMS
 catalog=CatalogIndex(old.ROOT/'generated_protocols')
 for fields,start in [(MEASUREMENTS,0x2000),(ENERGIES,0x3080)]:
  for i,(key,unit) in enumerate(fields):
   c=catalog.get('acrel','meter',key);p=new.by_key['acrel','meter',key]
   assert (c['address_dec'],c['unit'],c['read_fc'],c['datatype'])==(start+2*i,unit,3,'FLOAT32')
   assert p.substream=='combiner_box_meter'
   assert p.interval_sec==(60 if fields is ENERGIES else 5 if key=='active_power_total_kw' else 30)
   assert p.retention_class==('T3_3Y' if fields is ENERGIES else 'T2_1Y')

def test_full_sweep_and_restart_no_bess_cadence_reset(tmp_path,monkeypatch):
 monkeypatch.setattr(time,'time',lambda:1800000000.0)
 c=cfg(tmp_path);db=old.new_outbox(c)
 snap=old.four_pair_snapshot();snap['bank']['telemetry']['system_soc']={'value':55,'quality':'good'};snap['meters']={'acrel_meter_1':meter()}
 try:
  result=asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI(snap)).collect_once())
  assert result['record_count']==9529
  rr=recs(db);mm=[r for r in rr if r['vendor']=='acrel']
  assert len(mm)==34 and all(r['substream']=='combiner_box_meter' for r in mm)
  assert not any(r['substream']=='bess_aux_consumption_meter' for r in rr)
  assert all(r['quality']=='mock' and r['source_mode']=='mock' for r in mm)
  assert sum(r['retention_class']=='T3_3Y' for r in mm)==7
  assert all(row['payload_bytes']<=98304 for row in old.query_messages(db))
  assert not asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI(snap)).collect_once())['emitted']
 finally:db.close()

def test_periodic_5_30_60_and_per_point_timestamp(tmp_path,monkeypatch):
 now=[1800000000.0];monkeypatch.setattr(time,'time',lambda:now[0])
 c=cfg(tmp_path);db=old.new_outbox(c);asset=meter()
 for key,unit in ENERGIES:asset['telemetry'][key]['timestamp']='2027-01-15T07:59:00+00:00'
 snap={'meters':{'acrel_meter_1':asset},'timestamp':datetime.fromtimestamp(now[0],timezone.utc).isoformat()}
 api=old.SnapshotAPI(snap);p=GeneralAssetProducer(config=c,outbox=db,local_api=api)
 try:
  assert asyncio.run(p.collect_once())['record_count']==34
  energy=[r for r in recs(db) if r['category']=='meter_energy']
  assert all(r['source_updated_utc']=='2027-01-15T07:59:00+00:00' for r in energy)
  now[0]+=4.999;assert not asyncio.run(p.collect_once())['emitted']
  now[0]=1800000005.0;assert asyncio.run(p.collect_once())['record_count']==1
  now[0]=1800000030.0;assert asyncio.run(p.collect_once())['record_count']==27
  p=GeneralAssetProducer(config=c,outbox=db,local_api=api)
  now[0]=1800000060.0;assert asyncio.run(p.collect_once())['record_count']==34
 finally:db.close()

@pytest.mark.parametrize('kind',['absent','disabled','never_polled','unknown_vendor'])
def test_no_fabricated_meter_records(tmp_path,kind):
 c=cfg(tmp_path);db=old.new_outbox(c);asset=meter();snap={'meters':{'acrel_meter_1':asset}}
 if kind=='absent':snap={}
 if kind=='disabled':asset['disabled']=True
 if kind=='never_polled':asset['telemetry']={}
 if kind=='unknown_vendor':asset['vendor']='other'
 try:assert asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI(snap)).collect_once())['record_count']==0
 finally:db.close()

@pytest.mark.parametrize('quality,online',[('bad',False),('stale',False),('good',False),('good',True)])
def test_quality_and_hardware_source_preserved(tmp_path,quality,online):
 c=cfg(tmp_path);db=old.new_outbox(c);asset=meter();asset.update(source='hardware',online=online)
 for point in asset['telemetry'].values():point['quality']=quality
 try:
  asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI({'meters':{'acrel_meter_1':asset}})).collect_once())
  rr=recs(db);assert len(rr)==34
  assert all(r['quality']==('bad' if not online and quality=='good' else quality) and r['source_mode']=='hardware' and r['scaling_validated'] is False for r in rr)
 finally:db.close()

def test_atomic_capacity_failure_preserves_meter_checkpoint(tmp_path):
 c=cfg(tmp_path);db=old.new_outbox(c);p=GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI({'meters':{'acrel_meter_1':meter()}}))
 original=db.enqueue_s2_atomic
 def reject(**kwargs):raise OutboxCapacityError('injected full')
 try:
  db.enqueue_s2_atomic=reject;assert asyncio.run(p.collect_once())['reason']=='outbox_capacity'
  assert not db.s2_load_checkpoints(c.general_assets.stream)
  db.enqueue_s2_atomic=original;assert asyncio.run(p.collect_once())['record_count']==34
 finally:db.close()

def test_old_manifest_does_not_silently_enable_meter(tmp_path):
 c=old.cfg_for(tmp_path);db=old.new_outbox(c)
 try:assert asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=old.SnapshotAPI({'meters':{'acrel_meter_1':meter()}})).collect_once())['record_count']==0
 finally:db.close()

def test_upgrade_from_installed_conclusions_emits_only_new_meter(tmp_path,monkeypatch):
 monkeypatch.setattr(time,'time',lambda:1800000000.0)
 c=cfg(tmp_path);c.general_assets.s2_policy_manifest_path=str(old.ROOT/'configs/kalpa_s2_policy_frozen_v1_1.json');c.general_assets.s2_policy_expected_sha256=hashlib.sha256(Path(c.general_assets.s2_policy_manifest_path).read_bytes()).hexdigest()
 db=old.new_outbox(c);snap=old.four_pair_snapshot();snap['bank']['telemetry']['system_soc']={'value':55,'quality':'good'};snap['meters']={'acrel_meter_1':meter()};api=old.SnapshotAPI(snap)
 try:
  assert asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=api).collect_once())['record_count']==9495
  c=cfg(tmp_path)
  result=asyncio.run(GeneralAssetProducer(config=c,outbox=db,local_api=api).collect_once())
  assert result['record_count']==34 and result['message_count']==1
  assert result['messages'][0]['substream']=='combiner_box_meter'
 finally:db.close()
