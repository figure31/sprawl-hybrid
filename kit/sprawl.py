"""Shared helpers for the Sprawl agent kit.

Loads config from kit/config.json, signs EIP-712 messages with the agent's
private key, submits POST requests to the API, and wraps `cast` for
on-chain actions. Also owns local workspace + thread bookkeeping.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from eth_hash.auto import keccak
    from eth_utils import to_bytes, to_checksum_address
except ImportError:
    sys.stderr.write(
        "error: eth_account is required. Install with:\n"
        "    pip install eth-account eth-hash[pycryptodome] eth-utils\n"
    )
    sys.exit(1)


# =====================================================================
# Paths
# =====================================================================

KIT_DIR       = Path(__file__).resolve().parent
CONFIG_PATH   = KIT_DIR / "config.json"
ENV_PATH      = KIT_DIR / ".env"
WORKSPACE_DIR = KIT_DIR / "workspace"
HISTORY_PATH  = WORKSPACE_DIR / "history.jsonl"
THREADS_DIR   = WORKSPACE_DIR / "threads"
STYLE_PATH    = WORKSPACE_DIR / "style.md"


# =====================================================================
# Constants
# =====================================================================

MAX_LINK_BYTES                = 1000
MAX_NAME_BYTES                = 64
MAX_ENTITY_ID_BYTES           = 64
MAX_ENTITY_NAME_BYTES         = 128
MAX_ENTITY_DESCRIPTION_BYTES  = 500
MAX_ARC_ID_BYTES              = 64
MAX_ARC_DESCRIPTION_BYTES     = 500

ENTITY_TYPES = {"character", "place", "object", "event"}
ID_PATTERN   = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ASSET_LINK, ASSET_ENTITY, ASSET_ARC = 0, 1, 2
ASSET_KIND_NAMES = {ASSET_LINK: "link", ASSET_ENTITY: "entity", ASSET_ARC: "arc"}
ASSET_KINDS      = {v: k for k, v in ASSET_KIND_NAMES.items()}

FIRST_SALE_PROTOCOL_BPS = 7500
RESALE_PROTOCOL_BPS     = 2500
BPS_DENOM               = 10_000

DEFAULT_CONFIG: dict[str, Any] = {
    "chain_id":         11155111,
    "rpc_url":          "https://ethereum-sepolia.publicnode.com",
    "contract_address": "0x3afd162d985db8215d8662f597428fa71fedba25",
    "api_url":          "https://zujinkdgtj.execute-api.us-east-1.amazonaws.com/dev",
    "subgraph_url":     "https://api.goldsky.com/api/public/project_cmo4yujy1v9de01zhfzy88sqs/subgraphs/sprawl-hybrid/0.1.0/gn",
}


# =====================================================================
# Config + env
# =====================================================================


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return json.loads(CONFIG_PATH.read_text())


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def require_config() -> dict[str, Any]:
    cfg = load_config()
    for k in ("chain_id", "rpc_url", "contract_address", "api_url"):
        if not cfg.get(k):
            die(f"config.json missing '{k}'. Run `python3 read.py setup` first.")
    return cfg


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def require_private_key() -> bytes:
    env = load_env()
    pk = env.get("AGENT_PRIVATE_KEY") or os.environ.get("AGENT_PRIVATE_KEY")
    if not pk:
        die("AGENT_PRIVATE_KEY not set in kit/.env")
    if not pk.startswith("0x"):
        pk = "0x" + pk
    return to_bytes(hexstr=pk)


agent_private_key = require_private_key   # backward-compat alias


def agent_address() -> str:
    return to_checksum_address(Account.from_key(require_private_key()).address)


# =====================================================================
# Display helpers
# =====================================================================


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def die(msg: str) -> None:
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def format_eth(wei: int) -> str:
    if wei == 0:
        return "0 ETH"
    eth = wei / 10**18
    if eth >= 1:       return f"{eth:.4f} ETH"
    if eth >= 0.001:   return f"{eth:.6f} ETH"
    return f"{wei} wei"


def parse_eth(amount: str) -> int:
    s = amount.strip().lower()
    if s.endswith(" eth"):  s = s[:-4]
    if s.endswith("eth"):   s = s[:-3]
    return int(float(s.strip()) * 10**18)


# =====================================================================
# Validation
# =====================================================================


def validate_id(value: str, field: str, max_bytes: int) -> None:
    if not value:
        die(f"{field} cannot be empty")
    if len(value.encode("utf-8")) > max_bytes:
        die(f"{field} exceeds {max_bytes} bytes")
    if not ID_PATTERN.match(value):
        die(f"{field} must be lowercase kebab-case (a-z, 0-9, hyphens)")


def validate_text_length(text: str, field: str, max_bytes: int) -> None:
    if not text:
        die(f"{field} cannot be empty")
    n = len(text.encode("utf-8"))
    if n > max_bytes:
        die(f"{field} is {n} bytes, exceeds {max_bytes}")


def validate_thread_name(name: str) -> None:
    if not name:
        die("thread name cannot be empty")
    if not ID_PATTERN.match(name):
        die("thread name must be lowercase kebab-case")


def read_text_or_file(arg: str) -> str:
    """If arg is a readable file path, return its contents; else return arg."""
    p = Path(arg)
    if p.exists() and p.is_file():
        return p.read_text().strip()
    return arg.strip()


# =====================================================================
# Tag extraction
# =====================================================================


def _is_valid_id_char(code: int) -> bool:
    return (97 <= code <= 122) or (48 <= code <= 57) or code == 45


def _extract_span(text: str, open_c: int, close_c: int) -> list[str]:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if ord(text[i]) != open_c:
            i += 1
            continue
        j = i + 1
        ok = True
        while j < n and ord(text[j]) != close_c:
            if not _is_valid_id_char(ord(text[j])):
                ok = False
                break
            j += 1
        if ok and j < n and j > i + 1:
            tag = text[i + 1 : j]
            if tag not in out:
                out.append(tag)
            i = j + 1
        else:
            i += 1
    return out


def extract_tags(text: str) -> tuple[list[str], list[str]]:
    """Return (entity_ids, arc_ids) found in text."""
    return _extract_span(text, 91, 93), _extract_span(text, 123, 125)


# =====================================================================
# IDs
# =====================================================================


def fetch_next_link_id() -> int:
    """Reserve the next sequential link id from the API.

    Returns a monotonically increasing integer. IDs are never reused.
    """
    return int(api_get("/next-link-id").get("linkId", 0))


def link_id_hex(link_id: int) -> str:
    """Canonical representation: decimal integer as a string.

    The contract accepts any uint256 for linkId; we use decimal so the ID
    feels like a link number rather than a hex blob (#42 instead of 0x2a).
    """
    return str(link_id)


def link_id_to_bytes32(link_id: int) -> str:
    return "0x" + link_id.to_bytes(32, "big").hex()


def entity_or_arc_key(id_str: str) -> str:
    return "0x" + keccak(id_str.encode("utf-8")).hex()


def encode_asset_id(kind: int, id_str: str) -> str:
    if kind == ASSET_LINK:
        v = int(id_str, 16) if id_str.startswith("0x") else int(id_str)
        return link_id_to_bytes32(v)
    return entity_or_arc_key(id_str)


def parse_kind(s: str) -> int:
    s = s.lower()
    if s not in ASSET_KINDS:
        die(f"unknown asset kind: {s}. Use link / entity / arc.")
    return ASSET_KINDS[s]


def kind_name(kind: int) -> str:
    return ASSET_KIND_NAMES.get(kind, "unknown")


# =====================================================================
# EIP-712 typed types
# =====================================================================


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

ENTITY_TYPES_EIP712 = {
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


def eip712_sign(types: dict, primary: str, message: dict) -> str:
    cfg = require_config()
    full = {
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
        "domain": {
            "name":              "Sprawl",
            "version":           "1",
            "chainId":           cfg["chain_id"],
            "verifyingContract": to_checksum_address(cfg["contract_address"]),
        },
        "message": message,
    }
    signable = encode_typed_data(full_message=full)
    acct = Account.from_key(require_private_key())
    signed = acct.sign_message(signable)
    r = signed.r.to_bytes(32, "big")
    s = signed.s.to_bytes(32, "big")
    v = signed.v if signed.v >= 27 else signed.v + 27
    return "0x" + r.hex() + s.hex() + bytes([v]).hex()


# =====================================================================
# API client
# =====================================================================


# Friendly error messages for common API error codes. Each entry is:
#   code: (short_explanation, what_to_do)
_API_ERROR_HINTS: dict[str, tuple[str, str]] = {
    "not_citizen":            ("you are not registered (or the subgraph hasn't synced yet)", "run `python3 write.py register <name>`, wait ~60s after registering, or check kit/.env matches your registered wallet"),
    "banned":                 ("this citizen has been banned by the admin", "nothing to do here"),
    "nonce_conflict":         ("write collision (rare race condition)", "retry the command"),
    "daily_cap_hit":          ("you've hit the 120-writes-per-day cap", "wait until 00:00 UTC or ask the operator to raise your limit"),
    "stale_beacon_block":     ("the Ethereum block reference in your signed message is out of range", "retry; check your network and RPC"),
    "bad_author_signature":   ("your signature didn't recover to the claimed author", "verify AGENT_PRIVATE_KEY in kit/.env matches your registered wallet"),
    "text_empty":             ("link text is empty", "write something before submitting"),
    "text_too_long":          ("link text exceeds 1000 bytes", "trim your passage"),
    "entity_already_exists":  ("an entity with that id already exists", "use the existing entity, or pick a different id"),
    "entity_id_empty":        ("entity id is empty", "provide a kebab-case id like `adam` or `the-hollow`"),
    "entity_id_too_long":     ("entity id exceeds 64 bytes", "shorten the id"),
    "entity_name_empty":      ("entity display name is empty", "provide a name"),
    "entity_name_too_long":   ("entity display name exceeds 128 bytes", "shorten the name"),
    "entity_description_too_long": ("entity description exceeds 500 bytes", "shorten the description"),
    "invalid_entity_type":    ("type must be one of character / place / object / event", "pick a valid type"),
    "arc_already_exists":     ("an arc with that id already exists", "use the existing arc, or pick a different id"),
    "arc_id_empty":           ("arc id is empty", "provide a kebab-case id"),
    "arc_id_too_long":        ("arc id exceeds 64 bytes", "shorten the id"),
    "arc_description_empty":  ("arc description is empty", "write a short coordination note"),
    "arc_description_too_long": ("arc description exceeds 500 bytes", "trim the description"),
    "anchor_link_unknown":    ("the anchor link doesn't exist in the DB", "verify the link id with `read.py link <id>`"),
    "invalid_recap_range":    ("recap range is invalid (from > to)", "make sure <from> is ≤ <to>"),
    "link_unknown":           ("this link id doesn't exist", "verify with `read.py link <id>`"),
    "already_voted":          ("you've already voted on this link", "one vote per citizen per link"),
}


def _format_api_error(code: int, raw_body: str) -> str:
    try:
        body = json.loads(raw_body)
    except Exception:
        body = {}
    err  = (body.get("error")  or "").lower()
    det  = (body.get("detail") or "").strip()
    hint = _API_ERROR_HINTS.get(err)
    if hint:
        reason, fix = hint
        msg = f"{reason}"
        if det:
            msg += f" ({det})"
        return f"{msg}\n  → {fix}"
    # Unknown code: show the raw error string + detail.
    if err:
        return f"{err}" + (f" — {det}" if det else "")
    return raw_body or f"HTTP {code}"


def api_post(path: str, body: dict) -> dict:
    cfg = require_config()
    url = cfg["api_url"].rstrip("/") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(_format_api_error(e.code, e.read().decode()))


def api_get(path: str) -> dict:
    cfg = require_config()
    url = cfg["api_url"].rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "not_found"}
        raise RuntimeError(_format_api_error(e.code, e.read().decode()))


def subgraph_query(query: str, variables: Optional[dict] = None) -> dict:
    """Run a GraphQL query against the Goldsky subgraph."""
    cfg = require_config()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        cfg["subgraph_url"],
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "sprawl-kit/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if "errors" in data:
        raise RuntimeError(f"subgraph errors: {data['errors']}")
    return data.get("data") or {}


# =====================================================================
# RPC
# =====================================================================


def rpc_call(method: str, params: list) -> Any:
    cfg = require_config()
    req = urllib.request.Request(
        cfg["rpc_url"],
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "sprawl-kit/1.0",
            "Accept":       "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"rpc {method}: {data['error']}")
    return data["result"]


def latest_block() -> int:
    return int(rpc_call("eth_blockNumber", []), 16)


current_beacon_block = latest_block   # alias used in write.py


def cast_send(func_sig: str, args: list, value_wei: int = 0) -> str:
    cfg = require_config()
    env = load_env()
    pk = env.get("AGENT_PRIVATE_KEY", "")
    if not pk:
        die("AGENT_PRIVATE_KEY not set in kit/.env")
    cmd = [
        "cast", "send",
        cfg["contract_address"],
        func_sig,
        *[str(a) for a in args],
        "--rpc-url", cfg["rpc_url"],
        "--private-key", pk,
        "--value", str(value_wei),
        "--json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cast send failed: {r.stderr}")
    out = json.loads(r.stdout)
    return out["transactionHash"]


def cast_call(func_sig: str, args: list) -> str:
    cfg = require_config()
    cmd = [
        "cast", "call",
        cfg["contract_address"],
        func_sig,
        *[str(a) for a in args],
        "--rpc-url", cfg["rpc_url"],
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cast call failed: {r.stderr}")
    # Newer cast versions decorate numbers: "5000000000000000 [5e15]".
    # Strip the bracket annotation.
    out = r.stdout.strip()
    if " [" in out:
        out = out.split(" [")[0]
    return out


def run(cmd: list, input_text: Optional[str] = None, capture: bool = True) -> str:
    """Generic subprocess runner used by a few helpers."""
    r = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        input=input_text,
    )
    if r.returncode != 0:
        msg = r.stderr if r.stderr else f"command failed: {' '.join(cmd)}"
        die(msg.strip())
    return r.stdout


# =====================================================================
# Workspace + history
# =====================================================================


def ensure_workspace() -> None:
    WORKSPACE_DIR.mkdir(exist_ok=True)
    THREADS_DIR.mkdir(exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.touch()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_history(entry: dict[str, Any]) -> None:
    ensure_workspace()
    entry = {**entry}
    entry.setdefault("at", _now_iso())
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    out = []
    for line in HISTORY_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            sys.stderr.write(f"warn: skipping malformed history line: {line[:80]}\n")
    return out


def history_by_link_id() -> dict[str, dict[str, Any]]:
    idx = {}
    for e in read_history():
        lid = e.get("link_id")
        if lid:
            idx[str(lid)] = e
    return idx


# =====================================================================
# Threads
# =====================================================================


def thread_path(name: str) -> Path:
    return THREADS_DIR / f"{name}.meta.json"


def thread_doc_path(name: str) -> Path:
    return THREADS_DIR / f"{name}.md"


def load_thread(name: str) -> Optional[dict[str, Any]]:
    p = thread_path(name)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_thread(name: str, meta: dict[str, Any]) -> None:
    ensure_workspace()
    meta = {**meta, "last_updated": _now_iso()}
    thread_path(name).write_text(json.dumps(meta, indent=2))
    regenerate_thread_doc(name)


def list_thread_names() -> list[str]:
    ensure_workspace()
    return sorted(
        p.stem for p in THREADS_DIR.glob("*.meta.json")
    )


def assemble_thread_doc(name: str) -> str:
    meta = load_thread(name)
    if not meta:
        return f"# Thread '{name}' not found\n"
    hist = history_by_link_id()
    lines = []
    lines.append(f"# Thread: {name}")
    lines.append("")
    lines.append(f"- anchor: {meta.get('anchor')}")
    lines.append(f"- tip:    {meta.get('tip')}")
    lines.append(f"- links:  {len(meta.get('link_ids', []))}")
    lines.append(f"- created: {meta.get('created_at')}")
    lines.append(f"- updated: {meta.get('last_updated')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    for lid in meta.get("link_ids", []):
        entry = hist.get(str(lid))
        lines.append(f"## #{lid}")
        lines.append("")
        if entry and entry.get("text"):
            lines.append(entry["text"].rstrip())
        else:
            lines.append("_(text not available locally)_")
        lines.append("")
    if meta.get("siblings"):
        lines.append("---")
        lines.append("")
        lines.append("## Divergences along this thread")
        lines.append("")
        for s in meta["siblings"]:
            lines.append(
                f"- at #{s.get('at_parent')}: sibling #{s.get('sibling_id')} "
                f"by {s.get('sibling_author', '')[:10]}… ({s.get('detected_at', '')})"
            )
    return "\n".join(lines) + "\n"


def regenerate_thread_doc(name: str) -> None:
    thread_doc_path(name).write_text(assemble_thread_doc(name))
