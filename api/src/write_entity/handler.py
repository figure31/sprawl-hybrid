"""POST /entities"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from eth_hash.auto import keccak

from sprawl_common import db, eip712, gates, http, operator


VALID_TYPES = {"character", "place", "object", "event"}


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)
        required = ["entityId", "name", "entityType", "description",
                    "authoredAt", "nonce", "beaconBlock", "author", "authorSig"]
        for f in required:
            if f not in body:
                return http.bad_request("missing_field", f)

        entity_id   = body["entityId"]
        name        = body["name"]
        entity_type = body["entityType"]
        description = body.get("description", "")
        authored_at = int(body["authoredAt"])
        nonce       = int(body["nonce"])
        beacon      = int(body["beaconBlock"])
        author      = body["author"].lower()

        if not entity_id: return http.bad_request("entity_id_empty")
        if len(entity_id.encode("utf-8")) > 64: return http.bad_request("entity_id_too_long")
        if not name: return http.bad_request("entity_name_empty")
        if len(name.encode("utf-8")) > 128: return http.bad_request("entity_name_too_long")
        if entity_type not in VALID_TYPES: return http.bad_request("invalid_entity_type", entity_type)
        if len(description.encode("utf-8")) > 500: return http.bad_request("entity_description_too_long")

        citizen = db.fetch_citizen(author)
        if not citizen or not citizen.get("isRegistered"): return http.forbidden("not_citizen")
        if citizen.get("isBanned"): return http.forbidden("banned")

        ok, reason = gates.beacon_is_fresh(beacon)
        if not ok: return http.bad_request("stale_beacon_block", reason or "")

        allowed, count = gates.check_and_bump_daily(author)
        if not allowed: return http.forbidden("daily_cap_hit", f"{gates.DAILY_WRITE_CAP} writes/day")

        # Reject if already written (pre-collection dedup).
        existing = db.entities_table().get_item(Key={"entityId": entity_id})
        if existing.get("Item"): return http.bad_request("entity_already_exists")

        msg = {
            "entityId":    entity_id,
            "name":        name,
            "entityType":  entity_type,
            "description": description,
            "authoredAt":  authored_at,
            "nonce":       nonce,
            "beaconBlock": beacon,
            "author":      author,
        }
        chain_id = int(os.environ["CHAIN_ID"])
        contract = os.environ["CONTRACT_ADDRESS"]
        digest = eip712.entity_digest(chain_id, contract, msg)

        author_sig = eip712.Sig.from_packed_hex(body["authorSig"])
        if not eip712.verify_sig(digest, author_sig, author):
            return http.unauthorized("bad_author_signature")

        if not db.bump_nonce_atomic(author, nonce):
            return http.bad_request("nonce_conflict")

        operator_sig = operator.cosign(digest)

        bundle = {
            "type":        "Entity",
            "payload":     msg,
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "receivedAt":  int(time.time()),
        }
        db.archive_bundle(author, nonce, bundle)

        entity_id_hash = "0x" + keccak(entity_id.encode("utf-8")).hex()
        db.entities_table().put_item(Item={
            "entityId":      entity_id,
            "entityIdHash":  entity_id_hash,
            "name":          name,
            "entityType":    entity_type,
            "description":   description,
            "creator":       author,
            "authoredAt":    Decimal(authored_at),
            "nonce":         Decimal(nonce),
            "beaconBlock":   Decimal(beacon),
            "authorSig":     author_sig.to_packed_hex(),
            "operatorSig":   operator_sig.to_packed_hex(),
            "cleared":       False,
            "collected":     False,
            "createdAt":     Decimal(int(time.time())),
        })

        return http.ok({"ok": True, "entityId": entity_id})

    except Exception as e:
        return http.server_error(str(e))
