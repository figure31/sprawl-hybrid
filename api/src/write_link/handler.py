"""POST /links — accept a citizen-authored link (or recap).

Request body:
{
  "linkId":       "0x...",           // uint128, deterministic from (author, nonce)
  "parentId":     "0",               // decimal or 0x-hex string
  "authoredAt":   1713500000,
  "nonce":        7,
  "beaconBlock":  18500000,
  "isRecap":      false,
  "coversFromId": "0",
  "coversToId":   "0",
  "author":       "0xAlice...",
  "text":         "the sun rises over the empty city",
  "authorSig":    "0x...65 bytes..."
}

On success: {"ok": true, "linkId": "..."}
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from sprawl_common import db, eip712, gates, http, operator, tags


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)

        # Required fields.
        required = ["linkId", "parentId", "authoredAt", "nonce", "beaconBlock",
                    "isRecap", "coversFromId", "coversToId", "author", "text", "authorSig"]
        for f in required:
            if f not in body:
                return http.bad_request("missing_field", f)

        link_id      = _normalize_uint(body["linkId"])
        parent_id    = _normalize_uint(body["parentId"])
        authored_at  = int(body["authoredAt"])
        nonce        = int(body["nonce"])
        beacon_block = int(body["beaconBlock"])
        is_recap     = bool(body["isRecap"])
        covers_from  = _normalize_uint(body["coversFromId"])
        covers_to    = _normalize_uint(body["coversToId"])
        author       = body["author"].lower()
        text         = body["text"]

        if not text:
            return http.bad_request("text_empty")
        if len(text.encode("utf-8")) > 1000:
            return http.bad_request("text_too_long")
        if is_recap and covers_from > covers_to:
            return http.bad_request("invalid_recap_range")

        # Citizen check.
        citizen = db.fetch_citizen(author)
        if not citizen or not citizen.get("isRegistered"):
            return http.forbidden("not_citizen")
        if citizen.get("isBanned"):
            return http.forbidden("banned")

        # Beacon block freshness: reject if claimed block is ahead of the
        # chain tip or too old. See MAINNET_PLAN.md §3.3.
        ok, reason = gates.beacon_is_fresh(beacon_block)
        if not ok:
            return http.bad_request("stale_beacon_block", reason or "")

        # Per-citizen daily write cap (see MAINNET_PLAN.md §8.1).
        allowed, count = gates.check_and_bump_daily(author)
        if not allowed:
            return http.forbidden("daily_cap_hit", f"{gates.DAILY_WRITE_CAP} writes/day")

        # Verify author signature against the reconstructed digest.
        msg = {
            "linkId":       link_id,
            "parentId":     parent_id,
            "authoredAt":   authored_at,
            "nonce":        nonce,
            "beaconBlock":  beacon_block,
            "isRecap":      is_recap,
            "coversFromId": covers_from,
            "coversToId":   covers_to,
            "author":       author,
            "text":         text,
        }
        chain_id = int(os.environ["CHAIN_ID"])
        contract = os.environ["CONTRACT_ADDRESS"]
        digest = eip712.link_digest(chain_id, contract, msg)

        author_sig = eip712.Sig.from_packed_hex(body["authorSig"])
        if not eip712.verify_sig(digest, author_sig, author):
            return http.unauthorized("bad_author_signature")

        # Atomic nonce bump. If this fails, someone is racing or replaying.
        if not db.bump_nonce_atomic(author, nonce):
            return http.bad_request("nonce_conflict")

        # Co-sign with operator key.
        operator_sig = operator.cosign(digest)

        # Archive bundle in S3 (immutable record).
        bundle = {
            "type":        "Link",
            "payload":     msg,
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "receivedAt":  int(time.time()),
        }
        db.archive_bundle(author, nonce, bundle)

        # Upsert link row in DynamoDB.
        link_id_hex = _hex_id(link_id)
        db.links_table().put_item(Item={
            "linkId":      link_id_hex,
            "parentId":    _hex_id(parent_id),
            "author":      author,
            "text":        text,
            "authoredAt":  Decimal(authored_at),
            "nonce":       Decimal(nonce),
            "beaconBlock": Decimal(beacon_block),
            "isRecap":     is_recap,
            "coversFromId": _hex_id(covers_from),
            "coversToId":   _hex_id(covers_to),
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "cleared":     False,
            "collected":   False,
            "voteCount":   Decimal(0),
            "createdAt":   Decimal(int(time.time())),
        })

        # Extract entity mentions and arc references from the text, write
        # one row per tag into the respective mention tables. IDs are stored
        # as strings even if no matching entity/arc exists yet (first-wins
        # resolution happens at render time).
        entity_ids, arc_ids = tags.extract_all(text)
        now_ts = Decimal(int(time.time()))
        for eid in entity_ids:
            db.entity_mentions_table().put_item(Item={
                "entityId":   eid,
                "linkId":     link_id_hex,
                "createdAt":  now_ts,
            })
        for aid in arc_ids:
            db.arc_references_table().put_item(Item={
                "arcId":      aid,
                "linkId":     link_id_hex,
                "createdAt":  now_ts,
            })

        return http.ok({
            "ok": True,
            "linkId": link_id_hex,
            "mentions": {"entities": entity_ids, "arcs": arc_ids},
        })

    except Exception as e:
        return http.server_error(str(e))


def _normalize_uint(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v)
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s)


def _hex_id(v: int) -> str:
    """Sequential integer as canonical decimal string.

    Kept named _hex_id for API stability, but IDs are now decimal numbers
    (0, 1, 2, ...) rather than hex hashes.
    """
    return str(v)
