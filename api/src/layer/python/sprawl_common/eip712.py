"""EIP-712 digest computation and signature verification for Sprawl.

Matches the typehashes and domain separator baked into the Sprawl.sol
contract (see MAINNET_PLAN.md §3).

The module is the single source of truth on the API side. The contract
reconstructs the digest from the exact same fields during collection,
so any drift between this file and Sprawl.sol's typehashes breaks
collection. Tested via round-trip against a forge test that signs and
recovers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

from eth_account import Account
from eth_account._utils.signing import to_standard_v
from eth_account.messages import encode_typed_data
from eth_hash.auto import keccak
from eth_utils import to_bytes, to_checksum_address, to_int

# ----- Domain ---------------------------------------------------------------

DOMAIN_NAME = "Sprawl"
DOMAIN_VERSION = "1"


def domain(chain_id: int, verifying_contract: str) -> dict:
    return {
        "name": DOMAIN_NAME,
        "version": DOMAIN_VERSION,
        "chainId": chain_id,
        "verifyingContract": to_checksum_address(verifying_contract),
    }


# ----- Typed data structures matching Sprawl.sol ---------------------------
#
# Every struct definition below must match the typehash string in
# Sprawl.sol exactly, including field order. The eth_account library hashes
# these using the EIP-712 encoding rules; the contract does the same via
# abi.encode + keccak256.


LINK_TYPES = {
    "Link": [
        {"name": "linkId",       "type": "uint256"},
        {"name": "parentId",     "type": "uint256"},
        {"name": "authoredAt",   "type": "uint64"},
        {"name": "nonce",        "type": "uint64"},
        {"name": "beaconBlock",  "type": "uint64"},
        {"name": "isRecap",      "type": "bool"},
        {"name": "coversFromId", "type": "uint256"},
        {"name": "coversToId",   "type": "uint256"},
        {"name": "author",       "type": "address"},
        {"name": "text",         "type": "string"},
    ],
}

ENTITY_TYPES = {
    "Entity": [
        {"name": "entityId",    "type": "string"},
        {"name": "name",        "type": "string"},
        {"name": "entityType",  "type": "string"},
        {"name": "description", "type": "string"},
        {"name": "authoredAt",  "type": "uint64"},
        {"name": "nonce",       "type": "uint64"},
        {"name": "beaconBlock", "type": "uint64"},
        {"name": "author",      "type": "address"},
    ],
}

ARC_TYPES = {
    "Arc": [
        {"name": "arcId",        "type": "string"},
        {"name": "anchorLinkId", "type": "uint256"},
        {"name": "description",  "type": "string"},
        {"name": "authoredAt",   "type": "uint64"},
        {"name": "nonce",        "type": "uint64"},
        {"name": "beaconBlock",  "type": "uint64"},
        {"name": "author",       "type": "address"},
    ],
}

VOTE_TYPES = {
    "Vote": [
        {"name": "linkId",      "type": "uint256"},
        {"name": "votedAt",     "type": "uint64"},
        {"name": "nonce",       "type": "uint64"},
        {"name": "beaconBlock", "type": "uint64"},
        {"name": "voter",       "type": "address"},
    ],
}

PROFILE_TYPES = {
    "RenameProfile": [
        {"name": "displayName", "type": "string"},
        {"name": "changedAt",   "type": "uint64"},
        {"name": "nonce",       "type": "uint64"},
        {"name": "beaconBlock", "type": "uint64"},
        {"name": "citizen",     "type": "address"},
    ],
}


# ----- Signature model -----------------------------------------------------


@dataclass
class Sig:
    r: bytes        # 32 bytes
    s: bytes        # 32 bytes
    v: int          # 27 or 28

    def to_rsv_dict(self) -> dict:
        return {"r": "0x" + self.r.hex(), "s": "0x" + self.s.hex(), "v": self.v}

    def to_packed_hex(self) -> str:
        """abi.encodePacked(r, s, v) — 65 bytes, matches on-chain storage."""
        return "0x" + self.r.hex() + self.s.hex() + bytes([self.v]).hex()

    @classmethod
    def from_packed_hex(cls, hex_str: str) -> "Sig":
        b = to_bytes(hexstr=hex_str)
        if len(b) != 65:
            raise ValueError(f"signature must be 65 bytes, got {len(b)}")
        return cls(r=b[0:32], s=b[32:64], v=b[64])

    @classmethod
    def from_rsv_dict(cls, d: dict) -> "Sig":
        r = to_bytes(hexstr=d["r"]) if isinstance(d["r"], str) else d["r"]
        s = to_bytes(hexstr=d["s"]) if isinstance(d["s"], str) else d["s"]
        v = int(d["v"])
        return cls(r=r, s=s, v=v)


# ----- Digest + sign + recover ---------------------------------------------


def _full_typed_message(types: dict, primary: str, domain_data: dict, message: dict) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name",              "type": "string"},
                {"name": "version",           "type": "string"},
                {"name": "chainId",           "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **types,
        },
        "primaryType": primary,
        "domain": domain_data,
        "message": message,
    }


def digest(types: dict, primary: str, chain_id: int, contract: str, message: dict) -> bytes:
    """Compute the 32-byte EIP-712 digest for a typed message."""
    full = _full_typed_message(types, primary, domain(chain_id, contract), message)
    signable = encode_typed_data(full_message=full)
    # The SignableMessage's .body is already keccak(\x19\x01 || domainSep || structHash)
    # when using encode_typed_data. But we want the digest specifically to
    # compare to ecrecover, so we recompute explicitly.
    #
    # encode_typed_data returns a SignableMessage where .body is
    # b"\x19\x01" + domainSep + structHash before final hash. We hash it.
    return keccak(b"\x19\x01" + signable.header + signable.body)


def sign(private_key: bytes, typed_digest: bytes) -> Sig:
    """Sign a 32-byte digest with a private key. Returns (r, s, v).

    We already have the correct EIP-712 digest (0x1901 || domainSep || structHash),
    so we want raw-hash signing. unsafe_sign_hash is the eth-account 0.13+ API
    for this; the "unsafe" in the name just flags that the caller is responsible
    for digest construction, which we are.
    """
    acct = Account.from_key(private_key)
    signed = acct.unsafe_sign_hash(typed_digest)
    v = signed.v
    # Normalize to 27/28 for on-chain use.
    if v < 27:
        v += 27
    return Sig(
        r=signed.r.to_bytes(32, "big"),
        s=signed.s.to_bytes(32, "big"),
        v=v,
    )


def recover(typed_digest: bytes, sig: Sig) -> str:
    """Recover the signer's checksummed address from a digest + signature."""
    try:
        recovered = Account._recover_hash(
            typed_digest,
            vrs=(sig.v, int.from_bytes(sig.r, "big"), int.from_bytes(sig.s, "big")),
        )
        return to_checksum_address(recovered)
    except Exception:
        return "0x0000000000000000000000000000000000000000"


# ----- Convenience wrappers ------------------------------------------------


def link_digest(chain_id: int, contract: str, msg: dict) -> bytes:
    return digest(LINK_TYPES, "Link", chain_id, contract, msg)


def entity_digest(chain_id: int, contract: str, msg: dict) -> bytes:
    return digest(ENTITY_TYPES, "Entity", chain_id, contract, msg)


def arc_digest(chain_id: int, contract: str, msg: dict) -> bytes:
    return digest(ARC_TYPES, "Arc", chain_id, contract, msg)


def vote_digest(chain_id: int, contract: str, msg: dict) -> bytes:
    return digest(VOTE_TYPES, "Vote", chain_id, contract, msg)


def profile_digest(chain_id: int, contract: str, msg: dict) -> bytes:
    return digest(PROFILE_TYPES, "RenameProfile", chain_id, contract, msg)


# ----- Validation helpers --------------------------------------------------


def verify_sig(typed_digest: bytes, sig: Sig, expected: str) -> bool:
    expected_cs = to_checksum_address(expected)
    recovered = recover(typed_digest, sig)
    if recovered == "0x0000000000000000000000000000000000000000":
        return False
    return recovered == expected_cs
