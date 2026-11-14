# Cairn: an offline voice trail guide (hackathon submission)

**Cairn** is our project for the On-Device AI Hackathon: Cleared for Takeoff. It's an always-on, voice-driven trail guide for Muir Woods that runs entirely on a Qualcomm Dragonwing IQ-9075, with Liquid AI's LFM3-Omni-2B on the Hexagon NPU. It warns you when you go off-route, are too slow to beat sunset, or stop moving. It answers "which way to Cathedral Grove?" at a junction, and tells you about landmarks as you approach them, with no signal needed.

**Start here: [apps/cairn/README.md](apps/cairn/README.md)**

The rest of this repository is the organizers' starter kit, which Cairn was built from.

---

# On-Device AI Hackathon: Cleared for Takeoff

**LiquidAI × Qualcomm Dragonwing**

Reference documentation for the hackathon: build on-device AI agents with **LiquidAI** models on the **Qualcomm Dragonwing IQ-9075 EVK**, then demonstrate them with the network removed.

**Event page:** [luma.com/0nsdnkoa](https://luma.com/0nsdnkoa) for the schedule, venue, and logistics. **Submitting:** see [how to submit](docs/judging.md#how-to-submit).

## The evening

November 13, 2026, San Francisco, 5:00–9:00 PM Pacific. Teams of up to four. The event page is the source of truth if anything here differs.

| Time | What happens |
| --- | --- |
| 5:00 PM | Doors open, food, networking |
| 6:00 PM | On-device AI workshop |
| 6:15 PM | Building starts |
| 8:00 PM | Submissions open |
| 8:15 PM | Building ends. The Wi-Fi goes off for the showcase, judging, and awards |

## Two hours, from a fresh board

Boards start with a fresh Ubuntu install and nothing else, and the build window is two hours. A plan that fits; the times are targets, not measurements:

| By | Goal | How |
| --- | --- | --- |
| 7:00 | First NPU response | One person starts setup at once: [board shell](docs/developer-guide.md#0-board-access-and-internet-for-setup) → [NPU packages and `fastrpc`](docs/developer-guide.md#32-driver-packages) → [GenieX](docs/developer-guide.md#33-geniex-cli) → [model from the USB drive](docs/developer-guide.md#4-stage-your-model) → [server and first request](docs/developer-guide.md#5-local-server-openai-compatible). Everyone else reads an example and sketches the app |
| 7:45 | Your app's loop works | Start from the [always-on agent](examples/always-on-agent/README.md) with `--gate rule`, or from [tool calling](examples/tool-calling/README.md). Change the input, the rule, the action, and the prompt |
| 8:00 | It works with Wi-Fi off | Judges ask you to turn the board's Wi-Fi off at your table, so rehearse it once. Open a session that does not depend on Wi-Fi: the UART console you used for setup, or a USB-C link to the laptop. Run `sudo nmcli radio wifi off` there, then run the app. `sudo nmcli radio wifi on` restores it; see [demo format](docs/judging.md#demo-format) |
| 8:15 | Submitted | A GitHub repository link and an Arduino Project Hub post; see [how to submit](docs/judging.md#how-to-submit) |

**Minimum for a demo:** the core loop runs on the board with Wi-Fi off, its JSONL log shows what it decided and did, and you can answer "why not a cloud API call?".

**Only if you have time:** the d1 gate and its calibration, the metrics collector, a long network-off rehearsal, and the ZETIC route.

> **Start here:** [`docs/developer-guide.md`](docs/developer-guide.md). Using a coding agent? Open this repo in your agent and use the prompt below. [`AGENTS.md`](AGENTS.md) and the [getting-started skill](skills/iq9-getting-started/SKILL.md) guide it through access, setup, and verification.

## Get started with your agent

> Let's get started with this repo on my IQ9. Read `AGENTS.md` and `docs/developer-guide.md`. We have about two hours in total, so take the shortest path and skip optional extras.
>
> First, help me access the board and get it connected to the internet for setup. Ask what connections I have available and guide me through any manual steps.
>
> Then check what's already installed and propose a setup plan. Reuse existing software and models, and ask before installations, downloads, or system and network changes.
>
> Start with the reference LFM generator; leave d1 optional. Verify a useful local response and confirm NPU execution, then summarize what's working, what remains unverified, and which example to try next.

No SSH connection yet? Start with the board's **micro-USB debug UART**, then configure Wi-Fi with `nmcli` or use Ethernet. Event boards use username **`ubuntu`**, password **`liquidxqualcomm`** for UART and SSH. See [board access and internet for setup](docs/developer-guide.md#0-board-access-and-internet-for-setup) for host-specific steps, password handling, and network checks. Internet is for approved setup/staging; the final demo still runs without external network access.

## Platform and models

* **Hardware:** Dragonwing IQ-9075 EVK only (QCS9075, Hexagon v73 NPU, 36 GB LPDDR5). External sensors are optional and will not be provided; participants are welcome to bring their own.
* **Generator and tools:** Choose **any model in LiquidAI's LFM3 family** that fits your project.
* **Reference generator:** This repo's examples use `LiquidAI/LFM3-1.7B-GGUF` (Q4_0) via GenieX v0.10.0—a starting point, not a required model.
* **Optional ZETIC route:** [Ixian Hexagon SDK 0.2.1 and the IQ9 collection](examples/zetic-lfm/README.md), with a capped CLI wrapper for LFM3 text/image/WAV → text. SDK 0.2.1 and the current packages have been run on the IQ-9075; see the example for its verified scope.
* **Optional decision layer:** `LiquidAI/d1-2B`, a decision model that answers typed questions in one pass with no output tokens. A rule in code works too.

Start with [LiquidAI's LFM3 family on Hugging Face](https://huggingface.co/collections/LiquidAI/lfm3), then review each model's card and file list. Other LFM models are also options. The example READMEs explain their reference choices.

For ZETIC's six public Dragonwing packages, see the [LFM3 and d1 on Dragonwing IQ9 collection](https://huggingface.co/collections/ZETIC-ai/lfm3-and-d1-on-dragonwing-iq9). The [Ixian Hexagon SDK releases](https://github.com/zetic-ai/IxianHexagon/releases) supply the runtime and prebuilt CLIs; [ZETIC sample apps](https://github.com/zetic-ai/LFM3-zetic-apps) supply the fixtures. Follow our [pinned staging recipe](examples/zetic-lfm/README.md#stage-once-with-approval) rather than the older apps guide's gated model/SDK paths.

For any model you choose, verify [format, precision, runtime, and NPU compatibility](docs/runtimes.md#model-format-and-precision), [stage its required assets before the network cut](docs/offline-readiness.md#disk-first), and test it on the board offline before a demo. Reference-stack results do not verify another model or precision.

## Goals

Build intelligence that does not need the internet. The event isolates the moments where local inference creates real value:

* **Privacy:** data never leaves the board
* **Resilience:** the system keeps working when the network is down
* **Latency:** local responses without a network round trip
* **Autonomy:** the agent acts on its own between human prompts
* **Predictable marginal cost:** no per-token API bill

The proof: a competing system is still functional when the network is removed.

## Objectives

The event is organized around three pillars:

| Pillar                           | Focus             | What a winning project does                                                                          |
| -------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------- |
| **01 — The Network Has Crashed** | Main constraint   | Delivers its core value offline and explains why edge-native architecture matters                    |
| **02 — Always-On Local Agent**   | Flagship track    | Monitors local signals persistently and acts when something matters, beyond prompt-and-response chat |
| **03 — Tiny Agent Olympics**     | Performance layer | Shows speed, footprint, endurance, tool use, or multimodal execution on a common platform            |

Judging weights and the "why not just a cloud API call?" question are in [`docs/judging.md`](docs/judging.md).

## Repository outline

| Path                            | Purpose                                                                                                                                                                        |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `README.md`                     | This file: goals, objectives, repo map                                                                                                                                         |
| `docs/developer-guide.md`       | Main guide: setup → first model → server → decision and generation loop → offline                                                                                              |
| `docs/decision-models.md`       | d1-2B: question types and Transformers on the IQ-9075 CPU                                                                                                                                |
| `docs/runtimes.md`              | GenieX versions, CLI, server endpoints and request fields, benchmarking, pitfalls                                                                                              |
| `docs/hardware.md`             | IQ-9075 facts, expected software, quick checks                                                                                                                                 |
| `docs/offline-readiness.md`     | Pre-cut checklist and network-off rehearsal                                                                                                                                    |
| `docs/judging.md`               | Scoring rubric, demo format, metrics to show                                                                                                                                   |
| `examples/system-one-decision/` | `system_one()` with Transformers on the CPU. Installation, pinned fetch, environment check, example.                                                                              |
| `examples/lfm-server-npu/`      | LFM3-1.7B: send a capped, validated request to an existing NPU server; lifecycle stays in a separate terminal                                                                   |
| `examples/zetic-lfm/`          | Optional ZETIC Ixian CLI: capped LFM3 text/image/audio → text, collection coverage, separate d1 guidance, staging, and verification limits                                         |
| `examples/always-on-agent/`     | The always-on loop, runnable: observe, decide (a rule, or d1), act through an allowlist, explain alerts (LFM), log to JSONL. Runs without d1                                    |
| `examples/tool-calling/`        | LFM3-1.7B: native `tools` calls, schema validation, allowlist, and a policy check before any write action                                                                      |
| `starter/`                      | `run.sh`: checks the board, runs one decision pass and one generation, and points to the next example                                                                          |
| `skills/`                       | Agent skills with tested scripts: preflight, GenieX/LFM client, d1 calibration, agent scaffold, tool calling, offline audit, demo metrics. Start with [`AGENTS.md`](AGENTS.md) |
| `AGENTS.md`                     | Instructions for coding agents: rules, skills, where to run, what to ask before doing                                                                                          |
| `apps/`                         | Where hackathon projects go (`skills/iq9-always-on-agent/scripts/scaffold.sh apps/<name>`); their logs and metrics are not git-ignored                                         |

**Start from working examples:** First complete [generator setup and verification](skills/iq9-getting-started/SKILL.md#5-verify-the-first-generator-milestone), without requiring d1. Then explore model requests, the [always-on agent](examples/always-on-agent/README.md) (`--gate rule` needs no d1), or guarded tool calling. `./starter/run.sh` is an optional **full two-model reference check** after setting up both LFM and d1, not the first onboarding step.

## Developer Notes

### External monitors and headless setups

**Organizer-reported setup issue:** the current Adreno APT update causes a GPU-driver issue when using an external monitor on the IQ-9075. Teams using a monitor must **remove/roll back the Adreno APT update** before using that setup. This repo's setup recipes no longer recommend installing/updating `qcom-adreno1`. Check installed versions and confirm the removal/rollback procedure with the organizers before changing drivers; the affected/fixed package versions are not recorded here.

**SSH is not affected by this monitor issue.** A headless setup using SSH and an app web server over a configured **USB-C network link** is allowed during the network-off demo. Keep the link local-only: disable internet sharing/forwarding on the laptop and remove all external uplinks. Serve UI assets locally and keep inference on the board. See [display and USB-C access](docs/offline-readiness.md#display-and-usb-c-access) for the checklist and SSH port forwarding.

### Rules for the reference stack

These apply to GenieX v0.10.0 with LFM3-1.7B Q4_0 on the NPU and, if used, d1-2B on the CPU. Re-check them for any other model or runtime.

* **Run d1-2B through Transformers on the CPU**, not GenieX's `/v1/decisions` or `/v1/systemone` endpoints.
* **Cap every request and validate the answer.** Use `max_completion_tokens` on chat and `max_tokens` on raw completions. A reply can report tokens and still have empty `content`: treat that as a failure for text requests, and validate `tool_calls` for tool requests. Keep one retry as a fallback.
* **LFM3-1.7B reasons by default.** For direct answers, use `enable_think: false` on chat or a raw completion with the reasoning block closed. Neither guarantees a usable answer, so check `content`.
* **Make raw-completion prompts unique.** Identical `/v1/completions` prompts can return continuing commentary instead of a fresh answer. Add an event timestamp or tag.
* **Plan for idle unloading.** The default keepalive is 300 s. Set `--keepalive` or `GENIEX_KEEPALIVE` longer than the demo's quiet gaps, or send a warm-up request. See [`docs/runtimes.md`](docs/runtimes.md#server-lifecycle-model-loading-and-idle-unloading).
* **Run the decision gate and the generator one at a time.** d1 slows down substantially while the LFM is generating.
* **Calibrate the decision gate on your own task.** Do not use 0.5 or another app's threshold. Label your app's states and evaluate on held-out data with [`calibrate_threshold.py`](skills/iq9-d1-decision-gate/scripts/calibrate_threshold.py).

## Quick start

On the IQ-9075, after the [setup steps](docs/developer-guide.md#3-setup), from the repo root:

```bash
bash skills/iq9-preflight/scripts/preflight.sh --profile generator --json
```

Reuse an appropriate existing server, or run this in a separate board terminal:

```bash
skills/iq9-geniex-lfm/scripts/serve.sh run
```

Then send a capped, validated request and inspect the current server's NPU evidence:

```bash
python3 skills/iq9-geniex-lfm/scripts/lfm_client.py --sentence 'Explain why local inference is useful when the internet is unavailable.'
skills/iq9-geniex-lfm/scripts/serve.sh status
```

Check the answer and the `HTP0` device line that `serve.sh status` prints from `~/geniex-serve.log`; a preflight pass alone is not verification. See the [generator skill](skills/iq9-geniex-lfm/SKILL.md) for server reuse and output guards. If your project needs d1, set it up separately with [`examples/system-one-decision`](examples/system-one-decision/README.md); it is optional.

For local image/audio projects or an alternative text route, see the [ZETIC example](examples/zetic-lfm/README.md) and [why it is included](docs/developer-guide.md#25-optional-zetic-route). It uses separate SDK/model assets, not the GenieX cache or OpenAI-compatible endpoints.

## Licensing

This repository's code and docs are under the [MIT License](LICENSE). They do not include LiquidAI model weights/model code or ZETIC SDK binaries. Models and third-party runtimes are staged separately, and each keeps its own licence:

* `LiquidAI/d1-2B` and `LiquidAI/LFM3-1.7B-GGUF` are under the **LFM Open License v1.0** (`lfm1.0`). It includes a commercial-use limit for organisations above US$10M annual revenue. Read each model card's LICENSE before you publish or commercialise a demo.
* Other checkpoints may have different terms. Read the LICENSE for each selected model's weights and code; the Hub's license tag alone is not a substitute for those terms.
* GenieX is distributed by Qualcomm under its own terms.
* ZETIC's LFM3 and d1 packages retain the LFM Open License v1.0. SDK dependencies and Hexagon-built libraries have separate terms; inspect the vendor's `LICENSES` and `PROVENANCE.json`, and confirm redistribution permission before supplying event media. Public model access is not blanket redistribution permission.
