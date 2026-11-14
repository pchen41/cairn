# Arduino Project Hub write-up (paste-ready draft)

**Title:** Cairn: an offline voice trail guide for Muir Woods on the Dragonwing IQ-9075

**One-line summary:** Ask "which way to Cathedral Grove?" at a trail junction and get a spoken-style answer, computed and generated entirely on the board, with no signal.

## The problem

In the backcountry you lose cell signal exactly where you need help most: at an unmarked junction, when the light is going, or when you have wandered off the trail. Phone apps that rely on a cloud model go quiet there. A trail guide has to work with no connection at all.

## What Cairn does

Cairn is an always-on trail companion running on a Qualcomm Dragonwing IQ-9075 with Liquid AI's LFM3-Omni-2B on the Hexagon NPU.

- **Watches** your GPS track (replayed from a file, or set by clicking the map in the demo UI) against a local trail map of Muir Woods and Mount Tamalpais.
- **Decides** in plain Python: off-route beyond a distance threshold, too slow to get back to the trailhead before sunset, or no movement for too long. The model does no maths.
- **Acts** only through an allowlist: log an alert, compute the nearest bail-out point with distance and compass bearing, or work out which trail to take at a junction.
- **Listens** when you hold the talk button: LFM3-Omni-2B transcribes your question on the NPU. Code matches the intent (a destination, water, bail-out, sunset), computes the facts from the map, and remembers your itinerary ("Camp Alice Eastwood first, then Pantoll") and where you came from.
- **Explains** like a friend who knows the area: one capped Omni request turns the computed facts, plus a snippet of local knowledge (redwoods, John Muir, the Dipsea, Mount Tamalpais) stored on the board, into two or three plain sentences. It can speak them on the board's speakers.
- **Is proactive**: when you approach a landmark such as Cathedral Grove, it tells you once what's ahead and how far. Safety alerts always come first.
- **Logs** every step as a flushed JSONL line, which the UI shows live next to the exact prompt sent to the model and its raw output.

## Why not a cloud API call?

There is no signal in the backcountry, and that is when a hiker most needs directions. An always-on watcher that is meant to keep you safe cannot depend on a connection, so the model, the map and every decision live on the device.

## Hardware

- Qualcomm Dragonwing IQ-9075 EVK (Ubuntu 24.04, Hexagon v73 NPU, two NSPs)
- A laptop connected over a USB-C link, used only as the microphone and the screen. Inference runs on the board.

## Software

- Liquid AI LFM3-Omni-2B (ZETIC Ixian Hexagon SDK 0.2.1 package, v73, two NSPs) for speech recognition and the answer text
- Python 3 standard library: trail graph, Dijkstra routing, haversine distance and bearing, rules, allowlist, JSONL log, and a local web UI (inline SVG map, no map tiles or CDN)

## Offline proof

Everything runs on the board: the model, the trail map, the area knowledge and the UI server, which is bound to localhost. The laptop connects over a USB-C cable, not Wi-Fi, so the board's Wi-Fi can be switched off (`sudo nmcli radio wifi off`). While Cairn answers, the board holds no outbound connections.

## Measured on our board

Medians from Cairn's own logs on one IQ-9075 (small samples, not a benchmark):

- Explain an alert, junction or landmark: 4.81 s wall, of which 3.76 s is model load, then 62 tokens/s decode (6 calls).
- Transcribe a spoken question: 4.23 s (1 call). Answer a question: 4.85 s (2 calls).
- Speech output: about 11 s to generate 25 s of audio.
- 9 of 9 model calls passed every check, each with current-request Hexagon v73 two-NSP evidence.

## Code

GitHub: https://github.com/pchen41/cairn (app in `apps/cairn/`)
