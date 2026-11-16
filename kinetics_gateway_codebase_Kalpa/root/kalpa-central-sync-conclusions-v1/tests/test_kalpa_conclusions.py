import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.central_sync.config import CentralBackendConfig
from app.central_sync.client import CentralBackendClient
from app.central_sync.general_asset_producer import GeneralAssetProducer
from app.central_sync.s2_policy import S2PolicyManifest, S2PolicyError
import test_s2_frozen_optimized_phase4 as old

NEW = old.ROOT / 'configs/kalpa_s2_policy_frozen_v1_1.json'

def newcfg(tmp):
    cfg=old.cfg_for(tmp)
    cfg.general_assets.s2_policy_manifest_path=str(NEW)
    cfg.general_assets.s2_policy_expected_sha256=hashlib.sha256(NEW.read_bytes()).hexdigest()
    return cfg

def records(db):
    return [record for row in old.query_messages(db) for record in json.loads(row['payload_json'])['records']]

def test_preserves_all_existing_intervals_and_checkpoint_revisions():
    before=S2PolicyManifest(old.MANIFEST);after=S2PolicyManifest(NEW)
    assert len(after.by_key)==2472
    for key,p in before.by_key.items():
        q=after.by_key[key]
        assert (p.point_id,p.substream,p.mode,p.interval_sec,p.revision)==(q.point_id,q.substream,q.mode,q.interval_sec,q.revision)
    extras=set(after.by_key)-set(before.by_key)
    assert extras=={('lineage','bank','system_soc')}
    soc=after.by_key[next(iter(extras))]
    assert (soc.substream,soc.mode,soc.interval_sec,soc.retention_class)==('ems_system','PERIODIC',30,'T2_1Y')
    assert sum(p.retention_class=='T3_3Y' for p in after.by_key.values())==12
    assert not any(p.substream=='utility_meter' for p in after.by_key.values())

def test_full_four_pair_generation_retention_bounded_messages_and_soc(tmp_path,monkeypatch):
    monkeypatch.setattr('time.time',lambda:1800000000.0)
    cfg=newcfg(tmp_path);db=old.new_outbox(cfg)
    snapshot=old.four_pair_snapshot()
    snapshot['bank']['telemetry']['system_soc']={'value':73.5,'quality':'good'}
    try:
        producer=GeneralAssetProducer(config=cfg,outbox=db,local_api=old.SnapshotAPI(snapshot))
        assert asyncio.run(producer.collect_once())['record_count']==9495
        rr=records(db)
        soc=[r for r in rr if r['signal']=='system_soc']
        assert len(soc)==1
        assert (soc[0]['substream'],soc[0]['value'],soc[0]['unit'],soc[0]['central_record_sec'])==('ems_system',73.5,'%',30)
        assert soc[0]['point_id']=='bank.system_data.system_soc'
        energy=[r for r in rr if r['retention_class']=='T3_3Y']
        assert len(energy)==30
        assert len({r['point_id'] for r in energy})==12
        assert all(r['vendor']=='lineage' for r in energy)
        assert all(row['payload_bytes']<=98304 for row in old.query_messages(db))
        reopened=GeneralAssetProducer(config=cfg,outbox=db,local_api=old.SnapshotAPI(snapshot))
        assert not asyncio.run(reopened.collect_once())['emitted']
    finally:db.close()

def test_upgrade_does_not_reemit_existing_9494_signals(tmp_path,monkeypatch):
    monkeypatch.setattr('time.time',lambda:1800000000.0)
    cfg=old.cfg_for(tmp_path);db=old.new_outbox(cfg)
    snapshot=old.four_pair_snapshot();snapshot['bank']['telemetry']['system_soc']={'value':50,'quality':'good'}
    api=old.SnapshotAPI(snapshot)
    try:
        assert asyncio.run(GeneralAssetProducer(config=cfg,outbox=db,local_api=api).collect_once())['record_count']==9494
        cfg=newcfg(tmp_path)
        result=asyncio.run(GeneralAssetProducer(config=cfg,outbox=db,local_api=api).collect_once())
        assert result['record_count']==1
        assert sum(r['signal']=='system_soc' for r in records(db))==1
        # Old queued records are immutable and preserve their original labels.
        assert any(r['retention_class']=='T1_90D' for r in records(db))
    finally:db.close()

def test_soc_periodic_boundary_and_restart(tmp_path,monkeypatch):
    now=[1800000000.0];monkeypatch.setattr('time.time',lambda:now[0])
    cfg=newcfg(tmp_path);db=old.new_outbox(cfg)
    snap={'bank':{'asset_id':'bms_bank','asset_type':'bank','telemetry':{'system_soc':{'value':50,'quality':'good'}}},'normalized':{'vendors':{'bms':'lineage','pcs':'elecod'}}}
    api=old.SnapshotAPI(snap)
    try:
        p=GeneralAssetProducer(config=cfg,outbox=db,local_api=api)
        assert asyncio.run(p.collect_once())['record_count']==1
        now[0]+=29.999;snap['bank']['telemetry']['system_soc']['value']=51
        assert not asyncio.run(p.collect_once())['emitted']
        p=GeneralAssetProducer(config=cfg,outbox=db,local_api=api)
        assert not asyncio.run(p.collect_once())['emitted']
        now[0]=1800000030.0
        assert asyncio.run(p.collect_once())['record_count']==1
        assert records(db)[-1]['trigger_reason']=='periodic'
        assert records(db)[-1]['value']==51
    finally:db.close()

def test_new_endpoint_default_and_fallback(monkeypatch):
    import httpx
    monkeypatch.setattr(CentralBackendClient,'_build_http_client',lambda self:httpx.AsyncClient(trust_env=False))
    assert CentralBackendConfig().ingest_path=='/api/v1/gateway/ingest'
    client=CentralBackendClient(SimpleNamespace(base_url='https://example.invalid'),gateway_id='test')
    assert client.ingest_url=='https://example.invalid/api/v1/gateway/ingest'
    asyncio.run(client.close())

def test_manifest_checksum_and_invalid_retention_rejected(tmp_path):
    with pytest.raises(S2PolicyError,match='checksum'):
        S2PolicyManifest(NEW,expected_sha256='0'*64)
    data=json.loads(NEW.read_text());data['points'][0]['retention_class']='TYPO'
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(data))
    with pytest.raises(S2PolicyError,match='retention'):S2PolicyManifest(bad)
