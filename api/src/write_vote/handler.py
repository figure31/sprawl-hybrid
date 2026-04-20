"""POST /votes"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from sprawl_common import db, eip712, gates, http, operator


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)
        required = ["linkId", "votedAt", "nonce", "beaconBlock", "voter", "authorSig"]
        for f in required:
            if f not in body:
                return http.bad_request("missing_field", f)

        link_id  = _normalize_uint(body["linkId"])
        voted_at = int(body["votedAt"])
        nonce    = int(body["nonce"])
        beacon   = int(body["beaconBlock"])
        voter    = body["voter"].lower()

        citizen = db.fetch_citizen(voter)
        if not citizen or not citizen.get("isRegistered"): return http.forbidden("not_citizen")
        if citizen.get("isBanned"): return http.forbidden("banned")

        ok, reason = gates.beacon_is_fresh(beacon)
        if not ok: return http.bad_request("stale_beacon_block", reason or "")

        allowed, count = gates.check_and_bump_daily(voter)
        if not allowed: return http.forbidden("daily_cap_hit", f"{gates.DAILY_WRITE_CAP} writes/day")

        link_key = _hex_id(link_id)
        link_row = db.links_table().get_item(Key={"linkId": link_key})
        if not link_row.get("Item"): return http.bad_request("link_unknown")

        # Reject if this voter already voted on this link.
        existing = db.votes_table().get_item(Key={"linkId": link_key, "voter": voter})
        if existing.get("Item"): return http.bad_request("already_voted")

        msg = {
            "linkId":      link_id,
            "votedAt":     voted_at,
            "nonce":       nonce,
            "beaconBlock": beacon,
            "voter":       voter,
        }
        chain_id = int(os.environ["CHAIN_ID"])
        contract = os.environ["CONTRACT_ADDRESS"]
        digest = eip712.vote_digest(chain_id, contract, msg)

        author_sig = eip712.Sig.from_packed_hex(body["authorSig"])
        if not eip712.verify_sig(digest, author_sig, voter):
            return http.unauthorized("bad_author_signature")

        if not db.bump_nonce_atomic(voter, nonce):
            return http.bad_request("nonce_conflict")

        operator_sig = operator.cosign(digest)

        bundle = {
            "type":        "Vote",
            "payload":     msg,
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "receivedAt":  int(time.time()),
        }
        db.archive_bundle(voter, nonce, bundle)

        db.votes_table().put_item(Item={
            "linkId":      link_key,
            "voter":       voter,
            "votedAt":     Decimal(voted_at),
            "nonce":       Decimal(nonce),
            "beaconBlock": Decimal(beacon),
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "createdAt":   Decimal(int(time.time())),
        })

        # Increment vote count atomically on the link.
        db.links_table().update_item(
            Key={"linkId": link_key},
            UpdateExpression="ADD voteCount :one",
            ExpressionAttributeValues={":one": Decimal(1)},
        )

        return http.ok({"ok": True})

    except Exception as e:
        return http.server_error(str(e))


def _normalize_uint(v) -> int:
    if isinstance(v, int): return v
    s = str(v)
    if s.startswith("0x") or s.startswith("0X"): return int(s, 16)
    return int(s)


def _hex_id(v: int) -> str:
    return str(v)
