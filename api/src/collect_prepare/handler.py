"""GET /collect/prepare/{kind}/{id}

Returns the complete bundle a client needs to submit the collect tx
on-chain: all original fields + author signature + operator co-signature,
structured as the contract expects. Mirrors what the S3 archive holds.
"""

from __future__ import annotations

from sprawl_common import db, http


def lambda_handler(event, context):
    try:
        pp = event.get("pathParameters") or {}
        kind = pp.get("kind", "").lower()
        asset_id = pp.get("id", "")

        if kind == "link":
            return _prepare_link(asset_id)
        if kind == "entity":
            return _prepare_entity(asset_id)
        if kind == "arc":
            return _prepare_arc(asset_id)
        return http.bad_request("unknown_kind", kind)

    except Exception as e:
        return http.server_error(str(e))


def _prepare_link(link_id: str):
    resp = db.links_table().get_item(Key={"linkId": link_id})
    item = resp.get("Item")
    if not item:
        return http.not_found("link_not_found")
    if item.get("collected"):
        return http.bad_request("already_collected")
    if item.get("cleared"):
        return http.forbidden("cleared")

    bundle = {
        "kind":         "link",
        "linkId":       str(item["linkId"]),
        "parentId":     str(item["parentId"]),
        "authoredAt":   int(item["authoredAt"]),
        "nonce":        int(item["nonce"]),
        "beaconBlock":  int(item["beaconBlock"]),
        "isRecap":      bool(item["isRecap"]),
        "coversFromId": str(item.get("coversFromId", "0x0")),
        "coversToId":   str(item.get("coversToId", "0x0")),
        "author":       item["author"],
        "text":         item["text"],
        "authorSig":    item["authorSig"],
        "operatorSig":  item["operatorSig"],
    }
    return http.ok(bundle, cache_seconds=60)


def _prepare_entity(entity_id: str):
    resp = db.entities_table().get_item(Key={"entityId": entity_id})
    item = resp.get("Item")
    if not item:
        return http.not_found("entity_not_found")
    if item.get("collected"):
        return http.bad_request("already_collected")
    if item.get("cleared"):
        return http.forbidden("cleared")

    bundle = {
        "kind":        "entity",
        "entityId":    item["entityId"],
        "name":        item["name"],
        "entityType":  item["entityType"],
        "description": item.get("description", ""),
        "authoredAt":  int(item["authoredAt"]),
        "nonce":       int(item["nonce"]),
        "beaconBlock": int(item["beaconBlock"]),
        "author":      item["creator"],
        "authorSig":   item["authorSig"],
        "operatorSig": item["operatorSig"],
    }
    return http.ok(bundle, cache_seconds=60)


def _prepare_arc(arc_id: str):
    resp = db.arcs_table().get_item(Key={"arcId": arc_id})
    item = resp.get("Item")
    if not item:
        return http.not_found("arc_not_found")
    if item.get("collected"):
        return http.bad_request("already_collected")
    if item.get("cleared"):
        return http.forbidden("cleared")

    bundle = {
        "kind":         "arc",
        "arcId":        item["arcId"],
        "anchorLinkId": str(item["anchorLinkId"]),
        "description":  item["description"],
        "authoredAt":   int(item["authoredAt"]),
        "nonce":        int(item["nonce"]),
        "beaconBlock":  int(item["beaconBlock"]),
        "author":       item["creator"],
        "authorSig":    item["authorSig"],
        "operatorSig":  item["operatorSig"],
    }
    return http.ok(bundle, cache_seconds=60)
