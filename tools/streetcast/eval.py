"""
StreetCast A3 — claim-grounding evaluation.

Reads tools/streetcast/eval.json (the human-labeled briefings), regenerates each
briefing through the SAME engine the live POST /brief uses (api.run_brief),
scores the human grounding labels, logs a Weave Evaluation, and prints a table:
briefing, sentences, grounded, rate.

The grounding label is the human judgment in eval.json: for each cited sentence,
`grounded: true` means the cited clip actually shows the claim. The rate for a
briefing is grounded sentences / total cited sentences.

Run on the team-24 VM (so WANDB_* and the VSS creds are present):
    cd ~/vast-builders-challenge
    python tools/streetcast/eval.py

Score the labels without calling the API (no regeneration):
    python tools/streetcast/eval.py --no-regen
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_PATH = ROOT / "eval.json"

# Import the live engine (same module app/main.py imports its callables from).
sys.path.insert(0, str(ROOT))
import api  # noqa: E402

try:
    import weave
except ImportError:
    weave = None


def load_eval() -> list[dict]:
    if not EVAL_PATH.exists():
        raise SystemExit(f"{EVAL_PATH} not found — label eval.json first.")
    data = json.loads(EVAL_PATH.read_text())
    if not isinstance(data, list):
        raise SystemExit("eval.json must be a list of briefings.")
    return data


def score_claims(claims: list[dict]) -> tuple[int, int, float]:
    """rate = cited sentences whose clip actually shows the claim (human label)."""
    total = len(claims)
    grounded = sum(1 for c in claims if bool(c.get("grounded")))
    rate = (grounded / total) if total else 0.0
    return total, grounded, rate


def regenerate(destination: str, time_of_day: str, mode: str) -> list[dict]:
    """Re-run the briefing through the live engine; return the fresh script or []."""
    try:
        out = api.run_brief(destination, time_of_day, mode)
        return out.get("script", [])
    except Exception as exc:
        print(f"  ! regen failed for {destination}/{time_of_day}/{mode}: {exc}")
        return []


def briefing_label(e: dict) -> str:
    return f"{e.get('destination')}/{e.get('time_of_day')}/{e.get('mode')}"


def log_weave_evaluation(data: list[dict]) -> bool:
    """Best-effort Weave Evaluation. Returns True if logged."""
    if weave is None:
        print("weave not installed; skipping Evaluation logging.")
        return False
    try:
        team = getattr(api, "WANDB_TEAM", "")
        project = f"{team}/{api.WANDB_PROJECT}" if team else api.WANDB_PROJECT
        weave.init(project)

        dataset = [
            {
                "destination": e["destination"],
                "time_of_day": e["time_of_day"],
                "mode": e["mode"],
                "claims": e.get("claims", []),
            }
            for e in data
        ]

        @weave.op
        def streetcast_brief(destination: str, time_of_day: str, mode: str, claims: list) -> dict:
            return {"script": regenerate(destination, time_of_day, mode)}

        @weave.op
        def claim_grounding(claims: list, output: dict) -> dict:
            total, grounded, rate = score_claims(claims)
            return {"sentences": total, "grounded": grounded, "grounding_rate": rate}

        evaluation = weave.Evaluation(dataset=dataset, scorers=[claim_grounding])
        asyncio.run(evaluation.evaluate(streetcast_brief))
        return True
    except Exception as exc:
        print(f"Weave Evaluation logging skipped: {exc}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--no-regen",
        action="store_true",
        help="score the human labels only; do not call /brief or log to Weave",
    )
    args = ap.parse_args()

    data = load_eval()
    total_claims = sum(len(e.get("claims", [])) for e in data)
    if total_claims == 0:
        print("WARNING: every briefing has 0 claims — eval.json is unlabeled. "
              "The table will be empty until you label it.")

    weave_logged = False
    if not args.no_regen:
        weave_logged = log_weave_evaluation(data)

    # Always print the table straight from the human labels (correct even if
    # regeneration or Weave logging failed).
    rows = []
    tot_sent = tot_grounded = 0
    for e in data:
        total, grounded, rate = score_claims(e.get("claims", []))
        tot_sent += total
        tot_grounded += grounded
        rows.append((briefing_label(e), total, grounded, rate))

    overall = (tot_grounded / tot_sent) if tot_sent else 0.0

    print("\nStreetCast claim-grounding eval")
    print(f"{'briefing':42} {'sentences':>9} {'grounded':>9} {'rate':>7}")
    print("-" * 70)
    for label, total, grounded, rate in rows:
        print(f"{label:42} {total:>9} {grounded:>9} {rate:>6.0%}")
    print("-" * 70)
    print(f"{'TOTAL':42} {tot_sent:>9} {tot_grounded:>9} {overall:>6.0%}")

    if weave_logged:
        print("\nLogged to Weave as an Evaluation.")
    elif not args.no_regen:
        print("\n(Weave Evaluation not logged — see message above; the table above is from the labels.)")


if __name__ == "__main__":
    main()
