import unittest

from app.central_sync.general_asset_producer import classify_substream
from app.central_sync.uploader import (
    is_terminal_contract_rejection,
    should_retry_backend_rejection,
)


class ContractRecoveryPatchTests(unittest.TestCase):
    def test_solar_grid_named_points_remain_in_s2_11(self):
        asset = {"asset_id": "solar_plant", "asset_type": "solar_plant"}
        self.assertEqual(
            classify_substream(asset, "grid_active_power_kw", None),
            "solar_inverter",
        )
        self.assertEqual(
            classify_substream(asset, "pcc_export_power_kw", None),
            "solar_inverter",
        )

    def test_abb_acrel_meter_remains_in_s2_10(self):
        asset = {
            "asset_id": "grid_meter_1",
            "asset_type": "energy_meter",
            "vendor": "acrel",
        }
        self.assertEqual(
            classify_substream(asset, "grid_active_power_kw", None),
            "combiner_box_meter",
        )

    def test_only_known_contract_exclusions_are_forced_terminal(self):
        self.assertTrue(is_terminal_contract_rejection("substream_excluded"))
        self.assertTrue(is_terminal_contract_rejection("UNSUPPORTED_STREAM"))
        self.assertFalse(is_terminal_contract_rejection("rate_limited"))
        self.assertFalse(is_terminal_contract_rejection("backend_busy"))
        self.assertFalse(
            should_retry_backend_rejection("substream_excluded", True)
        )
        self.assertTrue(should_retry_backend_rejection("rate_limited", True))
        self.assertFalse(should_retry_backend_rejection("rate_limited", False))


if __name__ == "__main__":
    unittest.main()
