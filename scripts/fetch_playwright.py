#!/usr/bin/env python3
"""
Fetch @waldenpondstate posts by driving a real browser (Playwright) and
recording X's own GraphQL timeline responses while scrolling the profile's
"Posts & replies" tab. This sees exactly what a logged-in reader sees, so it
catches posts that both twitterapi.io endpoints drop (verified example:
status/2068000535186137421, a closure missing from search AND last_tweets).

One-time setup (from the repo root):
    python3 -m venv .venv
    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium

Login once (X requires it for timelines) — a browser window opens; log in,
then the session is saved to .x_auth.json (gitignored) for future runs:
    .venv/bin/python scripts/fetch_playwright.py --login

Fetch (headed by default; add --headless once you trust it):
    .venv/bin/python scripts/fetch_playwright.py --since 2023-05-01

Output: data/raw_tweets_playwright.jsonl in the shared schema
(tweet_id, snapshot_ts, created_at, text, url, source) — parse_closures.py
merges it automatically.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "raw_tweets_playwright.jsonl")
AUTH = os.path.join(ROOT, ".x_auth.json")
ACCOUNT = "waldenpondstate"
PROFILE_URL = f"https://x.com/{ACCOUNT}/with_replies"

def snowflake_dt(tid):
    return datetime.fromtimestamp(((int(tid) >> 22) + 1288834974657) / 1000,
                                  tz=timezone.utc)

def walk_tweets(node, found):
    """Recursively collect tweet objects: dicts with rest_id + legacy.full_text."""
    if isinstance(node, dict):
        legacy = node.get("legacy")
        if (node.get("rest_id") and isinstance(legacy, dict)
                and "full_text" in legacy and "created_at" in legacy):
            found.append(node)
        for v in node.values():
            walk_tweets(v, found)
    elif isinstance(node, list):
        for v in node:
            walk_tweets(v, found)

def author_of(tweet_obj):
    core = tweet_obj.get("core") or {}
    try:
        u = core["user_results"]["result"]
        return (u.get("legacy", {}).get("screen_name")
                or u.get("core", {}).get("screen_name") or "")
    except (KeyError, TypeError):
        return ""

def text_of(tweet_obj):
    # long posts carry full text in note_tweet; else legacy.full_text
    try:
        return tweet_obj["note_tweet"]["note_tweet_results"]["result"]["text"]
    except (KeyError, TypeError):
        return tweet_obj["legacy"]["full_text"]

def norm(tweet_obj):
    tid = str(tweet_obj["rest_id"])
    created = tweet_obj["legacy"]["created_at"]
    iso, stamp = created, ""
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
        iso, stamp = dt.isoformat(), dt.strftime("%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        pass
    return {
        "tweet_id": tid,
        "snapshot_ts": stamp,
        "created_at": iso,
        "text": text_of(tweet_obj).strip(),
        "url": f"https://twitter.com/{ACCOUNT}/status/{tid}",
        "source": "playwright",
    }

def do_login(headless):
    from playwright.sync_api import sync_playwright, Error
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # login is always headed
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://x.com/login")
        print("Log in to X in the browser window. The session is saved the moment "
              "login completes (up to 10 min); then you can close the window.",
              file=sys.stderr)
        saved = False
        try:
            for _ in range(600):
                if any(c["name"] == "auth_token"
                       for c in ctx.cookies("https://x.com")):
                    ctx.storage_state(path=AUTH)
                    saved = True
                    print(f"Login detected — session saved to {AUTH}. "
                          "You can close the browser window.", file=sys.stderr)
                    break
                page.wait_for_timeout(1000)
        except Error:
            # window closed mid-poll; try to salvage the session anyway
            try:
                if any(c["name"] == "auth_token"
                       for c in ctx.cookies("https://x.com")):
                    ctx.storage_state(path=AUTH)
                    saved = True
            except Error:
                pass
        try:
            browser.close()
        except Error:
            pass
        if saved:
            print("Done — session saved.", file=sys.stderr)
        else:
            sys.exit("No login captured. Rerun --login and finish logging in "
                     "before closing the window.")

def import_cookies():
    """Build the saved session from cookies of an ordinary, human login.

    In your regular browser: log in to x.com, then DevTools -> Application ->
    Cookies -> https://x.com and copy the values of `auth_token` and `ct0`.
    Run this in a normal terminal (it prompts; input isn't echoed into any log):
        .venv/bin/python scripts/fetch_playwright.py --import-cookies
    or pass them via env vars X_AUTH_TOKEN / X_CT0.
    """
    import getpass
    auth = os.environ.get("X_AUTH_TOKEN") or getpass.getpass("auth_token: ").strip()
    ct0 = os.environ.get("X_CT0") or getpass.getpass("ct0: ").strip()
    if not auth or not ct0:
        sys.exit("Both auth_token and ct0 are required.")
    exp = time.time() + 180 * 86400
    cookies = []
    for domain in (".x.com", ".twitter.com"):
        cookies += [
            {"name": "auth_token", "value": auth, "domain": domain, "path": "/",
             "expires": exp, "httpOnly": True, "secure": True, "sameSite": "None"},
            {"name": "ct0", "value": ct0, "domain": domain, "path": "/",
             "expires": exp, "httpOnly": False, "secure": True, "sameSite": "Lax"},
        ]
    with open(AUTH, "w") as f:
        json.dump({"cookies": cookies, "origins": []}, f)
    os.chmod(AUTH, 0o600)
    print(f"Session written to {AUTH}. Test with: "
          f"scripts/fetch_playwright.py --since 2026-06-01", file=sys.stderr)

def fetch(since, headless, max_scrolls, quiet_limit=8):
    from playwright.sync_api import sync_playwright

    known = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                known.add(json.loads(line)["tweet_id"])
            except (KeyError, json.JSONDecodeError):
                pass

    collected = {}          # tweet_id -> record (this run)
    def on_response(resp):
        if "/graphql/" not in resp.url:
            return
        if not any(k in resp.url for k in ("UserTweets", "UserTweetsAndReplies",
                                           "TweetDetail", "UserMedia")):
            return
        try:
            body = resp.json()
        except Exception:
            return
        found = []
        walk_tweets(body, found)
        for t in found:
            try:
                if author_of(t).lower() != ACCOUNT:
                    continue
                if "retweeted_status_result" in (t.get("legacy") or {}):
                    continue
                rec = norm(t)
                collected.setdefault(rec["tweet_id"], rec)
            except (KeyError, TypeError):
                continue

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_args = {"viewport": {"width": 1280, "height": 900}}
        if os.path.exists(AUTH):
            ctx_args["storage_state"] = AUTH
        else:
            print("No saved session (.x_auth.json). If X shows a login wall, run "
                  "with --login first.", file=sys.stderr)
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        last_count, quiet = 0, 0
        for i in range(max_scrolls):
            page.mouse.wheel(0, 2600)
            page.wait_for_timeout(900)
            if collected:
                oldest = min(snowflake_dt(t) for t in collected)
                if oldest < since:
                    print(f"  reached {oldest:%Y-%m-%d} (< --since), stopping",
                          file=sys.stderr)
                    break
            if len(collected) == last_count:
                quiet += 1
                if quiet >= quiet_limit:
                    print("  no new tweets after several scrolls — end of timeline "
                          "or rate-limited; stopping", file=sys.stderr)
                    break
                page.wait_for_timeout(1200)   # let slow responses land
            else:
                quiet, last_count = 0, len(collected)
            if i % 10 == 9:
                oldest = (min(snowflake_dt(t) for t in collected)
                          if collected else None)
                print(f"  scroll {i+1}: {len(collected)} tweets"
                      + (f", oldest {oldest:%Y-%m-%d}" if oldest else ""),
                      file=sys.stderr)
        browser.close()

    new = 0
    with open(OUT, "a") as f:
        for tid in sorted(collected, key=int):
            if tid in known:
                continue
            f.write(json.dumps(collected[tid], ensure_ascii=False) + "\n")
            new += 1
    print(f"Done. {len(collected)} tweets seen this run; {new} appended -> {OUT}",
          file=sys.stderr)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true",
                    help="open a browser to log in and save the session")
    ap.add_argument("--import-cookies", action="store_true",
                    help="build the session from auth_token/ct0 cookies copied "
                         "from your normal browser (no automated login)")
    ap.add_argument("--since", default="2019-01-01",
                    help="YYYY-MM-DD: scroll back until this date")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-scrolls", type=int, default=400)
    args = ap.parse_args()
    if args.import_cookies:
        import_cookies()
        return
    if args.login:
        do_login(headless=False)
        return
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    fetch(since, args.headless, args.max_scrolls)

if __name__ == "__main__":
    main()
