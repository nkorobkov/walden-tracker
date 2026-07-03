#!/usr/bin/env python3
"""
Fetch @waldenpondstate posts via twitterapi.io's USER TIMELINE endpoint
(/twitter/user/last_tweets) instead of advanced_search.

Why: advanced_search rides X's search index, which provably drops posts
(see audit.html — 29% miss rate vs Wayback ground truth). The timeline
endpoint reads the profile itself, including replies, so it's the
completeness fix. Pages newest -> oldest; stop with --since.

Usage:
    export TWITTERAPI_IO_KEY=...      # or put it in ../.env
    python3 scripts/fetch_timeline.py --since 2025-07-02
    python3 scripts/fetch_timeline.py --since 2020-01-01 --max-pages 100

Output: data/raw_tweets_timeline.jsonl, same schema as the other fetchers,
so parse_closures.py merges it automatically (dedupe is by tweet id).
Resumable: reruns skip already-saved ids but keep paging to --since.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

from fetch_twitterapi import api_get, norm, ACCOUNT, PAGE_SLEEP

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
OUT = os.path.join(DATA, "raw_tweets_timeline.jsonl")

def load_env_key():
    env = os.path.join(HERE, "..", ".env")
    if os.path.exists(env):
        for line in open(env):
            if line.startswith("TWITTERAPI_IO_KEY="):
                return line.split("=", 1)[1].strip()
    return ""

def snowflake_dt(tid):
    ms = (int(tid) >> 22) + 1288834974657
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def tweets_of(resp):
    d = resp.get("data")
    if isinstance(d, dict) and isinstance(d.get("tweets"), list):
        return d["tweets"]
    if isinstance(resp.get("tweets"), list):
        return resp["tweets"]
    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD: page back until this date")
    ap.add_argument("--max-pages", type=int, default=60, help="safety cap")
    ap.add_argument("--api-key",
                    default=os.environ.get("TWITTERAPI_IO_KEY") or load_env_key())
    args = ap.parse_args()
    if not args.api_key:
        sys.exit("No API key (env TWITTERAPI_IO_KEY or ../.env).")
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # ids already known from ANY source, to report what's genuinely new
    known = set()
    for path in glob.glob(os.path.join(DATA, "raw_tweets*.jsonl")):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    known.add(json.loads(line)["tweet_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
    saved = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                saved.add(json.loads(line)["tweet_id"])
            except (KeyError, json.JSONDecodeError):
                pass

    cursor, page, fetched, new = "", 0, 0, 0
    done = False
    with open(OUT, "a") as f:
        while not done and page < args.max_pages:
            page += 1
            params = {"userName": ACCOUNT, "includeReplies": "true"}
            if cursor:
                params["cursor"] = cursor
            resp = api_get("/twitter/user/last_tweets", params, args.api_key)
            tweets = tweets_of(resp)
            if not tweets:
                print(f"  page {page}: empty response, stopping "
                      f"(status={resp.get('status')!r} msg={resp.get('msg')!r})",
                      file=sys.stderr)
                break
            oldest = None
            for t in tweets:
                rec = norm(t)
                tid = rec["tweet_id"]
                if not tid:
                    continue
                dt = snowflake_dt(tid)
                oldest = min(oldest, dt) if oldest else dt
                if dt < since:
                    done = True
                    continue
                fetched += 1
                if tid not in saved:
                    rec["source"] = "twitterapi.io/last_tweets"
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    saved.add(tid)
                    if tid not in known:
                        new += 1
            f.flush()
            print(f"  page {page}: {len(tweets)} tweets, oldest {oldest:%Y-%m-%d}, "
                  f"new-to-dataset so far: {new}", file=sys.stderr)
            if not resp.get("has_next_page") or not resp.get("next_cursor"):
                break
            cursor = resp["next_cursor"]
            time.sleep(PAGE_SLEEP)

    print(f"Done. {fetched} tweets in range since {args.since}; "
          f"{new} were NOT in any existing source -> {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
