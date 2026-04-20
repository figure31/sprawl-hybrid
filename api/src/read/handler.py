"""GET /* — single handler for every read route.

Dispatches on the path, queries DynamoDB, returns JSON with cache
headers that CloudFront respects.
"""

from __future__ import annotations

import os
from boto3.dynamodb.conditions import Key

from sprawl_common import db, http


def lambda_handler(event, context):
    try:
        path = event.get("resource", "")
        pp   = event.get("pathParameters") or {}
        qp   = event.get("queryStringParameters") or {}

        limit = int(qp.get("limit", "50"))
        limit = max(1, min(limit, 500))

        if path == "/citizens":
            return _list_citizens(limit)
        if path == "/citizens/{address}":
            return _citizen(pp.get("address", "").lower())
        if path == "/citizens/{address}/stats":
            return _citizen_stats(pp.get("address", "").lower())
        if path == "/citizens/by-name/{name}":
            return _citizen_by_name(pp.get("name", ""))
        if path == "/next-link-id":
            return _next_link_id()
        if path == "/links/{linkId}":
            return _link(pp.get("linkId", ""))
        if path == "/links/{linkId}/children":
            return _link_children(pp.get("linkId", ""), limit)
        if path == "/links/{linkId}/context":
            return _link_context(pp.get("linkId", ""))
        if path == "/entities":
            return _entity_list(limit)
        if path == "/entities/{entityId}":
            return _entity(pp.get("entityId", ""))
        if path == "/entities/by-type/{type}":
            return _entity_by_type(pp.get("type", ""), limit)
        if path == "/entities/{entityId}/mentions":
            return _entity_mentions(pp.get("entityId", ""), limit)
        if path == "/arcs":
            return _arc_list(limit)
        if path == "/arcs/{arcId}":
            return _arc(pp.get("arcId", ""))
        if path == "/arcs/by-anchor/{linkId}":
            return _arc_by_anchor(pp.get("linkId", ""), limit)
        if path == "/arcs/{arcId}/references":
            return _arc_references(pp.get("arcId", ""), limit)
        if path == "/search":
            return _search(qp.get("q", ""), limit)
        if path == "/votes/by-link/{linkId}":
            return _votes_by_link(pp.get("linkId", ""), limit)
        if path == "/votes/by-voter/{address}":
            return _votes_by_voter(pp.get("address", "").lower(), limit)
        if path == "/feed/recent-links":
            return _recent_links(limit)
        if path == "/feed/by-author/{address}":
            return _feed_by_author(pp.get("address", "").lower(), limit)

        return http.not_found("unknown_route", path)

    except Exception as e:
        return http.server_error(str(e))


# ----- Helpers -------------------------------------------------------------


CACHE_SHORT = 10
CACHE_MEDIUM = 30
CACHE_LONG = 120


def _citizen(address: str):
    item = db.fetch_citizen(address)
    if not item:
        return http.not_found("citizen_not_found")
    return http.ok(db.from_decimals(item), CACHE_MEDIUM)


def _citizen_by_name(name: str):
    resp = db.citizens_table().query(
        IndexName="byName",
        KeyConditionExpression=Key("name").eq(name),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return http.not_found("citizen_not_found")
    return http.ok(db.from_decimals(items[0]), CACHE_MEDIUM)


def _list_citizens(limit: int):
    resp = db.citizens_table().scan(Limit=limit)
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_MEDIUM)


def _citizen_stats(address: str):
    """Draft counts authored by a citizen (off-chain writes)."""
    def count_by(table, index, key_name):
        resp = table.query(
            IndexName=index,
            KeyConditionExpression=Key(key_name).eq(address),
            Select="COUNT",
        )
        return int(resp.get("Count", 0))

    links_total = count_by(db.links_table(),    "byAuthor",  "author")
    # Distinguish recaps from links if we want; for now lump under "links".
    entities    = count_by(db.entities_table(), "byCreator", "creator")
    arcs        = count_by(db.arcs_table(),     "byCreator", "creator")
    votes       = count_by(db.votes_table(),    "byVoter",   "voter")
    return http.ok({
        "address":      address,
        "totalLinks":    links_total,
        "totalEntities": entities,
        "totalArcs":     arcs,
        "totalVotes":    votes,
    }, CACHE_SHORT)


def _next_link_id():
    n = db.next_counter("linkId")
    return http.ok({"linkId": n}, cache_seconds=0)


def _link(link_id: str):
    resp = db.links_table().get_item(Key={"linkId": link_id})
    if "Item" not in resp:
        return http.not_found("link_not_found")
    return http.ok(db.from_decimals(resp["Item"]), CACHE_SHORT)


def _link_children(parent_id: str, limit: int):
    resp = db.links_table().query(
        IndexName="byParent",
        KeyConditionExpression=Key("parentId").eq(parent_id),
        Limit=limit,
        ScanIndexForward=True,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_SHORT)


