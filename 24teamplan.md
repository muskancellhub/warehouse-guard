# StreetCast Build Plan

Oct 9, 2026 · @Team Aekovera

Team 24 ships StreetCast today: one engine that turns street video into cited events, with two faces. RideReport: one ride in, every 311 report out, with fleet numbers (not plates) and time lost. Block briefing: pick a neighborhood, get a one-minute narrated montage a newsroom could air. Project due 4:30 PM; 2-minute demo at the table, top 5 re-demo at 6 PM.

## Who does what

| Person | Where | Owns | First action right now |
| --- | --- | --- | --- |
| Engineer A (backend) | VM | Brief API: searches, W&B script generation, Weave tracing | Stop uploading videos. Paste A1 into Cursor. |
| Muskan | VM | The app: form, montage player, narration, stats strip, deploy | Paste B1 into Cursor. Build with mock data until /brief is live. |
| Kritika | Laptop | The 18 queries, eval set, README, demo script, submission | Send Engineer A the query list (section below), then start labeling. |

Only Engineer A and Muskan launch VMs (two per team, cannot change later). Both select team-24. Sync code through one GitHub repo, folder tools/streetcast/.

## Checkpoints

| By | Must be working | Owner |
| --- | --- | --- |
| 12:45 | Both VMs up, team-24 selected, `check that everything is working` passes; PLAN.md saved | Engineer A, Muskan |
| 1:00 | Query list sent to Engineer A; app form + player running on mock JSON | Kritika, Muskan |
| 1:45 | POST /brief returns script + clips for new\_york / dusk / cycling | Engineer A |
| 2:30 | App calls real /brief; montage plays with narration and source chips | Muskan |
| 3:00 | Feature freeze. 10 briefings hand-checked, eval.json done, Weave eval logged | Kritika, Engineer A |
| 3:30 | App deployed via deploy-app-no-registry, opens from the App button | Muskan |
| 4:00 | Demo rehearsed 3 times; README pushed; `help me submit our project` run in Cursor | All |
| 4:30 | Submitted at the link on the slide, all names on it | Kritika |

If anything breaks: `/ask-cosmos` in Cursor, take the output to an organizer.

## Rules that disqualify

- No outside video, no YouTube, no phone footage. The provided archive is licensed; ours is not. Uploads also skip the Segmenter, so they never index properly.
- Only re-ingest, never redeploy the pipeline. We are not re-ingesting at all today; the captions already describe what we need.
- Stay inside team-24 credentials, buckets and UI.
- Submission needs a repo link. An incomplete submission may not be judged. Due 4:30 PM at the green link on the judging slide (reads like tokensand.com/vastnyc; confirm with a host).
- Two judging rounds: 2-minute demo at the table, then top 5 give a 3-minute demo to the room at 6 PM. Demos over slides.

## Engineer A (VM): brief API

Step 0, in the VM terminal:

```
cd ~/vast-builders-challenge
agent
```

Type `/model`, choose Auto. Then paste: `Run a git pull, then check that everything is working.`

**A1, paste once:**

```
We're building StreetCast, a street-video agent with two modes on one engine. Engine: take a set of indexed segments (a ride = all segments of one nyc_bike_gopro-1 video in time order; a block = top search hits for a location), read their Cosmos descriptions and YOLO detections, and ask W&B Inference for strict JSON events: {clip, start, end, category, company, fleet_number, rider_reaction, street, text}. Categories are real NYC 311 types: blocked bike lane, double parking, street obstruction, sanitation, graffiti, construction, or none. Never report license plates. Merge duplicates: same company and fleet number, or same description within 60 seconds, is one event. Mode 1 RideReport: one ride in; out comes a ride summary (counts by category; time lost = seconds of segments where the rider stopped or slowed, split by cause), plus one drafted 311 complaint per event with clip, timestamp and street. Mode 2 Block briefing: location, time of day and travel mode in; out comes a 60-second narrated script where every sentence cites a clip and time range. Trace every LLM call in Weave. Save as tools/streetcast/PLAN.md. Don't do anything else yet.
```

**A2, the API:**

