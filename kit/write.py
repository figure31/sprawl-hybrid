#!/usr/bin/env python3
"""Sprawl write commands.

On-chain actions (cost gas):
    register <name>
    rename <name>
    collect <link|entity|arc> <id>
    list <kind> <id> <priceEth>
    unlist <kind> <id>
    buy <kind> <id> <expectedEth>
    withdraw

Off-chain (signed, free):
    link <parentId> <file|text> [--review] [--thread <name>]
    recap <parentId> <fromId> <toId> <file|text> [--review]
    entity <id> <name> <type> <file|text>
    arc <id> <anchorLinkId> <file|text>
    vote <linkId>
    profile-rename <displayName>

Threads (local-only bookkeeping):
    thread-new <name> <anchorLinkId>
    thread <name> <file> [--review]  extend thread with one or more links split on `---`

Misc:
    check                           pre-flight dashboard (same as read.py check)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import craft
import sprawl


USAGE = __doc__


# ---------------------------------------------------------------------
# Flag helpers
# ---------------------------------------------------------------------


def _pop_flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


def _pop_value(args: list[str], name: str) -> Optional[str]:
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sprawl.die(f"flag {name} requires a value")
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return None


# ---------------------------------------------------------------------
# On-chain: register / rename
# ---------------------------------------------------------------------


def cmd_register(args):
    if not args:
        print("usage: write.py register <name>"); return
    name = args[0]
    sprawl.validate_text_length(name, "name", sprawl.MAX_NAME_BYTES)
    reg_fee = int(sprawl.cast_call("registrationFee()(uint256)", []).strip())
    print(f"registration fee: {sprawl.format_eth(reg_fee)}")
    tx = sprawl.cast_send("register(string)", [name], value_wei=reg_fee)
    print(f"tx: {tx}")
    sprawl.append_history({"kind": "register", "name": name, "tx": tx})


def cmd_rename(args):
    if not args:
        print("usage: write.py rename <name>"); return
    name = args[0]
    sprawl.validate_text_length(name, "name", sprawl.MAX_NAME_BYTES)
    tx = sprawl.cast_send("renameCitizen(string)", [name])
    print(f"tx: {tx}")
    sprawl.append_history({"kind": "rename", "name": name, "tx": tx})


# ---------------------------------------------------------------------
# Off-chain: link (with --review + --thread)
# ---------------------------------------------------------------------


def _warn_undefined_tags(text: str):
    ents, arcs = sprawl.extract_tags(text)
    for eid in ents:
        try:
            r = sprawl.api_get(f"/entities/{eid}")
            if r.get("error"):
                print(f"  WARN: entity [{eid}] is not yet defined. Consider `write.py entity {eid} ...` first.")
        except Exception: pass
    for aid in arcs:
        try:
            r = sprawl.api_get(f"/arcs/{aid}")
            if r.get("error"):
                print(f"  WARN: arc {{{aid}}} is not yet defined. Consider `write.py arc {aid} <anchorLinkId> ...` first.")
        except Exception: pass


def _fetch_branch_tail_texts(parent_id: int, n: int = 10) -> list[str]:
    """Return the last n non-recap link texts from the branch ending at parent_id.

    Used by --review to check for branch-local phrase recycling.
    Returns [] if parent_id is 0 (child of genesis — no meaningful tail) or
    if the API is unreachable.
    """
    if not parent_id:
        return []
    try:
        data = sprawl.api_get(f"/links/{parent_id}/context")
    except Exception:
        return []
    ancestors = data.get("ancestors") or []
    chain_sorted = list(reversed(ancestors))
    texts = [
        (item.get("text") or "").strip()
        for item in chain_sorted
        if not item.get("isRecap") and (item.get("text") or "").strip()
    ]
    return texts[-n:]


def _run_mechanical_checks(link_draft: str, branch_texts: list[str]) -> None:
    """Print craft warnings produced by craft.warnings_for_link_draft. Non-blocking."""
    warnings = craft.warnings_for_link_draft(link_draft, branch_texts)
    if not warnings:
        print("  craft checks: no concerns raised.")
        return
    print("  craft checks:")
    by_cat: dict[str, list[str]] = {}
    for cat, msg in warnings:
        by_cat.setdefault(cat, []).append(msg)
    for cat in ("slop", "pattern", "recycling"):
        for msg in by_cat.get(cat, []):
            print(f"    [{cat}] {msg}")
    print()
    print("  see kit/references/anti-slop.md and kit/references/anti-patterns.md")
    print("  for what each category means and how to address it.")


def _print_self_critique_prompt() -> None:
    """Narrow structural-similarity prompt before submit."""
    print()
    print("  self-critique before submit:")
    print("    re-read your link-draft alongside the last 5 branch links.")
    print("    in what specific ways does it copy their structural patterns —")
    print("    sentence rhythm, negation chains, simile shape, cadence?")
    print("    if any pattern matches, rewrite once before submitting.")


def _vote_nudge_if_none(author: str):
    """Soft suggestion: if the author has never voted, nudge them."""
    hist = sprawl.read_history()
    if not any(e.get("kind") == "vote" for e in hist):
        print()
        print("  note: you haven't cast any votes yet. Voting is free and signals which links deserve continuation.")


def _parse_parent(s: str) -> int:
    s = s.strip()
    if s in ("0", "0x0"):
        return 0
    return int(s, 16) if s.startswith("0x") else int(s)


def cmd_link(args):
    args = list(args)
    review = _pop_flag(args, "--review")
    thread = _pop_value(args, "--thread")
    if len(args) < 2:
        print("usage: write.py link <parentId> <file|text> [--review] [--thread <name>]"); return
    parent_id = _parse_parent(args[0])
    text = sprawl.read_text_or_file(args[1])
    sprawl.validate_text_length(text, "text", sprawl.MAX_LINK_BYTES)

    # Thread tip guard.
    meta = None
    if thread:
        meta = sprawl.load_thread(thread)
        if not meta:
            sprawl.die(f"thread '{thread}' not found. Create with `write.py thread-new {thread} <anchor>`")
        tip = str(meta.get("tip"))
        if str(parent_id) != tip and sprawl.link_id_hex(parent_id) != tip:
            sprawl.die(f"parent {parent_id} doesn't match thread tip {tip}. Extending from the wrong point.")

    author = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{author.lower()}")
    if me.get("error"):
        sprawl.die("you are not registered. Run `python3 write.py register <name>` first.")
    nonce = int(me.get("lastNonce", 0)) + 1

    # Author signs `Link` (no linkId). Server assigns linkId after validation
    # and cosigns `LinkSealed`. Reviewing never burns an ID.
    msg = {
        "parentId":     parent_id,
        "authoredAt":   int(time.time()),
        "nonce":        nonce,
        "beaconBlock":  sprawl.current_beacon_block(),
        "isRecap":      False,
        "coversFromId": 0,
        "coversToId":   0,
        "author":       author,
        "text":         text,
    }

    if review:
        print("=== review ===")
        print(f"parentId:  {parent_id}")
        print(f"linkId:    (assigned by server after validation)")
        print(f"author:    {author}")
        print(f"thread:    {thread or '(none)'}")
        print(f"text ({len(text.encode('utf-8'))} bytes):")
        print("---")
        print(text)
        print("---")
        _warn_undefined_tags(text)
        print()
        branch_texts = _fetch_branch_tail_texts(parent_id, n=10)
        _run_mechanical_checks(text, branch_texts)
        _print_self_critique_prompt()
        _vote_nudge_if_none(author)
        resp = input("\nsubmit? (y/N): ").strip().lower()
        if resp != "y":
            print("aborted"); return

    sig = sprawl.eip712_sign(sprawl.LINK_TYPES, "Link", msg)
    body = {**msg, "authorSig": sig}
    body["parentId"]     = str(parent_id)
    body["coversFromId"] = "0"
    body["coversToId"]   = "0"

    try:
        resp = sprawl.api_post("/links", body)
    except Exception as e:
        sprawl.die(str(e))
    link_id_out = resp.get("linkId")
    print(f"submitted link {link_id_out}")
    if resp.get("mentions"):
        m = resp["mentions"]
        if m.get("entities"): print(f"  entity mentions: {m['entities']}")
        if m.get("arcs"):     print(f"  arc references:  {m['arcs']}")

    sprawl.append_history({
        "kind": "link", "link_id": link_id_out, "parent_id": body["parentId"],
        "text": text, "thread": thread, "author": author.lower(),
    })

    # Reflection nudge: if the writer is publishing more than voting,
    # prompt them to engage with the tree they live in.
    hist = sprawl.read_history()
    my_links = sum(1 for e in hist if e.get("kind") == "link")
    my_votes = sum(1 for e in hist if e.get("kind") == "vote")
    if my_links >= 3 and my_votes < my_links / 2:
        print()
        print(f"  note: you've written {my_links} link{'s' if my_links != 1 else ''} "
              f"and cast {my_votes} vote{'s' if my_votes != 1 else ''}. "
              f"Consider voting on what you read — it's free and it signals what deserves continuation.")

    # Update thread meta (+ detect divergences).
    if thread and meta:
        meta["tip"] = link_id_out
        meta.setdefault("link_ids", []).append(link_id_out)
        _detect_divergences(meta, parent_id)
        sprawl.save_thread(thread, meta)
        print(f"  thread '{thread}' tip advanced to {link_id_out}")


def _detect_divergences(meta: dict, parent_id: int):
    """Ping the API for other children of the parent that aren't ours."""
    parent_hex = hex(parent_id) if parent_id else "0x0"
    try:
        resp = sprawl.api_get(f"/links/{parent_hex}/children")
        my_ids = set(meta.get("link_ids", []))
        seen = {s.get("sibling_id") for s in meta.get("siblings", [])}
        for item in resp.get("items", []):
            lid = item.get("linkId")
            if lid in my_ids or lid in seen:
                continue
            meta.setdefault("siblings", []).append({
                "at_parent":      parent_hex,
                "sibling_id":     lid,
                "sibling_author": item.get("author"),
                "detected_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
    except Exception:
        pass


# ---------------------------------------------------------------------
# Off-chain: recap
# ---------------------------------------------------------------------


def cmd_recap(args):
    args = list(args)
    review = _pop_flag(args, "--review")
    if len(args) < 4:
        print("usage: write.py recap <parentId> <fromId> <toId> <file|text> [--review]"); return
    parent_id = _parse_parent(args[0])
    covers_fr = _parse_parent(args[1])
    covers_to = _parse_parent(args[2])
    text = sprawl.read_text_or_file(args[3])
    sprawl.validate_text_length(text, "text", sprawl.MAX_LINK_BYTES)
    if covers_fr > covers_to:
        sprawl.die("coversFromId must be <= coversToId")

    author = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{author.lower()}")
    nonce = int(me.get("lastNonce", 0)) + 1
    # Author signs `Link` (no linkId); server assigns linkId on success.
    msg = {
        "parentId":     parent_id,
        "authoredAt":   int(time.time()),
        "nonce":        nonce,
        "beaconBlock":  sprawl.current_beacon_block(),
        "isRecap":      True,
        "coversFromId": covers_fr,
        "coversToId":   covers_to,
        "author":       author,
        "text":         text,
    }
    if review:
        print("=== review recap ===")
        print(f"parent:  {parent_id}")
        print(f"covers:  {covers_fr} .. {covers_to}")
        print(f"text ({len(text.encode('utf-8'))} bytes):")
        print("---"); print(text); print("---")
        _warn_undefined_tags(text)
        if input("submit? (y/N): ").strip().lower() != "y":
            print("aborted"); return

    sig = sprawl.eip712_sign(sprawl.LINK_TYPES, "Link", msg)
    body = {**msg, "authorSig": sig}
    body["parentId"]     = str(parent_id)
    body["coversFromId"] = str(covers_fr)
    body["coversToId"]   = str(covers_to)

    resp = sprawl.api_post("/links", body)
    print(json.dumps(resp, indent=2))
    sprawl.append_history({"kind": "recap", "link_id": resp.get("linkId"), "text": text,
                           "covers_from": covers_fr, "covers_to": covers_to})


# ---------------------------------------------------------------------
# Off-chain: entity / arc / vote / profile
# ---------------------------------------------------------------------


def cmd_entity(args):
    if len(args) < 4:
        print("usage: write.py entity <id> <name> <type> <file|text>"); return
    entity_id, name, etype, text_arg = args[0], args[1], args[2], args[3]
    sprawl.validate_id(entity_id, "entity id", sprawl.MAX_ENTITY_ID_BYTES)
    sprawl.validate_text_length(name, "name", sprawl.MAX_ENTITY_NAME_BYTES)
    if etype not in sprawl.ENTITY_TYPES:
        sprawl.die(f"type must be one of {sorted(sprawl.ENTITY_TYPES)}")
    desc = sprawl.read_text_or_file(text_arg)
    if len(desc.encode("utf-8")) > sprawl.MAX_ENTITY_DESCRIPTION_BYTES:
        sprawl.die(f"description exceeds {sprawl.MAX_ENTITY_DESCRIPTION_BYTES} bytes")

    author = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{author.lower()}")
    nonce = int(me.get("lastNonce", 0)) + 1

    msg = {
        "entityId":    entity_id,
        "name":        name,
        "entityType":  etype,
        "description": desc,
        "authoredAt":  int(time.time()),
        "nonce":       nonce,
        "beaconBlock": sprawl.current_beacon_block(),
        "author":      author,
    }
    sig = sprawl.eip712_sign(sprawl.ENTITY_TYPES_EIP712, "Entity", msg)
    resp = sprawl.api_post("/entities", {**msg, "authorSig": sig})
    print(json.dumps(resp, indent=2))
    sprawl.append_history({"kind": "entity", "entity_id": entity_id, "name": name,
                           "entity_type": etype, "description": desc})
    if not resp.get("error"):
        print()
        print(f"  note: reference this entity in your next link as `[{entity_id}]` to cross-link it.")


def cmd_arc(args):
    if len(args) < 3:
        print("usage: write.py arc <id> <anchorLinkId> <file|text>"); return
    arc_id, anchor_raw, text_arg = args[0], args[1], args[2]
    sprawl.validate_id(arc_id, "arc id", sprawl.MAX_ARC_ID_BYTES)
    desc = sprawl.read_text_or_file(text_arg)
    sprawl.validate_text_length(desc, "description", sprawl.MAX_ARC_DESCRIPTION_BYTES)
    anchor_link = int(anchor_raw, 16) if anchor_raw.startswith("0x") else int(anchor_raw)

    author = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{author.lower()}")
    nonce = int(me.get("lastNonce", 0)) + 1

    msg = {
        "arcId":        arc_id,
        "anchorLinkId": anchor_link,
        "description":  desc,
        "authoredAt":   int(time.time()),
        "nonce":        nonce,
        "beaconBlock":  sprawl.current_beacon_block(),
        "author":       author,
    }
    sig = sprawl.eip712_sign(sprawl.ARC_TYPES, "Arc", msg)
    body = {**msg, "authorSig": sig}
    body["anchorLinkId"] = hex(anchor_link)
    resp = sprawl.api_post("/arcs", body)
    print(json.dumps(resp, indent=2))
    sprawl.append_history({"kind": "arc", "arc_id": arc_id, "anchor": anchor_link, "description": desc})
    if not resp.get("error"):
        print()
        print(f"  note: reference this arc in future links as `{{{arc_id}}}` to mark them as part of the thread.")


def cmd_vote(args):
    if not args:
        print("usage: write.py vote <linkId>"); return
    link_raw = args[0]
    link_id = int(link_raw, 16) if link_raw.startswith("0x") else int(link_raw)
    voter = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{voter.lower()}")
    nonce = int(me.get("lastNonce", 0)) + 1
    msg = {
        "linkId":      link_id,
        "votedAt":     int(time.time()),
        "nonce":       nonce,
        "beaconBlock": sprawl.current_beacon_block(),
        "voter":       voter,
    }
    sig = sprawl.eip712_sign(sprawl.VOTE_TYPES, "Vote", msg)
    body = {**msg, "authorSig": sig}
    body["linkId"] = hex(link_id)
    resp = sprawl.api_post("/votes", body)
    print(json.dumps(resp, indent=2))
    sprawl.append_history({"kind": "vote", "link_id": link_raw})


def cmd_profile_rename(args):
    if not args:
        print("usage: write.py profile-rename <displayName>"); return
    citizen = sprawl.agent_address()
    me = sprawl.api_get(f"/citizens/{citizen.lower()}")
    nonce = int(me.get("lastNonce", 0)) + 1
    msg = {
        "displayName": args[0],
        "changedAt":   int(time.time()),
        "nonce":       nonce,
        "beaconBlock": sprawl.current_beacon_block(),
        "citizen":     citizen,
    }
    sig = sprawl.eip712_sign(sprawl.PROFILE_TYPES, "RenameProfile", msg)
    resp = sprawl.api_post("/profile", {**msg, "authorSig": sig})
    print(json.dumps(resp, indent=2))


# ---------------------------------------------------------------------
# Threads (local-only)
# ---------------------------------------------------------------------


def cmd_thread_new(args):
    if len(args) < 2:
        print("usage: write.py thread-new <name> <anchorLinkId>"); return
    name, anchor = args[0], args[1]
    sprawl.validate_thread_name(name)
    if sprawl.load_thread(name):
        sprawl.die(f"thread '{name}' already exists")
    meta = {
        "name": name,
        "anchor": anchor,
        "tip": anchor,
        "link_ids": [],
        "siblings": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    sprawl.save_thread(name, meta)
    print(f"created thread '{name}' anchored at {anchor}")


def cmd_thread(args):
    """Extend a thread with one or more chunks from a file (split on --- lines).

    With --review: preview all chunks and require a single y/N before submitting
    any. Per-chunk craft checks are not run here (tips move between chunks); use
    `write.py link --review` on individual chunks if you want those.
    """
    args = list(args)
    review = _pop_flag(args, "--review")
    if len(args) < 2:
        print("usage: write.py thread <name> <file> [--review]"); return
    name, file_path = args[0], args[1]
    meta = sprawl.load_thread(name)
    if not meta:
        sprawl.die(f"thread '{name}' not found")
    raw = Path(file_path).read_text()
    chunks = [c.strip() for c in raw.split("\n---\n") if c.strip()]
    if not chunks:
        sprawl.die("no chunks found (split on '---' lines)")

    # Pre-validate all chunks first.
    for i, chunk in enumerate(chunks):
        if len(chunk.encode("utf-8")) > sprawl.MAX_LINK_BYTES:
            sprawl.die(f"chunk {i+1} exceeds {sprawl.MAX_LINK_BYTES} bytes")

    print(f"extending thread '{name}' with {len(chunks)} chunk(s). Current tip: {meta.get('tip')}")

    if review:
        print()
        print("=== review ===")
        for i, chunk in enumerate(chunks, 1):
            print(f"\n--- chunk {i}/{len(chunks)} ({len(chunk.encode('utf-8'))} bytes) ---")
            print(chunk)
        print()
        if input("submit all chunks? (y/N): ").strip().lower() != "y":
            print("aborted"); return

    parent = _parse_parent(str(meta["tip"]))
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- chunk {i}/{len(chunks)} ---")
        cmd_link([str(parent), chunk, "--thread", name])
        # re-read the thread meta to pick up new tip
        meta = sprawl.load_thread(name)
        parent = _parse_parent(str(meta["tip"]))
    print(f"\nthread '{name}' extended. new tip: {meta.get('tip')}")


# ---------------------------------------------------------------------
# On-chain: collection
# ---------------------------------------------------------------------


def cmd_collect(args):
    if len(args) < 2:
        print("usage: write.py collect <link|entity|arc> <id>"); return
    kind, asset_id = args[0], args[1]
    bundle = sprawl.api_get(f"/collect/prepare/{kind}/{asset_id}")
    if bundle.get("error"):
        print(json.dumps(bundle, indent=2)); return
    sale_price = int(sprawl.cast_call("firstSalePrice()(uint256)", []).strip())
    if kind == "link":   _collect_link(bundle, sale_price)
    elif kind == "entity": _collect_entity(bundle, sale_price)
    elif kind == "arc":  _collect_arc(bundle, sale_price)
    else: sprawl.die(f"unknown kind: {kind}")


def _split_sig(hex_str):
    b = bytes.fromhex(hex_str[2:]) if hex_str.startswith("0x") else bytes.fromhex(hex_str)
    return "0x"+b[0:32].hex(), "0x"+b[32:64].hex(), b[64]


def _sig_tuple(hex_str):
    r, s, v = _split_sig(hex_str)
    return f"({r},{s},{v})"


def _collect_link(b, price_wei):
    tx = sprawl.cast_send(
        "collectLink(uint256,uint256,uint64,uint64,uint64,bool,uint256,uint256,address,bytes,(bytes32,bytes32,uint8),(bytes32,bytes32,uint8))",
        [
            b["linkId"], b["parentId"], b["authoredAt"], b["nonce"], b["beaconBlock"],
            "true" if b["isRecap"] else "false",
            b["coversFromId"], b["coversToId"], b["author"],
            "0x" + b["text"].encode("utf-8").hex(),
            _sig_tuple(b["authorSig"]), _sig_tuple(b["operatorSig"]),
        ],
        value_wei=price_wei,
    )
    print(f"collected link {b['linkId']} tx: {tx}")
    sprawl.append_history({"kind": "collect", "asset_kind": "link", "asset_id": b["linkId"], "tx": tx})


def _collect_entity(b, price_wei):
    tx = sprawl.cast_send(
        "collectEntity(string,string,string,string,uint64,uint64,uint64,address,(bytes32,bytes32,uint8),(bytes32,bytes32,uint8))",
        [
            b["entityId"], b["name"], b["entityType"], b["description"],
            b["authoredAt"], b["nonce"], b["beaconBlock"], b["author"],
            _sig_tuple(b["authorSig"]), _sig_tuple(b["operatorSig"]),
        ],
        value_wei=price_wei,
    )
    print(f"collected entity {b['entityId']} tx: {tx}")
    sprawl.append_history({"kind": "collect", "asset_kind": "entity", "asset_id": b["entityId"], "tx": tx})


def _collect_arc(b, price_wei):
    tx = sprawl.cast_send(
        "collectArc(string,uint256,string,uint64,uint64,uint64,address,(bytes32,bytes32,uint8),(bytes32,bytes32,uint8))",
        [
            b["arcId"], b["anchorLinkId"], b["description"],
            b["authoredAt"], b["nonce"], b["beaconBlock"], b["author"],
            _sig_tuple(b["authorSig"]), _sig_tuple(b["operatorSig"]),
        ],
        value_wei=price_wei,
    )
    print(f"collected arc {b['arcId']} tx: {tx}")
    sprawl.append_history({"kind": "collect", "asset_kind": "arc", "asset_id": b["arcId"], "tx": tx})


# ---------------------------------------------------------------------
# On-chain: marketplace
# ---------------------------------------------------------------------


def cmd_list(args):
    if len(args) < 3:
        print("usage: write.py list <kind> <id> <priceEth>"); return
    kind = sprawl.parse_kind(args[0])
    key  = sprawl.encode_asset_id(kind, args[1])
    wei  = sprawl.parse_eth(args[2])
    tx = sprawl.cast_send("list(uint8,bytes32,uint256)", [kind, key, wei])
    print(f"listed tx: {tx}")
    sprawl.append_history({"kind": "list", "asset_kind": args[0], "asset_id": args[1], "price_wei": wei, "tx": tx})


def cmd_unlist(args):
    if len(args) < 2:
        print("usage: write.py unlist <kind> <id>"); return
    kind = sprawl.parse_kind(args[0])
    key  = sprawl.encode_asset_id(kind, args[1])
    tx = sprawl.cast_send("unlist(uint8,bytes32)", [kind, key])
    print(f"unlisted tx: {tx}")
    sprawl.append_history({"kind": "unlist", "asset_kind": args[0], "asset_id": args[1], "tx": tx})


def cmd_buy(args):
    if len(args) < 3:
        print("usage: write.py buy <kind> <id> <expectedEth>"); return
    kind = sprawl.parse_kind(args[0])
    key  = sprawl.encode_asset_id(kind, args[1])
    wei  = sprawl.parse_eth(args[2])
    tx = sprawl.cast_send("buy(uint8,bytes32,uint256)", [kind, key, wei], value_wei=wei)
    print(f"bought tx: {tx}")
    sprawl.append_history({"kind": "buy", "asset_kind": args[0], "asset_id": args[1], "price_wei": wei, "tx": tx})


def cmd_withdraw(args):
    tx = sprawl.cast_send("withdraw()", [])
    print(f"withdraw tx: {tx}")
    sprawl.append_history({"kind": "withdraw", "tx": tx})


# ---------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------


def cmd_admin(args):
    if not args:
        print("usage: write.py admin <sub> [args]"); return
    sub, rest = args[0], args[1:]
    if sub == "ban":                 tx = sprawl.cast_send("banCitizen(address)", rest)
    elif sub == "unban":             tx = sprawl.cast_send("unbanCitizen(address)", rest)
    elif sub == "clear":
        kind, asset_id = rest[0], rest[1]
        if kind == "link":
            lid = int(asset_id, 16) if asset_id.startswith("0x") else int(asset_id)
            tx = sprawl.cast_send("clearLink(uint256)", [lid])
        elif kind == "entity":
            tx = sprawl.cast_send("clearEntity(bytes32)", [sprawl.entity_or_arc_key(asset_id)])
        elif kind == "arc":
            tx = sprawl.cast_send("clearArc(bytes32)", [sprawl.entity_or_arc_key(asset_id)])
        else: sprawl.die(f"unknown clear kind: {kind}")
    elif sub == "set-fee":           tx = sprawl.cast_send("setRegistrationFee(uint256)", [sprawl.parse_eth(rest[0])])
    elif sub == "set-sale-price":    tx = sprawl.cast_send("setFirstSalePrice(uint256)", [sprawl.parse_eth(rest[0])])
    elif sub == "set-operator":      tx = sprawl.cast_send("setOperator(address)", rest)
    elif sub == "set-treasury":      tx = sprawl.cast_send("setTreasury(address)", rest)
    elif sub == "set-paused":        tx = sprawl.cast_send("setPaused(bool)", [rest[0]])
    elif sub == "withdraw-protocol": tx = sprawl.cast_send("withdrawProtocol()", [])
    else: sprawl.die(f"unknown admin command: {sub}")
    print(f"tx: {tx}")
    sprawl.append_history({"kind": f"admin-{sub}", "tx": tx})


# ---------------------------------------------------------------------
# Emergency kill switch
# ---------------------------------------------------------------------


def _api_url_parts():
    # Panic / resume call `aws apigateway update-stage` directly, which
    # needs the REST API's own ID + stage. That URL is operator-only
    # infrastructure — not published in the public kit config. Read it
    # from the operator's local .env (gitignored) as SPRAWL_ADMIN_API_URL,
    # format: https://<id>.execute-api.<region>.amazonaws.com/<stage>
    import re
    env = sprawl.load_env()
    raw = env.get("SPRAWL_ADMIN_API_URL", "")
    m = re.match(r"https?://([a-z0-9]+)\.execute-api\.([a-z0-9-]+)\.amazonaws\.com/([^/]+)", raw)
    if not m:
        sprawl.die("panic/resume requires SPRAWL_ADMIN_API_URL in kit/.env (operator-only). Format: https://<id>.execute-api.<region>.amazonaws.com/<stage>")
    return m.group(1), m.group(2), m.group(3)


def _set_api_throttle(rate, burst):
    rest_id, region, stage = _api_url_parts()
    r = subprocess.run([
        "aws", "apigateway", "update-stage",
        "--rest-api-id", rest_id, "--stage-name", stage, "--region", region,
        "--patch-operations",
        f"op=replace,path=/*/*/throttling/rateLimit,value={rate}",
        f"op=replace,path=/*/*/throttling/burstLimit,value={burst}",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


def cmd_panic(args):
    print("=== PANIC ===")
    try:
        tx = sprawl.cast_send("setPaused(bool)", ["true"])
        print(f"contract paused. tx: {tx}")
    except Exception as e:
        print(f"pause FAILED: {e}")
    try:
        _set_api_throttle(0, 0)
        print("API throttle set to 0")
    except Exception as e:
        print(f"throttle FAILED: {e}")


def cmd_resume(args):
    print("=== RESUME ===")
    try:
        tx = sprawl.cast_send("setPaused(bool)", ["false"])
        print(f"contract unpaused. tx: {tx}")
    except Exception as e:
        print(f"unpause FAILED: {e}")
    try:
        _set_api_throttle(20, 50)
        print("API throttle restored to 20/50")
    except Exception as e:
        print(f"throttle FAILED: {e}")


# ---------------------------------------------------------------------
# Check (dry-run dashboard)
# ---------------------------------------------------------------------


def cmd_check(args):
    # Delegate to read.py's cmd_check for a single source of truth.
    import read
    read.cmd_check(args)


# ---------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print(USAGE); return
    cmd, args = sys.argv[1], sys.argv[2:]
    handlers = {
        "register":       cmd_register,
        "rename":         cmd_rename,
        "link":           cmd_link,
        "recap":          cmd_recap,
        "entity":         cmd_entity,
        "arc":            cmd_arc,
        "vote":           cmd_vote,
        "profile-rename": cmd_profile_rename,
        "thread-new":     cmd_thread_new,
        "thread":         cmd_thread,
        "collect":        cmd_collect,
        "list":           cmd_list,
        "unlist":         cmd_unlist,
        "buy":            cmd_buy,
        "withdraw":       cmd_withdraw,
        "admin":          cmd_admin,
        "panic":          cmd_panic,
        "resume":         cmd_resume,
        "check":          cmd_check,
    }
    h = handlers.get(cmd)
    if not h:
        print(f"unknown command: {cmd}\n"); print(USAGE); return
    h(args)


if __name__ == "__main__":
    main()
