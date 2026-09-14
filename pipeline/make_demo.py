#!/usr/bin/env python3
"""Generate 5 story summaries in parallel from groups_clean.json → stories.json."""

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai

# ── Settings ───────────────────────────────────────────────────────────────────
NUM_GROUPS   = 5
MAX_ARTICLES = 8
INPUT_FILE   = "groups_clean.json"
OUTPUT_FILE  = "stories.json"
MODEL_NAME   = "models/gemini-2.5-flash"

# ── Gemini setup ───────────────────────────────────────────────────────────────
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Error: GEMINI_API_KEY environment variable is not set.")
client = genai.Client(api_key=api_key)


# ── Helpers ────────────────────────────────────────────────────────────────────

def strip_fences(text):
    """Remove ```json … ``` wrappers if Gemini added them despite being told not to."""
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text.strip())
    return re.sub(r"\n?```$", "", text.strip())


def latest_date(articles):
    """Return the most recent published date from a list of article dicts, or ''."""
    dates = [a["published"] for a in articles if a.get("published")]
    return max(dates) if dates else ""


def make_story(group_idx, group):
    """Call Gemini for one group and return the finished story dict.

    Returns None if the call or JSON parse fails, so the caller can skip it.
    This function is run in a worker thread — one thread per group.
    """
    articles = group["articles"][:MAX_ARTICLES]

    # Build the articles block that goes into the prompt
    articles_text = ""
    for i, art in enumerate(articles, start=1):
        articles_text += (
            f"[{i}] Source: {art['source']}\n"
            f"    Title:   {art['title']}\n"
            f"    Summary: {art['summary']}\n\n"
        )

    prompt = f"""You are a neutral news analyst. I will give you {len(articles)} articles about the same news event from different publications. Analyse them and return a single JSON object.

RULES:
- Return ONLY the JSON object. No markdown, no code fences, no preamble, no explanation.
- Every field must be present even if the list is empty.
- Be neutral. Do not favour any outlet.
- Obey every length limit below exactly — do not exceed them.
- "topics": choose 1 to 3 tags from this list only: politics, economy, jobs, climate, tech, sport, campus, courts, culture, health, crime, world

REQUIRED JSON SHAPE (fill in the values, keep the exact keys):
{{
  "headline": "max 10 words, punchy newspaper headline style, no spin",
  "read_seconds": 90,
  "topics": ["politics", "world"],
  "affects": ["max 3 tags, each 1-3 words, e.g. commuters"],
  "regions": ["geographic regions this story concerns, e.g. Karnataka, national, global"],
  "agreed_facts": [
    {{"text": "a fact most or all papers agree on — at most 4 items total", "sources": ["Paper A", "Paper B"]}}
  ],
  "contested": [
    {{"point": "what the papers disagree on or frame differently — at most 2 items total",
      "positions": [
        {{"outlet": "Paper A", "claim": "how Paper A framed this point"}},
        {{"outlet": "Paper B", "claim": "how Paper B framed this point"}}
      ]
    }}
  ],
  "framing": [
    {{"outlet": "Paper A", "note": "the angle this paper led with — at most 3 outlets, pick the most different ones"}}
  ],
  "jargon": [
    {{"term": "a hard word from the story — at most 3 terms, pick the hardest", "plain": "one plain-English sentence"}}
  ],
  "people": [
    {{"name": "full name — at most 3 people, the most important ones", "who": "one sentence on who they are"}}
  ]
}}

ARTICLES:
{articles_text}"""

    delays = [2, 8, 20]
    for attempt, wait in enumerate(delays, start=1):
        try:
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
            raw = response.text
            break
        except Exception as exc:
            print(f"  [group {group_idx}] attempt {attempt} failed: {exc} — retrying in {wait}s")
            time.sleep(wait)
    else:
        return None

    cleaned = strip_fences(raw)
    try:
        story = json.loads(cleaned)
    except json.JSONDecodeError as err:
        print(f"  [group {group_idx}] ✗ JSON parse failed: {err}")
        print(f"  [group {group_idx}]   Raw (first 400 chars): {raw[:400]}")
        return None

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

with ThreadPoolExecutor(max_workers=len(selected)) as pool:
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
