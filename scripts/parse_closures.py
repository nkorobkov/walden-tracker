#!/usr/bin/env python3
"""
Parse raw_tweets.jsonl into structured closure records -> closures.csv

Each tweet is classified and, where possible, we extract:
  - date        the event date (normalized YYYY-MM-DD)
  - time        the time the event happened (closure time for capacity closures)
  - reopen_time the stated reopening time, if any
  - category    capacity_closure | night_closing | reopening | full_closure |
                event | other

The park closes its parking lots when it hits visitor capacity, so
`capacity_closure` rows are the core "parking closure" dataset.
"""
import csv
import json
import os
import re
from datetime import datetime

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
MONTHS.update({m[:3].lower(): i for m, i in list(MONTHS.items()) if m})

def norm_date(text, fallback_year):
    """Return YYYY-MM-DD for the first date found in text, else None."""
    # 1) M/D/YY or M/D/YYYY
    m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', text)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # 2) "Month D, YYYY" or "Month Dth" (year optional)
    m = re.search(r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?'
                  r'(?:,?\s*(\d{4}))?', text)
    if m and m.group(1).lower() in MONTHS:
        mo = MONTHS[m.group(1).lower()]
        d = int(m.group(2))
        y = int(m.group(3)) if m.group(3) else fallback_year
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

TIME_RE = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?', re.I)

def times(text):
    out = []
    for m in TIME_RE.finditer(text):
        h = int(m.group(1)) % 12
        if m.group(3).lower() == "p":
            h += 12
        mm = int(m.group(2) or 0)
        out.append((f"{h:02d}:{mm:02d}", m.start()))
    return out

def classify(t):
    tl = t.lower()
    # 1) A definitive "we are open again" statement wins over everything else
    #    (even if the same tweet mentions capacity, e.g. a "...dashboard for
    #    capacity closures" promo footer). Note the hyphenated "re-open" forms
    #    and the present-tense "is now reopen".
    if re.search(
            r'has re-?opened|have re-?opened|is now re-?open|are now re-?open'
            r'|is re-?open\b|now re-?open\b|has been re-?opened'
            r'|is open to all|are open to all|are now open'
            r'|open with limited|parking areas?[^.]*\bare (now )?open'
            # 2024+ phrasing: "Walden Pond is now able to safely accommodate
            # visitors" / "is once again open and able to accommodate ..."
            r'|able to (safely )?accommodate|once again open'
            r'|resuming regular', tl):
        return "reopening"
    # 2) Capacity / parking-full closure. Require real closure phrasing so the
    #    "capacity closures" promo text above does NOT match here. Handles
    #    its/our/visitor variants and "closed for visitor capacity".
    if re.search(
            r'closed for (visitor )?capacity|for visitor capacity'
            r'|reached (its |our )?(maximum |visitor )?capacity'
            r'|reached (its |our )?maximum|maximum (safe )?number(?! of visitors will)'
            r'|at (full )?capacity', tl):
        return "capacity_closure"
    if re.search(r'closing nightly|closes at|closed? for the night|out gate|exit gate', tl):
        return "night_closing"
    if re.search(r'closed to all (incoming )?visitors|closed to visitation'
                 r'|closed (to \w+ )?until further notice|until further notice'
                 r'|will be (officially )?closed|will close walden|officially closed', tl):
        return "full_closure"
    if re.search(r'\bjoin\b|program|event|talk|walk with|ranger|celebrat|anniversary', tl):
        return "event"
    return "other"

def reopen_time(t):
    tl = t.lower()
    m = re.search(r'reopen[a-z]*\s+(?:at\s+)?', tl)
    if not m:
        return ""
    tail = t[m.end():]
    tt = times(tail)
    return tt[0][0] if tt else ""

def load_all():
    """Read every raw_tweets*.jsonl (Wayback + twitterapi.io), dedupe by id.
    Prefer the record that actually has text, then the one with created_at."""
    import glob
    best = {}
    for path in sorted(glob.glob(os.path.join(DATA, "raw_tweets*.jsonl"))):
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            tid = r.get("tweet_id")
            if not tid:
                continue
            prev = best.get(tid)
            score = (bool(r.get("text")), bool(r.get("created_at")))
            if prev is None or score > prev[0]:
                best[tid] = (score, r)
    return [v[1] for v in best.values()]

def main():
    rows = []
    for r in load_all():
        text = (r.get("text") or "").strip()
        if not text or text.startswith("**CORRECTION"):
            continue
        fy = int((r.get("snapshot_ts") or "0000")[:4]) or 2020
        cat = classify(text)
        date = norm_date(text, fy)
        tt = times(text)
        # closure time: first time in the tweet (usually the timestamp of event)
        ctime = tt[0][0] if tt else ""
        rows.append({
            "tweet_id": r["tweet_id"],
            "date": date or "",
            "time": ctime,
            "reopen_time": reopen_time(text),
            "category": cat,
            "text": text,
            "url": r.get("url", ""),
        })
    rows.sort(key=lambda x: (x["date"] or "0000", x["time"]))
    with open(os.path.join(DATA, "closures.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "time", "reopen_time",
                                          "category", "text", "url", "tweet_id"])
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    c = Counter(r["category"] for r in rows)
    print(f"Parsed {len(rows)} tweets -> closures.csv")
    for k, v in c.most_common():
        print(f"  {k:16} {v}")
    dated = [r for r in rows if r["category"] == "capacity_closure" and r["date"]]
    print(f"  capacity closures with a parsed date: {len(dated)}")

if __name__ == "__main__":
    main()