def _link_context(link_id: str):
    # Return the link + parent chain up to root. Bounded walk to prevent
    # runaway queries.
    ancestors = []
    current = link_id
    for _ in range(32):
        resp = db.links_table().get_item(Key={"linkId": current})
        item = resp.get("Item")
        if not item:
            break
        ancestors.append(db.from_decimals(item))
        parent = item.get("parentId")
        if not parent or parent == "0x0" or parent == current:
            break
        current = parent
    return http.ok({"ancestors": ancestors}, CACHE_SHORT)


def _entity(entity_id: str):
    resp = db.entities_table().get_item(Key={"entityId": entity_id})
    if "Item" not in resp:
        return http.not_found("entity_not_found")
    return http.ok(db.from_decimals(resp["Item"]), CACHE_MEDIUM)


def _entity_by_type(etype: str, limit: int):
    resp = db.entities_table().query(
        IndexName="byType",
        KeyConditionExpression=Key("entityType").eq(etype),
        Limit=limit,
        ScanIndexForward=False,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_MEDIUM)


def _arc(arc_id: str):
    resp = db.arcs_table().get_item(Key={"arcId": arc_id})
    if "Item" not in resp:
        return http.not_found("arc_not_found")
    return http.ok(db.from_decimals(resp["Item"]), CACHE_MEDIUM)


def _arc_by_anchor(link_id: str, limit: int):
    resp = db.arcs_table().query(
        IndexName="byAnchor",
        KeyConditionExpression=Key("anchorLinkId").eq(link_id),
        Limit=limit,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_MEDIUM)


def _votes_by_link(link_id: str, limit: int):
    resp = db.votes_table().query(
        KeyConditionExpression=Key("linkId").eq(link_id),
        Limit=limit,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_SHORT)


def _votes_by_voter(voter: str, limit: int):
    resp = db.votes_table().query(
        IndexName="byVoter",
        KeyConditionExpression=Key("voter").eq(voter),
        Limit=limit,
        ScanIndexForward=False,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_SHORT)


def _recent_links(limit: int):
    # Full scan of the links table sorted by createdAt is possible but
    # cost-risky at scale. For v1 we scan + sort client-side; at scale we'd
    # add a dedicated GSI partitioned by a time bucket.
    resp = db.links_table().scan(Limit=limit * 4)
    items = sorted(
        resp.get("Items", []),
        key=lambda x: int(x.get("createdAt", 0)),
        reverse=True,
    )[:limit]
    return http.ok({"items": db.from_decimals(items)}, CACHE_SHORT)


def _feed_by_author(author: str, limit: int):
    resp = db.links_table().query(
        IndexName="byAuthor",
        KeyConditionExpression=Key("author").eq(author),
        Limit=limit,
        ScanIndexForward=False,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_MEDIUM)


def _entity_list(limit: int):
    resp = db.entities_table().scan(Limit=limit)
    items = sorted(
        resp.get("Items", []),
        key=lambda x: int(x.get("authoredAt", 0)),
        reverse=True,
    )[:limit]
    return http.ok({"items": db.from_decimals(items)}, CACHE_MEDIUM)


def _arc_list(limit: int):
    resp = db.arcs_table().scan(Limit=limit)
    items = sorted(
        resp.get("Items", []),
        key=lambda x: int(x.get("authoredAt", 0)),
        reverse=True,
    )[:limit]
    return http.ok({"items": db.from_decimals(items)}, CACHE_MEDIUM)


def _entity_mentions(entity_id: str, limit: int):
    resp = db.entity_mentions_table().query(
        KeyConditionExpression=Key("entityId").eq(entity_id),
        Limit=limit,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_SHORT)


def _arc_references(arc_id: str, limit: int):
    resp = db.arc_references_table().query(
        KeyConditionExpression=Key("arcId").eq(arc_id),
        Limit=limit,
    )
    return http.ok({"items": db.from_decimals(resp.get("Items", []))}, CACHE_SHORT)


def _search(query: str, limit: int):
    # Simple substring search via table scan + client-side filter.
    # Acceptable for small corpora; at scale we'd push this into OpenSearch
    # or DynamoDB full-text scan with contains().
    if not query or len(query) < 2:
        return http.bad_request("query_too_short", "minimum 2 characters")
    q = query.lower()
    resp = db.links_table().scan(Limit=500)
    hits = [
        item for item in resp.get("Items", [])
        if q in str(item.get("text", "")).lower() and not item.get("cleared")
    ][:limit]
    return http.ok({"items": db.from_decimals(hits), "query": query}, CACHE_SHORT)


def _global_stats():
    # Off-chain counts only. On-chain stats (citizens, collected assets,
    # sales, volume) live in the Goldsky subgraph; query ProtocolStats there.
    def count(tbl):
        resp = tbl.scan(Select="COUNT")
        return int(resp.get("Count", 0))

    return http.ok({
        "totalLinks":    count(db.links_table()),
        "totalEntities": count(db.entities_table()),
        "totalArcs":     count(db.arcs_table()),
        "totalVotes":    count(db.votes_table()),
    }, CACHE_LONG)
