"""Goldsky subgraph client.

Replaces the in-house indexer. Queries on-chain state (registrations,
bans, collection events, sales) from the subgraph via GraphQL.

The subgraph URL lives in the SUBGRAPH_URL env var set by the SAM template.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Optional


def _graphql(query: str, variables: Optional[dict] = None) -> dict:
    url = os.environ["SUBGRAPH_URL"]
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "sprawl-api/1.0",
            "Accept":       "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    if "errors" in data:
        raise RuntimeError(f"subgraph errors: {data['errors']}")
    return data.get("data") or {}


def fetch_citizen_onchain(address: str) -> Optional[dict]:
    """Return the citizen's on-chain registry record, or None if not registered.

    Shape: {id, name, isBanned, registeredAt}
    """
    q = """
    query($id: ID!) {
      citizen(id: $id) {
        id
        name
        isBanned
        registeredAt
      }
    }
    """
    data = _graphql(q, {"id": address.lower()})
    return data.get("citizen")


def current_operator() -> Optional[str]:
    """Return the contract's current operator address from the subgraph."""
    q = """
    query {
      protocolStats(id: "global") { currentOperator }
    }
    """
    try:
        data = _graphql(q)
        stats = data.get("protocolStats")
        return stats and stats.get("currentOperator")
    except Exception:
        return None
