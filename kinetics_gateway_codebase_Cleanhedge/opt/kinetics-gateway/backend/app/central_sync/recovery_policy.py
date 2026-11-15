"""Bounded recovery opportunities; no queue mutation or network operations."""
from __future__ import annotations
import json
import math
import time
from collections import deque
from pathlib import Path

DEFAULTS = {
    "opportunity_interval_sec": 30.0,
    "max_live_unacked": 25,
    "max_live_age_sec": 30.0,
    "max_load_per_cpu": 2.0,
    "eligible_samples": 2,
}
POLICY_PATH = "/etc/kinetics-gateway/recovery_policy.json"

def load_policy(path=POLICY_PATH):
    values = dict(DEFAULTS)
    if Path(path).exists():
        supplied = json.loads(Path(path).read_text())
        if not isinstance(supplied, dict) or set(supplied) - set(values):
            raise ValueError("Unknown recovery policy settings")
        values.update(supplied)
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Invalid recovery setting: {key}")
        if value <= 0:
            raise ValueError(f"Recovery setting must be positive: {key}")
    if int(values["eligible_samples"]) != values["eligible_samples"] or values["eligible_samples"] < 2:
        raise ValueError("eligible_samples must be an integer >= 2")
    if int(values["max_live_unacked"]) != values["max_live_unacked"]:
        raise ValueError("max_live_unacked must be an integer")
    if values["opportunity_interval_sec"] < 10:
        raise ValueError("opportunity_interval_sec must be >= 10")
    return values

class RecoveryPolicy:
    def __init__(self, settings=None, *, clock=time.monotonic):
        self.settings = dict(DEFAULTS if settings is None else settings)
        self.clock = clock
        self.last_opportunity = clock()
        self.eligible = 0
        self.counts = deque(maxlen=3)

    def decide(self, *, live, mem_mb, load1, paused, config, cpu_count):
        now = self.clock()
        count = int(live.get("unacked_count") or 0)
        age = live.get("oldest_age_sec")
        self.counts.append(count)
        load_limit = max(float(config.max_load1), max(1, cpu_count) * self.settings["max_load_per_cpu"])
        info = {"mode": "paused", "opportunity_interval_sec": self.settings["opportunity_interval_sec"],
                "opportunity_live_limit": self.settings["max_live_unacked"],
                "opportunity_age_limit_sec": self.settings["max_live_age_sec"],
                "opportunity_load_limit": load_limit, "cpu_count": cpu_count}
        def block(reason):
            self.eligible = 0
            info["reason"] = reason
            return False, reason, info
        # These protections are never bypassed by the opportunity timer.
        if paused:
            return block("transport_paused")
        if not live.get("available"):
            return block(str(live.get("reason") or "live_outbox_unavailable"))
        if mem_mb < config.min_available_memory_mb:
            return block(f"mem_available_mb={mem_mb}")
        if count < 0 or (count > 0 and (age is None or not math.isfinite(float(age)))):
            return block("live_age_unavailable")
        if not math.isfinite(load1) or load1 > load_limit:
            return block(f"load1={load1:.2f}")
        # Preserve original low-pressure admission rules.
        normal = (count <= config.live_pause_sendable_count and
                  (age is None or float(age) <= config.live_pause_oldest_age_sec) and
                  load1 <= config.max_load1)
        if normal:
            self.last_opportunity = now
            self.eligible = 0
            info["mode"] = "normal"
            return True, None, info
        if count > self.settings["max_live_unacked"]:
            return block(f"live_unacked_count={count}")
        if age is not None and float(age) > self.settings["max_live_age_sec"]:
            return block(f"live_oldest_age_sec={float(age):.1f}")
        # Do not add recovery work to a clearly growing live queue.
        c = list(self.counts)
        if len(c) == 3 and count >= 20 and c[0] < c[1] < c[2] and c[2] - c[0] >= 10:
            return block("live_queue_growing")
        self.eligible += 1
        remaining = max(0.0, self.settings["opportunity_interval_sec"] - (now - self.last_opportunity))
        info["wait_remaining_sec"] = round(remaining, 2)
        if remaining > 0 or self.eligible < self.settings["eligible_samples"]:
            info["reason"] = "opportunity_wait"
            return False, "opportunity_wait", info
        self.last_opportunity = now
        self.eligible = 0
        info["mode"] = "bounded_opportunity"
        return True, None, info

