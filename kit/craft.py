"""Sprawl craft module — text analysis for quality gates.

Shared by read.py (branch voice report) and write.py (--review checks).
All scans are pass-through: they surface patterns, they do not block submission.

Data sources:
  - Universal slop + anti-pattern taxonomy: adapted from Nous Research's
    AutoNovel project (https://github.com/NousResearch/autonovel).
  - Fiction-specific AI tells: AutoNovel CRAFT.md §6.

See kit/references/anti-slop.md and kit/references/anti-patterns.md for the
educational versions of these lists.
"""

from __future__ import annotations

import re
from collections import Counter


# =====================================================================
# Word and phrase lists
# =====================================================================

TIER1_WORDS = {
    "delve", "utilize", "leverage", "facilitate", "elucidate", "embark",
    "endeavor", "encompass", "multifaceted", "tapestry", "testament",
    "paradigm", "synergy", "holistic", "catalyze", "juxtapose", "realm",
    "myriad", "plethora",
}

TIER2_WORDS = {
    "robust", "comprehensive", "seamless", "cutting-edge", "innovative",
    "streamline", "empower", "foster", "enhance", "elevate", "optimize",
    "scalable", "pivotal", "intricate", "profound", "resonate", "navigate",
    "cultivate", "bolster", "cornerstone",
}

TIER3_FILLER = [
    "it's worth noting that", "it is worth noting that",
    "let's dive into", "let us dive into", "as we can see",
    "in conclusion", "in today's world", "at the end of the day",
    "when it comes to",
]

FICTION_TELLS = [
    (re.compile(r"\ba sense of \w+", re.IGNORECASE),                               "a sense of [X]"),
    (re.compile(r"\bcouldn.?t help but (feel|think|notice|wonder)", re.IGNORECASE), "couldn't help but [verb]"),
    (re.compile(r"\bthe weight of (the |a |an )?\w+", re.IGNORECASE),              "the weight of [X]"),
    (re.compile(r"\bthe air was thick with", re.IGNORECASE),                       "the air was thick with..."),
    (re.compile(r"\beyes widened\b", re.IGNORECASE),                               "eyes widened"),
    (re.compile(r"\ba wave of \w+ (washed|crashed|swept|rolled)", re.IGNORECASE),  "a wave of [X] washed over"),
    (re.compile(r"\ba pang of \w+", re.IGNORECASE),                                "a pang of [X]"),
    (re.compile(r"\bheart (pounded|hammered|raced|thundered) in (his|her|their) chest", re.IGNORECASE), "heart pounded in chest"),
    (re.compile(r"\b(raven|dark|golden|chestnut|auburn) hair (spilled|cascaded|tumbled|fell)", re.IGNORECASE), "[adj] hair [verbed]"),
    (re.compile(r"\bpiercing (blue|green|grey|gray|hazel|brown) eyes", re.IGNORECASE), "piercing [color] eyes"),
    (re.compile(r"\ba knowing (smile|look|glance|nod)", re.IGNORECASE),             "a knowing [X]"),
    (re.compile(r"\ba sense of (unease|dread|foreboding)", re.IGNORECASE),          "a sense of unease/dread"),
]

SYCOPHANCY_OPENINGS = [
    re.compile(r"^\s*great question", re.IGNORECASE),
    re.compile(r"^\s*that.?s (a |an )?(excellent|great|wonderful|fantastic) (point|question)", re.IGNORECASE),
]

NEGATION_PATTERN = re.compile(
    r"\b(did|does|do|was|were|is|are|am|had|have|has|could|would|should|will|can|may|might)\s+not\b",
    re.IGNORECASE,
)

SIMILE_PATTERN = re.compile(
    r"\bthe way (a|an|the|his|her|their|he|she|it|they|you|i)\s+\w+",
    re.IGNORECASE,
)

BALANCED_ANTITHESIS_PATTERNS = [
    re.compile(r"\bnot just \w+[^.]{0,40}?,? but\b", re.IGNORECASE),
    re.compile(r"\bnot (an |a |the )?\w+,? but (an |a |the )?\w+", re.IGNORECASE),
]

EM_DASH_CHARS = ("\u2014", "\u2013", "--")

TRIADIC_LIST_PATTERN = re.compile(
    r"\b(\w+), (\w+),? and (\w+)\b",
    re.IGNORECASE,
)

