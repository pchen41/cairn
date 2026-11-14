# Arduino Project Hub write-up (paste-ready draft)

**Title:** Cairn: an offline voice trail guide on the Dragonwing IQ-9075

**One-line summary:** Ask "which way to the lake?" at a trail junction and get a spoken-style answer, computed and generated entirely on the board, with no signal.

## The problem

In the backcountry you lose cell signal exactly where you need help most: at an unmarked junction, when the light is going, or when you have wandered off the trail. Phone apps that rely on a cloud model go quiet there. A trail guide has to work with no connection at all.

## What Cairn does

Cairn is an always-on trail companion running on a Qualcomm Dragonwing IQ-9075 with Liquid AI's LFM3-Omni-2B on the Hexagon NPU.

- **Watches** your GPS track (replayed from a file, or set by clicking the map in the demo UI) against a local trail map of the Marin Headlands.
- **Decides** in plain Python: off-route beyond a distance threshold, too slow to get back to the trailhead before sunset, or no movement for too long. The model does no maths.
- **Acts** only through an allowlist: log an alert, compute the nearest bail-out point with distance and compass bearing, or work out which trail to take at a junction.
- **Listens** when you hold the talk button: LFM3-Omni-2B transcribes your question on the NPU. Code matches the intent (a destination, water, bail-out, sunset), computes the facts from the map, and remembers your itinerary ("Pirates Cove first, then Rodeo Lagoon") and where you came from.
- **Explains**: one capped Omni request turns the computed facts into two or three plain sentences.
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

At the judging table the board's Wi-Fi is switched off (`sudo nmcli radio wifi off`) and Cairn keeps working: alerts fire, voice questions are transcribed and answered, and the log shows each request's Hexagon device line.

## Measured on our board

(Fill in from apps/cairn/README.md: wall time per Omni request, model load time, decode tokens/s, sample count.)

## Code

GitHub: (link to the public repo)
