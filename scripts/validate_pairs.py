#!/usr/bin/env python3
"""
Build human-checkable validation files from the collected tweets:

  closures_with_reopen.csv  - every closure tweet, its parsed date/time, and the
                              ACTUAL reopen time taken from the next reopening
                              tweet that follows it chronologically.
  non_closure_tweets.csv    - every other tweet, with the category I assigned,
                              so you can eyeball whether the classification holds.

Chronological order & timestamps come from the tweet ID itself (Twitter
"snowflake": the post time in ms is (id >> 22) + 1288834974657), which is exact
and uniform across both data sources.
"""
import csv
import os
from datetime import datetime, timezone
import parse_closures as P   # reuse classify(), times(), norm_date()

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# Closure-related closure rows (each gets paired with a following reopening).
CLOSURE_CATS = {"capacity_closure", "full_closure", "night_closing"}
# Genuinely closure-UNRELATED tweets (the review file to eyeball).
UNRELATED_CATS = {"event", "other"}

def posted_dt(tweet_id):
    """Exact UTC post time decoded from the tweet's snowflake id."""
    try:
        ms = (int(tweet_id) >> 22) + 1288834974657
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None

def first_time(text):
    t = P.times(text)
    return t[0][0] if t else ""

def reopen_time_of(text):
    """Time the reopening tweet states (usually the first time it mentions)."""
    return first_time(text)

def main():
    # 1) normalize every tweet into a comparable record
    recs = []
    for r in P.load_all():
        text = (r.get("text") or "").strip()
        if not text or text.startswith("**CORRECTION"):
            continue
        tid = r["tweet_id"]
        pdt = posted_dt(tid)
        recs.append({
            "id": tid,
            "posted": pdt,
            "category": P.classify(text),
            "date": P.norm_date(text, (pdt.year if pdt else 2020)),
            "time": first_time(text),
            "text": text,
            "url": r.get("url", ""),
        })

    # 2) chronological order (by exact post time; fall back to numeric id)
    recs.sort(key=lambda x: (x["posted"] or datetime.min.replace(tzinfo=timezone.utc),
                             int(x["id"]) if x["id"].isdigit() else 0))

    # 2b) drop duplicate re-posts: the account sometimes re-announces the SAME
    #     closure hours later with unchanged text (same event date+time). Keep
    #     the first; a later copy would otherwise count as a phantom second
    #     closure and mis-shape that day's pairing. Only closures with a parsed
    #     date+time can be compared this way.
    seen, deduped, dropped = set(), [], 0
    for r in recs:
        # effective event date: parsed from text, else the posting date — matches
        # how build_site resolves it, so re-posts with an unparseable date (e.g.
        # "SAT 7/8" with no year) still collapse onto their dated twin.
        edate = r["date"] or (r["posted"].strftime("%Y-%m-%d") if r["posted"] else None)
        if r["category"] in CLOSURE_CATS and edate and r["time"]:
            key = (r["category"], edate, r["time"])
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
        deduped.append(r)
    recs = deduped
    if dropped:
        print(f"(dropped {dropped} duplicate closure re-posts)")

    # 3) index of reopening tweets for forward lookup
    reopen_idx = [i for i, r in enumerate(recs) if r["category"] == "reopening"]

    def fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else ""

    # 4) closures file. Two reopen signals, because the account uses both styles:
    #    - est_reopen_in_tweet : "Reopening at 2pm" stated inside the closure tweet
    #    - reopen_*            : a following "has reopened" tweet, only if it lands
    #                            within the window below (same closure event)
    WINDOW_H = 36
    closures, unrelated = [], []
    matched_reopen = set()          # reopen_idx values consumed by a closure
    import bisect

    def row(closure_type, r, rp, flag):
        gap = ""
        if rp and r and r["posted"] and rp["posted"]:
            gap = round((rp["posted"] - r["posted"]).total_seconds() / 3600, 2)
        return {
            "closure_type": closure_type,
            "closure_posted_utc": fmt(r["posted"]) if r else "",
            "closure_date": (r["date"] or "") if r else "",
            "closure_time": r["time"] if r else "",
            "est_reopen_in_tweet": P.reopen_time(r["text"]) if r else "",
            "reopen_posted_utc": fmt(rp["posted"]) if rp else "",
            "reopen_date": (rp["date"] or "") if rp else "",
            "reopen_time": reopen_time_of(rp["text"]) if rp else "",
            "gap_hours": gap,
            "flag": flag,
            "closure_text": r["text"] if r else "",
            "closure_url": r["url"] if r else "",
            "reopen_text": rp["text"] if rp else "",
            "reopen_url": rp["url"] if rp else "",
            "tweet_id": (r["id"] if r else (rp["id"] if rp else "")),
        }

    for i, r in enumerate(recs):
        if r["category"] in CLOSURE_CATS:
            # first reopening tweet strictly after this closure, within the window
            j = bisect.bisect_right(reopen_idx, i)
            rp, flag = None, ""
            if j < len(reopen_idx):
                cand = recs[reopen_idx[j]]
                gap_h = ((cand["posted"] - r["posted"]).total_seconds() / 3600
                         if r["posted"] and cand["posted"] else 0)
                if gap_h <= WINDOW_H:
                    rp = cand
                    matched_reopen.add(reopen_idx[j])
                    between = [k for k in range(i + 1, reopen_idx[j])
                              if recs[k]["category"] in CLOSURE_CATS]
                    if between:
                        flag = f"{len(between)}_more_closures_before_reopen"
                else:
                    flag = "no_reopen_tweet_within_36h"
            else:
                flag = "no_reopen_tweet_after"
            closures.append(row(r["category"], r, rp, flag))
        elif r["category"] in UNRELATED_CATS:
            unrelated.append({
                "posted_utc": fmt(r["posted"]),
                "category": r["category"],
                "date": r["date"] or "",
                "time": r["time"],
                "text": r["text"],
                "url": r["url"],
                "tweet_id": r["id"],
            })

    # Reopening tweets never consumed by a closure -> surface as orphan rows so
    # nothing closure-related is hidden (usually means the matching closure
    # predates our data, or two reopenings were posted for one closure).
    for idx in reopen_idx:
        if idx not in matched_reopen:
            closures.append(row("reopening_orphan", None, recs[idx],
                                "reopening_without_matched_closure"))

    closures.sort(key=lambda c: c["closure_posted_utc"] or c["reopen_posted_utc"])

    with open(os.path.join(DATA, "closure_related.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(closures[0].keys()))
        w.writeheader(); w.writerows(closures)
    with open(os.path.join(DATA, "closure_unrelated.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(unrelated[0].keys()))
        w.writeheader(); w.writerows(unrelated)

    # 5) summary for a quick sanity read
    from collections import Counter
    print(f"closure_related.csv : {len(closures)} rows")
    for k, v in Counter(c["closure_type"] for c in closures).most_common():
        print(f"   {k:18} {v}")
    paired = sum(1 for c in closures if c["closure_posted_utc"] and c["reopen_posted_utc"])
    print(f"   closures paired with a reopen : {paired}")
    print(f"closure_unrelated.csv : {len(unrelated)} rows")
    for k, v in Counter(o["category"] for o in unrelated).most_common():
        print(f"   {k:18} {v}")

if __name__ == "__main__":
    main()
