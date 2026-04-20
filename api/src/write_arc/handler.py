"""POST /arcs"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from eth_hash.auto import keccak

from sprawl_common import db, eip712, gates, http, operator


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)
        required = ["arcId", "anchorLinkId", "description",
                    "authoredAt", "nonce", "beaconBlock", "author", "authorSig"]
        for f in required:
            if f not in body:
                return http.bad_request("missing_field", f)

        arc_id        = body["arcId"]
        anchor_link   = _normalize_uint(body["anchorLinkId"])
        description   = body["description"]
        authored_at   = int(body["authoredAt"])
        nonce         = int(body["nonce"])
        beacon        = int(body["beaconBlock"])
        author        = body["author"].lower()

        if not arc_id: return http.bad_request("arc_id_empty")
        if len(arc_id.encode("utf-8")) > 64: return http.bad_request("arc_id_too_long")
        if not description: return http.bad_request("arc_description_empty")
        if len(description.encode("utf-8")) > 500: return http.bad_request("arc_description_too_long")

        citizen = db.fetch_citizen(author)
        if not citizen or not citizen.get("isRegistered"): return http.forbidden("not_citizen")
        if citizen.get("isBanned"): return http.forbidden("banned")

        ok, reason = gates.beacon_is_fresh(beacon)
        if not ok: return http.bad_request("stale_beacon_block", reason or "")

        allowed, count = gates.check_and_bump_daily(author)
        if not allowed: return http.forbidden("daily_cap_hit", f"{gates.DAILY_WRITE_CAP} writes/day")

        # Anchor link must exist (collected or uncollected). Not strict
        # existence-in-tree — the contract enforces collected-anchor at
        # actual collect time. Here we only require the anchor link has
        # at least been written off-chain.
        anchor_row = db.links_table().get_item(Key={"linkId": _hex_id(anchor_link)})
        if not anchor_row.get("Item"):
            return http.bad_request("anchor_link_unknown")

        existing = db.arcs_table().get_item(Key={"arcId": arc_id})
        if existing.get("Item"): return http.bad_request("arc_already_exists")

        msg = {
            "arcId":        arc_id,
            "anchorLinkId": anchor_link,
            "description":  description,
            "authoredAt":   authored_at,
            "nonce":        nonce,
            "beaconBlock":  beacon,
            "author":       author,
        }
        chain_id = int(os.environ["CHAIN_ID"])
        contract = os.environ["CONTRACT_ADDRESS"]
        digest = eip712.arc_digest(chain_id, contract, msg)

        author_sig = eip712.Sig.from_packed_hex(body["authorSig"])
        if not eip712.verify_sig(digest, author_sig, author):
            return http.unauthorized("bad_author_signature")

        if not db.bump_nonce_atomic(author, nonce):
            return http.bad_request("nonce_conflict")

        operator_sig = operator.cosign(digest)

        bundle = {
            "type":        "Arc",
            "payload":     msg,
            "authorSig":   author_sig.to_packed_hex(),
            "operatorSig": operator_sig.to_packed_hex(),
            "receivedAt":  int(time.time()),
        }
        db.archive_bundle(author, nonce, bundle)

        arc_id_hash = "0x" + keccak(arc_id.encode("utf-8")).hex()
        db.arcs_table().put_item(Item={
            "arcId":        arc_id,
            "arcIdHash":    arc_id_hash,
            "anchorLinkId": _hex_id(anchor_link),
            "description":  description,
            "creator":      author,
            "authoredAt":   Decimal(authored_at),
            "nonce":        Decimal(nonce),
            "beaconBlock":  Decimal(beacon),
            "authorSig":    author_sig.to_packed_hex(),
            "operatorSig":  operator_sig.to_packed_hex(),
            "cleared":      False,
            "collected":    False,
            "createdAt":    Decimal(int(time.time())),
        })

        return http.ok({"ok": True, "arcId": arc_id})

    except Exception as e:
        return http.server_error(str(e))


def _normalize_uint(v) -> int:
    if isinstance(v, int): return v
    s = str(v)
    if s.startswith("0x") or s.startswith("0X"): return int(s, 16)
    return int(s)


def _hex_id(v: int) -> str:
    return str(v)
