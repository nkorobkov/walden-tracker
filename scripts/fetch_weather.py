#!/usr/bin/env python3
"""
Fetch daily weather checkpoints for every day of every tracked season
(Concord, MA — next to Walden Pond) from the free Open-Meteo archive API.

    python3 fetch_weather.py      # -> weather.csv

Columns (temps °F, precip inches, cloud %):
  date, t9, t12, t15          temperature at 9am / noon / 3pm local
  tmax_day                    max hourly temp 8am-5pm (the "beach window")
  precip_day                  total precipitation 8am-6pm
  cloud_day                   mean cloud cover 9am-5pm

The archive lags realtime by a few days; the most recent days are filled from
the forecast API's past_days window so the current season stays complete.
"""
import csv
import json
import os
import urllib.request
from datetime import date, timedelta

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
LAT, LON = 42.4395, -71.3358   # Walden Pond
HOURLY = "temperature_2m,precipitation,cloud_cover"
COMMON = (f"latitude={LAT}&longitude={LON}&hourly={HOURLY}"
          "&timezone=America%2FNew_York&temperature_unit=fahrenheit"
          "&precipitation_unit=inch")

# season windows mirror build_site.py coverage
def seasons():
    today = date.today()
    for y in range(2016, today.year + 1):
        a = date(y, 5, 1)
        b = min(date(y, 10, 31), today)
        if a <= b:
            yield a, b

def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())

def day_rows(hourly):
    """Reduce hourly arrays to one row per day of checkpoints."""
    days = {}
    for i, ts in enumerate(hourly["time"]):
        d, hh = ts[:10], int(ts[11:13])
        rec = days.setdefault(d, {"t": {}, "p": 0.0, "c": [], "tmax": None})
        t = hourly["temperature_2m"][i]
        p = hourly["precipitation"][i]
        c = hourly["cloud_cover"][i]
        if t is None:
            continue
        if hh in (9, 12, 15):
            rec["t"][hh] = t
        if 8 <= hh <= 17:
            rec["tmax"] = t if rec["tmax"] is None else max(rec["tmax"], t)
        if 8 <= hh <= 18 and p is not None:
            rec["p"] += p
        if 9 <= hh <= 17 and c is not None:
            rec["c"].append(c)
    out = {}
    for d, r in days.items():
        if r["tmax"] is None:
            continue
        out[d] = {
            "date": d,
            "t9": r["t"].get(9, ""), "t12": r["t"].get(12, ""), "t15": r["t"].get(15, ""),
            "tmax_day": round(r["tmax"], 1),
            "precip_day": round(r["p"], 2),
            "cloud_day": round(sum(r["c"]) / len(r["c"])) if r["c"] else "",
        }
    return out

def main():
    all_days = {}
    for a, b in seasons():
        url = (f"https://archive-api.open-meteo.com/v1/archive?{COMMON}"
               f"&start_date={a}&end_date={b}")
        data = get(url)
        rows = day_rows(data["hourly"])
        all_days.update(rows)
        print(f"{a.year}: {len(rows)} days ({a} → {b})")

    # top up the archive lag (typically ~5 days) from the forecast API
    recent = get(f"https://api.open-meteo.com/v1/forecast?{COMMON}"
                 "&past_days=10&forecast_days=1")
    today = date.today().isoformat()
    for d, row in day_rows(recent["hourly"]).items():
        if d <= today and d not in all_days:
            all_days[d] = row
            print(f"filled from forecast API: {d}")

    rows = [all_days[d] for d in sorted(all_days)]
    with open(os.path.join(DATA, "weather.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "t9", "t12", "t15",
                                          "tmax_day", "precip_day", "cloud_day"])
        w.writeheader()
        w.writerows(rows)
    print(f"weather.csv: {len(rows)} days")

if __name__ == "__main__":
    main()
