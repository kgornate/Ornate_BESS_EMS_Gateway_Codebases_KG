from __future__ import annotations

import socket

import pytest

from app.protocols.command_reliability import (
    CommandOutcomeUnknown,
    CommandReadbackMismatch,
    verified_command_metadata,
)
from app.protocols.modbus_rtu import ModbusRtuClient
from app.protocols.modbus_tcp import ModbusTcpClient


class _FailingSocket:
    def __init__(self) -> None:
        self.send_calls = 0

    def sendall(self, payload: bytes) -> None:
        self.send_calls += 1
        raise socket.timeout("simulated uncertain TCP write")

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        pass


class _SilentSerial:
    is_open = True

    def __init__(self) -> None:
        self.write_calls = 0

    def reset_input_buffer(self) -> None:
        pass

    def write(self, payload: bytes) -> int:
        self.write_calls += 1
        return len(payload)

    def flush(self) -> None:
        pass

    def read(self, count: int) -> bytes:
        return b""

    def close(self) -> None:
        self.is_open = False


def test_tcp_write_timeout_is_not_blindly_replayed() -> None:
    client = ModbusTcpClient("192.0.2.10", timeout=0.01)
    sock = _FailingSocket()
    client._sock = sock  # type: ignore[assignment]
    with pytest.raises(CommandOutcomeUnknown):
        client.write_single_register(1, 100, 7)
    assert sock.send_calls == 1


def test_rtu_write_timeout_is_not_blindly_replayed_even_with_retries() -> None:
    port = _SilentSerial()
    client = ModbusRtuClient(
        "/dev/fake",
        38400,
        retries=5,
        inter_request_delay_ms=0,
        serial_factory=lambda: port,
    )
    with pytest.raises(CommandOutcomeUnknown):
        client.write_single_register(1, 100, 7)
    assert port.write_calls == 1


def test_successful_ack_and_equal_readback_is_verified() -> None:
    result = verified_command_metadata(
        expected_registers=[0x1234, 0x5678],
        actual_registers=[0x1234, 0x5678],
    )
    assert result["command_outcome"] == "applied_verified"
    assert result["transport_acknowledged"] is True
    assert result["blind_replay_performed"] is False


def test_uncertain_write_with_equal_readback_is_reconciled_without_replay() -> None:
    result = verified_command_metadata(
        expected_registers=[7],
        actual_registers=[7],
        write_error=CommandOutcomeUnknown("simulated timeout"),
    )
    assert result["command_outcome"] == "reconciled_applied_after_transport_error"
    assert result["transport_acknowledged"] is False
    assert result["readback_verified"] is True
    assert result["blind_replay_performed"] is False


def test_mismatched_readback_is_never_reported_as_success() -> None:
    with pytest.raises(CommandReadbackMismatch) as captured:
        verified_command_metadata(
            expected_registers=[7],
            actual_registers=[0],
        )
    assert captured.value.expected_registers == [7]
    assert captured.value.actual_registers == [0]
