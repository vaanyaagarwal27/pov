#!/usr/bin/env python3
"""Throwaway script: test interest + region filtering against stories.json."""

import json

# ── Pretend user preferences ───────────────────────────────────────────────────
USER_INTERESTS = ["politics", "economy"]
USER_STATE     = "Karnataka"

with open("stories.json", "r", encoding="utf-8") as fh:
    stories = json.load(fh)

matched = 0
unmatched = 0

for story in stories:
    headline = story.get("headline", "(no headline)")
    topics   = story.get("topics",  [])
    regions  = story.get("regions", [])

    # Match if any topic overlaps with interests, OR region is state or national
    topic_hit  = bool(set(topics) & set(USER_INTERESTS))
    region_hit = USER_STATE in regions or "national" in regions
    match      = topic_hit or region_hit

    label = "MATCH" if match else "skip"
    print(f"[{label}] {headline}")
    print(f"       topics={topics}  regions={regions}")
    print()

    if match:
        matched += 1
    else:
        unmatched += 1

print(f"Matched: {matched}  |  Skipped: {unmatched}  |  Total: {matched + unmatched}")
