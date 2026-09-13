#!/usr/bin/env python3
"""Fetch RSS feeds from Indian newspapers and save articles to articles.json."""

import feedparser
import json
import hashlib
import os
import re
import socket
from datetime import datetime, timedelta
from collections import defaultdict

# Every network call will give up after 10 seconds
socket.setdefaulttimeout(10)

# ── Edit this list to add or remove feeds ──────────────────────────────────────
# Each entry needs a human-readable "source" name and a "url" for the RSS feed.
#
# Notes on specific sources:
#   Reuters / AP  — Neither outlet provides a public RSS feed anymore. The URLs
#                   below are Google News search feeds filtered to their sites;
#                   they return real Reuters/AP articles with original links.
#   The Signal    — The Core Newsletter (tcd.thecore.in), the daily Indian
#                   business/economy briefing published under The Signal brand.
#   The Boring    — No working public RSS feed was found for The Boring News
#                   (theboringnews.com). It has been left out of this list.
#   The Telegraph — telegraphindia.com blocks feed requests (HTTP 403).
#   The Pioneer   — dailypioneer.com returns an HTML page instead of XML.
#                   Both are kept below so they appear in the test report.
FEEDS = [
    # ── Indian national ────────────────────────────────────────────────────────
    {
        "source": "The Hindu",
        "url": "https://www.thehindu.com/news/national/?service=rss",
    },
    {
        "source": "Indian Express",
        "url": "https://indianexpress.com/feed/",
    },
    {
        "source": "Times of India",
        "url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    },
    {
        "source": "NDTV",
        "url": "https://feeds.feedburner.com/NDTV-LatestNews",
    },
    {
        "source": "India Today",
        "url": "https://www.indiatoday.in/rss/1206578",
    },
    {
        "source": "Livemint",
        "url": "https://www.livemint.com/rss/news",
    },
    {
        "source": "Hindustan Times",
        "url": "https://www.hindustantimes.com/feeds/rss/latest/rssfeed.xml",
    },
    {
        "source": "The Telegraph",
        "url": "https://www.telegraphindia.com/feeds/rss.jsp?id=4",
    },
    {
        "source": "New Indian Express",
        "url": "https://prod-qt-images.s3.amazonaws.com/production/newindianexpress/feed.xml",
    },
    {
        "source": "Economic Times",
        "url": "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
    },
    {
        "source": "The Pioneer",
        "url": "https://www.dailypioneer.com/rss.xml",
    },
    {
        "source": "Business Standard",
        "url": "https://www.business-standard.com/rss/india-news-216.rss",
    },
    # ── Karnataka ──────────────────────────────────────────────────────────────
    {
        "source": "Deccan Herald",
        "url": "https://www.deccanherald.com/feed",
    },
    {
        "source": "Bangalore Mirror",
        "url": "https://bangaloremirror.indiatimes.com/rss/bangalore/rssfeed.cms",
    },
    # ── International ──────────────────────────────────────────────────────────
    {
        "source": "Reuters",
        "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-IN&gl=IN&ceid=IN:en",
    },
    {
        "source": "AP",
        "url": "https://news.google.com/rss/search?q=site:apnews.com&hl=en-IN&gl=IN&ceid=IN:en",
    },
    {
        "source": "BBC",
        "url": "https://feeds.bbci.co.uk/news/rss.xml",
    },
    {
        "source": "The Guardian",
        "url": "https://www.theguardian.com/world/rss",
    },
    {
        "source": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
    },
    # ── Indian digital outlets ─────────────────────────────────────────────────
    {
        "source": "The Signal",
        "url": "https://tcd.thecore.in/feed",
    },
]

# The file where all articles are stored
OUTPUT_FILE = "articles.json"

# Articles older than this many hours are dropped when saving
CUTOFF_HOURS = 48


# ── Helper functions ───────────────────────────────────────────────────────────

def strip_html(text):
    """Remove HTML tags (like <b>, <p>, <img ...>) and return plain text."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def make_id(link):
    """Create a short unique ID by MD5-hashing the article URL."""
    return hashlib.md5(link.encode("utf-8")).hexdigest()


def parse_date(entry):
    """Return the publication date as an ISO string (e.g. '2026-09-12T10:30:00').
    Returns an empty string if no date is available."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6]).isoformat()
        except Exception:
            pass
    return ""


def fetch_feed(source, url):
    """Download and parse one RSS feed.

    Returns a tuple: (list_of_article_dicts, error_message).
    On success, error_message is None.
    On failure, the list is empty and error_message explains what went wrong.
    """
    try:
        feed = feedparser.parse(url)

        # bozo=True means the feed had XML errors. We still try to use any
        # entries that were parsed; only give up if there are none at all.
        if feed.bozo and not feed.entries:
            return [], str(feed.bozo_exception)

        articles = []
        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            if not link:
                continue  # skip entries without a URL

            title = getattr(entry, "title", "") or ""

            # feedparser puts <description> content into entry.summary.
            # For Atom feeds, full text may be in entry.content instead.
            raw_summary = getattr(entry, "summary", "")
            if not raw_summary and hasattr(entry, "content") and entry.content:
                raw_summary = entry.content[0].get("value", "")

            articles.append({
                "id":        make_id(link),
                "title":     title,
                "summary":   strip_html(raw_summary),
                "link":      link,
                "source":    source,
                "published": parse_date(entry),
            })

        return articles, None

    except Exception as e:
        # Catch anything unexpected (timeout, DNS failure, etc.) so one
        # broken feed never stops the whole script.
        return [], str(e)


def load_existing(path):
    """Load the articles already saved on disk.
    Returns a dict keyed by article id so lookups are fast."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {a["id"]: a for a in data if "id" in a}
    except Exception as e:
        print(f"Warning: could not read {path} ({e}). Starting fresh.")
        return {}


def save_articles(path, articles_dict):
    """Write all articles to disk as a JSON list."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(articles_dict.values()), f, ensure_ascii=False, indent=2)


# ── Step 1: Test every feed and print a report ─────────────────────────────────
print("=" * 62)
print("FEED TEST REPORT")
print("=" * 62)
print(f"  {'STATUS':<6}  {'SOURCE':<20}  ARTICLES / ERROR")
print("-" * 62)

all_fetched = []  # collects articles from every feed that worked

for feed_info in FEEDS:
    source = feed_info["source"]
    url    = feed_info["url"]
    articles, error = fetch_feed(source, url)

    if error:
        print(f"  {'FAIL':<6}  {source:<20}  {error}")
    else:
        print(f"  {'OK':<6}  {source:<20}  {len(articles)} articles")
        all_fetched.extend(articles)

print("=" * 62)
print()

# ── Step 2: Load articles that were saved in a previous run ────────────────────
existing = load_existing(OUTPUT_FILE)
initial_count = len(existing)

# ── Step 3: Add only articles that are not already in the file ────────────────
added = 0
for article in all_fetched:
    if article["id"] not in existing:
        existing[article["id"]] = article
        added += 1

# ── Step 3b: Drop articles older than CUTOFF_HOURS ────────────────────────────
# Articles with a missing or unparseable date are kept so real news isn't lost.
cutoff = datetime.utcnow() - timedelta(hours=CUTOFF_HOURS)
kept = {}
dropped = 0
for aid, article in existing.items():
    pub = article.get("published", "")
    try:
        date = datetime.fromisoformat(pub) if pub else None
    except ValueError:
        date = None
    if date is None or date >= cutoff:
        kept[aid] = article
    else:
        dropped += 1
existing = kept
print(f"Recency filter ({CUTOFF_HOURS}h): kept {len(existing)}, dropped {dropped} as too old")

# ── Step 4: Save the updated collection back to disk ──────────────────────────
save_articles(OUTPUT_FILE, existing)

# ── Step 5: Print a final summary ─────────────────────────────────────────────
print("SUMMARY")
print("-" * 40)
print(f"Total articles in file : {len(existing)}")
print(f"New articles this run  : {added}")
print()

# Count how many articles came from each source
counts = defaultdict(int)
for article in existing.values():
    counts[article.get("source", "Unknown")] += 1

print("Articles by source:")
for source, count in sorted(counts.items()):
    print(f"  {source:<22} {count}")
