"""Gate helpers used by every write Lambda.

Centralizes the beacon-block freshness check and per-citizen daily
rate limit so adding a gate (or tightening an existing one) is a
one-file change.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Optional

from . import db, rpc


# Max daily writes per citizen (rolling 24h window).
DAILY_WRITE_CAP = 120

# Beacon block tolerance. Lambda rejects any signed message whose
# beaconBlock is more than this many blocks behind the current tip, or
# ahead of the tip at all. 256 blocks is roughly 50 minutes on
# mainnet / Sepolia, giving plenty of leeway for slow networks.
BEACON_MAX_LAG = 256


def beacon_is_fresh(claimed_beacon: int) -> tuple[bool, Optional[str]]:
    """Return (ok, reason_if_not)."""
    try:
        current = rpc.block_number()
    except Exception as e:
        # If RPC is unreachable, we fail open — better than blocking writes
        # because of a transient RPC issue. Log and accept.
        return True, None
    if claimed_beacon > current:
        return False, f"beacon_in_future: claimed={claimed_beacon} tip={current}"
    if claimed_beacon + BEACON_MAX_LAG < current:
        return False, f"beacon_too_old: claimed={claimed_beacon} tip={current}"
    return True, None


def _day_bucket_start(now_ts: int) -> int:
    """Return the Unix timestamp of the start of the current 24-hour bucket.

    We use calendar days (UTC) so every citizen's cap resets at midnight UTC.
    Cleaner than a rolling window and trivially atomic via conditional updates.
    """
    return (now_ts // 86400) * 86400


def check_and_bump_daily(address: str) -> tuple[bool, int]:
    """Atomically enforce the per-citizen daily write cap.

    Returns (allowed, current_count_after_bump). If allowed is False, the
    citizen has already hit the cap for today; current_count_after_bump is
    left at whatever was there (no increment happens on rejection).
    """
    now = int(time.time())
    bucket = _day_bucket_start(now)

    # First, try to increment assuming the bucket matches and we're under cap.
    try:
        resp = db.citizens_table().update_item(
            Key={"address": address.lower()},
            UpdateExpression="SET writesBucket = :b, writesToday = if_not_exists(writesToday, :zero) + :one",
            ConditionExpression=(
                "(attribute_not_exists(writesBucket) OR writesBucket = :b) "
                "AND (attribute_not_exists(writesToday) OR writesToday < :cap)"
            ),
            ExpressionAttributeValues={
                ":b":    Decimal(bucket),
                ":zero": Decimal(0),
                ":one":  Decimal(1),
                ":cap":  Decimal(DAILY_WRITE_CAP),
            },
            ReturnValues="UPDATED_NEW",
        )
        new_count = int(resp["Attributes"]["writesToday"])
        return True, new_count
    except Exception:
        pass

    # If that failed, the bucket may have rolled over; reset and try once more.
    try:
        resp = db.citizens_table().update_item(
            Key={"address": address.lower()},
            UpdateExpression="SET writesBucket = :b, writesToday = :one",
            ConditionExpression="writesBucket < :b OR attribute_not_exists(writesBucket)",
            ExpressionAttributeValues={":b": Decimal(bucket), ":one": Decimal(1)},
            ReturnValues="UPDATED_NEW",
        )
        return True, 1
    except Exception:
        # Still failed: we're at cap in the current bucket.
        return False, DAILY_WRITE_CAP
