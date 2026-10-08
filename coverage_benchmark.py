#!/usr/bin/env python3
"""
Street-level coverage benchmark.

Draws random points on mapped roads worldwide and checks each one for official Google
Street View imagery nearby.

    python coverage_benchmark.py sample --n 120 --seed 42 --out data/points.csv
    python coverage_benchmark.py label  --points data/points.csv
    python coverage_benchmark.py check  --points data/points.csv   # needs GOOGLE_MAPS_API_KEY
    python coverage_benchmark.py report --points data/points.csv

`sample` and `label` use OpenStreetMap services (Overpass and Nominatim) and need no key.
`check` uses the Street View Static API metadata endpoint, which needs a Google Maps
Platform API key. Metadata requests are not billed.
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import requests

USER_AGENT = ("street-level-coverage-benchmark/1.0 "
              "(+https://github.com/ritabratamaiti/street-level-coverage-benchmark)")
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

HIGHWAY_CLASSES = ("motorway|trunk|primary|secondary|tertiary|residential|"
                   "unclassified|living_street|service")

# Sampling regions: (name, lat_min, lat_max, lon_min, lon_max, weight). Coarse rectangles
# with hand-set weights that roughly follow land area. They overlap: South Asia and
# Southeast Asia also sit inside the Asia box. Reports use the country each point falls
# in, not these boxes.
SAMPLING_REGIONS = [
    ("Africa", -34, 37, -17, 51, 30),
    ("Europe", 36, 70, -10, 40, 10),
    ("Asia", 5, 70, 40, 145, 44),
    ("South Asia", 6, 35, 68, 92, 10),
    ("Southeast Asia", -10, 23, 95, 141, 8),
    ("North America", 15, 70, -168, -52, 24),
    ("South America", -55, 12, -81, -35, 18),
    ("Oceania", -45, -10, 113, 179, 8),
]

CSV_FIELDS = ["id", "lat", "lon", "highway", "sampling_region", "country_code", "country",
              "continent", "covered", "result", "capture_date", "checked_on"]

AFRICA = set("dz ao bj bw bf bi cv cm cf td km cd cg ci dj eg gq er sz et ga gm gh gn gw ke "
             "ls lr ly mg mw ml mr mu ma mz na ne ng rw st sn sc sl so za ss sd tz tg tn ug "
             "zm zw eh".split())
EUROPE = set("al ad at by be ba bg hr cy cz dk ee fi fr de gr hu is ie it lv li lt lu mt md "
             "mc me nl mk no pl pt ro sm rs sk si es se ch ua gb va xk".split())
NORTH_AMERICA = set("us ca mx gt bz hn sv ni cr pa cu do ht jm bs bb tt gl pr ag dm gd kn "
                    "lc vc".split())
SOUTH_AMERICA = set("br ar cl co pe ve ec bo py uy gy sr gf fk".split())
OCEANIA = set("au nz pg fj sb vu nc pf ws to ki fm mh pw nr tv".split())


def continent_of(country_code, lon):
    cc = (country_code or "").lower()
    if cc == "ru":
        return "Europe" if lon < 60 else "Asia"  # split at the Urals
    for name, members in (("Africa", AFRICA), ("Europe", EUROPE),
                          ("North America", NORTH_AMERICA), ("South America", SOUTH_AMERICA),
                          ("Oceania", OCEANIA)):
        if cc in members:
            return name
    return "Asia" if cc else ""


# --------------------------------------------------------------------------- sampling

def sample_land_point(rng):
    total = sum(r[5] for r in SAMPLING_REGIONS)
    pick = rng.uniform(0, total)
    acc = 0.0
    for name, lat0, lat1, lon0, lon1, w in SAMPLING_REGIONS:
        acc += w
        if pick <= acc:
            # Uniform in sin(latitude), so the draw is uniform by area within the box.
            u = rng.uniform(math.sin(math.radians(lat0)), math.sin(math.radians(lat1)))
            return name, math.degrees(math.asin(u)), rng.uniform(lon0, lon1)
    name, lat0, lat1, lon0, lon1, _ = SAMPLING_REGIONS[-1]
    return name, rng.uniform(lat0, lat1), rng.uniform(lon0, lon1)


class OverpassUnavailable(RuntimeError):
    pass


def roads_in_window(session, overpass_url, lat, lon, window_deg, attempts=5):
    """
    Return the roads in a window, or [] if it has none.

    A failed request is retried on the same window rather than treated as empty. Busy
    servers time out more often on dense areas, so redrawing on failure would quietly
    under-sample cities.
    """
    half = window_deg / 2
    query = (
        "[out:json][timeout:25];"
        f'way["highway"~"^({HIGHWAY_CLASSES})$"]'
        f"({lat - half:.4f},{lon - half:.4f},{lat + half:.4f},{lon + half:.4f});"
        "out geom 40;"
    )
    for attempt in range(attempts):
        try:
            r = session.post(overpass_url, data={"data": query}, timeout=60)
            if r.status_code == 200:
                body = r.json()
                # A query that runs out of time still returns 200, with a remark and no
                # elements. Treating that as an empty window would skip dense areas.
                remark = body.get("remark") or ""
                if "error" not in remark.lower():
                    return [
                        {"hw": (e.get("tags") or {}).get("highway"),
                         "geom": [(p["lat"], p["lon"]) for p in (e.get("geometry") or [])]}
                        for e in body.get("elements", [])
                        if e.get("geometry")
                    ]
                problem = remark.strip()[:80]
            else:
                problem = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as exc:
            problem = type(exc).__name__
        wait = 5 * 2 ** attempt
        print(f"  Overpass {problem}; retrying this window in {wait} s", flush=True)
        time.sleep(wait)
    raise OverpassUnavailable(f"Overpass failed {attempts} times at {overpass_url}. "
                              "Try again later or pass --overpass-url with a mirror.")


def sample_road_point(rng, session, overpass_url, window_deg, max_tries):
    """Pick a random window by area, then a random node on a road inside it."""
    for _ in range(max_tries):
        region, lat, lon = sample_land_point(rng)
        ways = roads_in_window(session, overpass_url, lat, lon, window_deg)
        time.sleep(1.0)  # be polite to the public Overpass instance
        if ways:
            way = rng.choice(ways)
            plat, plon = rng.choice(way["geom"])
            return region, plat, plon, way["hw"]
    return None


def cmd_sample(args):
    rng = random.Random(args.seed)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    rows = []
    try:
        while len(rows) < args.n:
            got = sample_road_point(rng, session, args.overpass_url, args.window_deg,
                                    args.max_tries)
            if got is None:
                continue
            region, lat, lon, hw = got
            rows.append({"id": len(rows), "lat": f"{lat:.7f}", "lon": f"{lon:.7f}",
                         "highway": hw, "sampling_region": region})
            print(f"  sampled {len(rows)}/{args.n}", flush=True)
    except OverpassUnavailable as exc:
        if rows:
            write_points(args.out, rows)
            print(f"wrote the {len(rows)} points drawn so far to {args.out}")
        sys.exit(str(exc))
    write_points(args.out, rows)
    print(f"wrote {args.out}")


# --------------------------------------------------------------------------- labelling

def cmd_label(args):
    rows = read_points(args.points)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for row in rows:
        if row.get("country_code") and not args.force:
            continue
        params = {"format": "jsonv2", "lat": row["lat"], "lon": row["lon"], "zoom": 3,
                  "accept-language": "en"}
        address = {}
        for attempt in range(3):
            try:
                r = session.get(NOMINATIM_URL, params=params, timeout=30)
                if r.status_code == 200:
                    address = r.json().get("address", {})
                    break
            except requests.RequestException:
                pass
            time.sleep(3 * (attempt + 1))
        row["country_code"] = (address.get("country_code") or "").lower()
        row["country"] = address.get("country") or ""
        row["continent"] = continent_of(row["country_code"], float(row["lon"]))
        print(f"  {row['id']}: {row['country'] or '(none)'}", flush=True)
        time.sleep(1.1)  # Nominatim usage policy: at most one request per second
    write_points(args.points, rows)


# --------------------------------------------------------------------------- checking

VALID_STATUSES = {"OK", "ZERO_RESULTS", "NOT_FOUND"}


def metadata(session, key, lat, lon, radius_m):
    params = {"location": f"{lat:.7f},{lon:.7f}", "radius": int(radius_m),
              "source": "outdoor", "key": key}
    for attempt in range(5):
        try:
            r = session.get(METADATA_URL, params=params, timeout=30)
            body = r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 ** attempt)
            continue
        status = body.get("status")
        if status in VALID_STATUSES:
            return body
        if status in ("OVER_QUERY_LIMIT", "UNKNOWN_ERROR"):
            time.sleep(2 ** attempt)
            continue
        # A bad key must stop the run. Recording it as "no coverage" would corrupt the result.
        sys.exit(f"Street View metadata request failed: {status} "
                 f"{body.get('error_message', '')}".strip())
    sys.exit("Street View metadata request kept failing; stopping rather than guessing.")


def official_coverage(session, key, lat, lon, radius_m):
    """
    Probe the point and four offsets around it. One query returns only the nearest
    panorama, and a third-party upload there would hide official imagery a few metres
    away, so any official panorama at any probe counts.

    The offset is radius_m / 111,320 degrees on both axes, matching the published run.
    East and west probes therefore sit slightly further out away from the equator.
    """
    d = radius_m / 111320.0
    saw_other = False
    for dlat, dlon in [(0, 0), (d, 0), (-d, 0), (0, d), (0, -d)]:
        body = metadata(session, key, lat + dlat, lon + dlon, radius_m)
        time.sleep(0.1)
        if body.get("status") != "OK":
            continue
        if "google" in (body.get("copyright") or "").lower():
            return True, "official", body.get("date", "")
        saw_other = True
    return False, ("crowdsourced only" if saw_other else "none found"), ""


def cmd_check(args):
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        sys.exit("Set GOOGLE_MAPS_API_KEY. Metadata requests are not billed, but need a key.")
    rows = read_points(args.points)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    today = date.today().isoformat()
    for row in rows:
        covered, result, captured = official_coverage(session, key, float(row["lat"]),
                                                      float(row["lon"]), args.radius)
        row.update({"covered": str(covered).lower(), "result": result,
                    "capture_date": captured, "checked_on": today})
        print(f"  {row['id']}: {result}", flush=True)
    out = args.out or args.points
    write_points(out, rows)
    print(f"wrote {out}")


# --------------------------------------------------------------------------- reporting

def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def tally(rows, key):
    out = defaultdict(lambda: [0, 0])
    for r in rows:
        out[r[key] or "(unknown)"][0] += 1
        out[r[key] or "(unknown)"][1] += r["covered"] == "true"
    return dict(sorted(out.items(), key=lambda kv: (-kv[1][0], kv[0])))


def print_table(title, groups):
    print(f"\n{title}")
    print(f"  {'':22}{'points':>7}{'covered':>9}{'without':>9}   95% interval (without)")
    for name, (n, k) in groups.items():
        lo, hi = wilson(n - k, n)
        print(f"  {name:22}{n:7}{k:9}{(n - k) / n * 100:8.1f}%   {lo * 100:4.0f}% to {hi * 100:3.0f}%")


def cmd_report(args):
    rows = [r for r in read_points(args.points) if r.get("covered") in ("true", "false")]
    if not rows:
        sys.exit("No checked points. Run `check` first.")
    n = len(rows)
    k = sum(r["covered"] == "true" for r in rows)
    lo, hi = wilson(n - k, n)
    print(f"Points checked: {n}")
    print(f"Without official coverage: {n - k} ({(n - k) / n * 100:.1f}%, "
          f"95% interval {lo * 100:.0f}% to {hi * 100:.0f}%)")

    by_continent = tally(rows, "continent")
    by_region = tally(rows, "sampling_region")
    by_country = tally(rows, "country")
    print_table("By continent (country of each point)", by_continent)
    print_table("By sampling region (the boxes points were drawn from)", by_region)
    if args.by_country:
        print_table("By country", by_country)
    print("\nResult detail:", dict(Counter(r["result"] for r in rows)))

    def as_dict(groups):
        return {name: {"points": n_, "covered": k_} for name, (n_, k_) in groups.items()}

    summary = {
        "points": n,
        "covered": k,
        "without_coverage_pct": round((n - k) / n * 100, 1),
        "without_coverage_95ci_pct": [round(lo * 100, 1), round(hi * 100, 1)],
        "by_continent": as_dict(by_continent),
        "by_sampling_region": as_dict(by_region),
        "by_country": as_dict(by_country),
        "checked_on": sorted({r["checked_on"] for r in rows if r.get("checked_on")}),
    }
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.summary}")
    if args.geojson:
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": {
                "id": int(r["id"]), "covered": r["covered"] == "true", "result": r["result"],
                "country": r["country"], "continent": r["continent"], "highway": r["highway"],
                "marker-color": "#2e7d32" if r["covered"] == "true" else "#c62828",
                "marker-size": "small",
            },
        } for r in rows]
        Path(args.geojson).write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, indent=1) + "\n",
            encoding="utf-8")
        print(f"wrote {args.geojson}")


# --------------------------------------------------------------------------- io

def read_points(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_points(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def main():
    ap = argparse.ArgumentParser(description="Street-level coverage benchmark")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="draw random points on mapped roads")
    s.add_argument("--n", type=int, default=120)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--window-deg", type=float, default=0.35,
                   help="side of the random window searched for roads, in degrees")
    s.add_argument("--max-tries", type=int, default=12)
    s.add_argument("--overpass-url", default=OVERPASS_URL,
                   help="Overpass API endpoint; pass a mirror if the main one is busy")
    s.add_argument("--out", default="data/new_points.csv")
    s.set_defaults(func=cmd_sample)

    lb = sub.add_parser("label", help="add country and continent (Nominatim)")
    lb.add_argument("--points", required=True)
    lb.add_argument("--force", action="store_true", help="relabel points that have a country")
    lb.set_defaults(func=cmd_label)

    c = sub.add_parser("check", help="check official Street View coverage")
    c.add_argument("--points", required=True)
    c.add_argument("--radius", type=float, default=120.0, help="search radius in metres")
    c.add_argument("--out", help="write here instead of updating --points in place")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("report", help="summarise a checked points file")
    r.add_argument("--points", required=True)
    r.add_argument("--by-country", action="store_true")
    r.add_argument("--summary", help="write a JSON summary here")
    r.add_argument("--geojson", help="write a GeoJSON map of the points here")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