```
Write tools/streetcast/api.py as a FastAPI service. GET /rides lists the nyc_bike_gopro-1 parent videos. POST /ride {video}: use the videos skill to list that video's segments in time order with descriptions, YOLO detections and stream URLs; send the descriptions in batches to W&B Inference (OpenAI-compatible, WANDB_API_KEY and base URL are in the environment; list models and pick a strong instruct model) with the JSON schema from PLAN.md; merge duplicates; compute time_lost; draft one 311 complaint per event (category, street from signs, what happened, company and fleet number, clip and timestamp). Return {segments, events, summary, complaints}. POST /brief {destination, time_of_day, mode}: run the 6 queries for that mode from tools/streetcast/queries.json with the time_of_day word appended, filtered by location, keep top 3 per query, dedupe, ask the LLM for 6 to 8 second-person sentences each citing a clip and start-end, drop uncited ones, return {script, clips, stats}. weave.init(WANDB_PROJECT) and @weave.op on every LLM call. Test POST /ride on the GOPR0130 video first and show me events and summary.
```

**A3, once Kritika's eval.json exists:**

```
Write tools/streetcast/eval.py: for each briefing in eval.json (destination, time_of_day, mode, and a list of claims each marked grounded true/false by a human), regenerate the briefing and compute claim grounding rate = cited sentences whose clip actually shows the claim. Log it as a Weave Evaluation. Print a table: briefing, sentences, grounded, rate.
```

## Muskan (VM): the app

Same Step 0 as Engineer A. Then paste:

**B1, the app:**

```
Build tools/streetcast/app, a single-page app that fits the deploy-app-no-registry skill, two tabs. Tab 1 "Ride": pick a ride from GET /rides and press Replay. Left: the ride's segments stream back-to-back from the VSS /videos/stream endpoint. Right: a live feed where each event from POST /ride pops in when the replay reaches its start time, with a category badge, company, fleet number and street. Counters on top: blocked lanes, sanitation, obstructions, reports ready, time lost in seconds with percent by cause. Clicking an event pauses and shows the drafted 311 complaint with a "Ready to file" button that marks it filed. Tab 2 "Block briefing": destination dropdown (new_york, san_francisco, toronto, nashville, neighborhood), time of day, travel mode, Brief me button; POST /brief; play the cited clips in order seeking to start and stopping at end, speak each sentence with window.speechSynthesis, large caption, a source chip (camera, clip, start-end) that pauses and shows the clip. Dark theme, big type for a projector. Build both tabs against tools/streetcast/mock_ride.json and mock_brief.json first; the API comes later.
```

**B2, after /brief is live:**

```
Point the app at Engineer A's POST /brief. Add a Rerun with another mode button that keeps the destination. Handle an empty result with a message, never a blank screen.
```

**B3, deploy:**

```
Deploy tools/streetcast/app with the deploy-app-no-registry skill at /app on my team host. Confirm it opens from the App button on the workshop page.
```

Mock file for B1 (save as tools/streetcast/mock\_brief.json, Muskan builds against this until 2:30):

```
mock_ride.json
{"video":"GOPR0130","summary":{"duration_s":180,"events":4,"reports_ready":4,"time_lost_s":42,"by_cause":{"delivery truck":0.7,"construction":0.3}},"events":[{"clip":"20261008_072353_GOPR0130_chunk_0000.mp4","start":20,"end":25,"category":"blocked bike lane","company":"USPS","fleet_number":"6531286","rider_reaction":"stopped","street":"not visible","text":"USPS truck 6531286 parked in the lane; rider stopped behind it."},{"clip":"20261008_072353_GOPR0130_chunk_0000.mp4","start":20,"end":25,"category":"street obstruction","company":null,"fleet_number":null,"rider_reaction":"stopped","street":"not visible","text":"Orange barricade with graffiti in front of the truck."},{"clip":"20261008_072353_GOPR0130_chunk_0000.mp4","start":20,"end":25,"category":"sanitation","company":null,"fleet_number":null,"rider_reaction":"riding","street":"not visible","text":"Pile of black trash bags on the sidewalk."},{"clip":"20261008_073853_GX010001_chunk_0005.mp4","start":15,"end":20,"category":"double parking","company":"UPS","fleet_number":null,"rider_reaction":"slowed","street":"not visible","text":"UPS truck parked with rear doors open; cyclists pass in the lane."}]}

mock_brief.json
{"script":[{"text":"You're heading to Walker Street and Broadway at dusk.","clip":"20261008_065905_VID_20261006_174811_003_chunk_0003.mp4","start":20,"end":25},{"text":"Expect a delivery truck on the corner and a worker with a cone directing traffic.","clip":"20261008_065905_VID_20261006_174811_003_chunk_0003.mp4","start":20,"end":25},{"text":"Cyclists share the lane here; a USPS truck boxed one in yesterday.","clip":"20261008_072419_GOPR0130_chunk_0001.mp4","start":0,"end":5}],"clips":[],"stats":{"person":14,"car":6,"truck":2,"bicycle":1}}
```

