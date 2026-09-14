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

# ── Region normalisation ───────────────────────────────────────────────────────
_ALLOWED_REGIONS = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya",
    "mizoram", "nagaland", "odisha", "punjab", "rajasthan", "sikkim",
    "tamil nadu", "telangana", "tripura", "uttar pradesh", "uttarakhand",
    "west bengal", "delhi", "jammu and kashmir", "national",
    "united states", "europe", "gulf", "china", "south asia", "middle east",
    "east asia", "africa", "latin america", "global",
}

def normalise_regions(regions):
    seen = set()
    kept = []
    dropped = []
    for r in regions:
        v = r.strip().lower()
        if v == "india":
            v = "national"
        if v in _ALLOWED_REGIONS:
            if v not in seen:
                seen.add(v)
                kept.append(v)
        else:
            dropped.append(r)
    if dropped:
        if "national" not in seen:
            if "global" not in seen:
                kept.append("global")
                seen.add("global")
        print(f"dropped regions: {dropped} -> {kept}")
    if not kept:
        return ["national"]
    return kept


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
  "regions": ["1–4 values from the fixed list below; see REGIONS RULES"],
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

REGIONS RULES:
The "regions" field must contain 1 to 4 values drawn ONLY from the two lists below. Never invent a value.

INDIAN states/territories (use exact lowercase spelling):
andhra pradesh, arunachal pradesh, assam, bihar, chhattisgarh, goa, gujarat, haryana, himachal pradesh, jharkhand, karnataka, kerala, madhya pradesh, maharashtra, manipur, meghalaya, mizoram, nagaland, odisha, punjab, rajasthan, sikkim, tamil nadu, telangana, tripura, uttar pradesh, uttarakhand, west bengal, delhi, jammu and kashmir, national

WORLD buckets (use exact lowercase spelling):
united states, europe, gulf, china, south asia, middle east, east asia, africa, latin america, global

Mapping rules — never write a country, city, sea or continent name not on the list; use the bucket instead:
- "national" means all of India. Never write "india".
- "gulf" = UAE, Saudi Arabia, Qatar, Kuwait, Oman, Bahrain.
- "middle east" = Israel, Iran, Yemen, Iraq, Syria, Palestine, Lebanon.
- "south asia" = Pakistan, Bangladesh, Sri Lanka, Nepal, Afghanistan.
- "east asia" = Japan, Korea, Taiwan. China gets its own value, "china".
- "global" is for stories that are worldwide or fit no bucket. It is NOT the default for anything foreign.
- A story about an Indian state that is also nationally significant gets both the state and "national".

Examples:
  BRICS summit, India and China attending → ["national", "china", "global"]   NOT ["India", "China", "Brazil"]
  Houthi attacks on Red Sea shipping, Saudi pipeline shut → ["middle east", "gulf", "global"]   NOT ["Saudi Arabia", "Yemen", "Red Sea"]
  India vs Sri Lanka Asia Cup final in Dubai → ["national", "south asia"]   NOT ["India", "Sri Lanka", "Dubai", "Asia"]
  Tamil Nadu bypolls → ["tamil nadu", "national"]

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

story["regions"] = normalise_regions(story.get("regions", []))

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
