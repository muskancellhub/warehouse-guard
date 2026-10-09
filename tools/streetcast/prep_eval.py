"""
StreetCast — pre-fill eval.json for labeling.

For each briefing config in eval.json, regenerate it through the live engine and
fill its `claims` array with the generated sentences, each with text, clip,
start, end, and a stream_url to watch — leaving `grounded` as null for you to set.

Run on the team-24 VM:
    cd ~/vast-builders-challenge
    python tools/streetcast/prep_eval.py

Then open tools/streetcast/eval.json and set "grounded": true / false on each claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_PATH = ROOT / "eval.json"

sys.path.insert(0, str(ROOT))
import api  # noqa: E402


def main() -> None:
    data = json.loads(EVAL_PATH.read_text())
    total = 0
    for e in data:
        dest, tod, mode = e["destination"], e["time_of_day"], e["mode"]
        try:
            out = api.run_brief(dest, tod, mode)
        except Exception as exc:
            print(f"  ! {dest}/{tod}/{mode}: regen failed ({exc}) — leaving empty")
            e["claims"] = []
            continue
        script = out.get("script", [])
        stream_by_clip = {c.get("clip"): c.get("stream_url") for c in out.get("clips", [])}
        e["claims"] = [
            {
                "text": s.get("text"),
                "clip": s.get("clip"),
                "start": s.get("start"),
                "end": s.get("end"),
                "stream_url": stream_by_clip.get(s.get("clip")),
                "grounded": None,
            }
            for s in script
        ]
        total += len(e["claims"])
        print(f"  {dest}/{tod}/{mode}: {len(e['claims'])} sentences")

    EVAL_PATH.write_text(json.dumps(data, indent=2))
    print(f"\nWrote {total} claims across {len(data)} briefings to {EVAL_PATH}")
    if total < 60:
        print("NOTE: under 60 claims. Swap any empty (san_francisco/toronto) "
              "briefings for more new_york time/mode combos and rerun.")


if __name__ == "__main__":
    main()
