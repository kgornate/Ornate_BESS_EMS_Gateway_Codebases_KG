from __future__ import annotations

from typing import Any


class CommandOutcomeUnknown(RuntimeError):
    """The write may have reached the device; callers must read back, not replay."""


class CommandReadbackMismatch(RuntimeError):
    """The device responded, but authoritative readback differs from the command."""

    def __init__(
        self,
        message: str,
        *,
        expected_registers: list[int],
        actual_registers: list[int],
    ) -> None:
        super().__init__(message)
        self.expected_registers = list(expected_registers)
        self.actual_registers = list(actual_registers)


def verified_command_metadata(
    *,
    expected_registers: list[int],
    actual_registers: list[int],
    write_error: Exception | None = None,
) -> dict[str, Any]:
    if list(actual_registers) != list(expected_registers):
        detail = (
            f"Command readback mismatch expected={list(expected_registers)} "
            f"actual={list(actual_registers)}"
        )
        if write_error is not None:
            detail += f" after ambiguous write outcome: {write_error}"
        raise CommandReadbackMismatch(
            detail,
            expected_registers=list(expected_registers),
            actual_registers=list(actual_registers),
        )
    if write_error is None:
        return {
            "command_outcome": "applied_verified",
            "transport_acknowledged": True,
            "readback_verified": True,
            "blind_replay_performed": False,
        }
    return {
        "command_outcome": "reconciled_applied_after_transport_error",
        "transport_acknowledged": False,
        "readback_verified": True,
        "blind_replay_performed": False,
        "transport_error": str(write_error),
    }
