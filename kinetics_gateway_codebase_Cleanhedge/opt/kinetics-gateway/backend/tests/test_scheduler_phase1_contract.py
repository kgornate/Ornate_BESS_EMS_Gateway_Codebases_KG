from pathlib import Path


def test_scheduler_uses_compact_control_view_not_full_rack_snapshot():
    source = Path(
        "/root/bess_seven_day_v302_compatible_scheduler.py"
    )
    if not source.exists():
        source = Path(__file__).resolve().parents[4] / "root" / "bess_seven_day_v302_compatible_scheduler.py"
    text = source.read_text(encoding="utf-8")
    assert '"/api/control-sequence/control-view"' in text
    assert '"/api/bms/racks"' not in text
    assert "ambiguous_reconcile_required" in text
    assert "/status/compact?fresh=true" in text
