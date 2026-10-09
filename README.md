# StreetCast

**Team 24 · VAST Builders Challenge NYC · Oct 2026**

StreetCast turns street video into cited, actionable street-issue reports. Nobody files
a 311 complaint from a moving bike, so the problems a rider passes — blocked lanes,
double-parked delivery trucks, trash piles, graffiti, construction — go unrecorded.
StreetCast watches the ride instead: it reads what the cameras already saw and produces
filed-ready 311 reports and a narrated neighborhood briefing, every claim tied to the
clip and second it came from.

One engine, two faces:

- **RideReport** — one bike ride in, every street-issue event out, each as a drafted 311
  complaint with the clip, timestamp, street, and (for commercial vehicles) company +
  fleet number. Never a license plate.
- **Block briefing** — pick a neighborhood, time of day, and travel mode, and get a
  ~60-second narrated montage a newsroom could air, where every sentence cites a clip
  and a start–end range.

---

## How it works

```
VAST search (VastDB index)
        │  find the segments for a ride or a location
        ▼
Cosmos descriptions + YOLO11 detections   ← already computed per segment at ingest
        │  natural-language scene + object boxes
        ▼
W&B Inference (OpenAI-compatible)
        │  strict-JSON event extraction against the PLAN.md schema
        │  merge duplicates · classify to NYC 311 categories · drop plates
        ▼
   ┌────────────────────────┬─────────────────────────────┐
   │ RideReport             │ Block briefing              │
   │ summary + 311 drafts   │ cited 6–8-sentence montage  │
   └────────────────────────┴─────────────────────────────┘

Every LLM call is traced in Weave (project: vastdata/team-24).
```

The captions and detections are read, never regenerated — no re-ingest. The engine's job
is extraction, deduping, grounding, and framing.

**Merge rule:** two events are one when they share the same company + fleet number, or the
same description within a 60-second window.

**311 categories (exact):** `blocked bike lane`, `double parking`, `street obstruction`,
`sanitation`, `graffiti`, `construction`, `none`.

---

## Sponsor tools

| Tool | What it does here |
| --- | --- |
| **VAST** | Semantic search over indexed segments, VastDB as the index, and segment streaming for the replay and montage |
| **NVIDIA Cosmos Reason** | Per-segment natural-language scene descriptions the engine reads |
| **NVIDIA Cosmos Embed** | Embeddings behind the block-briefing searches |
| **YOLO11** | Per-segment object detections (vehicles, people) used alongside the descriptions |
| **W&B Inference** | OpenAI-compatible LLM endpoint for strict-JSON event extraction and briefing-script generation |
| **W&B Weave** | Traces every LLM call; hosts the claim-grounding Evaluation |
| **Cursor** | Built the service and app on the workshop VMs using the challenge skills |

---

## Inputs and outputs

**`GET /rides`** → the `nyc_bike_gopro-1` parent videos.

**`POST /ride {video}`** →
```json
{ "segments": [...], "events": [...], "summary": {...}, "complaints": [...] }
```
`summary` carries counts by category and `time_lost` (seconds of segments where the rider
stopped or slowed, split by cause). Each complaint is a drafted 311 report.

**`POST /brief {destination, time_of_day, mode}`** →
```json
{ "script": [{"text": "...", "clip": "...", "start": 0, "end": 5}], "clips": [...], "stats": {...} }
```
`mode` is `walking` / `cycling` / `driving`; the six queries for that mode
(`tools/streetcast/queries.json`) run with the time-of-day word appended. Any sentence
not tied to a clip is dropped before the script is returned.

**Real run (GOPR0130):** 44 events, 315 s of time lost, 46 % of it to a single USPS truck
(fleet 6531286) — identified by fleet number, never a plate.

---

## How to run

The service and app run on the team-24 workshop VM, where the W&B and VSS credentials and
the `/config/<team>.config` file live.

**API** (`tools/streetcast/`):
```bash
pip install -r tools/streetcast/requirements.txt
# env: WANDB_API_KEY, WANDB_PROJECT=team-24, WANDB_TEAM=vastdata
#      WANDB_INFERENCE_BASE=https://api.inference.wandb.ai/v1
uvicorn tools.streetcast.api:app --host 0.0.0.0 --port 8000
```
The service lists the available W&B models at startup and picks a strong instruct model;
`weave.init("vastdata/team-24")` turns on tracing.

**App + deploy** (serves the two-tab UI and proxies the API):
```bash
bash tools/streetcast/deploy.sh          # run on the workshop VM
# → app code into a ConfigMap, VSS creds into a Secret, Ingress at /app on the team host
```
Open `https://workshop.thecosmoslabs.com` and click **App**.

---

## Evaluation

Claim grounding is measured directly: 10 briefings are regenerated, every cited sentence
is checked against the clip it points to, and the **claim-grounding rate** (cited sentences
whose clip actually shows the claim) is logged as a Weave Evaluation.

`tools/streetcast/eval.json` holds the hand-labeled set; `tools/streetcast/eval.py`
regenerates each briefing and logs the rate.

| Briefings | Claims | Grounded | Rate |
| --- | --- | --- | --- |
| _TBD_ | _TBD_ | _TBD_ | _TBD_ |

_(Filled from the Weave Evaluation once eval.json is labeled.)_

---

## Repo layout

```
tools/streetcast/
  api.py          FastAPI engine — /rides, /ride, /brief; Weave-traced LLM calls
  queries.json    6 block-briefing search queries per travel mode
  PLAN.md         the engine spec (schema, merge rules, both modes)
  eval.py         regenerates briefings, logs claim-grounding to Weave
  eval.json       hand-labeled evaluation set
  requirements.txt
app/              the two-tab app (Ride / Block briefing) + saved demo JSON
deploy.sh         deploy the app to the team cluster at /app
```

## Team

Team Aekovera — Kritika Jha, Abhishek Pandey, Muskan, and Engineer A.
