# CleanEdge Central Sync v1.0.3 — Contract and SD-Recovery Patch

This focused patch corrects two related field issues without changing the
northbound envelope, record fields, cadence policy, asset polling, automation,
hardware writes, command policy, or backend credentials.

## Corrections

1. HopeComBox/string-inverter signals, including signals whose names begin with
   `grid_`, are classified as S2.11 `solar_inverter`.
2. The separately identified ABB/Acrel MFM remains S2.10
   `combiner_box_meter`, matching the frozen CleanEdge workbook.
3. Known terminal backend contract rejections such as `substream_excluded` are
   dead-lettered once even if a backend response incorrectly marks them
   retryable. This prevents one excluded substream from permanently ageing the
   live queue and suppressing bounded SD archive recovery.
4. Temporary errors, missing ACKs, transport failures and rate limiting retain
   the existing retry behavior.

## Backend dependency

The backend currently rejects S2.10 `combiner_box_meter`. The backend allowlist
must enable this existing substream before ABB MFM messages can be accepted.
Until then, S2.10 messages retain local rejection evidence but do not retry
forever or block other live and archived data.

## Install

```sh
cd /root/cleanedge-central-sync-v1.0.3-contract-recovery-patch
sha256sum -c SHA256SUMS
./deployment/install.sh
```

The installer prints the exact backup directory required for rollback.

## Focused verification

```sh
systemctl --no-pager --full status kinetics-central-sync.service

journalctl -u kinetics-central-sync.service --since "-10 minutes" --no-pager \
  | grep -Ei 'ingest|accepted|already_processed|substream_excluded|error|traceback' \
  | tail -n 100

/usr/local/bin/kinetics-delivery-dashboard

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
from pathlib import Path

for name in ("uploader_status.json", "overflow_recovery_status.json"):
    path = Path("/var/lib/kinetics-central-sync") / name
    print("\n", path)
    if path.exists():
        data = json.loads(path.read_text())
        print(json.dumps(data, indent=2)[:8000])
PY
```

Expected result: accepted live traffic continues; excluded S2.10 rows become
permanent/dead evidence instead of retry rows; live queue age/count falls; and
the archive recovery budget becomes non-zero when live health is within its
configured bounds.
