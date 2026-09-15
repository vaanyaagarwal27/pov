#!/usr/bin/env python3
"""Generate 5 story summaries in parallel from groups_clean.json → stories.json."""

import hashlib
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from summarise import build_prompt, strip_fences, normalise_regions

# ── Settings ───────────────────────────────────────────────────────────────────
NUM_GROUPS   = 30
MAX_ARTICLES = 8
INPUT_FILE   = "groups_clean.json"
OUTPUT_FILE  = "stories.json"
MODEL_NAME   = "models/gemini-2.5-flash"

# ── Gemini setup ───────────────────────────────────────────────────────────────
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Error: GEMINI_API_KEY environment variable is not set.")
client = genai.Client(api_key=api_key)


# ── Quota circuit-breaker ──────────────────────────────────────────────────────
_quota_exhausted     = threading.Event()
_quota_lock          = threading.Lock()
_consecutive_429s    = [0]   # mutable so threads can share it via closure


# ── Helpers ────────────────────────────────────────────────────────────────────

def latest_date(articles):
    """Return the most recent published date from a list of article dicts, or ''."""
    dates = [a["published"] for a in articles if a.get("published")]
    return max(dates) if dates else ""


def make_story(group_idx, group):
    """Call Gemini for one group and return the finished story dict.

    Returns None if the call or JSON parse fails, so the caller can skip it.
    This function is run in a worker thread — one thread per group.
    """
    if _quota_exhausted.is_set():
        return None

    articles = group["articles"][:MAX_ARTICLES]
    prompt = build_prompt(articles)

    time.sleep(1.5)
    all_429 = True
    delays = [2, 8, 20]
    for attempt, wait in enumerate(delays, start=1):
        try:
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
            raw = response.text
            with _quota_lock:
                _consecutive_429s[0] = 0
            break
        except Exception as exc:
            if "429" in str(exc):
                print(f"  [group {group_idx}] attempt {attempt} rate-limited (429) — retrying in 65s")
                time.sleep(65)
            else:
                all_429 = False
                print(f"  [group {group_idx}] attempt {attempt} failed: {exc} — retrying in {wait}s")
                time.sleep(wait)
    else:
        if all_429:
            with _quota_lock:
                _consecutive_429s[0] += 1
                if _consecutive_429s[0] >= 2 and not _quota_exhausted.is_set():
                    _quota_exhausted.set()
                    print("daily quota exhausted — skipping remaining groups")
        return None

    cleaned = strip_fences(raw)
    try:
        story = json.loads(cleaned)
    except json.JSONDecodeError as err:
        print(f"  [group {group_idx}] ✗ JSON parse failed: {err}")
        print(f"  [group {group_idx}]   Raw (first 400 chars): {raw[:400]}")
        return None

    story["regions"] = normalise_regions(story.get("regions", []))

    # Add fields that are computed from the group structure, not by Gemini
    story["id"]           = hashlib.md5(
                                "|".join(sorted(a["link"] for a in group["articles"])).encode()
                            ).hexdigest()
    story["source_count"] = len(group["sources"])
    story["published"]    = latest_date(group["articles"])

    print(f"  [group {group_idx}] ✓ {story.get('headline', '')[:65]}")
    return story


# ── Main ───────────────────────────────────────────────────────────────────────
with open(INPUT_FILE, "r", encoding="utf-8") as fh:
    groups = json.load(fh)

selected = groups[:NUM_GROUPS]
print(f"Loaded {len(groups)} groups — processing first {len(selected)} in parallel …\n")

# Run one Gemini call per group concurrently.
# We allocate a fixed slot per group so the final list is in the original order
# regardless of which thread finishes first.
stories = [None] * len(selected)

with ThreadPoolExecutor(max_workers=2) as pool:
    futures = {
        pool.submit(make_story, idx, group): idx
        for idx, group in enumerate(selected)
    }
    for future in as_completed(futures):
        idx = futures[future]
        try:
            stories[idx] = future.result()
        except Exception:
            print(f"  [group {idx}] ✗ failed after retries — skipped")

# Drop failed stories (None slots) while preserving order
stories = [s for s in stories if s is not None]
failed = len(selected) - len(stories)

with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
    json.dump(stories, fh, ensure_ascii=False, indent=2)

print(f"\nSaved {len(stories)} / {len(selected)} stories ({failed} failed)")