TAG_STRIP_PATTERN = re.compile(r"\[[a-z0-9-]+\]|\{[a-z0-9-]+\}")
WORD_PATTERN      = re.compile(r"[a-z]+(?:-[a-z]+)?", re.IGNORECASE)

# Pure-function-word trigrams are noisy; filter them from ngram counts.
_FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "so", "if", "of", "to", "in",
    "on", "at", "by", "for", "with", "as", "is", "was", "were", "be",
    "been", "it", "its", "he", "she", "they", "we", "i", "you", "his",
    "her", "their", "our", "my", "your", "that", "this", "these", "those",
    "had", "have", "has", "do", "did", "does", "not", "no", "yes", "from",
    "into", "onto", "out", "up", "down", "over", "under",
}


# =====================================================================
# Text utilities
# =====================================================================


def strip_tags(text: str) -> str:
    """Remove Sprawl-protocol tags so analysis isn't thrown off."""
    return TAG_STRIP_PATTERN.sub("", text)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip tags and punctuation, return word tokens."""
    return [m.group(0).lower() for m in WORD_PATTERN.finditer(strip_tags(text))]


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# =====================================================================
# Link-draft-level scans
# =====================================================================


def scan_tier1(text: str) -> list[str]:
    tokens = set(tokenize(text))
    return sorted(tokens & TIER1_WORDS)


def scan_tier2(text: str) -> list[tuple[str, int]]:
    tokens = tokenize(text)
    counts = Counter(tokens)
    hits = [(w, counts[w]) for w in TIER2_WORDS if counts.get(w)]
    # Only flag when the cumulative tier-2 count is 3+ (cluster signal).
    if sum(c for _, c in hits) >= 3:
        return sorted(hits)
    return []


def scan_tier3(text: str) -> list[str]:
    t = strip_tags(text).lower()
    return [p for p in TIER3_FILLER if p in t]


def scan_fiction_tells(text: str) -> list[str]:
    t = strip_tags(text)
    seen: list[str] = []
    for pat, label in FICTION_TELLS:
        if pat.search(t) and label not in seen:
            seen.append(label)
    return seen


def scan_sycophancy(text: str) -> bool:
    return any(p.search(text) for p in SYCOPHANCY_OPENINGS)


def count_negations(text: str) -> int:
    return len(NEGATION_PATTERN.findall(strip_tags(text)))


def count_simile_crutch(text: str) -> int:
    return len(SIMILE_PATTERN.findall(strip_tags(text)))


def count_balanced_antithesis(text: str) -> int:
    t = strip_tags(text)
    return sum(len(p.findall(t)) for p in BALANCED_ANTITHESIS_PATTERNS)


def count_em_dashes(text: str) -> int:
    n = 0
    for c in EM_DASH_CHARS:
        n += text.count(c)
    return n


def count_triadic_lists(text: str) -> int:
    return len(TRIADIC_LIST_PATTERN.findall(strip_tags(text)))


# =====================================================================
# Branch-level analysis
# =====================================================================


def top_repeated_ngrams(
    texts: list[str],
    n: int = 3,
    min_count: int = 2,
    k: int = 5,
) -> list[tuple[str, int]]:
    """Return n-grams appearing ≥ min_count times across texts, top k by count.

    Pure function-word ngrams are filtered. Count is total occurrences across
    all texts (a phrase repeated within one text counts as internal reuse; the
    same phrase in two different texts also counts).
    """
    counter: Counter = Counter()
    for t in texts:
        for g in ngrams(tokenize(t), n):
            if all(w in _FUNCTION_WORDS for w in g):
                continue
            counter[g] += 1
    hits = [(" ".join(g), c) for g, c in counter.items() if c >= min_count]
    hits.sort(key=lambda x: (-x[1], x[0]))
    return hits[:k]


def count_patterns_per_link(texts: list[str]) -> dict[str, float]:
    """Per-link averages of key anti-pattern counts."""
    if not texts:
        return {}
    n = len(texts)
    return {
        "negation":      sum(count_negations(t)            for t in texts) / n,
        "simile_crutch": sum(count_simile_crutch(t)        for t in texts) / n,
        "balanced":      sum(count_balanced_antithesis(t)  for t in texts) / n,
        "em_dash":       sum(count_em_dashes(t)            for t in texts) / n,
        "triadic":       sum(count_triadic_lists(t)        for t in texts) / n,
    }


_PATTERN_LABELS = {
    "negation":      'negation chains ("did not", "was not", "could not"…)',
    "simile_crutch": '"the way X does Y" similes',
    "balanced":      '"not X, but Y" balanced antithesis',
    "em_dash":       "em-dash overload",
    "triadic":       'triadic lists ("A, B, and C")',
}

_PATTERN_THRESHOLDS = {
    # Per-link average thresholds for "this pattern has become a branch tic."
    # Chosen so that a pattern appearing in roughly half the links of a
    # 10-link tail triggers the warning.
    "negation":      2.0,   # negations are cheap; require >2 per link average
    "simile_crutch": 0.5,   # ~half the links using "the way X does Y"
    "balanced":      0.4,   # ~4 of 10 links using "not X, but Y"
    "em_dash":       2.0,
    "triadic":       0.5,
}


def branch_voice_report(texts: list[str]) -> dict:
    """Summarize the ambient voice of a set of branch link texts.

    Returns:
      - top_ngrams: list of (phrase, count), most-repeated 3-grams
      - averages:   dict of pattern name -> per-link average count
      - present:    list of (label, avg) for patterns above threshold
    """
    top = top_repeated_ngrams(texts, n=3, min_count=2, k=5)
    avgs = count_patterns_per_link(texts)
    present = [
        (_PATTERN_LABELS[k], round(v, 2))
        for k, v in avgs.items()
        if v >= _PATTERN_THRESHOLDS.get(k, 0)
    ]
    return {"top_ngrams": top, "averages": avgs, "present": present}


# =====================================================================
# Link-draft warnings (used by write.py --review)
#
# Terminology: "link-draft" is the text of a link before submit — the file
# sitting on disk that --review is about to scan. Once submitted it becomes
# a "link" (signed, on-chain-referenced, permanent).
# =====================================================================


def warnings_for_link_draft(link_draft: str, branch_texts: list[str] | None = None) -> list[tuple[str, str]]:
    """All mechanical scans on a link-draft. Returns [(category, message), …].

    Categories:
      - "slop"       universal AI-writing tells
      - "pattern"    structural anti-patterns exceeding per-link thresholds
      - "recycling"  3-grams the link-draft shares with the branch's repeated tics
    """
    out: list[tuple[str, str]] = []

    t1 = scan_tier1(link_draft)
    if t1:
        out.append(("slop", f"tier-1 words (kill on sight): {', '.join(t1)}"))

    t2 = scan_tier2(link_draft)
    if t2:
        labels = ", ".join(f"{w}×{c}" for w, c in t2)
        out.append(("slop", f"tier-2 cluster (≥3 total): {labels}"))

    t3 = scan_tier3(link_draft)
    if t3:
        out.append(("slop", "zero-information filler: " + "; ".join(repr(p) for p in t3)))

    ft = scan_fiction_tells(link_draft)
    if ft:
        out.append(("slop", "fiction AI-tells: " + "; ".join(ft)))

    if scan_sycophancy(link_draft):
        out.append(("slop", "sycophantic opening — delete"))

    n_neg = count_negations(link_draft)
    if n_neg > 2:
        out.append(("pattern",
                    f"{n_neg} negation constructions in one link "
                    f'(did/was/could not…). Cap is ~2 per link.'))

    n_sim = count_simile_crutch(link_draft)
    if n_sim > 1:
        out.append(("pattern",
                    f"{n_sim} uses of 'the way X…' simile. Cap is ~1 per link."))

    n_bal = count_balanced_antithesis(link_draft)
    if n_bal > 0:
        out.append(("pattern",
                    f"{n_bal} 'not X, but Y' / 'not just X but Y' construction(s). "
                    f"Overused LLM rhetorical move."))

    n_em = count_em_dashes(link_draft)
    if n_em > 2:
        out.append(("pattern",
                    f"{n_em} em-dashes. >2 per link signals overuse."))

    n_tri = count_triadic_lists(link_draft)
    if n_tri > 1:
        out.append(("pattern",
                    f"{n_tri} triadic lists ('A, B, and C'). Two items are often stronger."))

    if branch_texts:
        branch_top = top_repeated_ngrams(branch_texts, n=3, min_count=2, k=10)
        link_draft_trigrams = {" ".join(g) for g in ngrams(tokenize(link_draft), 3)}
        collisions = [phrase for phrase, _ in branch_top if phrase in link_draft_trigrams]
        if collisions:
            out.append(("recycling",
                        "link-draft reuses 3-grams already repeated in this branch: "
                        + "; ".join(repr(c) for c in collisions)))

    return out
