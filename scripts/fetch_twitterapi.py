#!/usr/bin/env python3
"""
Backfill @waldenpondstate tweets via the twitterapi.io third-party API.

This fills the 2020-2026 gap the Wayback Machine can't reach. twitterapi.io
reads public tweets through its own infrastructure (no X login, no risk to any
account of yours) and bills per request.

Usage:
    export TWITTERAPI_IO_KEY=sk-...            # your api key
    python3 fetch_twitterapi.py               # full history of @waldenpondstate
    python3 fetch_twitterapi.py --since 2020-01-01 --until 2026-07-01
    python3 fetch_twitterapi.py --max-pages 10   # safety cap while testing

Output: raw_tweets_api.jsonl, one JSON object per tweet, in the SAME schema as
the Wayback scraper (tweet_id, snapshot_ts, text, url) plus created_at, so
parse_closures.py consumes both sources transparently.

Docs: https://docs.twitterapi.io  (endpoint: GET /twitter/tweet/advanced_search)
Stdlib only. Resumable: already-saved tweet ids are skipped on rerun.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = "https://api.twitterapi.io"
ACCOUNT = "waldenpondstate"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw_tweets_api.jsonl")
PAGE_SLEEP = 0.4          # politeness between pages
RETRIES = 4

def api_get(path, params, key):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "x-api-key": key,
        "User-Agent": "walden-tracker/1.0",
    })
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {body}"
            if e.code in (401, 403):        # bad key — don't hammer
                raise SystemExit(f"Auth failed ({e.code}). Check TWITTERAPI_IO_KEY.\n{body}")
            if e.code == 402:               # out of credits — stop cleanly, keep what we saved
                raise SystemExit("Out of twitterapi.io credits. Saved tweets are kept; "
                                 "recharge and rerun to resume (already-saved ids are skipped).")
            if e.code == 429:               # rate limited — back off
                time.sleep(3 * (attempt + 1)); continue
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e); time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after {RETRIES} tries: {last}")

def find_tweets(resp):
    """Locate the tweet list regardless of minor response-shape differences."""
    for k in ("tweets", "data"):
        v = resp.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and isinstance(v.get("tweets"), list):
            return v["tweets"]
    return []

def norm(t):
    tid = str(t.get("id") or t.get("id_str") or t.get("tweet_id") or "")
    text = (t.get("text") or t.get("full_text") or "").strip()
    created = t.get("createdAt") or t.get("created_at") or ""
    # created is like "Thu Dec 13 08:41:26 +0000 2018" -> ISO + compact stamp
    iso, stamp = created, ""
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
        iso = dt.isoformat()
        stamp = dt.strftime("%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        pass
    return {
        "tweet_id": tid,
        "snapshot_ts": stamp or (created[:14] if created else ""),
        "created_at": iso,
        "text": text,
        "url": f"https://twitter.com/{ACCOUNT}/status/{tid}" if tid else "",
        "source": "twitterapi.io",
    }

def build_query(args):
    q = f"from:{ACCOUNT}"
    if args.since:
        q += f" since:{args.since}_00:00:00_UTC"
    if args.until:
        q += f" until:{args.until}_00:00:00_UTC"
    return q

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--max-pages", type=int, default=0, help="0 = no cap")
    ap.add_argument("--api-key", default=os.environ.get("TWITTERAPI_IO_KEY", ""))
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("No API key. Set TWITTERAPI_IO_KEY env var or pass --api-key. "
                 "Get one at https://twitterapi.io (sign up -> dashboard).")

    seen = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try: seen.add(json.loads(line)["tweet_id"])
                except Exception: pass
        print(f"{len(seen)} tweets already saved in {OUT}; skipping those.", file=sys.stderr)

    query = build_query(args)
    print(f"Query: {query!r} (queryType=Latest)", file=sys.stderr)

    cursor, page, new = "", 0, 0
    with open(OUT, "a") as f:
        while True:
            page += 1
            resp = api_get("/twitter/tweet/advanced_search",
                           {"query": query, "queryType": "Latest", "cursor": cursor},
                           args.api_key)
            tweets = find_tweets(resp)
            for t in tweets:
                rec = norm(t)
                if not rec["tweet_id"] or rec["tweet_id"] in seen:
                    continue
                seen.add(rec["tweet_id"])
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                new += 1
            f.flush()
            oldest = tweets[-1].get("createdAt", "?") if tweets else "?"
            print(f"  page {page}: +{len(tweets)} tweets (total new {new}) oldest={oldest}",
                  file=sys.stderr)

            if not resp.get("has_next_page") or not resp.get("next_cursor"):
                break
            if args.max_pages and page >= args.max_pages:
                print("  hit --max-pages cap", file=sys.stderr); break
            cursor = resp["next_cursor"]
            time.sleep(PAGE_SLEEP)

    print(f"Done. {new} new tweets -> {OUT} ({len(seen)} total).", file=sys.stderr)

if __name__ == "__main__":
    main()
