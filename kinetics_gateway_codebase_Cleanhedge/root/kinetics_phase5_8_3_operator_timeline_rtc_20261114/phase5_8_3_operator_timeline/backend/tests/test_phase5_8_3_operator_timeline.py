import json
from pathlib import Path

from app.api import routes


def _write(path: Path, count: int = 8) -> None:
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": f"2026-11-14T10:{index:02d}:00+05:30",
                    "event": f"event_{index}",
                    "message": f"Event {index}",
                }
            )
            for index in range(count)
        )
        + "\n",
        encoding="utf-8",
    )


def test_event_history_is_newest_first_and_pageable(tmp_path, monkeypatch):
    source = tmp_path / "automation_events.jsonl"
    _write(source)
    monkeypatch.setattr(routes, "AUTOMATION_EVENTS_PATH", source)

    first = routes._automation_event_history(limit=3)
    assert [item["event"] for item in first["events"]] == [
        "event_7",
        "event_6",
        "event_5",
    ]
    assert first["has_more"] is True
    assert first["next_before"] == "2026-11-14T10:05:00+05:30"

    second = routes._automation_event_history(
        limit=3,
        before=first["next_before"],
    )
    assert [item["event"] for item in second["events"]] == [
        "event_4",
        "event_3",
        "event_2",
    ]


def test_event_history_tolerates_missing_and_partial_records(tmp_path, monkeypatch):
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setattr(routes, "AUTOMATION_EVENTS_PATH", missing)
    assert routes._automation_event_history(limit=10)["events"] == []

    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"timestamp":"2026-11-14T12:00:00+05:30","event":"ok"}\n'
        '{"partial":',
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "AUTOMATION_EVENTS_PATH", source)
    result = routes._automation_event_history(limit=10)
    assert [item["event"] for item in result["events"]] == ["ok"]
    assert result["skipped_invalid"] == 1
