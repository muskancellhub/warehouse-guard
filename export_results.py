#!/usr/bin/env python3
"""Save the StreetCast engine's results as the files the app serves.

Run on the workshop VM, with the engine (tools/streetcast/api.py) running:

    cd tools/streetcast && PORT=8000 python api.py           # terminal 1
    python3 export_results.py --api http://localhost:8000    # terminal 2
    bash deploy.sh                                           # publish

Writes app/ride_<VIDEO>.json and app/brief_<place>_<time>_<mode>.json. Login tokens
(stream_url) are stripped and long per-segment text is dropped, so the files are safe
to commit and small enough for the ConfigMap.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
sys.path.insert(0, APP)
from main import normalize_brief, normalize_ride  # noqa: E402  (same rules the server applies)

# The engine filters briefings by location only, so the NYC neighborhoods search all of New York.
ENGINE_DESTINATION = {"walker_broadway": "new_york", "8th_ave_34th": "new_york", "nyc_bike_lanes": "new_york"}

DEFAULT_BRIEFS = [
    "walker_broadway:dusk:walking",
    "walker_broadway:dusk:cycling",
    "8th_ave_34th:day:driving",
    "new_york:night:cycling",
    "san_francisco:day:walking",
    "toronto:day:driving",
]

HEAVY_SEGMENT_FIELDS = ("description", "detections", "object_classes", "filename", "original_video", "location")


def call(api, path, body=None, timeout=900):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def write(name, data):
    path = os.path.join(APP, name)
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    return os.path.getsize(path)


def export_ride(api, video):
    data = normalize_ride(call(api, "/ride", {"video": video}))
    for seg in data.get("segments") or []:
        for field in HEAVY_SEGMENT_FIELDS:
            seg.pop(field, None)
    data.setdefault("video", video)
    data["label"] = f"{data['video']} · NYC bike ride"
    events = data.get("events") or []
    missing = sum(1 for e in events if not e.get("source"))
    size = write(f"ride_{video}.json", data)
    print(f"  ride {video}: {len(events)} events, {missing} without video, {size // 1024} KB")


def export_brief(api, spec):
    place, time_of_day, mode = spec.split(":")
    engine_place = ENGINE_DESTINATION.get(place, place)
    data = normalize_brief(call(api, "/brief", {"destination": engine_place, "time_of_day": time_of_day, "mode": mode}))
    for clip in data.get("clips") or []:
        clip.pop("description", None)
    data["request"] = {"destination": place, "time_of_day": time_of_day, "mode": mode}
    lines = data.get("script") or []
    missing = sum(1 for line in lines if not line.get("source"))
    size = write(f"brief_{place}_{time_of_day}_{mode}.json", data)
    note = "" if engine_place == place else f" (searched {engine_place})"
    print(f"  brief {spec}: {len(lines)} lines, {missing} without video, {size // 1024} KB{note}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="engine base URL")
    parser.add_argument("--rides", nargs="*", help="ride ids, e.g. GOPR0130 (default: every ride the engine lists)")
    parser.add_argument("--briefs", nargs="*", default=DEFAULT_BRIEFS, help="place:time:mode, e.g. walker_broadway:dusk:walking")
    args = parser.parse_args()

    try:
        health = call(args.api, "/health", timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"Can't reach the engine at {args.api} ({exc}). Start it with: cd tools/streetcast && PORT=8000 python api.py")
    print(f"engine ok: {health}")

    rides = args.rides
    if rides is None:
        listed = call(args.api, "/rides").get("rides") or []
        rides = [r.get("video") or r.get("ride_id") or r.get("id") if isinstance(r, dict) else str(r) for r in listed]
        rides = [r for r in rides if r]
    failures = 0
    print(f"rides: {', '.join(rides) or 'none'}")
    for video in rides:
        try:
            export_ride(args.api, video)
        except Exception as exc:
            failures += 1
            print(f"  ride {video}: FAILED ({exc})")
    print("briefings:")
    for spec in args.briefs:
        try:
            export_brief(args.api, spec)
        except Exception as exc:
            failures += 1
            print(f"  brief {spec}: FAILED ({exc})")

    total_kb = sum(os.path.getsize(os.path.join(APP, f)) for f in os.listdir(APP)) // 1024
    print(f"app/ is now {total_kb} KB (ConfigMap limit is about 1000 KB)")
    print("Next: bash deploy.sh" if not failures else f"{failures} export(s) failed; fix and re-run, then bash deploy.sh")


if __name__ == "__main__":
    main()
