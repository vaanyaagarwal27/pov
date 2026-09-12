#!/usr/bin/env python3
"""Use Gemini to split over-merged groups, then save to groups_clean.json."""

import json
import os
import re
from collections import Counter
from google import genai

# ── Settings ───────────────────────────────────────────────────────────────────
SUSPICIOUS_THRESHOLD = 15   # groups with more articles than this go to Gemini
INPUT_FILE  = "groups.json"
OUTPUT_FILE = "groups_clean.json"
MODEL_NAME  = "models/gemini-2.5-flash"

# Same stopwords as group.py — used to re-compute keywords for new sub-groups
STOPWORDS = {
    "the", "a", "an", "in", "of", "to", "and", "on", "for", "with", "at",
    "by", "from", "into", "about", "over", "under", "than", "through",
    "between", "before", "after", "against", "amid", "ahead", "since",
    "during", "including", "following", "per", "off", "up", "out",
    "as", "but", "or", "if", "so", "yet", "while", "when", "where",
    "how", "why", "what", "who", "its", "their", "they", "his", "her",
    "our", "we", "he", "she", "it", "this", "that", "all", "not", "no",
    "is", "are", "was", "were", "be", "been", "has", "have", "had",
    "will", "say", "says", "said",
    "new", "also", "more", "now", "still", "just", "then", "two", "three",
    "one", "first", "last", "next", "news", "india", "reuters",
}


# ── Helper functions ───────────────────────────────────────────────────────────

def extract_keywords(title):
    """Pull significant words out of a title (same logic as group.py)."""
    words = re.findall(r"[a-zA-Z]+", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) >= 3}


def build_group(articles):
    """Construct a group dict from a list of article dicts.

    Re-derives keywords (words that appear in at least 2 titles) and sources
    from the given articles — used after Gemini splits a big group apart.
    """
    kw_counter = Counter()
    for art in articles:
        for kw in extract_keywords(art.get("title", "")):
            kw_counter[kw] += 1
    shared_kws = [kw for kw, cnt in kw_counter.most_common() if cnt >= 2]
    return {
        "keywords": shared_kws,
        "sources":  sorted({art["source"] for art in articles}),
        "articles": articles,
    }


def strip_fences(text):
    """Remove ```json ... ``` or ``` ... ``` wrappers if Gemini added them."""
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text.strip())
    return re.sub(r"\n?```$", "", text.strip())


def ask_gemini_to_split(client, group):
    """Send a group's titles to Gemini and ask it to cluster by real-world event.

    Returns a list of dicts like:
        [{"label": "Event name", "articles": [1, 4, 7]}, ...]
    Returns None if the call fails or the JSON can't be parsed.
    """
    articles = group["articles"]

    # Build a numbered list of just the titles
    titles_block = ""
    for i, art in enumerate(articles, start=1):
        titles_block += f"[{i}] {art['title']}\n"

    prompt = f"""You are a news editor. The {len(articles)} article titles below were grouped by keyword matching, but keyword matching is imprecise — they may actually describe DIFFERENT real-world events.

Your job: separate them into the correct distinct events. Articles about the same event stay together. Articles about genuinely different events must go into separate groups, even if those events overlap in time or topic.

Return ONLY a JSON array. No markdown, no code fences, no explanation before or after.
Each element must have exactly two keys:
  "label"    — a short (3–7 word) description of that event
  "articles" — the list of article numbers (integers) that belong to it

Every article number from 1 to {len(articles)} must appear in exactly one group.

ARTICLE TITLES:
{titles_block}"""

    response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
    raw = response.text

    cleaned = strip_fences(raw)
    try:
        events = json.loads(cleaned)
    except json.JSONDecodeError as err:
        print(f"      ✗ JSON parse failed: {err}")
        print(f"        Raw response (first 400 chars): {raw[:400]}")
        return None

    if not isinstance(events, list):
        print(f"      ✗ Expected a list, got {type(events).__name__}")
        return None

    return events


# ── Step 1: Configure Gemini ───────────────────────────────────────────────────
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Error: GEMINI_API_KEY environment variable is not set.")
client = genai.Client(api_key=api_key)

# ── Step 2: Load groups ────────────────────────────────────────────────────────
with open(INPUT_FILE, "r", encoding="utf-8") as fh:
    original_groups = json.load(fh)

groups_before = len(original_groups)
print(f"Loaded {groups_before} groups from {INPUT_FILE}")