## Kritika (laptop)

**1. Queries.** Send this to Engineer A now; he saves it as tools/streetcast/queries.json. The time-of-day word gets appended automatically.

| Mode | Queries |
| --- | --- |
| walking | pedestrians crossing at a busy crosswalk · construction barriers or scaffolding on the sidewalk · police vehicle or officer on the street · people waiting at a bus stop · crowded sidewalk near storefronts · rain or wet street with umbrellas |
| cycling | truck or van stopped in the bike lane · cyclist riding in a green bike lane · delivery truck parked with rear doors open · orange cones narrowing the lane · cyclist stopped behind a parked truck · car turning across a crosswalk with a cyclist |
| driving | double-parked vehicle blocking a lane · yellow taxi stopped at the intersection · bus pulling into the intersection · vehicle entering on a red light · pedestrians stepping into the road between cars · road work with cones and a lane closure |

**2. Eval set.** In the VSS UI, run 10 briefings' worth of searches (new\_york dusk cycling, new\_york day walking, san\_francisco day driving, and so on). For each generated briefing, open each cited clip and mark every sentence grounded true or false. Save as tools/streetcast/eval.json: `[{destination, time_of_day, mode, claims:[{text, clip, grounded}]}]`. Target: 10 briefings, 60+ claims. This is the Technical Implementation line in the rubric.

**3. README** (repo root): problem in two sentences · how it works (one diagram or numbered list: search → Cosmos descriptions → W&B script → cited montage) · which sponsor tool does what (VAST search + VastDB, NVIDIA Cosmos Reason + Embed, YOLO11, W&B Inference + Weave, Cursor) · inputs and outputs · how to run · eval table.

**4. Submission at 4:00.** In Cursor on either VM: `help me submit our project`. It writes SUBMISSION.md. Then submit at the link on the slide with the repo URL and all four names.

**5. Demo.** Rehearse three times with Muskan driving and you narrating.

## Demo script (2 minutes)

1. (0:00) Play the USPS clip, full screen. "This is five seconds of a bike ride in New York: a USPS truck blocking the street, a vandalised barrier, a trash pile. Four problems, zero reports filed. Watch what our agent does with one ride."
2. (0:20) Ride tab, press Replay on GOPR0130. Events pop in as the ride plays: blocked lane, USPS 6531286, obstruction, sanitation. Say nothing for ten seconds.
3. (0:50) Point at the summary: "Twelve-minute ride: three blocked lanes, two trash piles, one barrier. Six reports ready. The rider lost three minutes, seventy percent of it to delivery trucks."
4. (1:05) Click the USPS event. The 311 draft opens with clip, timestamp, street and fleet number. "Fleet numbers, never plates. Accountability for companies, privacy for people."
5. (1:25) Block briefing tab, new\_york, dusk, walking, Brief me. Let two narrated sentences play. "Same events, other direction: pick a block, get the briefing. A newsroom can run this for any neighborhood."
6. (1:45) Eval table: "Ten rides and briefings hand-checked, N claims, N grounded, logged in Weave." Close: "VAST, Cosmos, YOLO, W&B, Cursor. One loop: search, describe, extract, cite, act."

Do not open slides. Have the app already loaded before the judges arrive.

## Judging criteria, mapped

| Criterion | What we show |
| --- | --- |
| Idea: compelling use of the video search and summary stack | The product is the search and summary stack, turned into something a New Yorker would use |
| Technical: reproducible, documented inputs, outputs, evaluation | README + eval.json + Weave Evaluation with claim-grounding rate |
| Design: thought-out, intuitive | Three fields, one button, a video that talks; source chips on every claim |
| Impact: at least 3 sponsor tools | VAST (search, VastDB, streaming), NVIDIA (Cosmos Reason, Cosmos Embed, YOLO11), W&B (Inference, Weave), Cursor (built with skills) |
| Presentation: 2-minute demo, demos over slides | Live app, no slides |
