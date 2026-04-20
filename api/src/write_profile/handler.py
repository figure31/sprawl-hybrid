"""POST /profile — change the off-chain display name for a citizen.

Separate from the on-chain `renameCitizen` which updates the contract's
registry. This endpoint is for richer profile metadata we might attach
in the off-chain layer only.

No operator co-sign is needed because nothing from this endpoint ever
reaches the contract. The author signature alone gates the update.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from sprawl_common import db, eip712, gates, http


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)
        required = ["displayName", "changedAt", "nonce", "beaconBlock", "citizen", "authorSig"]
        for f in required:
            if f not in body:
                return http.bad_request("missing_field", f)

        display_name = body["displayName"]
        changed_at   = int(body["changedAt"])
        nonce        = int(body["nonce"])
        beacon       = int(body["beaconBlock"])
        citizen_addr = body["citizen"].lower()

        if not display_name: return http.bad_request("display_name_empty")
        if len(display_name.encode("utf-8")) > 64: return http.bad_request("display_name_too_long")

        citizen = db.fetch_citizen(citizen_addr)
        if not citizen or not citizen.get("isRegistered"): return http.forbidden("not_citizen")
        if citizen.get("isBanned"): return http.forbidden("banned")

        ok, reason = gates.beacon_is_fresh(beacon)
        if not ok: return http.bad_request("stale_beacon_block", reason or "")

        allowed, count = gates.check_and_bump_daily(citizen_addr)
        if not allowed: return http.forbidden("daily_cap_hit", f"{gates.DAILY_WRITE_CAP} writes/day")

        msg = {
            "displayName": display_name,
            "changedAt":   changed_at,
            "nonce":       nonce,
            "beaconBlock": beacon,
            "citizen":     citizen_addr,
        }
        chain_id = int(os.environ["CHAIN_ID"])
        contract = os.environ["CONTRACT_ADDRESS"]
        digest = eip712.profile_digest(chain_id, contract, msg)

        sig = eip712.Sig.from_packed_hex(body["authorSig"])
        if not eip712.verify_sig(digest, sig, citizen_addr):
            return http.unauthorized("bad_author_signature")

        if not db.bump_nonce_atomic(citizen_addr, nonce):
            return http.bad_request("nonce_conflict")

        db.citizens_table().update_item(
            Key={"address": citizen_addr},
            UpdateExpression="SET profileDisplayName = :n, profileChangedAt = :t",
            ExpressionAttributeValues={
                ":n": display_name,
                ":t": Decimal(changed_at),
            },
        )

        return http.ok({"ok": True})

    except Exception as e:
        return http.server_error(str(e))
