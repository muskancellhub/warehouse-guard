"""
StreetCast brief API — adaptable FastAPI service for Team 24.

Contracts target tools/streetcast/PLAN.md and Muskan's mock_*.json shapes.
Field aliases and nulls are tolerated so teammates can integrate without matching
exact internals.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
import weave
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config (env + /config/<team>.config). Never log secrets.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
QUERIES_PATH = ROOT / "queries.json"
FALLBACK_QUERIES = {
    "walking": [
        "pedestrians crossing at a busy crosswalk",
        "construction barriers or scaffolding on the sidewalk",
        "police vehicle or officer on the street",
        "people waiting at a bus stop",
        "crowded sidewalk near storefronts",
        "rain or wet street with umbrellas",
    ],
    "cycling": [
        "truck or van stopped in the bike lane",
        "cyclist riding in a green bike lane",
        "delivery truck parked with rear doors open",
        "orange cones narrowing the lane",
        "cyclist stopped behind a parked truck",
        "car turning across a crosswalk with a cyclist",
    ],
    "driving": [
        "double-parked vehicle blocking a lane",
        "yellow taxi stopped at the intersection",
        "bus pulling into the intersection",
        "vehicle entering on a red light",
        "pedestrians stepping into the road between cars",
        "road work with cones and a lane closure",
    ],
}

CATEGORIES = {
    "blocked bike lane",
    "double parking",
    "street obstruction",
    "sanitation",
    "graffiti",
    "construction",
    "none",
}

RIDE_CAMERA = "nyc_bike_gopro-1"
WANDB_BASE = os.environ.get("WANDB_INFERENCE_BASE", "https://api.inference.wandb.ai/v1")
PREFERRED_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "openai/gpt-oss-120b",
    "Qwen/Qwen3.8-27B",
    "meta-llama/Llama-3.1-8B-Instruct",
    "openai/gpt-oss-20b",
]


def _load_team_config() -> None:
    """Source /config/*.config into os.environ if present (no overwrite of set vars)."""
    cfg_dir = Path("/config")
    if not cfg_dir.is_dir():
        return
    configs = sorted(cfg_dir.glob("*.config"))
    if len(configs) != 1:
        return
    for line in configs[0].read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_team_config()

BACKEND = os.environ.get("BACKEND") or os.environ.get("INGRESS_URL") or ""
WANDB_API_KEY = os.environ.get("WANDB_API_KEY") or ""
WANDB_PROJECT = os.environ.get("WANDB_PROJECT") or "team-24"
WANDB_TEAM = os.environ.get("WANDB_TEAM") or "vastdata"
VAST_USER = os.environ.get("USERNAME") or ""
VAST_PASS = os.environ.get("PASSWORD") or ""

_token: Optional[str] = None
_token_at: float = 0.0
_chosen_model: Optional[str] = None

weave.init(f"{WANDB_TEAM}/{WANDB_PROJECT}" if WANDB_TEAM else WANDB_PROJECT)

app = FastAPI(title="StreetCast", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models (aliases welcome)
# ---------------------------------------------------------------------------


class RideRequest(BaseModel):
    video: Optional[str] = None
    original_video: Optional[str] = None
    ride: Optional[str] = None

    def ride_id(self) -> str:
        raw = self.video or self.original_video or self.ride
        if not raw:
            raise HTTPException(400, "Provide video / original_video / ride")
        m = re.search(r"(GOPR\d+|GX\d+)", raw, re.I)
        return (m.group(1) if m else Path(raw).stem).upper().replace("GX0", "GX0")


class BriefRequest(BaseModel):
    destination: str = Field(..., description="e.g. new_york")
    time_of_day: str = Field(..., description="e.g. dusk, day, night")
    mode: str = Field(..., description="walking | cycling | driving")
    # aliases teammates might send
    location: Optional[str] = None
    travel_mode: Optional[str] = None

    def normalized(self) -> tuple[str, str, str]:
        dest = (self.destination or self.location or "").strip().lower().replace(" ", "_")
        tod = self.time_of_day.strip().lower()
        mode = (self.mode or self.travel_mode or "").strip().lower()
        aliases = {
            "nyc": "new_york",
            "newyork": "new_york",
            "sf": "san_francisco",
            "sanfran": "san_francisco",
        }
        dest = aliases.get(dest, dest)
        if mode not in ("walking", "cycling", "driving"):
            raise HTTPException(400, f"mode must be walking|cycling|driving, got {mode!r}")
        return dest, tod, mode


# ---------------------------------------------------------------------------
# VSS helpers
# ---------------------------------------------------------------------------


def _login(force: bool = False) -> str:
    global _token, _token_at
    if not force and _token and (time.time() - _token_at) < 50 * 60:
        return _token
    if not BACKEND or not VAST_USER:
        raise HTTPException(500, "BACKEND/INGRESS_URL and USERNAME not configured")
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{BACKEND}/api/v1/auth/login",
            json={"username": VAST_USER, "password": VAST_PASS},
        )
        r.raise_for_status()
        _token = r.json()["access_token"]
        _token_at = time.time()
        return _token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_login()}"}


def _get(path: str, params: Optional[dict] = None) -> Any:
    with httpx.Client(timeout=120.0) as client:
        r = client.get(f"{BACKEND}{path}", headers=_headers(), params=params or {})
        if r.status_code == 401:
            _login(force=True)
            r = client.get(f"{BACKEND}{path}", headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> Any:
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{BACKEND}{path}", headers=_headers(), json=body)
        if r.status_code == 401:
            _login(force=True)
            r = client.post(f"{BACKEND}{path}", headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()


def _stream_url(source: str) -> str:
    token = _login()
    return f"{BACKEND}/api/v1/videos/stream?source={quote(source, safe='')}&token={quote(token)}"


def _explore_all() -> list[dict]:
    chunks: list[dict] = []
    offset = 0
    while True:
        data = _get(
            "/api/v1/videos/explore",
            {"scope": "all", "limit": 48, "offset": offset},
        )
        batch = data.get("chunks") or []
        if not batch:
            break
        chunks.extend(batch)
        total = int(data.get("total") or 0)
        offset += len(batch)
        if offset >= total or len(batch) < 48:
            break
    return chunks


def _ride_id_from_chunk(chunk: dict) -> Optional[str]:
    blob = f"{chunk.get('filename','')} {chunk.get('original_video','')}"
    m = re.search(r"(GOPR\d+|GX\d+)", blob, re.I)
    return m.group(1).upper() if m else None


def _list_rides() -> list[dict]:
    by_ride: dict[str, list[dict]] = defaultdict(list)
    for c in _explore_all():
        if (c.get("camera_id") or "") != RIDE_CAMERA and "GOPR" not in (
            c.get("filename") or ""
        ):
            # still accept GOPR even if camera tag drifts
            if not re.search(r"GOPR\d+", c.get("filename") or "", re.I):
                continue
        rid = _ride_id_from_chunk(c)
        if rid:
            by_ride[rid].append(c)
    rides = []
    for rid, chunks in sorted(by_ride.items()):
        chunks = sorted(chunks, key=lambda x: x.get("filename") or "")
        rides.append(
            {
                "id": rid,
                "video": rid,
                "camera_id": RIDE_CAMERA,
                "chunk_count": len(chunks),
                "location": chunks[0].get("location"),
                "first_filename": chunks[0].get("filename"),
                "original_videos": [c.get("original_video") for c in chunks],
            }
        )
    return rides


def _segments_for_ride(ride_id: str) -> list[dict]:
    ride_id_u = ride_id.upper()
    chunks = [
        c
        for c in _explore_all()
        if _ride_id_from_chunk(c) and _ride_id_from_chunk(c).upper() == ride_id_u
    ]
    chunks = sorted(chunks, key=lambda x: x.get("filename") or "")
    if not chunks:
        raise HTTPException(404, f"No chunks found for ride {ride_id}")

    segments: list[dict] = []
    timeline_offset = 0.0
    for chunk in chunks:
        ov = chunk.get("original_video")
        if not ov:
            continue
        try:
            data = _get("/api/v1/tools/segments", {"original_video": ov})
        except Exception:
            # fallback: use explore timeline
            data = {"segments": chunk.get("timeline") or []}
        segs = data.get("segments") or []
        for s in segs:
            start = float(s.get("segment_start_sec") if s.get("segment_start_sec") is not None else 0)
            end = float(
                s.get("segment_end_sec")
                if s.get("segment_end_sec") is not None
                else (start + float(s.get("duration") or 5))
            )
            source = s.get("source") or ""
            filename = s.get("filename") or (Path(source).name if source else "")
            counts = s.get("object_counts")
            if isinstance(counts, str):
                try:
                    counts = json.loads(counts)
                except json.JSONDecodeError:
                    counts = {}
            segments.append(
                {
                    "clip": filename,
                    "filename": filename,
                    "source": source,
                    "start": start,
                    "end": end,
                    "ride_start": timeline_offset + start,
                    "ride_end": timeline_offset + end,
                    "description": s.get("reasoning_content")
                    or s.get("description")
                    or "",
                    "detections": counts or {},
                    "object_classes": s.get("object_classes") or "",
                    "original_video": ov,
                    "stream_url": _stream_url(source) if source else None,
                    "camera_id": s.get("camera_id") or chunk.get("camera_id"),
                    "location": s.get("location") or chunk.get("location"),
                }
            )
        # advance ride clock by chunk duration
        timeline_offset += float(chunk.get("chunk_duration_sec") or 30.0)
    segments.sort(key=lambda s: (s["ride_start"], s["clip"]))
    return segments


# ---------------------------------------------------------------------------
# W&B Inference + Weave
# ---------------------------------------------------------------------------


def _openai_client() -> OpenAI:
    if not WANDB_API_KEY:
        raise HTTPException(500, "WANDB_API_KEY not set")
    return OpenAI(
        api_key=WANDB_API_KEY,
        base_url=WANDB_BASE,
        project=f"{WANDB_TEAM}/{WANDB_PROJECT}",
        default_headers={"OpenAI-Project": f"{WANDB_TEAM}/{WANDB_PROJECT}"},
    )


def _pick_model(client: OpenAI) -> str:
    global _chosen_model
    if _chosen_model:
        return _chosen_model
    ids: list[str] = []
    try:
        ids = [m.id for m in client.models.list().data]
    except Exception:
        ids = list(PREFERRED_MODELS)
    for pref in PREFERRED_MODELS:
        if pref in ids:
            _chosen_model = pref
            return pref
    _chosen_model = ids[0] if ids else PREFERRED_MODELS[-1]
    return _chosen_model


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m:
            return json.loads(m.group(1))
        raise


@weave.op
def llm_chat(system: str, user: str, temperature: float = 0.2) -> str:
    client = _openai_client()
    model = _pick_model(client)
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


EXTRACT_SYSTEM = """You extract NYC 311-style street events from bike-cam / street segment descriptions.
Return ONLY valid JSON: {"events":[...]} with objects:
{clip, start, end, category, company, fleet_number, rider_reaction, street, text}

Rules:
- category MUST be one of: blocked bike lane, double parking, street obstruction, sanitation, graffiti, construction, none
- Never invent or report license plates. Prefer company + fleet_number when visible.
- company/fleet_number/street may be null; street default "not visible" if unknown
- rider_reaction: stopped | slowed | riding | null
- text: one short sentence of what happened
- Only emit real events you can support from the text; use category "none" sparingly (omit empty noise)
- start/end are seconds within the clip (numbers)
"""


@weave.op
def extract_events_batch(batch: list[dict]) -> list[dict]:
    payload = [
        {
            "clip": s.get("clip"),
            "start": s.get("start"),
            "end": s.get("end"),
            "ride_start": s.get("ride_start"),
            "description": (s.get("description") or "")[:1200],
            "detections": s.get("detections") or {},
        }
        for s in batch
    ]
    user = (
        "Extract 311 events from these segments. "
        "Use the given clip/start/end when possible.\n\n"
        + json.dumps(payload, indent=2)
    )
    raw = llm_chat(EXTRACT_SYSTEM, user)
    data = _extract_json(raw)
    events = data.get("events", data) if isinstance(data, dict) else data
    if not isinstance(events, list):
        return []
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        cat = (e.get("category") or "none").strip().lower()
        if cat not in CATEGORIES:
            # soft map
            for c in CATEGORIES:
                if c in cat:
                    cat = c
                    break
            else:
                cat = "none"
        # strip plate-like tokens from text
        text = str(e.get("text") or "")
        text = re.sub(
            r"\b([A-Z]{1,3}[-\s]?\d{2,4}[A-Z]{0,3}|\d{3}-?[A-Z]{3})\b",
            "[redacted]",
            text,
            flags=re.I,
        )
        out.append(
            {
                "clip": e.get("clip") or batch[0].get("clip"),
                "start": float(e.get("start") if e.get("start") is not None else batch[0].get("start") or 0),
                "end": float(e.get("end") if e.get("end") is not None else batch[0].get("end") or 5),
                "category": cat,
                "company": e.get("company"),
                "fleet_number": e.get("fleet_number") or e.get("fleetNumber"),
                "rider_reaction": e.get("rider_reaction") or e.get("riderReaction"),
                "street": e.get("street") or "not visible",
                "text": text,
                "ride_start": e.get("ride_start"),
            }
        )
    return out


BRIEF_SYSTEM = """You write a ~60-second second-person street briefing for a newsroom montage.
Return ONLY JSON: {"script":[{"text","clip","start","end"}, ...]} with 6 to 8 sentences.
Every sentence MUST cite a clip filename and start/end from the provided clip list.
Drop anything you cannot cite. Speak to the traveler ("You're heading…", "Expect…").
Do not invent clips. Never mention license plates.
"""


@weave.op
def generate_briefing_script(destination: str, time_of_day: str, mode: str, clips: list[dict]) -> list[dict]:
    user = json.dumps(
        {
            "destination": destination,
            "time_of_day": time_of_day,
            "mode": mode,
            "clips": [
                {
                    "clip": c.get("clip"),
                    "start": c.get("start"),
                    "end": c.get("end"),
                    "description": (c.get("description") or "")[:800],
                    "query": c.get("query"),
                }
                for c in clips
            ],
        },
        indent=2,
    )
    raw = llm_chat(BRIEF_SYSTEM, user)
    data = _extract_json(raw)
    script = data.get("script", data) if isinstance(data, dict) else data
    if not isinstance(script, list):
        return []
    allowed = {c.get("clip") for c in clips}
    clean = []
    for s in script:
        if not isinstance(s, dict):
            continue
        clip = s.get("clip")
        text = (s.get("text") or "").strip()
        if not text or not clip or clip not in allowed:
            # fuzzy: allow basename match
            if clip and any(clip in (a or "") or (a or "") in clip for a in allowed):
                pass
            else:
                continue
        clean.append(
            {
                "text": text,
                "clip": clip,
                "start": float(s.get("start") or 0),
                "end": float(s.get("end") or 5),
            }
        )
    return clean[:8]


# ---------------------------------------------------------------------------
# Domain logic
# ---------------------------------------------------------------------------


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower().strip())


def merge_duplicates(events: list[dict]) -> list[dict]:
    """Same company+fleet OR similar text within 60s → one event."""
    if not events:
        return []
    # attach trip time if missing
    enriched = []
    for e in events:
        e = dict(e)
        if e.get("ride_start") is None:
            e["ride_start"] = float(e.get("start") or 0)
        enriched.append(e)
    enriched.sort(key=lambda e: float(e.get("ride_start") or 0))

    merged: list[dict] = []
    for e in enriched:
        matched = None
        for m in merged:
            same_fleet = (
                e.get("company")
                and m.get("company")
                and e.get("fleet_number")
                and m.get("fleet_number")
                and str(e["company"]).lower() == str(m["company"]).lower()
                and str(e["fleet_number"]) == str(m["fleet_number"])
            )
            close_text = (
                _norm_text(e.get("text") or "")
                and _norm_text(e.get("text") or "") == _norm_text(m.get("text") or "")
                and abs(float(e["ride_start"]) - float(m["ride_start"])) <= 60
            )
            # soft text similarity: shared prefix
            soft = False
            t1, t2 = _norm_text(e.get("text") or ""), _norm_text(m.get("text") or "")
            if t1 and t2 and abs(float(e["ride_start"]) - float(m["ride_start"])) <= 60:
                if t1[:40] == t2[:40] or t1 in t2 or t2 in t1:
                    soft = True
            if same_fleet or close_text or soft:
                matched = m
                break
        if matched is None:
            merged.append(e)
            continue
        # merge fields
        matched["start"] = min(float(matched["start"]), float(e["start"]))
        matched["end"] = max(float(matched["end"]), float(e["end"]))
        matched["ride_start"] = min(float(matched["ride_start"]), float(e["ride_start"]))
        for k in ("company", "fleet_number", "street", "rider_reaction", "text", "clip"):
            if not matched.get(k) and e.get(k):
                matched[k] = e[k]
        if matched.get("category") in (None, "none") and e.get("category") not in (None, "none"):
            matched["category"] = e["category"]
    # keep actionable events; drop category "none"
    return [e for e in merged if (e.get("category") or "none") != "none"]


def compute_summary(segments: list[dict], events: list[dict]) -> dict:
    duration = 0.0
    if segments:
        duration = max(float(s.get("ride_end") or 0) for s in segments)
    by_cat: dict[str, int] = defaultdict(int)
    for e in events:
        cat = e.get("category") or "none"
        if cat != "none":
            by_cat[cat] += 1

    time_lost = 0.0
    cause_seconds: dict[str, float] = defaultdict(float)
    for e in events:
        reaction = (e.get("rider_reaction") or "").lower()
        if reaction not in ("stopped", "slowed"):
            continue
        span = max(0.0, float(e.get("end") or 0) - float(e.get("start") or 0))
        # prefer ride window if present
        if e.get("ride_end") is not None and e.get("ride_start") is not None:
            span = max(span, float(e["ride_end"]) - float(e["ride_start"]))
        if span <= 0:
            span = 5.0
        time_lost += span
        cause = e.get("company") or e.get("category") or "other"
        cause_seconds[str(cause).lower()] += span

    by_cause = {}
    if time_lost > 0:
        by_cause = {k: round(v / time_lost, 3) for k, v in cause_seconds.items()}

    return {
        "duration_s": round(duration),
        "events": len([e for e in events if e.get("category") != "none"]),
        "reports_ready": len([e for e in events if e.get("category") not in (None, "none")]),
        "time_lost_s": round(time_lost),
        "by_category": dict(by_cat),
        "by_cause": by_cause,
    }


def draft_complaints(events: list[dict]) -> list[dict]:
    complaints = []
    for e in events:
        if (e.get("category") or "none") == "none":
            continue
        complaints.append(
            {
                "category": e.get("category"),
                "street": e.get("street") or "not visible",
                "what_happened": e.get("text"),
                "company": e.get("company"),
                "fleet_number": e.get("fleet_number"),
                "clip": e.get("clip"),
                "timestamp": f"{e.get('start')}-{e.get('end')}",
            }
        )
    return complaints


def load_queries() -> dict[str, list[str]]:
    if QUERIES_PATH.exists():
        try:
            data = json.loads(QUERIES_PATH.read_text())
            # accept {mode: [q,...]} or {modes:{...}} or list of {mode,query}
            if isinstance(data, dict) and any(
                k in data for k in ("walking", "cycling", "driving")
            ):
                return {k: list(v) for k, v in data.items() if isinstance(v, list)}
            if isinstance(data, dict) and "modes" in data:
                return data["modes"]
        except Exception as exc:
            print(f"warning: queries.json unreadable ({exc}); using fallback")
    else:
        print("warning: queries.json missing; using fallback from PLAN")
    return FALLBACK_QUERIES


def search_hits(query: str, location: str, top_k: int = 3) -> list[dict]:
    body = {
        "query": query,
        "top_k": top_k,
        "llm_top_n": 0,
        "min_similarity": 0.25,
        "metadata_filters": {"location": location},
        "include_public": True,
    }
    try:
        data = _post("/api/v1/search", body)
    except Exception:
        # location filter may be too strict — retry without
        body.pop("metadata_filters", None)
        data = _post("/api/v1/search", body)
    results = data.get("results") or []
    hits = []
    for r in results[:top_k]:
        filename = r.get("filename") or Path(r.get("source") or "").name
        counts = r.get("object_counts")
        if isinstance(counts, str):
            try:
                counts = json.loads(counts)
            except json.JSONDecodeError:
                counts = {}
        hits.append(
            {
                "clip": filename,
                "source": r.get("source"),
                "start": float(r.get("segment_start_sec") or r.get("best_match_start_sec") or 0),
                "end": float(r.get("segment_end_sec") or r.get("best_match_end_sec") or 5),
                "description": r.get("reasoning_content") or "",
                "detections": counts or {},
                "query": query,
                "similarity": r.get("similarity_score"),
                "stream_url": _stream_url(r["source"]) if r.get("source") else None,
            }
        )
    return hits


def aggregate_stats(clips: list[dict]) -> dict[str, int]:
    keys = ("person", "car", "truck", "bicycle", "bus", "motorcycle")
    stats = {k: 0 for k in keys}
    for c in clips:
        det = c.get("detections") or {}
        if isinstance(det, str):
            try:
                det = json.loads(det)
            except json.JSONDecodeError:
                det = {}
        for k in keys:
            stats[k] += int(det.get(k) or 0)
    return stats


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {
        "ok": True,
        "backend": bool(BACKEND),
        "wandb": bool(WANDB_API_KEY),
        "model": _chosen_model,
    }


@app.get("/rides")
def rides():
    return {"rides": _list_rides()}


@app.post("/ride")
def ride(req: RideRequest):
    ride_id = req.ride_id()
    # normalize accidental casing
    m = re.search(r"(GOPR\d+|GX\d+)", ride_id, re.I)
    ride_id = m.group(1) if m else ride_id

    segments = _segments_for_ride(ride_id)
    # batch LLM
    batch_size = int(os.environ.get("STREETCAST_BATCH", "6"))
    raw_events: list[dict] = []
    for i in range(0, len(segments), batch_size):
        batch = segments[i : i + batch_size]
        try:
            raw_events.extend(extract_events_batch(batch))
        except Exception as exc:
            print(f"extract batch failed: {exc}")
            continue

    # stamp ride_start from matching segment when missing
    by_clip = {s["clip"]: s for s in segments}
    for e in raw_events:
        seg = by_clip.get(e.get("clip") or "")
        if seg and e.get("ride_start") is None:
            e["ride_start"] = seg["ride_start"] + float(e.get("start") or 0)
            e["ride_end"] = seg["ride_start"] + float(e.get("end") or 5)

    events = merge_duplicates(raw_events)
    summary = compute_summary(segments, events)
    complaints = draft_complaints(events)

    # response shape matches mock_ride.json (+ extras for debugging)
    return {
        "video": ride_id,
        "segments": [
            {
                "clip": s["clip"],
                "start": s["start"],
                "end": s["end"],
                "ride_start": s["ride_start"],
                "ride_end": s["ride_end"],
                "description": s["description"],
                "detections": s["detections"],
                "stream_url": s["stream_url"],
                "source": s["source"],
            }
            for s in segments
        ],
        "events": [
            {
                "clip": e.get("clip"),
                "start": e.get("start"),
                "end": e.get("end"),
                "category": e.get("category"),
                "company": e.get("company"),
                "fleet_number": e.get("fleet_number"),
                "rider_reaction": e.get("rider_reaction"),
                "street": e.get("street"),
                "text": e.get("text"),
            }
            for e in events
        ],
        "summary": summary,
        "complaints": complaints,
    }


@app.post("/brief")
def brief(req: BriefRequest):
    destination, time_of_day, mode = req.normalized()
    queries = load_queries().get(mode) or FALLBACK_QUERIES.get(mode) or []
    if not queries:
        raise HTTPException(400, f"No queries for mode={mode}")

    collected: list[dict] = []
    seen_clips: set[str] = set()
    for q in queries:
        q_full = f"{q} {time_of_day}".strip()
        for hit in search_hits(q_full, destination, top_k=3):
            clip = hit.get("clip") or ""
            if clip in seen_clips:
                continue
            seen_clips.add(clip)
            collected.append(hit)

    if not collected:
        return {
            "script": [],
            "clips": [],
            "stats": {},
            "message": "No clips found for that destination/mode. Try another location or mode.",
        }

    script = generate_briefing_script(destination, time_of_day, mode, collected)
    cited = {s["clip"] for s in script}
    clips_out = [c for c in collected if c.get("clip") in cited] or collected[:8]
    stats = aggregate_stats(clips_out)

    return {
        "script": script,
        "clips": [
            {
                "clip": c.get("clip"),
                "source": c.get("source"),
                "start": c.get("start"),
                "end": c.get("end"),
                "stream_url": c.get("stream_url"),
                "description": c.get("description"),
            }
            for c in clips_out
        ],
        "stats": stats,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
    )
