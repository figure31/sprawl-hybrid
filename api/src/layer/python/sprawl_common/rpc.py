"""Minimal JSON-RPC client for the indexer.

We don't pull in web3.py because it's heavy. A hand-rolled eth_getLogs
+ eth_blockNumber client covers everything the indexer Lambda needs.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


def _rpc(method: str, params: list) -> Any:
    url = os.environ["RPC_URL"]
    req = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={
            "Content-Type": "application/json",
            # Explicit UA: many RPCs (Cloudflare-fronted like drpc.org) reject
            # the default Python-urllib/3.x UA with 403.
            "User-Agent": "sprawl-indexer/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"rpc {method} failed: {data['error']}")
    return data["result"]


def block_number() -> int:
    return int(_rpc("eth_blockNumber", []), 16)


def get_logs(from_block: int, to_block: int, address: str, topics: list | None = None) -> list:
    params = [{
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": address,
    }]
    if topics is not None:
        params[0]["topics"] = topics
    return _rpc("eth_getLogs", params)


def call(to: str, data: str, block: str = "latest") -> str:
    return _rpc("eth_call", [{"to": to, "data": data}, block])
