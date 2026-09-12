#!/usr/bin/env python3
"""Group articles from articles.json by shared news event, save to groups.json."""

import json
import re
from datetime import datetime
from collections import defaultdict, Counter

# ── Tuning — change these two numbers to make grouping tighter or looser ───────
MIN_SHARED_KEYWORDS = 3   # articles must share at least this many keywords
MAX_DAY_GAP         = 3   # articles must be published within this many days

INPUT_FILE  = "articles.json"
OUTPUT_FILE = "groups.json"

# Words stripped from titles before matching.
# If groups look noisy, move common false-match words here.
# If groups are too small, remove words here that you want to count as signal.
STOPWORDS = {
    # articles / prepositions
    "the", "a", "an", "in", "of", "to", "and", "on", "for", "with", "at",
    "by", "from", "into", "about", "over", "under", "than", "through",
    "between", "before", "after", "against", "amid", "ahead", "since",
    "during", "including", "following", "per", "off", "up", "out",
    # conjunctions / pronouns
    "as", "but", "or", "if", "so", "yet", "while", "when", "where",
    "how", "why", "what", "who", "its", "their", "they", "his", "her",
    "our", "we", "he", "she", "it", "this", "that", "all", "not", "no",
    # verbs / auxiliaries
    "is", "are", "was", "were", "be", "been", "has", "have", "had",
    "will", "say", "says", "said",
    # common news filler words
    "new", "also", "more", "now", "still", "just", "then", "two", "three",
    "one", "first", "last", "next", "news", "india", "reuters"
}


# ── Helper functions ───────────────────────────────────────────────────────────

def extract_keywords(title):
    """Return a set of significant lowercase words from a title.

    Strips punctuation, removes stopwords, and drops very short words (< 3 chars)
    which tend to be noise (prepositions, abbreviations, etc.).
    """
    words = re.findall(r"[a-zA-Z]+", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) >= 3}


def parse_date(date_str):
    """Convert an ISO date string like '2026-09-12T10:30:00' to a datetime.
    Returns None if the string is blank or cannot be parsed."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


def within_window(date_a, date_b):
    """Return True if the two dates are within MAX_DAY_GAP days of each other.
    If either date is missing we allow the pair (can't disqualify what we can't measure)."""
    if date_a is None or date_b is None:
        return True
    gap_seconds = abs((date_a - date_b).total_seconds())
    return gap_seconds <= MAX_DAY_GAP * 86_400


# ── Step 1: Load articles ──────────────────────────────────────────────────────
print(f"Loading {INPUT_FILE} …")
with open(INPUT_FILE, "r", encoding="utf-8") as fh:
    articles = json.load(fh)
print(f"  {len(articles)} articles loaded.\n")

# ── Step 2: Pre-compute keywords and dates for every article ───────────────────
# Doing this once up front is much faster than re-computing inside the pair loop.
kw_list   = [extract_keywords(a.get("title", "")) for a in articles]
date_list = [parse_date(a.get("published", ""))    for a in articles]

# ── Step 3: Find all pairs that should be in the same group ────────────────────
# adjacency[i] = set of article indices that article i is connected to
adjacency = {}   # only populated for articles that match at least one other

n = len(articles)
for i in range(n):
    for j in range(i + 1, n):

        # Fast check first: both keyword sets need to be non-empty
        if not kw_list[i] or not kw_list[j]:
            continue

        # Date window check (cheap)
        if not within_window(date_list[i], date_list[j]):
            continue

        # Keyword overlap check (slightly more expensive, so done last)
        shared = kw_list[i] & kw_list[j]
        if len(shared) >= MIN_SHARED_KEYWORDS:
            adjacency.setdefault(i, set()).add(j)
            adjacency.setdefault(j, set()).add(i)

print(f"  {len(adjacency)} articles have at least one match.\n")

# ── Step 4: Find connected components using BFS ────────────────────────────────
# A "connected component" is a cluster of articles where every article is
# reachable from every other through the chain of keyword matches.
visited    = [False] * n
components = []   # each element is a list of article indices

for start in range(n):
    # Skip articles already assigned to a component, or with no connections
    if visited[start] or start not in adjacency:
        continue

    # BFS: explore all articles reachable from this one
    component = []
    queue     = [start]
    visited[start] = True

    while queue:
        node = queue.pop()
        component.append(node)
        for neighbor in adjacency.get(node, set()):
            if not visited[neighbor]:
                visited[neighbor] = True
                queue.append(neighbor)

    components.append(component)

# ── Step 5: Keep only groups covered by 2 or more different papers ─────────────
multi_source = []
for comp in components:
    sources = {articles[i]["source"] for i in comp}
    if len(sources) >= 2:
        multi_source.append(comp)

# Sort largest groups first — the most-covered stories appear at the top
multi_source.sort(key=lambda g: len(g), reverse=True)

# ── Step 6: Build the output objects ──────────────────────────────────────────
output_groups = []

for comp in multi_source:
    # Count how often each keyword appears across articles in this group.
    # Keywords that appear in at least 2 articles are the "shared" ones.
    kw_counter = Counter()
    for i in comp:
        for kw in kw_list[i]:
            kw_counter[kw] += 1
    shared_kws = [kw for kw, cnt in kw_counter.most_common() if cnt >= 2]

    group_articles = [
        {
            "title":   articles[i].get("title",   ""),
            "source":  articles[i].get("source",  ""),
            "summary": articles[i].get("summary", ""),
            "link":    articles[i].get("link",    ""),
        }
        for i in comp
    ]

    output_groups.append({
        "keywords": shared_kws,
        "sources":  sorted({articles[i]["source"] for i in comp}),
        "articles": group_articles,
    })

# ── Step 7: Save to groups.json ───────────────────────────────────────────────
with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
    json.dump(output_groups, fh, ensure_ascii=False, indent=2)

# ── Step 8: Print the report ──────────────────────────────────────────────────
print(f"Results saved to {OUTPUT_FILE}\n")
print(f"Groups found : {len(output_groups)}")
print(f"  (settings: >= {MIN_SHARED_KEYWORDS} shared keywords, within {MAX_DAY_GAP} days)")
print()

print("Top 10 groups by article count:")
print("=" * 72)

for rank, group in enumerate(output_groups[:10], start=1):
    kw_display  = ", ".join(group["keywords"][:8])  # show at most 8 keywords
    src_display = ", ".join(group["sources"])

    print(f"#{rank}  [{len(group['articles'])} articles]  {kw_display}")
    print(f"     Sources: {src_display}")
    for art in group["articles"]:
        title_short = art["title"][:62]
        print(f"     · {art['source']:22s}  {title_short}")
    print()
