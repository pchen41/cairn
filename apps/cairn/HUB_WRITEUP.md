# Arduino Project Hub write-up (paste-ready)

**Title:** Cairn: a trail guide that works with no signal

**Short description:** An AI hiking guide that runs entirely on the device. Ask it the way, show it what you see, and it warns you before you get lost, no cell service needed.

## The problem

Hikers tend to get lost in exactly the places where their phones stop working: a junction with no sign, a canyon with no reception, the last hour of daylight. Most "smart" apps go quiet there because the smart part lives in the cloud. A trail guide has to work with no connection at all.

## What Cairn does

Cairn is a trail guide you can talk to. Everything runs on the device, so it keeps working when you have zero bars. Our demo covers Muir Woods.

- **Ask it the way.** Hold the mic button and say "Which way to Cathedral Grove?" Cairn tells you which trail to take, which direction to head, and how far you have left. Its answers come back in a few plain sentences, and it can read them out loud.
- **Show it what you see.** Point the camera at a plant, a trail sign or an animal and ask "What is this?" In our test it looked at a bright yellow blob on the forest floor and told us it was a banana slug.
- **It speaks up before you get lost.** Cairn keeps an eye on where you are. If you wander off the trail, if you're moving too slowly to get back before sunset, or if you haven't moved in a while, it warns you and points you to the nearest way out.
- **It's like hiking with a friend who knows the area.** As you get close to a landmark like Cathedral Grove, it tells you what's ahead. It knows a bit of local history and nature too: the redwoods, John Muir, Mount Tamalpais.
- **It remembers.** Tell it "Camp Alice Eastwood first, then Pantoll" and it will guide you to each stop in turn. It also knows where you came from, so it won't send you back the way you just walked.

## Why not just use a cloud AI?

Because there's no signal on the trail, and that's when you need help most. A guide that's supposed to keep you safe can't depend on a connection. With Cairn, the AI model, the maps and the local knowledge all live on the device.

## How it works

- The AI is **Liquid AI's LFM3-Omni**, a small model that can listen, see and speak. It runs on the **Qualcomm Dragonwing IQ-9075**'s built-in AI processor (the NPU), not in the cloud.
- The navigation is regular code, not AI. Distances, compass directions, routes and the safety checks are calculated precisely from the trail map. The AI's job is to understand your question or photo and explain the answer in plain language. That way it can't make up a wrong turn.
- It can only take a short list of approved actions, such as raising an alert or looking up the nearest exit, and everything it does is written to a log you can inspect.
- The demo shows Cairn as a phone-app prototype. A laptop acts as the screen, microphone and camera, connected to the board by a USB cable, while all the AI runs on the board. The goal is to bring the same thing to phones with the same kind of AI chip.

## Does it really work offline?

Yes. The model, maps and local knowledge are stored on the board, and the app never reaches out to the internet. The laptop talks to the board over a USB cable rather than Wi-Fi, so the board's Wi-Fi can be switched off during the demo.

## How fast is it?

Measured on our board (small samples, not a formal benchmark):

- An answer takes about 5 seconds. Most of that is loading the model for each request, which is the obvious thing to speed up next.
- Understanding a spoken question takes about 4 seconds.
- Reading an answer out loud takes about 11 seconds to prepare 25 seconds of speech.
- Every AI request in our test log completed successfully, and each one ran on the board's AI processor.

## Hardware

- Qualcomm Dragonwing IQ-9075 EVK
- A laptop over USB-C, used as the screen, microphone and camera
- Speakers on the board

## Software

- Liquid AI LFM3-Omni-2B, running through ZETIC's Ixian Hexagon SDK on the board's NPU
- Python (standard library only) for the maps, routing, safety checks and the app's web interface

## Code

GitHub: https://github.com/pchen41/cairn (the app is in `apps/cairn/`)
