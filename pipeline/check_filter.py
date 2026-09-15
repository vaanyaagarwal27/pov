#!/usr/bin/env python3
"""Read stories.json and print a filtering/feed report. Changes nothing."""

import json

INPUT_FILE       = "stories.json"
SOURCE_THRESHOLD = 6
READ_BUDGET      = 480

USERS = [
    {"name": "A", "interests": ["politics", "world"],  "state": "karnataka"},
    {"name": "B", "interests": ["sport"],              "state": "karnataka"},
    {"name": "C", "interests": ["tech", "economy"],    "state": "maharashtra"},
    {"name": "D", "interests": ["culture", "campus"],  "state": "karnataka"},
]

with open(INPUT_FILE, "r", encoding="utf-8") as fh:
    stories = json.load(fh)

total = len(stories)

# ── PART 1: threshold table ────────────────────────────────────────────────────
print("PART 1 — source_count threshold")
for n in range(2, 13):
    count = sum(1 for s in stories if s.get("source_count", 0) >= n)
    pct   = round(count / total * 100) if total else 0
    print(f"  N={n}: {count} stories ({pct}%)")

print()

# ── PART 2: test user feeds ────────────────────────────────────────────────────
print("PART 2 — test user feeds")

for user in USERS:
    name      = user["name"]
    interests = {t.lower() for t in user["interests"]}
    state     = user["state"].lower()

    # Decide inclusion and tag for every story
    included = []
    for s in stories:
        topics      = {t.lower() for t in s.get("topics", [])}
        sc          = s.get("source_count", 0)
        topic_match = bool(topics & interests)
        big         = sc >= SOURCE_THRESHOLD

        if topic_match or big:
            tag = "both" if (topic_match and big) else ("interest" if topic_match else "big")
            included.append((s, tag))

    # Sort: state/national stories first; within each half, descending source_count
    def sort_key(item, _state=state):
        s, _ = item
        regions = {r.lower() for r in s.get("regions", [])}
        local   = _state in regions or "national" in regions
        return (0 if local else 1, -s.get("source_count", 0))

    interest_list = sorted([i for i in included if i[1] in ("interest", "both")], key=sort_key)
    big_list      = sorted([i for i in included if i[1] == "big"],                key=sort_key)

    # Alternate INTEREST / BIG; drain whichever remains when one list is exhausted
    feed          = []
    total_seconds = 0
    i_idx = b_idx = 0
    turn  = 0  # 0 = take from interest_list, 1 = take from big_list
    while total_seconds < READ_BUDGET:
        if turn == 0 and i_idx < len(interest_list):
            item = interest_list[i_idx]; i_idx += 1
        elif turn == 1 and b_idx < len(big_list):
            item = big_list[b_idx]; b_idx += 1
        elif i_idx < len(interest_list):
            item = interest_list[i_idx]; i_idx += 1
        elif b_idx < len(big_list):
            item = big_list[b_idx]; b_idx += 1
        else:
            break
        feed.append(item)
        total_seconds += item[0].get("read_seconds", 0)
        turn = 1 - turn

    # Tally tags
    tag_counts = {"interest": 0, "big": 0, "both": 0}
    for _, tag in feed:
        tag_counts[tag] += 1

    print()
    print(f"User {name}  interests: {', '.join(user['interests'])}  |  state: {user['state']}")
    for s, tag in feed:
        headline   = s.get("headline", "")
        sc         = s.get("source_count", 0)
        secs       = s.get("read_seconds", 0)
        topics_str = ",".join(s.get("topics", []))
        print(f"  [{tag}] {headline} ({sc} papers, {secs}s, topics: {topics_str})")

    print(
        f"  included before read-time cut: {len(included)} | "
        f"in feed: {len(feed)} | "
        f"{tag_counts['interest']} interest / {tag_counts['big']} big / {tag_counts['both']} both"
    )
