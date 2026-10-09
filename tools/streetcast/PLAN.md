# StreetCast — Engine Plan

Team 24 · Engineer A (backend) · shared with Muskan (app) and Kritika (queries / eval)

This plan is the **contract** for the shared engine. Implementations may diverge on field names, nesting, or helper helpers — treat schemas as soft contracts: accept aliases, tolerate nulls, and keep response shapes mock-compatible so Muskan’s app and Kritika’s eval can land without waiting on exact backend internals.

---

## Product in one paragraph

StreetCast turns indexed street video into cited events. One engine, two faces:

1. **RideReport** — one ride in → category counts, time lost by cause, drafted 311 complaints (fleet numbers, never plates).
2. **Block briefing** — location + time of day + travel mode in → ~60s narrated script; every sentence cites a clip and time range.

---

## Collaboration rules (read first)

| Teammate | Owns | What Engineer A must leave stable |
| --- | --- | --- |
| **Muskan** | `tools/streetcast/app` | Response shapes for `GET /rides`, `POST /ride`, `POST /brief` stay compatible with `mock_ride.json` / `mock_brief.json`. Prefer additive fields over renames. |
| **Kritika** | `queries.json`, `eval.json`, README, demo | Queries are loaded from `tools/streetcast/queries.json` (not hardcoded). Eval regenerates briefings via the same `/brief` path. |
| **Engineer A** | `api.py`, Weave traces, PLAN | Engine + API. Do not block the app on exact LLM model IDs or internal batch sizes. |

**Adaptability defaults**

- Accept either `video` or `original_video` / short id (`GOPR0130`) for ride selection.
- Accept `destination` aliases (`new_york` ↔ `New York`); normalize before search filters.
- Event fields may be `null` when unknown; never invent license plates.
- If a teammate’s mock uses a slightly different key (`fleetNumber` vs `fleet_number`), normalize on read/write at the API boundary.
- If `queries.json` is missing or incomplete, fall back to the query table in `24teamplan.md` and log a warning — do not fail the whole brief.
- Stream URLs and clip ids should be whatever VSS returns; the app only needs something playable via `/videos/stream`.

When in doubt: **preserve Muskan’s mock JSON shapes** and **Kritika’s query list**; adapt internally.

---

## Shared engine

### Inputs (segment set)

| Mode | How segments are chosen |
| --- | --- |
| Ride | All indexed segments of one parent video (camera / capture type `nyc_bike_gopro-1` or matching parent id), ordered by time |
| Block | Top search hits for a location (from Kritika’s mode queries + time-of-day word), deduped |

For each segment, read:

- Cosmos / reasoning **description** (`reasoning_content` or equivalent)
- YOLO **detections** (labels + counts; bbox optional)
- Clip identity: `source` / filename, `start` / `end` (or segment window)
- Playback: stream or playback URL when available

### LLM extraction (W&B Inference)

Call W&B Inference (OpenAI-compatible; `WANDB_API_KEY` + base URL from env). List models and pick a strong instruct model at runtime (do not hardcode a model that may not exist on the team’s account).

Ask for **strict JSON** events:

```json
{
  "clip": "string — segment filename or source key",
  "start": 0,
  "end": 5,
  "category": "blocked bike lane | double parking | street obstruction | sanitation | graffiti | construction | none",
  "company": "string | null",
  "fleet_number": "string | null",
  "rider_reaction": "stopped | slowed | riding | null",
  "street": "string | null — from signs/captions, else \"not visible\"",
  "text": "short human description of what happened"
}
```

**Hard rules**

- Categories are real NYC 311-style types only (list above), or `none`.
- **Never** report license plates. Prefer company + fleet number when visible in captions/detections.
- Batch segment descriptions (size adaptable: e.g. 4–12); merge results after all batches.

### Dedup

Merge into one event when either:

- Same `company` **and** `fleet_number`, or
- Same / near-same `text` (or description) within **60 seconds** on the timeline

Keep the earliest start, latest end, richest non-null fields.

### Weave

- `weave.init(WANDB_PROJECT)` (project from env; team from `WANDB_TEAM` if needed)
- Every LLM call wrapped in `@weave.op` (extraction, briefing script, optional complaint drafting)
- Trace inputs/outputs enough for Kritika’s eval later — no secrets in traces

---

## Mode 1 — RideReport

**In:** one ride / parent video id  
**Out:**

```json
{
  "video": "GOPR0130",
  "segments": [ /* ordered: clip, start, end, description?, detections?, stream_url? */ ],
  "events": [ /* schema above */ ],
  "summary": {
    "duration_s": 0,
    "events": 0,
    "reports_ready": 0,
    "time_lost_s": 0,
    "by_category": { "blocked bike lane": 0 },
    "by_cause": { "delivery truck": 0.7 }
  },
  "complaints": [
    {
      "category": "...",
      "street": "...",
      "what_happened": "...",
      "company": "...",
      "fleet_number": "...",
      "clip": "...",
      "timestamp": "start-end or clock if available"
    }
  ]
}
```

**Time lost:** seconds of segments (or event windows) where `rider_reaction` is `stopped` or `slowed`, split by cause (company type, category, or caption-derived cause). Fractions in `by_cause` should sum ≈ 1.0 when present.

**311 drafts:** one complaint per merged event (skip `category: none`). Align field names with Muskan’s mock where possible (`text` on events; complaint body can mirror mock event text).

---

## Mode 2 — Block briefing

**In:** `{ destination, time_of_day, mode }`  
**Queries:** load from `tools/streetcast/queries.json` keyed by `mode` (`walking` | `cycling` | `driving`). Append the `time_of_day` word to each query string. Filter search by location / destination. Keep top ~3 hits per query, then dedupe across queries.

**LLM:** ask for **6–8 second-person sentences**, each citing a clip and start–end. Drop any sentence without a valid citation.

**Out:**

```json
{
  "script": [
    { "text": "...", "clip": "...", "start": 0, "end": 5 }
  ],
  "clips": [ /* optional richer clip metadata for the player */ ],
  "stats": { "person": 0, "car": 0, "truck": 0, "bicycle": 0 }
}
```

`stats` = aggregate YOLO label counts over the cited / candidate clips (keys adaptable; Muskan’s mock uses person/car/truck/bicycle). Target narration length ~60 seconds total.

---

## Planned API surface (A2 — for alignment only)

Do not implement in A1. Shapes Muskan can code against now:

| Method | Path | Body / notes |
| --- | --- | --- |
| `GET` | `/rides` | Lists `nyc_bike_gopro-1` (or equivalent) parent videos |
| `POST` | `/ride` | `{ "video": "GOPR0130" }` → RideReport payload |
| `POST` | `/brief` | `{ "destination", "time_of_day", "mode" }` → briefing payload |

First smoke test (A2): `POST /ride` on **GOPR0130**.

---

## File layout (shared repo)

```
tools/streetcast/
  PLAN.md          ← this file (A1)
  api.py           ← A2
  queries.json     ← Kritika → Engineer A
  eval.json        ← Kritika
  eval.py          ← A3
  mock_ride.json   ← Muskan
  mock_brief.json  ← Muskan
  app/             ← Muskan
```

---

## Env (names only; values already on the VM)

- VSS / retrieval: team-24 backend JWT flow (`BACKEND`, login skill)
- `WANDB_API_KEY`, `WANDB_PROJECT`, `WANDB_TEAM`
- S3 / VastDB buckets as configured for team-24

Never commit secrets. Never invent plates.

---

## Out of scope for A1

No API code, no app, no eval runner, no deploy. Next paste is **A2** (`api.py` + Weave + GOPR0130 test).
