#!/usr/bin/env python3
"""
Build ../index.html (the closure-odds dashboard) from ../data/closure_related.csv
and scripts/site_template.html.

    python3 scripts/parse_closures.py     # rebuild data/closures.csv from raw tweets
    python3 scripts/validate_pairs.py     # rebuild data/closure_related.csv
    python3 scripts/build_site.py         # -> index.html (self-contained, open in browser)

Extracts per-event records (local closure/reopen moments), season coverage
windows, and injects them as JSON into site_template.html.
Times are minutes since local midnight (EDT = UTC-4, valid for the May-Oct
season). Closure time = time stated in the tweet when it's within a plausible
window of the posting time, else the posting time; reopening moment = the
matched reopening tweet's post time.
"""
import csv
import json
import os
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA = os.path.join(ROOT, "data")

def parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M") if s else None

def to_local(dt):
    return dt - timedelta(hours=4)  # EDT; the season is May-Oct so always -4

def hhmm_to_min(s):
    if not s:
        return None
    h, m = s.split(":")
    return int(h) * 60 + int(m)

def main():
    events, other = [], []
    with open(os.path.join(DATA, "closure_related.csv")) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["closure_type"] != "capacity_closure":
            if r["closure_type"] == "reopening_orphan":
                continue  # no closure info to anchor it to
            posted = parse_utc(r["closure_posted_utc"])
            other.append({
                "d": r["closure_date"] or (to_local(posted).strftime("%Y-%m-%d") if posted else ""),
                "type": r["closure_type"],
                "t": r["closure_text"], "u": r["closure_url"],
            })
            continue

        posted = parse_utc(r["closure_posted_utc"])
        local_posted = to_local(posted) if posted else None
        d = r["closure_date"] or (local_posted.strftime("%Y-%m-%d") if local_posted else "")
        if not d:
            continue
        stated = hhmm_to_min(r["closure_time"])
        posted_min = local_posted.hour * 60 + local_posted.minute if local_posted else None
        # trust the stated time if it's within [-30m, +4h] before the post
        # (tweets lag the event); anything else is usually a parse artifact
        cm = stated
        if cm is None or (posted_min is not None and d == local_posted.strftime("%Y-%m-%d")
                          and not (-30 <= posted_min - cm <= 240)):
            cm = posted_min

        # A pair flagged "N_more_closures_before_reopen" means another closure
        # was announced before this one's reopening — so the park must have
        # reopened (untweeted) before that next closure, and the matched
        # reopening actually belongs to the LATER closure. Its own reopening is
        # missing, so don't borrow that gap; treat it as reopen-unknown and let
        # interval() impute (median same-day, or overnight for a late closure).
        reopen_is_ours = bool(r["gap_hours"]) and \
            "more_closures_before_reopen" not in r["flag"]
        rm, rnext, gap = None, False, None
        if reopen_is_ours:
            gap = float(r["gap_hours"])
            rp = to_local(parse_utc(r["reopen_posted_utc"]))
            rm = rp.hour * 60 + rp.minute
            rnext = rp.strftime("%Y-%m-%d") != d

        dow = datetime.strptime(d, "%Y-%m-%d").weekday()
        events.append({
            "d": d, "dow": dow, "cm": cm, "rm": rm, "rn": rnext,
            "g": gap, "est": r["est_reopen_in_tweet"],
            "t": r["closure_text"], "u": r["closure_url"],
            "ru": r["reopen_url"] if reopen_is_ours else "",
        })

    # season coverage windows: denominator days for the probability estimates
    from datetime import date
    yesterday = date.today() - timedelta(days=1)  # today isn't a complete day yet
    coverage = {}
    for y in sorted({e["d"][:4] for e in events}):
        if y == "2019":
            coverage[y] = ["2019-08-31", "2019-10-31"]   # data starts Aug 31
        elif int(y) == yesterday.year and yesterday < date(int(y), 10, 31):
            coverage[y] = [f"{y}-05-01", yesterday.isoformat()]  # season in progress
        else:
            coverage[y] = [f"{y}-05-01", f"{y}-10-31"]

    # weather checkpoints (fetch_weather.py); embedded as
    # {date: [tmax_day, precip_day, cloud_day, t9, t12, t15]}
    weather = {}
    wpath = os.path.join(DATA, "weather.csv")
    if os.path.exists(wpath):
        with open(wpath) as f:
            for r in csv.DictReader(f):
                weather[r["date"]] = [
                    float(r["tmax_day"]), float(r["precip_day"]),
                    int(r["cloud_day"]) if r["cloud_day"] else None,
                    float(r["t9"]) if r["t9"] else None,
                    float(r["t12"]) if r["t12"] else None,
                    float(r["t15"]) if r["t15"] else None,
                ]
    else:
        print("weather.csv not found - run fetch_weather.py for weather features")

    data = json.dumps({"events": events, "full": other, "coverage": coverage,
                       "weather": weather}, separators=(",", ":"))
    with open(os.path.join(HERE, "site_template.html")) as f:
        tpl = f.read()
    out = tpl.replace("__DATA__", data)
    with open(os.path.join(ROOT, "index.html"), "w") as f:
        f.write(out)
    print(f"index.html: {len(events)} capacity closures, {len(other)} other events, "
          f"seasons {min(coverage)}-{max(coverage)}")

if __name__ == "__main__":
    main()
