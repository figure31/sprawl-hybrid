"""Operator key fetch + co-sign.

The operator private key lives in AWS Secrets Manager. Write-path Lambdas
fetch it once at cold start, cache in memory, and use it to co-sign every
accepted EIP-712 message. The Lambda container's filesystem never writes
it out.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import boto3
from eth_account import Account
from eth_utils import to_bytes, to_checksum_address

from . import eip712


_secrets_client = None


def _client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


@lru_cache(maxsize=1)
def _operator_private_key() -> bytes:
    name = os.environ["OPERATOR_SECRET_NAME"]
    resp = _client().get_secret_value(SecretId=name)
    raw = resp.get("SecretString") or resp["SecretBinary"].decode("utf-8")
    raw = raw.strip()
    # Accept either a raw 0x-prefixed hex key or a JSON wrapper {"key": "0x..."}
    if raw.startswith("{"):
        import json
        raw = json.loads(raw).get("key") or json.loads(raw).get("privateKey")
    if raw is None:
        raise RuntimeError("operator secret missing key material")
    return to_bytes(hexstr=raw)


def operator_address() -> str:
    return to_checksum_address(Account.from_key(_operator_private_key()).address)


def cosign(digest: bytes) -> eip712.Sig:
    """Sign a digest with the operator key. Returns the Sig struct."""
    return eip712.sign(_operator_private_key(), digest)
