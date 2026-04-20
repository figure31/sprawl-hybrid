"""DynamoDB + S3 helpers.

Thin wrappers around boto3 with consistent error handling and the table
names our API assumes (set via env vars from the SAM template).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Iterable, Optional

import boto3
from boto3.dynamodb.conditions import Key


_ddb = None
_s3 = None


def ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb")
    return _ddb


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


# ----- Table handles -------------------------------------------------------


def citizens_table():
    return ddb().Table(os.environ["CITIZENS_TABLE"])


def links_table():
    return ddb().Table(os.environ["LINKS_TABLE"])


def entities_table():
    return ddb().Table(os.environ["ENTITIES_TABLE"])


def arcs_table():
    return ddb().Table(os.environ["ARCS_TABLE"])


def votes_table():
    return ddb().Table(os.environ["VOTES_TABLE"])


def entity_mentions_table():
    return ddb().Table(os.environ["ENTITY_MENTIONS_TABLE"])


def arc_references_table():
    return ddb().Table(os.environ["ARC_REFERENCES_TABLE"])


def counters_table():
    return ddb().Table(os.environ["COUNTERS_TABLE"])


def next_counter(name: str) -> int:
    """Atomically increment a named counter and return the new value.

    Used by /next-link-id to hand out sequential IDs without races.
    """
    resp = counters_table().update_item(
        Key={"key": name},
        UpdateExpression="ADD #v :one",
        ExpressionAttributeNames={"#v": "value"},
        ExpressionAttributeValues={":one": Decimal(1)},
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["value"])


# ----- Nonce management ----------------------------------------------------


def bump_nonce_atomic(address: str, submitted_nonce: int) -> bool:
    """Atomically assert submitted_nonce == lastNonce + 1 and update.

    Returns True on success, False if the submitted nonce doesn't equal
    (current last + 1). DynamoDB's ConditionExpression does not support
    arithmetic, so we compute the expected prior value client-side.
    """
    try:
        citizens_table().update_item(
            Key={"address": address.lower()},
            UpdateExpression="SET lastNonce = :new",
            ConditionExpression="(attribute_not_exists(lastNonce) AND :new = :one) OR lastNonce = :prev",
            ExpressionAttributeValues={
                ":new":  Decimal(submitted_nonce),
                ":prev": Decimal(submitted_nonce - 1),
                ":one":  Decimal(1),
            },
        )
        return True
    except Exception:
        return False


def fetch_citizen(address: str) -> Optional[dict]:
    """Return a merged view of a citizen.

    On-chain fields (registered, banned, name) come from Goldsky.
    Off-chain fields (lastNonce, rate-limit counters) come from DynamoDB.
    If the DynamoDB row doesn't exist yet we auto-create it on first read
    so downstream nonce checks can run.
    """
    from . import subgraph
    onchain = subgraph.fetch_citizen_onchain(address)
    if not onchain:
        return None  # not registered
    # Ensure local row exists with baseline off-chain state.
    resp = citizens_table().get_item(Key={"address": address.lower()})
    local = resp.get("Item") or {}
    if not local:
        citizens_table().put_item(Item={
            "address":    address.lower(),
            "lastNonce":  Decimal(0),
        })
        local = {"address": address.lower(), "lastNonce": Decimal(0)}
    return {
        "address":       address.lower(),
        "name":          onchain.get("name", ""),
        "isRegistered":  True,
        "isBanned":      bool(onchain.get("isBanned", False)),
        "lastNonce":     int(local.get("lastNonce", 0)),
        "writesToday":   int(local.get("writesToday", 0)),
        "writesBucket":  int(local.get("writesBucket", 0)),
    }


# ----- S3 archive ----------------------------------------------------------


def archive_bundle(address: str, nonce: int, bundle: dict) -> str:
    """Drop a signed bundle into the S3 archive as an immutable object."""
    bucket = os.environ["ARCHIVE_BUCKET"]
    key = f"bundles/{address.lower()}/{nonce:020d}.json"
    s3().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(bundle, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{key}"


# ----- JSON serialization --------------------------------------------------


class _DecimalAwareEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def to_json(obj: Any) -> str:
    return json.dumps(obj, cls=_DecimalAwareEncoder, separators=(",", ":"))


def from_decimals(obj: Any) -> Any:
    """Recursively convert Decimal values to int/float for clean JSON output."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: from_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_decimals(v) for v in obj]
    return obj
