#!/usr/bin/env python3
"""Send one group of articles to Gemini and save a structured story to story.json."""

import json
import os
import re
from google import genai

# ── Settings ───────────────────────────────────────────────────────────────────
GROUP_INDEX  = 4   # which group from groups.json to process (0 = first)
MAX_ARTICLES = 8   # cap on how many articles to send to Gemini

GROUPS_FILE = "groups.json"
OUTPUT_FILE = "story.json"
MODEL_NAME  = "models/gemini-2.5-flash"

# ── Configure Gemini with the API key from the environment ─────────────────────
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Error: GEMINI_API_KEY environment variable is not set.")
client = genai.Client(api_key=api_key)

# ── Load groups and pick the one we want ──────────────────────────────────────
with open(GROUPS_FILE, "r", encoding="utf-8") as fh:
    groups = json.load(fh)

if GROUP_INDEX >= len(groups):
    raise SystemExit(f"Error: GROUP_INDEX={GROUP_INDEX} but groups.json only has {len(groups)} groups.")

group = groups[GROUP_INDEX]

# Show what we're about to process
keywords_display = ", ".join(group["keywords"][:6])
print(f"Group #{GROUP_INDEX}")
print(f"  Keywords : {keywords_display}")
print(f"  Sources  : {', '.join(group['sources'])}")
print(f"  Articles : {len(group['articles'])} total, using first {MAX_ARTICLES}")
print()

# Take a slice of the articles to keep the prompt a manageable size
articles_slice = group["articles"][:MAX_ARTICLES]

# ── Build the prompt ──────────────────────────────────────────────────────────
# Each article is condensed to title + source + summary.
articles_text = ""
for i, art in enumerate(articles_slice, start=1):
    articles_text += (
        f"[{i}] Source: {art['source']}\n"
        f"    Title: {art['title']}\n"
        f"    Summary: {art['summary']}\n\n"
    )

# The prompt tells Gemini exactly what shape to return and forbids any wrapping.
prompt = f"""You are a neutral news analyst. I will give you {len(articles_slice)} articles about the same news event from different publications. Analyse them and return a single JSON object.

RULES:
- Return ONLY the JSON object. No markdown, no code fences, no preamble, no explanation.
- Every field must be present even if the list is empty.
- Be neutral. Do not favour any outlet.

REQUIRED JSON SHAPE (fill in the values, keep the exact keys):
{{
  "headline": "one neutral sentence summarising the event, no spin",
  "read_seconds": 90,
  "affects": ["who this story affects, e.g. students, commuters, investors"],
  "regions": ["geographic regions this story concerns, e.g. Karnataka, national, global"],
  "agreed_facts": [
    {{"text": "a fact most or all papers agree on", "sources": ["Paper A", "Paper B"]}}
  ],
  "contested": [
    {{"point": "what the papers disagree on or frame differently",
      "positions": [
        {{"outlet": "Paper A", "claim": "how Paper A framed this point"}},
        {{"outlet": "Paper B", "claim": "how Paper B framed this point"}}
      ]
    }}
  ],
  "framing": [
    {{"outlet": "Paper A", "note": "the angle or emphasis this paper led with"}}
  ],
  "jargon": [
    {{"term": "a technical or uncommon word from the story", "plain": "one plain-English sentence explaining it"}}
  ],
  "people": [
    {{"name": "full name of a person mentioned", "who": "one sentence on who they are and their role in this story"}}
  ]
}}

ARTICLES:
{articles_text}"""

# ── Call Gemini ────────────────────────────────────────────────────────────────
print(f"Sending to Gemini ({MODEL_NAME}) …")
response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
raw      = response.text
print("Response received.\n")

# ── Strip code fences if Gemini added them despite being told not to ───────────
# Matches ```json ... ``` or ``` ... ``` with optional whitespace
cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip())
cleaned = re.sub(r"\n?```$", "", cleaned.strip())

# ── Parse the JSON safely ──────────────────────────────────────────────────────
try:
    story = json.loads(cleaned)
except json.JSONDecodeError as err:
    print("JSON parsing failed. Raw response from Gemini:")
    print(raw)
    raise SystemExit(f"Parse error: {err}")

# ── Save to story.json ─────────────────────────────────────────────────────────
with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
    json.dump(story, fh, ensure_ascii=False, indent=2)
print(f"Saved to {OUTPUT_FILE}\n")

# ── Print a readable version ───────────────────────────────────────────────────
print("=" * 70)
print(f"HEADLINE: {story.get('headline', '')}")
print(f"Read time: ~{story.get('read_seconds', '?')} seconds")
print(f"Affects : {', '.join(story.get('affects', []))}")
print(f"Regions : {', '.join(story.get('regions', []))}")

print("\nAGREED FACTS:")
for fact in story.get("agreed_facts", []):
    srcs = ", ".join(fact.get("sources", []))
    print(f"  • {fact.get('text', '')}  [{srcs}]")

print("\nCONTESTED POINTS:")
for c in story.get("contested", []):
    print(f"  Point: {c.get('point', '')}")
    for pos in c.get("positions", []):
        print(f"    {pos.get('outlet', ''):20s} → {pos.get('claim', '')}")

print("\nFRAMING BY OUTLET:")
for f in story.get("framing", []):
    print(f"  {f.get('outlet', ''):20s} → {f.get('note', '')}")

print("\nJARGON EXPLAINED:")
for j in story.get("jargon", []):
    print(f"  {j.get('term', '')}: {j.get('plain', '')}")

print("\nPEOPLE MENTIONED:")
for p in story.get("people", []):
    print(f"  {p.get('name', '')}: {p.get('who', '')}")

print("=" * 70)
