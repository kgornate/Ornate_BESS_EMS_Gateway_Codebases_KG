#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "payload/root/bess_solar_time_automation.py"
SPEC = importlib.util.spec_from_file_location("phase5_8_1_1_scheduler", MODULE_PATH)
assert SPEC and SPEC.loader
SCHEDULER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHEDULER)


class PreparedRuntimeTests(unittest.TestCase):
    def runtime(self, **overrides):
        runtime = {
            "run_id": "run-123",
            "run_status": "prepared",
            "stage": "automatic_prepared_zero_power",
            "requested_direction": "charge",
            "requested_power_kw": 5.0,
            "last_error": None,
        }
        runtime.update(overrides)
        return {"runtime": runtime, "summary": {}, "workflow": {}}

    def test_matching_backend_terminal_is_authoritative(self):
        item = self.runtime()
        self.assertTrue(
            SCHEDULER.matching_prepared_runtime(item, "run-123", "charge", 5.0)
        )
        self.assertFalse(SCHEDULER.ready_zero(item))

    def test_stale_runtime_from_another_run_is_rejected(self):
        self.assertFalse(
            SCHEDULER.matching_prepared_runtime(
                self.runtime(run_id="old-run"), "run-123", "charge", 5.0
            )
        )

    def test_prepared_runtime_with_error_is_rejected(self):
        self.assertFalse(
            SCHEDULER.matching_prepared_runtime(
                self.runtime(last_error="verification failed"),
                "run-123",
                "charge",
                5.0,
            )
        )

    def test_legacy_compact_predicate_is_not_a_completion_gate(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("if not ready_zero(final)", source)
        self.assertIn("fault_latch", source)
        self.assertIn("prepared_run_ids", source)


if __name__ == "__main__":
    unittest.main()
