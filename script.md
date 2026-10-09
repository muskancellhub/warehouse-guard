# StreetCast — Demo Script

Team 24 · VAST Builders Challenge NYC · 2-minute table demo (3-minute version for the 6 PM final)

**Setup before judges arrive:** app open on the projector, Ride tab selected, GOPR0130 loaded, the raw USPS clip queued full-screen. Muskan drives. Kritika narrates. Nobody opens slides.

---

## The story

StreetCast is one engine, two users, one endgame.

- **The rider (Maya)** records her ride. Afterwards she reviews it and files every street problem the agent found. Her review screen is the replay.
- **The newsroom (Dana, NY1)** picks a neighborhood and gets a narrated segment built from this week's filed events and the camera footage. The newscast is where the reports turn into pressure on companies and the city.

Rider files it, the city sees it, the newsroom airs it.

---

## Act 1 — The rider (0:00–1:20)

**0:00 — Cold open. Raw clip, no UI.**
Muskan plays the USPS clip full-screen.

> "Meet Maya. She delivers on a bike. This is five seconds of her ride in New York: a USPS truck blocking the street, a vandalised barrier, a trash pile. Four problems. Zero reports filed. Nobody files 311 from a bike."

> "Watch her evening instead."

**0:20 — Replay.**
Muskan switches to the app, Ride tab, presses **Replay**. The ride streams on the left. Say nothing. Let the cards land on the right as the rider reaches each problem:

- Blocked bike lane · USPS · fleet 6531286 · rider stopped
- Street obstruction · barricade, graffiti
- Sanitation · trash pile on sidewalk
- Double parking · UPS · doors open · rider slowed

The counters tick up: reports ready, time lost.

**0:50 — The number.**

> "Twelve-minute ride. Three blocked lanes, two trash piles, one barrier. Six reports ready. Maya lost three minutes, seventy percent of it to delivery trucks."

Muskan taps **File all**. The filed counter jumps.

> "Thirty seconds of her time. Every report has the clip and the timestamp."

**1:05 — The draft.**
Muskan clicks the USPS card. The 311 draft opens: category, street, what happened, company, fleet number, clip link.

> "Fleet numbers, never plates. Accountability for companies, privacy for people."

---

## Act 2 — The scale (1:20–1:25)

One line, while Muskan switches tabs:

> "Ten thousand Mayas later, every block in the city has a dated, cited record of what's blocking it. That isn't complaints anymore. That's data."

---

## Act 3 — The newsroom (1:25–1:45)

**1:25 — Your Block.**
Muskan is on the **Your Block** tab. Picks *Walker & Broadway*, *dusk*, *walking*. Presses **Brief me**. The six searches fire on screen, then the montage starts.

> "Meet Dana. She produces NY1's 'Your Block' segment. She picks a neighborhood."

Let two narrated sentences play. Then:

> "Every sentence cites a clip. No clip, no sentence. A producer can air this for any neighborhood, any night, from the same events Maya filed."

---

## Close (1:45–2:00)

Muskan shows the eval table.

> "Ten rides and briefings hand-checked. [N] claims, [N] grounded, logged in Weave."

> "VAST stores and searches it. Cosmos reads it. YOLO tracks it. W&B writes and traces it. Cursor built it. One loop: search, describe, extract, cite, act."

> "Rider files it. The city sees it. The newsroom airs it. That's StreetCast."

Stop. Take questions.

---

## 3-minute version (6 PM final, if top 5)

Add 60 seconds in two places:

- **After 0:50:** click a second card (the trash pile) and show that the agent put it in the *sanitation* 311 category, not *blocked lane*. "Six categories, merged duplicates: the same truck in three segments is one report."
- **After the montage:** click a source chip in the briefing. Pause on the clip. "This is the clip the sentence came from, at this second."

Everything else stays identical.

---

## Likely questions, short answers

- **How do you know the rider stopped?** Cosmos describes the rider's reaction per segment; we count seconds of stopped or slowed segments and attribute them to the event in frame.
- **What about private cars?** We never extract plates. Private vehicles become anonymous "double parking" events with no identifier.
- **Does it work live?** The replay is a stored ride today. The same pipeline runs on segments as they land in VastDB, so a live feed is a config change, not a rebuild.
- **Why these cameras?** The organizers' NYC footage, shot this week. Any camera network that lands in VAST works the same way.
- **Did you re-ingest?** Current captions already carried fleet numbers and reactions. We re-ran one ride with a street-issue prompt to make the extraction cleaner. [Say only if true at demo time.]

---

## Do not

- Open slides.
- Explain the architecture before the replay. The replay is the pitch.
- Talk during the first ten seconds of the replay.
- Demo a ride you have not rehearsed three times.
