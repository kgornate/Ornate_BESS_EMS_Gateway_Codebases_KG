from __future__ import annotations

import hashlib
from pathlib import Path

from app.central_sync.cleanedge_normalized import build_cleanedge_normalized_snapshot
from app.central_sync.config import load_central_sync_config
from app.central_sync.contracts import make_message
from app.central_sync.general_asset_producer import GeneralAssetProducer
from app.central_sync.outbox import OutboxStore
from app.central_sync.s2_policy import S2PolicyManifest


ROOT = Path(__file__).resolve().parents[1]


def test_cleanedge_config_is_safe_and_manifest_is_pinned():
    config = load_central_sync_config(ROOT / "configs/central_sync_cleanedge_v1_0.json")
    assert config.identity.site_id == "cleanedge"
    assert config.identity.gateway_id == "kinetics_gateway_1"
    assert config.enabled is False
    assert config.command_downlink.enabled is False
    manifest = Path(config.general_assets.s2_policy_manifest_path)
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == config.general_assets.s2_policy_expected_sha256
    frozen = S2PolicyManifest(manifest, expected_sha256=config.general_assets.s2_policy_expected_sha256)
    assert frozen.version == "cleanedge-s2-frozen-v1.0"
    assert len(frozen.by_key) > 900


def test_normalizer_uses_cache_and_preserves_missing_values():
    snapshot = {
        "gateway_id": "kinetics_gateway_1", "timestamp": "2026-11-12T00:00:00Z", "sequence": 7,
        "racks": [{
            "asset_id": "bms_rack_1", "asset_type": "rack", "rack_id": 1,
            "online": True, "timestamp": "2026-11-12T00:00:00Z",
            "telemetry": {
                "vrack": {"value": 710.5, "quality": "good"},
                "irack": {"value": -12.0, "quality": "good"},
                "rack_inner_soc": {"value": 62.5, "quality": "good"},
            },
        }],
        "pcs_devices": {"pcs_1": {
            "asset_id": "pcs_1", "asset_type": "pcs", "online": True,
            "timestamp": "2026-11-12T00:00:00Z",
            "telemetry": {"grid_active_power": {"value": -50.0, "quality": "good"}},
        }},
    }
    normalized = build_cleanedge_normalized_snapshot(snapshot)
    pair = normalized["pairs"]["pair_1"]
    assert pair["battery"]["voltage_v"] == 710.5
    assert pair["pcs"]["active_power_kw"] == -50.0
    assert pair["battery"]["max_cell_voltage_v"] is None
    assert pair["pcs"]["charging"] is True


def test_solar_aliases_reuse_cached_points_only(tmp_path):
    config = load_central_sync_config(ROOT / "configs/central_sync_cleanedge_v1_0.json")
    config.outbox.path = str(tmp_path / "outbox.db")
    config.outbox.min_free_space_mb = 0
    class CacheOnlyApi:
        pass
    producer = GeneralAssetProducer(
        config=config, outbox=OutboxStore(config.outbox.path, min_free_space_mb=0),
        local_api=CacheOnlyApi(),
    )
    asset = {"telemetry": {
        "pcc_export_power_kw": {"value": 11.2, "quality": "good", "unit": "kW"},
        "solar_active_power_kw": {"value": 100.0, "quality": "good", "unit": "kW"},
    }}
    expanded = {key: point for key, index, point in producer._expanded_telemetry(asset, "cleanedge_solar", "solar") if index is None}
    assert expanded["hopecombox__on_grid_active_power"] is asset["telemetry"]["pcc_export_power_kw"]
    assert expanded["hopecombox__ac_active_power"] is asset["telemetry"]["solar_active_power_kw"]
    producer.outbox.close()


def test_outbox_keeps_stable_identity_and_releases_acked_payload(tmp_path):
    db = OutboxStore(str(tmp_path / "outbox.db"), min_free_space_mb=0)
    msg = make_message(
        schema_version="1.0", message_id="cleanedge-message-1",
        gateway_id="kinetics_gateway_1", site_id="cleanedge", organization_id="ornate-solar",
        gateway_software_version="cleanedge-central-sync-v1.0", stream="gateway_health",
        substream="gateway", sequence=1, priority="P2", records=[{"status": "ok"}],
    )
    assert db.enqueue(msg) is True
    assert db.enqueue(msg) is False
    db.mark_accepted(msg.message_id, "already_processed", "duplicate acknowledged")
    row = db.conn.execute("SELECT COUNT(*) FROM outbox_messages WHERE message_id=?", (msg.message_id,)).fetchone()
    assert row[0] == 0
    receipt = db.conn.execute("SELECT ack_status FROM delivery_receipts_v1 WHERE message_id=?", (msg.message_id,)).fetchone()
    assert receipt[0] == "already_processed"
    db.close()