suspicious = [g for g in original_groups if len(g["articles"]) > SUSPICIOUS_THRESHOLD]
print(f"Suspicious groups (> {SUSPICIOUS_THRESHOLD} articles): {len(suspicious)}")
print()

# ── Step 3 & 4: Process each group ────────────────────────────────────────────
clean_groups = []          # the final flat list of groups
split_log    = []          # records for the before/after report

for orig_idx, group in enumerate(original_groups):
    n = len(group["articles"])

    if n <= SUSPICIOUS_THRESHOLD:
        # Small group — pass through as-is
        clean_groups.append(group)
        continue

    # Large group — ask Gemini to split it
    kw_preview = ", ".join(group["keywords"][:4])
    print(f"Group {orig_idx} ({n} articles) [{kw_preview}] → asking Gemini …")

    events = ask_gemini_to_split(client, group)

    if events is None or len(events) <= 1:
        # Gemini failed or said it's all one event — keep original
        reason = "parse error" if events is None else "Gemini kept as one event"
        print(f"      → Kept as one group ({reason})")
        clean_groups.append(group)
        split_log.append({"original_idx": orig_idx, "original_size": n,
                           "split": False, "reason": reason})
        continue

    # Map the 1-based article numbers from Gemini back to article dicts
    articles     = group["articles"]
    assigned_idx = set()
    sub_groups   = []

    for event in events:
        label    = event.get("label", "Unnamed event")
        nums     = event.get("articles", [])

        event_arts = []
        for num in nums:
            try:
                i = int(num) - 1          # convert 1-based → 0-based
            except (ValueError, TypeError):
                continue
            if 0 <= i < len(articles) and i not in assigned_idx:
                event_arts.append(articles[i])
                assigned_idx.add(i)

        if event_arts:
            sub_groups.append((label, event_arts))

    # Any articles Gemini forgot get their own catch-all sub-group
    leftovers = [articles[i] for i in range(len(articles)) if i not in assigned_idx]
    if leftovers:
        sub_groups.append(("Other (unassigned by Gemini)", leftovers))

    # Build a proper group dict for each sub-group and add to output
    print(f"      → Split into {len(sub_groups)} sub-groups:")
    new_group_dicts = []
    for label, arts in sub_groups:
        print(f"         • \"{label}\" — {len(arts)} articles")
        new_group_dicts.append(build_group(arts))

    clean_groups.extend(new_group_dicts)
    split_log.append({
        "original_idx":  orig_idx,
        "original_size": n,
        "split":         True,
        "sub_groups":    [(label, len(arts)) for label, arts in sub_groups],
    })

# ── Step 5: Sort final groups largest-first (same order as groups.json) ────────
clean_groups.sort(key=lambda g: len(g["articles"]), reverse=True)

# ── Step 5b: Drop single-source groups ────────────────────────────────────────
# A real story needs coverage from at least 2 different papers.
multi_source  = [g for g in clean_groups if len(g["sources"]) >= 2]
dropped_count = len(clean_groups) - len(multi_source)
clean_groups  = multi_source

print(f"Single-source groups dropped : {dropped_count}")
print(f"Groups remaining             : {len(clean_groups)}")

# ── Step 6: Save to groups_clean.json ─────────────────────────────────────────
with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
    json.dump(clean_groups, fh, ensure_ascii=False, indent=2)

# ── Step 7: Before / after report ─────────────────────────────────────────────
groups_after = len(clean_groups)
print()
print("=" * 65)
print("BEFORE / AFTER REPORT")
print("=" * 65)
print(f"  Groups before (from groups.json) : {groups_before}")
print(f"  After splitting                  : {groups_before + sum(len(e['sub_groups']) - 1 for e in split_log if e['split'])}")
print(f"  Dropped (single-source)          : {dropped_count}")
print(f"  Groups after                     : {groups_after}")
print(f"  Saved to                         : {OUTPUT_FILE}")

if split_log:
    print()
    print("Groups that were processed:")
    for entry in split_log:
        idx = entry["original_idx"]
        n   = entry["original_size"]
        if entry["split"]:
            print(f"\n  Group {idx} ({n} articles) → SPLIT into {len(entry['sub_groups'])}:")
            for label, count in entry["sub_groups"]:
                print(f"    [{count:3d} articles]  {label}")
        else:
            print(f"\n  Group {idx} ({n} articles) → KEPT  ({entry['reason']})")

print("=" * 65)
