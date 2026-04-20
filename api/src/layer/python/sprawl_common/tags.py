"""Tag extraction from link text.

Matches the original L2 subgraph's extractTags: scans character-by-character
pulling out every `<open><id><close>` span whose id contains only valid id
chars (a-z, 0-9, hyphen). Both attached ("word[id]") and standalone ("[id]")
tags are captured since we don't care what comes before the opening char.
De-duplicated within a single text.
"""

from __future__ import annotations

from typing import Iterable


def _is_valid_id_char(code: int) -> bool:
    return (
        (97 <= code <= 122)   # a-z
        or (48 <= code <= 57)  # 0-9
        or code == 45          # -
    )


def _extract_span(text: str, open_code: int, close_code: int) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if ord(text[i]) != open_code:
            i += 1
            continue
        j = i + 1
        ok = True
        while j < n and ord(text[j]) != close_code:
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


def extract_entity_ids(text: str) -> list[str]:
    """Extract `[entity-id]` mentions from the text."""
    return _extract_span(text, 91, 93)   # '[' ']'


def extract_arc_ids(text: str) -> list[str]:
    """Extract `{arc-id}` references from the text."""
    return _extract_span(text, 123, 125)  # '{' '}'


def extract_all(text: str) -> tuple[list[str], list[str]]:
    """Return (entity_ids, arc_ids) extracted from text."""
    return extract_entity_ids(text), extract_arc_ids(text)
