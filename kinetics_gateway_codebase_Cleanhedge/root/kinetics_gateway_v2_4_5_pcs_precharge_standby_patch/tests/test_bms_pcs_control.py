from __future__ import annotations

from typing import Any

from app.core.config import GatewayConfig, PcsDeviceConfig
from app.services.bms_pcs_control import BmsPcsControlService


class FakeStore:
    def __init__(self) -> None:
        self.commands: list[tuple[Any, ...]] = []
        self.events: list[tuple[Any, ...]] = []

    def audit_command(self, *args: Any, **kwargs: Any) -> None:
        self.commands.append(args)

    def event(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(args)


class FakeBms:
    def __init__(self) -> None:
        self.rack_enabled = 0
        self.precharge = 0
        self.system_fault = False
        self.recovery_value = 0
        self.insulation_value = 0

    def read_point(self, asset_id: str, key: str) -> dict[str, Any]:
        values = {
            "external_fault_state": {"value": 0x1C00 if self.system_fault else 0, "raw": 0x1C00 if self.system_fault else 0, "bitfields": {
                "emergency_stop_fault": 0,
                "system_fault": 1 if self.system_fault else 0,
                "system_full": 1 if self.system_fault else 0,
                "system_empty": 1 if self.system_fault else 0,
                "pcs_ctrl": 0,
            }},
            "rack_enable_state_l16": {"value": self.rack_enabled},
            "vrack": {"value": 1389.0},
            "irack_chg_limit": {"value": 200.0},
            "irack_dsg_limit": {"value": 200.0},
            "start_insulation_sampleing": {"value": self.insulation_value},
            "ir": {"value": 20000},
            "ir_pos": {"value": 20000},
            "ir_neg": {"value": 20000},
            "pre_charge_state": {"value": 3 if self.precharge else 0},
            "start_pre_chg_operate": {"value": self.precharge},
            "external_level1_alarm": {"value": 0, "raw": 0, "bitfields": {}},
            "bcu_external_lv1_alm_sum_ii": {"value": 0, "raw": 0, "bitfields": {}},
            "rack_fault": {"value": 0, "raw": 0, "bitfields": {}},
            "fault_info_sum2": {"value": 0, "raw": 0, "bitfields": {}},
            "bcu_external_fault_alarm": {"value": 0x4000 if self.system_fault else 0, "raw": 0x4000 if self.system_fault else 0, "bitfields": {"reserved1": 1 if self.system_fault else 0}},
            "external_critical": {"value": 0, "raw": 0, "bitfields": {}},
            "external_stop_alarm2": {"value": 0, "raw": 0, "bitfields": {}},
            "bcu_run_state": {"value": 4},
            "contactor_state": {"value": 5 if self.precharge else 0, "bitfields": {
                "positive_contactor_state": 1 if self.precharge else 0,
                "negitive_contactor_state": 1 if self.precharge else 0,
                "pre_charge_contactor_state": 0,
            }},
        }
        return values[key]

    def write_indexed_point(self, asset_id: str, key: str, index: int, value: int) -> dict[str, Any]:
        self.rack_enabled = 1 if value == 1 else 0
        return {"ok": True, "point_key": key, "index": index, "written_value": value}

    def write_point(self, asset_id: str, key: str, value: int) -> dict[str, Any]:
        if key == "start_pre_chg_operate":
            self.precharge = value
        elif key == "start_insulation_sampleing":
            self.insulation_value = value
        elif key == "one_click_revert":
            self.recovery_value = value
            if value == 1:
                self.system_fault = False
        return {"ok": True, "point_key": key, "written_value": value, "readback": {"value": value}}


class FakePcs:
    def __init__(self) -> None:
        self.state = 1
        self.precharged = True
        self.values = {
            "remote_on_off_command": 255,
            "remote_local_mode": 65280,
            "product_run_mode": 1,
            "pq_work_mode": 0,
            "active_power_setpoint": 0.0,
        }

    def read_point(self, asset_id: str, key: str) -> dict[str, Any]:
        values = {
            "dc_bus_voltage": 1389.0 if self.precharged else 0.2,
            "battery_voltage": 1389.0 if self.precharged else 0.0,
            "operating_state": self.state,
            "status_word_1": 0x0003 if self.state == 8 else 0x0000,
            "status_word_2": 0x0000 if self.precharged else 0x0040,
            "reg_1210": 0x0080 if self.precharged else 0x0000,
            "reg_121a": 0x0000,
            "grid_active_power": self.values["active_power_setpoint"],
            **self.values,
        }
        return {"value": values[key], "point_key": key}

    def write_point(self, asset_id: str, key: str, value: Any) -> dict[str, Any]:
        self.values[key] = value
        if key == "remote_on_off_command":
            self.state = 8 if value == 65280 else 1
        return {"ok": True, "point_key": key, "written_value": value, "readback": {"value": value}}


def configured_service() -> BmsPcsControlService:
    config = GatewayConfig()
    config.mode = "control_enabled"
    config.bms.write_enabled = True
    config.pcs.enabled = True
    config.pcs.write_enabled = True
    config.pcs.transport = "rtu"
    config.pcs.devices = [
        PcsDeviceConfig(asset_id="pcs_1", unit_id=1, enabled=True),
        PcsDeviceConfig(asset_id="pcs_2", unit_id=2, enabled=False),
        PcsDeviceConfig(asset_id="pcs_3", unit_id=3, enabled=False),
        PcsDeviceConfig(asset_id="pcs_4", unit_id=4, enabled=False),
    ]
    config.control_sequence.enabled = True
    config.control_sequence.valid_samples_required = 1
    config.control_sequence.sample_interval_seconds = 0.001
    config.control_sequence.pcs_start_timeout_seconds = 0.1
    return BmsPcsControlService(config, FakeBms(), FakePcs(), FakeStore())


def test_staged_sequence_happy_path() -> None:
    service = configured_service()
    confirm = "EXECUTE_STAGE_WRITE"
    assert service.precheck("pair_1", "discharge", 10.0)["ok"] is True
    assert service.enable_rack("internal", "pair_1", "discharge", 10.0, confirm)["ok"] is True
    assert service.start_precharge("internal", "pair_1", confirm)["ok"] is True
    assert service.verify_ready("pair_1")["ok"] is True
    assert service.configure_pcs("internal", "pair_1", confirm)["ok"] is True
    assert service.start_pcs("internal", "pair_1", confirm)["ok"] is True
    result = service.set_power("internal", "pair_1", "discharge", 10.0, confirm)
    assert result["commanded_signed_power_kw"] == 10.0
    assert service.verify_power("pair_1")["setpoint"]["value"] == 10.0
    assert service.safe_stop("internal", "pair_1", confirm, open_bms=True)["ok"] is True


def test_manual_insulation_sampling_is_disabled_per_vendor_instruction() -> None:
    service = configured_service()
    try:
        service.start_insulation("internal", "pair_1", "EXECUTE_STAGE_WRITE")
    except ValueError as error:
        assert "handled automatically" in str(error)
    else:
        raise AssertionError("Manual 0x0401 write must be blocked")


def test_prepare_pcs_standby_before_bms_precharge() -> None:
    service = configured_service()
    service.pcs_driver.precharged = False
    result = service.prepare_pcs_standby(
        "internal", "pair_1", "EXECUTE_STAGE_WRITE"
    )
    assert result["ok"] is True
    assert result["ready_for_bms_precharge"] is True
    assert result["samples"][-1]["pcs_operating_state"] == 8
    assert service.bms_driver.rack_enabled == 0
    assert service.pcs_driver.values["active_power_setpoint"] == 0.0


def test_power_above_project_cap_is_rejected() -> None:
    service = configured_service()
    result = service.precheck("pair_1", "charge", 241.0)
    assert result["ok"] is False
    assert result["checks"]["requested_power_within_240kw_limit"] is False


def test_bms_recovery_pulse_clears_latched_fault_from_safe_state() -> None:
    service = configured_service()
    service.bms_driver.system_fault = True
    result = service.recover_bms("internal", "pair_1", "EXECUTE_STAGE_WRITE")
    assert result["ok"] is True
    assert result["fault_cleared"] is True
    assert result["stage"] == "bms_recovered"
    assert [action["written_value"] for action in result["actions"]] == [1]
    assert service.bms_driver.recovery_value == 1


def test_bms_recovery_is_blocked_if_rack_is_enabled() -> None:
    service = configured_service()
    service.bms_driver.system_fault = True
    service.bms_driver.rack_enabled = 1
    try:
        service.recover_bms("internal", "pair_1", "EXECUTE_STAGE_WRITE")
    except ValueError as error:
        assert "rack_disabled" in str(error)
    else:
        raise AssertionError("Recovery should be blocked while the rack is enabled")
