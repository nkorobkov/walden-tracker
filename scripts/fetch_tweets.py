#!/usr/bin/env python3
"""
Fetch Walden Pond State Reservation (@waldenpondstate) tweets from the
Internet Archive (Wayback Machine).

Why the Wayback Machine instead of X/Twitter directly?
  - x.com blocks automated fetches (HTTP 402) and the API's free tier no
    longer allows reading tweets.
  - The Wayback Machine is a public, purpose-built archive that has snapshotted
    this public agency account since 2009. Polite, legitimate, and robust.

Approach:
  1. Ask the Wayback CDX API for every archived .../status/<id> URL (one per tweet).
  2. For each, fetch the raw archived HTML ("id_" snapshot) and pull the tweet
    text out of the <meta property="og:description"> tag.
  3. Write one JSON object per tweet to raw_tweets.jsonl.

Stdlib only. Rate-limited and cached so reruns are cheap and archive-friendly.
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

CDX = ("http://web.archive.org/cdx/search/cdx"
       "?url=twitter.com/waldenpondstate/status*"
       "&output=json&collapse=urlkey&filter=statuscode:200&fl=timestamp,original")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw_tweets.jsonl")
UA = "walden-tracker/1.0 (personal research; public parking-closure data)"
WORKERS = 4          # be gentle with the archive
RETRIES = 3

def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == RETRIES - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return ""

def cdx_list():
    rows = json.loads(get(CDX))
    # paginated APIs may return >1 page; this endpoint returns all rows here.
    # rows[0] is the header ["timestamp","original"]
    seen, out = set(), []
    for ts, original in rows[1:]:
        m = re.search(r"/status/(\d+)", original)
        if not m:
            continue
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        out.append((tid, ts, original))
    return out

META = re.compile(
    r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\'](.*?)["\']',
    re.I | re.S)
TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)

def unescape(s):
    import html
    return html.unescape(s).strip().strip("“”\"")

def extract(html_text):
    m = META.search(html_text)
    if m and m.group(1).strip():
        return unescape(m.group(1))
    m = TITLE.search(html_text)
    if m:
        t = unescape(m.group(1))
        # strip "Walden Pond State Reservation on Twitter: " prefix
        t = re.sub(r'^.*? on (?:Twitter|X):\s*', '', t)
        return t.strip().strip("“”\"")
    return ""

def fetch_one(item):
    tid, ts, original = item
    url = f"https://web.archive.org/web/{ts}id_/{original}"
    try:
        html_text = get(url)
    except Exception as e:
        return {"tweet_id": tid, "snapshot_ts": ts, "text": "", "error": str(e)}
    time.sleep(0.2)  # politeness delay per worker
    return {"tweet_id": tid, "snapshot_ts": ts, "text": extract(html_text),
            "url": f"https://twitter.com/waldenpondstate/status/{tid}"}

def main():
    print("Querying Wayback CDX API for archived tweets...", file=sys.stderr)
    items = cdx_list()
    print(f"  {len(items)} unique archived tweets found", file=sys.stderr)

    done = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["tweet_id"])
                except Exception:
                    pass
        print(f"  {len(done)} already cached in {OUT}, resuming", file=sys.stderr)

    todo = [it for it in items if it[0] not in done]
    print(f"  fetching {len(todo)}...", file=sys.stderr)

    n = 0
    with open(OUT, "a") as f, ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch_one, it): it for it in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if n % 25 == 0:
                print(f"  {n}/{len(todo)}", file=sys.stderr)
    print(f"Done. Wrote {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
