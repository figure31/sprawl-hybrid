"""POST /admin/clear/{kind}/{id} — off-chain soft-clear for uncollected content.

Gated by an admin signature. The on-chain `clearLink` / `clearEntity` / `clearArc`
calls are separate and are what the indexer mirrors into DynamoDB for
already-collected items. This endpoint is purely for uncollected links,
entities, or arcs sitting in our DB.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from eth_hash.auto import keccak
from eth_utils import to_checksum_address

from sprawl_common import db, eip712, http


def lambda_handler(event, context):
    try:
        body = http.parse_body(event)
        pp = event.get("pathParameters") or {}
        kind = pp.get("kind", "").lower()
        asset_id = pp.get("id", "")

        admin = os.environ.get("ADMIN_ADDRESS", "").lower()
        if not admin:
            return http.server_error("admin_not_configured")

        sig = eip712.Sig.from_packed_hex(body.get("adminSig", ""))
        # Admin signs a simple ClearRequest typed struct.
        digest = _clear_digest(kind, asset_id, int(body.get("nonce", 0)))
        if not eip712.verify_sig(digest, sig, admin):
            return http.unauthorized("bad_admin_signature")

        if kind == "link":
            db.links_table().update_item(
                Key={"linkId": asset_id},
                UpdateExpression="SET cleared = :c, #t = :empty, clearedAt = :now",
                ExpressionAttributeNames={"#t": "text"},
                ExpressionAttributeValues={":c": True, ":empty": "", ":now": Decimal(int(time.time()))},
            )
        elif kind == "entity":
            db.entities_table().update_item(
                Key={"entityId": asset_id},
                UpdateExpression="SET cleared = :c, description = :empty, clearedAt = :now",
                ExpressionAttributeValues={":c": True, ":empty": "", ":now": Decimal(int(time.time()))},
            )
        elif kind == "arc":
            db.arcs_table().update_item(
                Key={"arcId": asset_id},
                UpdateExpression="SET cleared = :c, description = :empty, clearedAt = :now",
                ExpressionAttributeValues={":c": True, ":empty": "", ":now": Decimal(int(time.time()))},
            )
        else:
            return http.bad_request("unknown_kind")

        return http.ok({"ok": True})

    except Exception as e:
        return http.server_error(str(e))


def _clear_digest(kind: str, asset_id: str, nonce: int) -> bytes:
    # Simple hash domain for admin actions.
    msg = f"clear|{kind}|{asset_id}|{nonce}".encode("utf-8")
    return keccak(b"\x19Sprawl admin:\n" + msg)
