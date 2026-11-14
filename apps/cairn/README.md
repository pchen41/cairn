# Cairn: an offline voice trail guide

Cairn is an always-on trail companion for Muir Woods that runs entirely on a Qualcomm Dragonwing IQ-9075. It watches your position against a local trail map and warns you when you go off-route, are too slow to get back before sunset, or have stopped moving for too long. At a junction you can hold a button and ask "which way to Cathedral Grove?". It tells you which trail to take, in two or three plain sentences, and can read the answer aloud on the board's speakers.

## Why not a cloud API call?

There is no signal in the backcountry, which is exactly where a hiker needs directions. An always-on watcher that is meant to keep you safe cannot depend on a connection, so the model, the map and every decision live on the board.

## How it works

```text
position (replayed track, or a click on the map)
   -> rules in plain Python: off_route, sunset pace, no_movement, junction arrival
   -> allowlisted actions: log_alert, nearest_bailout, route_at_junction, nearest_water, speak
   -> LFM3-Omni-2B on the Hexagon NPU (ZETIC Ixian SDK 0.2.1): explain only on alerts, junctions or questions
   -> one flushed JSONL line per iteration or request
```

- **The model does no maths.** Distances (haversine), compass bearings, Dijkstra routing over the trail graph, pace and time-to-sunset are all computed in code. Omni only does three things: transcribes the spoken question, turns the computed facts into short sentences, and optionally speaks them.
- **Memory.** Cairn keeps a breadcrumb trail of where you have been, so a route answer knows which junction you came from and won't send you back the way you came. It also keeps the last few questions and answers and an ordered itinerary ("Cathedral Grove first, then Pantoll").
- **Local knowledge.** `data/area_context.json` holds Wikipedia summaries (CC BY-SA 4.0) fetched once during setup. Code picks one or two relevant snippets per answer, so the guide talks like a friend who knows the area.
- **Guards.** Every model call is capped (ASR 96 tokens, answers 120). Each is checked for an empty answer, a natural stop and current-request Hexagon v73/two-NSP evidence, and retried once. Prompts carry a unique `[request:uuid]` tag. Errors are logged as data and the loop keeps running. Anything not on the allowlist is refused and logged.
- **Standard library only.** Nothing is fetched at runtime: no map tiles, CDN or fonts. The map is an inline SVG drawn from `data/trails.json`.

## Files

| File | What it is |
|---|---|
| `cairn.py` | The loop, rules, routing, allowlist, Omni calls and local web UI server |
| `ui.html` | The single-page UI: SVG map (click to spoof GPS), hold-to-talk, prompt/output/step-log panels |
| `run.py` | A copy of `examples/zetic-lfm/run.py` whose device check also accepts SDK 0.2.1's `nsp2=1` line |
| `data/trails.json` | Muir Woods trail graph: trailheads, junctions, water, bail-out points, destinations, sunset |
| `data/track.json` | Replayed hike (`make_track.py` regenerates it) that triggers every rule |
| `data/area_context.json` | Local background knowledge (Wikipedia summaries) |

## Run it

On the **board**, with the ZETIC SDK at `~/zetic-demo/sdk` and the LFM3-Omni-2B v73 package at `~/zetic-demo/models/lfm3-omni-2b`, from the repo root:

```bash
python3 apps/cairn/cairn.py replay --iterations 40          # headless always-on run over the track
python3 apps/cairn/cairn.py serve --port 8080               # web UI, bound to 127.0.0.1 only
```

From the **laptop**, open the UI through an SSH tunnel so the browser treats it as `localhost`, which the microphone needs:

```bash
ssh -o 'ProxyCommand nc -b en5 %h %p' -L 8080:127.0.0.1:8080 ubuntu@169.254.77.1
```

Then go to http://localhost:8080. The laptop is only the microphone and the screen; transcription, answers and speech run on the board. `--mock-llm` swaps in canned text for laptop-only UI tests.

### Offline access path

The demo runs with the board's Wi-Fi off, so the laptop reaches the board over a USB-C cable on the ADB port, configured as a USB network (CDC-NCM) gadget with link-local addresses and no internet sharing. Run `sudo bash apps/cairn/usb_link.sh` on the board from the UART console (it does not persist across a reboot). The board takes `169.254.77.1` and macOS self-assigns a `169.254.x` address on the new interface. Because macOS may route `169.254/16` over Wi-Fi, pin the SSH connection to the USB interface, for example `ssh -o 'ProxyCommand nc -b en5 %h %p' -L 8080:127.0.0.1:8080 ubuntu@169.254.77.1` (check the interface name with `ifconfig`).

## Measured on our board

IQ-9075 EVK, Ubuntu 24.04, ZETIC Ixian Hexagon SDK 0.2.1, LiquidAI LFM3-Omni-2B (ZETIC v73 two-NSP package, revision `01d43f16`). The CLI reloads the model for every call.

MEASUREMENTS_PLACEHOLDER

## Offline status

OFFLINE_PLACEHOLDER
