#!/usr/bin/env python3
"""Cairn: an offline backcountry trail guide on the Dragonwing IQ-9075.

Loop: observe (GPS position, replayed or spoofed) -> decide (rules in plain Python) -> act (allowlist)
-> explain (LFM3-Omni-2B on the NPU through the ZETIC CLI, only on alerts, junctions, or questions) -> log.

All maths (distance, bearing, routing, pace, sunset) is done in code. The model only transcribes the
hiker's spoken question and turns facts computed here into two or three plain sentences.

Subcommands:
  replay   headless always-on run over data/track.json
  serve    local web UI on 127.0.0.1 (map, spoofed GPS, push-to-talk, prompts and step log)

Standard library only. Nothing is fetched at runtime.
"""

import argparse
import collections
import heapq
import json
import math
import os
import sys
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
for _p in (HERE, os.path.join(HERE, "..", "..", "examples", "zetic-lfm")):
    if os.path.isfile(os.path.join(_p, "run.py")):
        sys.path.insert(0, _p)
        break
try:
    import run as zetic_run  # examples/zetic-lfm/run.py: capped CLI request, output and Hexagon checks
except ImportError:  # only --mock-llm works without it
    zetic_run = None

ALLOWED_ACTIONS = ("log_alert", "nearest_bailout", "route_at_junction", "nearest_water", "speak", "landmark_tip",
                   "describe_photo")
VISION_PROMPT = ("This photo is from a hiker's camera on a forest trail. In one or two short sentences, say what "
                 "is in it: a plant, tree, animal, sign or trail feature. If you are not sure, say so.")
VISION_MAX_TOKENS = 80
VOICE_STYLE = ("Perform TTS in American English using a calm, warm, deep male voice, "
               "speaking slowly and gently.")
PPM_HEADER = b"P6\n512 512\n255\n"  # the SDK takes exactly 512x512 RGB; the browser sends binary PPM

VOLUME = {"percent": 50}  # software gain applied to reply audio before playback (the board's stream mixer
                          # control accepted values but did not change loudness on our EVK)


def set_volume(percent, mock=False):
    """Set the reply volume 0..100 %. Applied in software to the WAV samples before aplay. Returns the percent."""
    VOLUME["percent"] = max(0, min(100, int(percent)))
    return VOLUME["percent"]


def scaled_copy(path):
    """Write a copy of a 16-bit PCM WAV with the current volume applied; returns its path (or the input on error)."""
    import array
    import wave
    gain = VOLUME["percent"] / 100.0
    if gain >= 0.999:
        return path
    try:
        with wave.open(path, "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        if params.sampwidth != 2:
            return path
        a = array.array("h", frames)
        for k in range(len(a)):
            a[k] = int(a[k] * gain)
        out = path[:-4] + "-vol.wav"
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(a.tobytes())
        return out
    except (OSError, EOFError, wave.Error):
        return path

EARTH_R = 6371000.0
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
COMPASS_WORDS = {"N": "north", "NNE": "north-northeast", "NE": "northeast", "ENE": "east-northeast",
                 "E": "east", "ESE": "east-southeast", "SE": "southeast", "SSE": "south-southeast",
                 "S": "south", "SSW": "south-southwest", "SW": "southwest", "WSW": "west-southwest",
                 "W": "west", "WNW": "west-northwest", "NW": "northwest", "NNW": "north-northwest"}
ASR_MAX_TOKENS = 96
ASR_PROMPT = "Transcribe the hiker question."


def strip_echo(text, prompt=ASR_PROMPT):
    """Omni ASR echoes the prompt at the end of the transcript; remove it (case-insensitive, trailing)."""
    t = (text or "").strip()
    for p in (prompt, prompt.rstrip(".")):
        if t.lower().endswith(p.lower()):
            t = t[:-len(p)].rstrip(" ,").strip()
            if t and t[-1] not in ".?!":
                t += "."
            break
    return t
EXPLAIN_MAX_TOKENS = 120


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- geometry

def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def bearing(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(deg):
    return COMPASS[int((deg + 11.25) // 22.5) % 16]


def nearest_on_polyline(p, line):
    """(distance_m, nearest_point) from p to a polyline, using a local equirectangular projection."""
    k = math.cos(math.radians(p[0]))
    best = (float("inf"), line[0])
    for a, b in zip(line, line[1:]):
        ax, ay = (a[1] - p[1]) * k, a[0] - p[0]
        bx, by = (b[1] - p[1]) * k, b[0] - p[0]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
        q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        d = haversine(p, q)
        if d < best[0]:
            best = (d, q)
    return best


def polyline_length(line):
    return sum(haversine(a, b) for a, b in zip(line, line[1:]))


def round_m(m):
    return int(round(m / 10.0) * 10)


# ---------------------------------------------------------------- trail graph

class Trails:
    def __init__(self, path):
        with open(path) as f:
            d = json.load(f)
        self.config = d["config"]
        self.nodes = {n["id"]: n for n in d["nodes"]}
        self.edges = []
        self.adj = collections.defaultdict(list)
        for e in d["edges"]:
            a, b = self.nodes[e["from"]], self.nodes[e["to"]]
            line = [(a["lat"], a["lon"])] + [tuple(p) for p in e["points"]] + [(b["lat"], b["lon"])]
            edge = {"from": e["from"], "to": e["to"], "trail": e["trail"], "line": line,
                    "length": polyline_length(line)}
            self.edges.append(edge)
            self.adj[e["from"]].append((e["to"], edge, line))
            self.adj[e["to"]].append((e["from"], edge, list(reversed(line))))

    def pos(self, nid):
        n = self.nodes[nid]
        return (n["lat"], n["lon"])

    def name(self, nid):
        return self.nodes[nid]["name"]

    def with_kind(self, kind):
        return [n for n in self.nodes if kind in self.nodes[n]["kinds"]]

    def dijkstra(self, src):
        dist, prev = {src: 0.0}, {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            for v, edge, line in self.adj[u]:
                nd = d + edge["length"]
                if nd < dist.get(v, float("inf")):
                    dist[v], prev[v] = nd, (u, edge, line)
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    def nearest_trail(self, p):
        best = None
        for e in self.edges:
            d, q = nearest_on_polyline(p, e["line"])
            if best is None or d < best[0]:
                best = (d, q, e)
        return best

    def nearest_node(self, p):
        return min(self.nodes, key=lambda n: haversine(p, self.pos(n)))

    def route(self, p, target, junction_m=60.0):
        """Shortest route from position p to node target. Starts at a junction the hiker is standing at,
        otherwise from either end of the nearest trail. Returns facts computed in code."""
        nn = self.nearest_node(p)
        if haversine(p, self.pos(nn)) <= junction_m:
            starts = [(nn, haversine(p, self.pos(nn)))]
        else:
            _, _, e = self.nearest_trail(p)
            starts = [(e["from"], haversine(p, self.pos(e["from"]))), (e["to"], haversine(p, self.pos(e["to"])))]
        best = None
        for s, lead in starts:
            dist, prev = self.dijkstra(s)
            if target in dist and (best is None or lead + dist[target] < best[0]):
                best = (lead + dist[target], s, prev, lead)
        if best is None:
            return None
        total, start, prev, lead = best
        path, legs, cur = [target], [], target
        while cur != start:
            u, edge, line = prev[cur]
            legs.append((u, cur, edge, line))
            path.append(u)
            cur = u
        path.reverse()
        legs.reverse()
        facts = {"destination": self.name(target), "remaining_m": round_m(total),
                 "remaining_km": round(total / 1000.0, 1),
                 "via": [self.name(n) for n in path]}
        at_junction = lead <= junction_m
        if legs:
            u, v, edge, line = legs[0]
            # Head along the first leg: aim at a point about 60 m along the trail, not the far node.
            aim = line[-1]
            acc = 0.0
            for a, b in zip(line, line[1:]):
                acc += haversine(a, b)
                if acc >= 60:
                    aim = b
                    break
            origin = self.pos(u) if at_junction else p
            deg = bearing(origin, aim)
            facts.update(take_trail=edge["trail"], toward=self.name(v), heading=COMPASS_WORDS[compass(deg)],
                         bearing_deg=int(round(deg)))
            if not at_junction:
                facts["first_walk_to"] = self.name(u)
                facts["first_walk_m"] = round_m(lead)
        else:
            deg = bearing(p, self.pos(target))
            facts.update(heading=COMPASS_WORDS[compass(deg)], bearing_deg=int(round(deg)), arrived=lead <= junction_m)
        return facts

    def nearest_of_kind(self, p, kind):
        best = None
        for nid in self.with_kind(kind):
            r = self.route(p, nid)
            if r and (best is None or r["remaining_m"] < best[1]["remaining_m"]):
                best = (nid, r)
        if best is None:
            return None
        nid, r = best
        straight = haversine(p, self.pos(nid))
        deg = bearing(p, self.pos(nid))
        r.update(name=self.name(nid), straight_line_m=round_m(straight),
                 straight_line_heading=COMPASS_WORDS[compass(deg)], straight_line_bearing_deg=int(round(deg)))
        return r


# ---------------------------------------------------------------- model calls (ZETIC Omni, or mock)

class Resident:
    """Keeps one `zetic_lfm3_cli chat -i` process loaded for text-only calls, so each answer skips the ~3.8 s model
    reload. Sends /reset before every request (no history carries over), caps tokens at process start, checks
    usage, natural stop and non-empty text, and keeps the process's Hexagon load line as device evidence.
    Any failure kills the process; the caller's retry starts a fresh one. Errors are returned as data."""
    STATS = re.compile(r"\[(\w+)\] finish (\w+) \| prompt (\d+) rows \(cached (\d+)\) -> (\d+) tokens")
    ANSI = re.compile(r"\x1b\[[0-9;]*m")

    def __init__(self, sdk, model, max_tokens):
        self.sdk, self.model, self.max_tokens = sdk, model, max_tokens
        self.p, self.q, self.evidence, self.load_ms = None, None, None, None

    def _reader(self, p, q):
        for line in p.stdout:
            q.put(line)
        q.put(None)

    def _get(self, deadline):
        import queue
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("resident model timed out")
        try:
            line = self.q.get(timeout=left)
        except queue.Empty:
            raise TimeoutError("resident model timed out")
        if line is None:
            raise RuntimeError("resident model process exited")
        return self.ANSI.sub("", line.rstrip("\n"))

    def start(self, timeout=90):
        import queue
        self.stop()
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = os.path.join(self.sdk, "lib") + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        cmd = [os.path.join(self.sdk, "bin", "zetic_lfm3_cli"), "chat", "-i", "--model", self.model, "--target", "v73",
               "--lib-dir", os.path.join(self.sdk, "lib"), "--ctx", "2048", "--temp", "0",
               "--max-tokens", str(self.max_tokens), "--verbose"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1, cwd=self.sdk, env=env)
        self.q = queue.Queue()
        threading.Thread(target=self._reader, args=(self.p, self.q), daemon=True).start()
        deadline, dev, loaded = time.monotonic() + timeout, None, False
        while True:
            line = self._get(deadline)
            if "hexrt open" in line and "v73" in line and re.search(r"\bnsp2=1\b", line):
                dev = line
            if "loaded lfm3-omni-2b v73" in line:
                loaded = True
            m = re.search(r"\| load ([\d.]+) ms", line)
            if m:
                self.load_ms = float(m.group(1))
                break
        if not (dev and loaded):
            self.stop()
            raise RuntimeError("resident model started without Hexagon v73/two-NSP evidence")
        self.evidence = dev

    def stop(self):
        if self.p is not None:
            try:
                self.p.kill()
            except OSError:
                pass
        self.p = None

    def generate(self, prompt, max_tokens, timeout=60):
        t0 = time.perf_counter()
        r = {"ok": False, "text": "", "completion_tokens": None, "finish_reason": None, "errors": [],
             "timings": {"model_load_ms": 0.0}, "device_evidence": None}
        try:
            if max_tokens > self.max_tokens:
                raise ValueError("max_tokens above the resident process cap")
            if self.p is None or self.p.poll() is not None:
                self.start()
                r["timings"]["model_load_ms"] = self.load_ms
            clean = " ".join(prompt.split()).lstrip("/-")
            self.p.stdin.write("/reset\n[request:%s] %s\n" % (uuid.uuid4().hex, clean))
            self.p.stdin.flush()
            deadline, lines = time.monotonic() + timeout, []
            while True:
                line = self._get(deadline)
                m = self.STATS.search(line)
                if m:
                    break
                lines.append(line)
            status, finish, rows, cached, tokens = m.groups()
            text = "\n".join(l[2:] if l.startswith("> ") else ("" if l.strip() == ">" else l) for l in lines)
            text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S | re.I).strip()
            r.update(text=text, completion_tokens=int(tokens), finish_reason=finish.lower())
            t = re.search(r"ttft ([\d.]+) ms", line)
            d = re.search(r"decode \d+ steps ([\d.]+) t/s", line)
            r["timings"]["runtime_ttft_ms"] = float(t.group(1)) if t else None
            r["timings"]["runtime_decode_tok_s"] = float(d.group(1)) if d else None
            if status.lower() != "ok":
                r["errors"].append("generation status %s" % status)
            if not 0 < int(tokens) <= max_tokens:
                r["errors"].append("zero completion tokens or token cap exceeded")
            if finish.lower() != "stop":
                r["errors"].append("generation did not finish naturally: %s" % finish)
            if not text:
                r["errors"].append("empty or reasoning-only answer")
            r["device_evidence"] = "%s [resident process, loaded once in %.0f ms]" % (self.evidence, self.load_ms or 0)
        except (OSError, ValueError, RuntimeError, TimeoutError) as e:
            r["errors"].append("%s: %s" % (type(e).__name__, e))
            self.stop()
        r["timings"]["wall_s"] = time.perf_counter() - t0
        r["ok"] = not r["errors"]
        return r


class Omni:
    """One capped ZETIC CLI request per call through examples/zetic-lfm/run.py, retried once.
    run.py tags each prompt with a unique request id, enforces the token cap, rejects empty or
    reasoning-only answers, and requires current-request Hexagon v73 evidence. Errors are data."""

    def __init__(self, sdk, model, mock=False, timeout=180):
        self.sdk, self.model, self.mock, self.timeout = sdk, model, mock, timeout
        self.resident = None if mock else Resident(sdk, model, EXPLAIN_MAX_TOKENS)
        self.lock = threading.Lock()
        self.busy = None

    def _once(self, prompt, audio, max_tokens, image=None):
        if self.mock:
            time.sleep(0.05)
            if image:
                text = "(mock vision) A tall coast redwood trunk with ferns at its base."
            elif audio:
                text = "(mock transcript) Which way to Cathedral Grove?"
            else:
                text = "(mock answer, no model ran) " + prompt[prompt.find("{"):][:160]
            return {"ok": True, "text": text, "completion_tokens": 12, "finish_reason": "stop", "errors": [],
                    "timings": {"wall_s": 0.05}, "device_evidence": "mock"}
        if self.resident is not None and audio is None and image is None:
            return self.resident.generate(prompt, max_tokens)
        if zetic_run is None:
            return {"ok": False, "text": "", "errors": ["run.py (ZETIC wrapper) not found next to cairn.py "
                                                         "or in examples/zetic-lfm"], "timings": {}}
        return zetic_run.run(self.sdk, self.model, prompt, audio=audio, image=image, max_tokens=max_tokens,
                             timeout=self.timeout)

    def call(self, purpose, prompt, audio=None, max_tokens=EXPLAIN_MAX_TOKENS, image=None):
        with self.lock:
            self.busy = purpose
            try:
                attempts = []
                for _ in range(2):
                    r = self._once(prompt, audio, max_tokens, image)
                    attempts.append(r)
                    if r.get("ok"):
                        break
            finally:
                self.busy = None
        r = attempts[-1]
        return {"purpose": purpose, "prompt": prompt, "audio": audio, "image": image, "max_tokens": max_tokens,
                "ok": bool(r.get("ok")), "text": r.get("text", ""), "tokens": r.get("completion_tokens"),
                "finish": r.get("finish_reason"), "errors": r.get("errors", []),
                "timings": r.get("timings", {}), "device_evidence": r.get("device_evidence"),
                "retried": len(attempts) > 1,
                "first_attempt_errors": attempts[0].get("errors", []) if len(attempts) > 1 else []}


_PLAYER = {"proc": None}
_PLAYER_LOCK = threading.Lock()


def play_wav(path):
    """Start aplay without waiting (kills a reply that is still playing). Returns errors as data."""
    import subprocess
    with _PLAYER_LOCK:
        old = _PLAYER["proc"]
        if old is not None and old.poll() is None:
            old.kill()
        try:
            _PLAYER["proc"] = subprocess.Popen(["aplay", "-q", "-D", "plughw:0,0", scaled_copy(path)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        except OSError as e:
            return "aplay failed to start: %s: %s" % (type(e).__name__, e)


def speak_wav(omni, text, out_wav, play=True):
    """TTS through the ZETIC CLI (--tts --out, capped at 60 tokens) under the model lock, then start aplay
    outside the lock without waiting for playback. Errors are data."""
    import re
    import subprocess
    res = {"ok": False, "wav": out_wav, "audio_s": None, "tts_wall_s": None, "play_started": None, "errors": []}
    if omni.mock:
        res["errors"].append("speak skipped (mock)")
        return res
    sdk, model = omni.sdk, omni.model
    tag = uuid.uuid4().hex
    cmd = [os.path.join(sdk, "bin", "zetic_lfm3_cli"), "chat", "--model", model, "--target", "v73",
           "--lib-dir", os.path.join(sdk, "lib"), "--ctx", "2048", "--temp", "0", "--max-tokens", "60",
           "--system", VOICE_STYLE, "--tts", "--out", out_wav, text.strip().lstrip("-").strip()]  # no request tag: TTS would read it aloud
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.path.join(sdk, "lib") + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    with omni.lock:
        omni.busy = "speak"
        try:
            t0 = time.perf_counter()
            try:
                cp = subprocess.run(cmd, cwd=sdk, env=env, capture_output=True, text=True, timeout=60)
                res["tts_wall_s"] = round(time.perf_counter() - t0, 2)
                m = re.search(r"\[speech\] (\d+) frames, ([\d.]+) s of audio", cp.stderr + cp.stdout)
                if m:
                    res["audio_s"] = float(m.group(2))
                if cp.returncode != 0:
                    res["errors"].append("tts exit %d" % cp.returncode)
                if not os.path.isfile(out_wav) or os.path.getsize(out_wav) <= 1024:
                    res["errors"].append("tts wrote no usable wav")
            except (OSError, subprocess.SubprocessError) as e:
                res["errors"].append("tts failed: %s: %s" % (type(e).__name__, e))
        finally:
            omni.busy = None
    if play and not res["errors"]:
        err = play_wav(out_wav)
        if err:
            res["errors"].append(err)
        else:
            res["play_started"] = now_iso()
    res["ok"] = not res["errors"]
    return res


SAY = ("You are Cairn, a hiking buddy who knows Muir Woods and Mount Tamalpais really well, walking right next to "
       "the hiker. Navigation facts (computed by code, correct): {facts}. {knowledge}{earlier}{ask}"
       "Answer only the current question, out loud, the way a friend would talk: casual, warm, contractions, "
       "\"you\", one or two short sentences. Mention only the next turn or the next place or two, not every "
       "waypoint, and round the distance from the facts the way people say it. Every distance, direction "
       "and time must come from the facts; never invent or change one. For general questions, share something from the "
       "local knowledge like a friend would. No lists, no markdown.")


PROMPT_DROP = {"remaining_m", "bearing_deg", "limit_m", "reason", "back_to_trail_bearing_deg", "window_s",
               "straight_line_bearing_deg", "pace_m_s", "sunset_local", "moved_m"}


def slim(x):
    """Keep prompts short: drop fields the hiker does not need (each ZETIC call re-reads the prompt)."""
    if isinstance(x, dict):
        return {k: slim(v) for k, v in x.items() if k not in PROMPT_DROP and v is not None}
    if isinstance(x, list):
        return [slim(v) for v in x]
    return x


KNOWLEDGE_CHARS = 300
GENERIC_WORDS = {"california", "national", "recreation", "area", "the", "and", "site"}


def load_area(path):
    try:
        with open(path) as f:
            return json.load(f).get("articles", [])
    except (OSError, ValueError):
        return []


def trim_sentences(text, limit):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "..."


TOPIC_ALIASES = {"area": "Muir Woods National", "history": "Muir Woods National", "here": "Muir Woods National",
                 "park": "Muir Woods National", "monument": "Muir Woods National",
                 "redwood": "Sequoia sempervirens", "redwoods": "Sequoia sempervirens",
                 "tree": "Sequoia sempervirens", "trees": "Sequoia sempervirens", "muir": "John Muir",
                 "tamalpais": "Mount Tamalpais", "mountain": "Mount Tamalpais", "mount": "Mount Tamalpais",
                 "dipsea": "Dipsea", "creek": "Redwood Creek", "salmon": "Redwood Creek", "kent": "William Kent",
                 "slug": "Banana slug", "slugs": "Banana slug", "yellow": "Banana slug",
                 "fern": "Polystichum", "ferns": "Polystichum", "bark": "Sequoia sempervirens",
                 "trunk": "Sequoia sempervirens", "tree": "Sequoia sempervirens"}
STOP = {"what", "where", "which", "there", "this", "that", "with", "about", "tell", "know", "near", "from",
        "have", "does", "your", "they", "them", "when", "would", "could", "should", "there", "want", "going"}


def pick_knowledge(articles, question, places, k=2):
    """Pick at most k snippets in code: the question's words (and topic aliases) weigh most, then the
    destination and nearby place names. Falls back to Muir Woods National Monument. About KNOWLEDGE_CHARS in total."""
    q = (question or "").lower()
    pl = " ".join(x for x in places if x).lower()
    qwords = [w.strip("?.,!'") for w in q.split()]
    qwords = [w for w in qwords if len(w) > 3 and w not in STOP]
    scored = []
    for a in articles:
        title = a.get("title", "")
        tl = title.lower()
        core = tl.split(" (")[0].split(",")[0]
        body = tl + " " + a.get("extract", "").lower()
        tw = [w for w in tl.replace("(", " ").replace(")", " ").replace(",", " ").split()
              if len(w) > 3 and w not in GENERIC_WORDS]
        score = 3 * sum(1 for w in tw if w in q) + sum(1 for w in qwords if w in body)
        score += sum(5 for w, t in TOPIC_ALIASES.items() if w in qwords and title.startswith(t))
        score += 2 * (core in pl) + sum(1 for w in tw if w in pl)
        if score:
            scored.append((score, title, a))
    scored.sort(key=lambda x: -x[0])
    chosen = [a for _, _, a in scored[:k]]
    if not chosen:
        chosen = [a for a in articles if a.get("title") == "Muir Woods National Monument"][:1]
    if not chosen:
        return []
    per = KNOWLEDGE_CHARS // len(chosen)
    return [{"title": a["title"], "text": trim_sentences(a.get("extract", ""), per)} for a in chosen]


def say_prompt(facts, question=None, turns=None, knowledge=None):
    facts = slim(facts)
    ask = "The hiker asked: \"%s\". " % question.strip().replace('"', "'") if question else ""
    earlier = ""
    if turns:  # last 2 turns, trimmed, so phrasing stays consistent; route maths stays in code
        earlier = ""  # past turns made the 2B model repeat itself; itinerary and breadcrumbs are kept in code
    know = ""
    if knowledge:
        know = "Local knowledge: " + " ".join("%s: %s" % (k["title"], k["text"]) for k in knowledge) + " "
    return SAY.format(facts=json.dumps(facts, separators=(",", ":")), knowledge=know, earlier=earlier, ask=ask)


# ---------------------------------------------------------------- the agent

class Cairn:
    def __init__(self, args):
        self.trails = Trails(os.path.join(DATA, "trails.json"))
        with open(os.path.join(DATA, "track.json")) as f:
            self.track = json.load(f)
        self.omni = Omni(os.path.expanduser(args.sdk), os.path.expanduser(args.model), mock=args.mock_llm)
        self.off_route_m = args.off_route_m
        self.still_s = args.still_s
        self.still_m = args.still_m
        self.junction_m = args.junction_m
        self.landmark_m = getattr(args, "landmark_m", 150.0)
        self.log_path = args.log
        self.area = load_area(os.path.join(DATA, "area_context.json"))
        self.speak_enabled = bool(getattr(args, "speak", False))
        self.speak_async = getattr(args, "cmd", "") == "serve"
        self.rec_dir = os.path.join(HERE, "recordings")
        os.makedirs(self.rec_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.records = []
        self.log_file = open(self.log_path, "a")
        self.reset()

    def reset(self):
        with self.lock:
            self.history = collections.deque()  # (datetime, (lat, lon))
            self.active = set()
            self.last_junction = None
            self.track_i = 0
            self.iteration = 0
            self.destination = self.trails.config["default_destination"]
            self.position = None
            self.sim_t = None
            self.breadcrumbs = []  # every position: {t, lat, lon, source}
            self.junctions_passed = []  # node ids, in order
            self.turns = collections.deque(maxlen=6)  # {q, a}
            self.itinerary = []  # ordered destination node ids
            self.reached = set()
            self.told_landmarks = set()  # each landmark tip fires once per session

    # ---- location history and itinerary (all in code)
    def target(self):
        """Next unreached itinerary stop, else the chosen destination."""
        for nid in self.itinerary:
            if nid not in self.reached:
                return nid
        return self.destination

    def itinerary_view(self):
        return [{"id": n, "name": self.trails.name(n), "reached": n in self.reached} for n in self.itinerary]

    def travel_context(self, p):
        """Where the hiker came from and which way they are moving, from the breadcrumb trail."""
        T = self.trails
        ctx = {}
        here = T.nearest_node(p)
        at = here if haversine(p, T.pos(here)) <= self.junction_m else None
        prev = [j for j in self.junctions_passed if j != at]
        if prev:
            ctx["came_from"] = T.name(prev[-1])
        recent = [j for j in self.junctions_passed[-4:]]
        if recent:
            ctx["recent_path"] = " > ".join(T.name(j) for j in recent)
        back = None
        for b in reversed(self.breadcrumbs[:-1]):
            q = (b["lat"], b["lon"])
            if haversine(q, p) >= 40:
                back = q
                break
        if back:
            ctx["moving"] = COMPASS_WORDS[compass(bearing(back, p))]
        return ctx

    def route_facts(self, p, target):
        r = self.trails.route(p, target, self.junction_m)
        if not r:
            return None, {}
        f = {k: v for k, v in r.items() if k != "bearing_deg"}
        ctx = self.travel_context(p)
        f.update(ctx)
        if ctx.get("came_from") and r.get("toward") == ctx["came_from"]:
            f["goes_back_the_way_you_came"] = True
        elif ctx.get("came_from") and r.get("toward"):
            f["not_back_toward"] = ctx["came_from"]
        if self.itinerary:
            f["itinerary"] = [("%s (done)" % v["name"]) if v["reached"] else v["name"] for v in self.itinerary_view()]
        return r, f

    def write(self, rec):
        with self.lock:
            rec["seq"] = len(self.records)
            self.records.append(rec)
            if len(self.records) > 2000:
                self.records = self.records[-2000:]
            try:
                self.log_file.write(json.dumps(rec) + "\n")
                self.log_file.flush()
            except OSError as e:
                rec.setdefault("errors", []).append("log write failed: %s" % e)

    # ---- observe
    def next_track_point(self):
        if self.track_i >= len(self.track):
            return None
        pt = self.track[self.track_i]
        self.track_i += 1
        return pt

    def sunset(self, t):
        hh, mm = map(int, self.trails.config["sunset_local"].split(":"))
        return t.replace(hour=hh, minute=mm, second=0, microsecond=0)

    # ---- decide (rules in code)
    def decide(self, t, p):
        T = self.trails
        rules = {}
        dist_off, nearest_pt, edge = T.nearest_trail(p)
        rules["off_route"] = {"fired": dist_off > self.off_route_m, "distance_off_trail_m": round_m(dist_off),
                              "limit_m": self.off_route_m, "nearest_trail": edge["trail"]}
        if rules["off_route"]["fired"]:
            deg = bearing(p, nearest_pt)
            rules["off_route"]["reason"] = ("%d m from the nearest trail (%s), limit %d m"
                                            % (round_m(dist_off), edge["trail"], self.off_route_m))
            rules["off_route"]["back_to_trail_heading"] = COMPASS_WORDS[compass(deg)]
            rules["off_route"]["back_to_trail_bearing_deg"] = int(round(deg))

        # no movement: covered window of still_s with every point within still_m of now
        window = [q for (tt, q) in self.history if (t - tt).total_seconds() <= self.still_s]
        covered = bool(self.history) and (t - self.history[0][0]).total_seconds() >= self.still_s
        spread = max((haversine(p, q) for q in window), default=0.0)
        fired = covered and spread < self.still_m
        rules["no_movement"] = {"fired": fired, "moved_m": round(spread, 1), "window_s": self.still_s,
                                "limit_m": self.still_m}
        if fired:
            rules["no_movement"]["reason"] = ("moved %.0f m in the last %d minutes" % (spread, self.still_s // 60))

        # sunset: route back to the home trailhead at recent pace must finish before sunset
        pace_window = [(tt, q) for (tt, q) in self.history if (t - tt).total_seconds() <= 900]
        span = (t - pace_window[0][0]).total_seconds() if pace_window else 0
        rule = {"fired": False}
        if span >= 300:
            moved = sum(haversine(a[1], b[1]) for a, b in zip(pace_window, pace_window[1:]))
            moved += haversine(pace_window[-1][1], p)
            pace = moved / span
            home = T.config["home_trailhead"]
            back = T.route(p, home, self.junction_m)
            remaining = back["remaining_m"] if back else 0
            sunset = self.sunset(t)
            left_s = (sunset - t).total_seconds()
            need_s = remaining / pace if pace > 0.05 else float("inf")
            rule = {"fired": need_s > left_s, "pace_m_s": round(pace, 2), "to_trailhead_m": remaining,
                    "minutes_to_sunset": int(left_s // 60),
                    "minutes_needed_at_this_pace": None if need_s == float("inf") else int(need_s // 60),
                    "sunset_local": T.config["sunset_local"], "trailhead": T.name(home)}
            if rule["fired"]:
                rule["reason"] = ("%s is %.1f km away; at your recent pace that takes %s but sunset is in %d min"
                                  % (T.name(home), remaining / 1000.0,
                                     "forever" if need_s == float("inf") else "%d min" % (need_s // 60),
                                     left_s // 60))
        rules["sunset"] = rule

        junction = None
        nn = T.nearest_node(p)
        # a real crossroads (3+ trails) or a destination; plain bridges along one trail do not count
        crossroads = len(T.adj[nn]) >= 3 or "destination" in T.nodes[nn]["kinds"]
        if crossroads and haversine(p, T.pos(nn)) <= self.junction_m:
            junction = nn
        new_alerts = [k for k, v in rules.items() if v.get("fired") and k not in self.active]
        self.active = {k for k, v in rules.items() if v.get("fired")}
        new_junction = junction if junction and junction != self.last_junction else None
        if junction:
            self.last_junction = junction
        elif self.last_junction and haversine(p, T.pos(self.last_junction)) > 2 * self.junction_m:
            self.last_junction = None
        return {"rules": rules, "new_alerts": new_alerts, "active": sorted(self.active),
                "at_junction": T.name(junction) if junction else None,
                "new_junction": T.name(new_junction) if new_junction else None, "_junction_id": new_junction}

    # ---- act (allowlist)
    def run_action(self, name, p, decision):
        if name not in ALLOWED_ACTIONS:
            return {"name": name, "result": "refused: not on the allowlist"}
        T = self.trails
        if name == "log_alert":
            return {"name": name, "result": {"rules": decision["new_alerts"]}}
        if name == "nearest_bailout":
            return {"name": name, "result": T.nearest_of_kind(p, "bailout")}
        if name == "nearest_water":
            return {"name": name, "result": T.nearest_of_kind(p, "water")}
        if name == "route_at_junction":
            return {"name": name, "result": T.route(p, self.target(), self.junction_m)}
        if name == "landmark_tip":
            return {"name": name, "result": {"landmark": decision.get("approaching_landmark")}}
        return {"name": name, "result": "refused"}

    def act(self, proposals, p, decision):
        actions, refused = [], []
        for name in proposals:
            a = self.run_action(name, p, decision)
            (refused if isinstance(a["result"], str) and a["result"].startswith("refused") else actions).append(a)
        return actions, refused

    def maybe_speak(self, rec, text):
        """Allowlisted 'speak' action after a successful answer. In serve it runs in a background thread
        (TTS under the model lock, aplay not awaited) so the HTTP answer returns at once; the result is
        logged as its own 'speak' record that the UI picks up by polling."""
        if not text or not self.speak_enabled:
            return
        if "speak" not in ALLOWED_ACTIONS:
            rec.setdefault("refused", []).append({"name": "speak", "result": "refused: not on the allowlist"})
            return
        name = "reply-%s.wav" % uuid.uuid4().hex
        rec["speech_pending"] = True

        def job():
            res = speak_wav(self.omni, text, os.path.join(self.rec_dir, name))
            srec = {"t": now_iso(), "kind": "speak", "for": rec.get("kind"), "errors": [],
                    "actions": [{"name": "speak", "result": res}]}
            if res["ok"]:
                srec["speech_url"] = "/api/audio/" + name
            elif not self.omni.mock:
                srec["errors"].append("speak failed: %s" % "; ".join(res["errors"])[:300])
            self.write(srec)

        if self.speak_async:
            threading.Thread(target=job, daemon=True).start()
        else:
            job()

    def knowledge_for(self, rec, question, *parts):
        k = pick_knowledge(self.area, question, parts)
        rec["knowledge"] = [x["title"] for x in k]
        return k

    def recent_pace(self, t, window_s=600):
        pts = [(tt, q) for tt, q in self.history if (t - tt).total_seconds() <= window_s]
        if len(pts) < 2:
            return None
        span = (pts[-1][0] - pts[0][0]).total_seconds()
        if span < 120:
            return None
        return sum(haversine(a[1], b[1]) for a, b in zip(pts, pts[1:])) / span

    def landmark_ahead(self, t, p):
        """Nearest untold landmark within landmark_m that is on the route or ahead in the direction of travel.
        Returns facts computed in code (distance, bearing, ETA at recent pace), or None."""
        T = self.trails
        r = T.route(p, self.target(), self.junction_m)
        on_route = set((r or {}).get("via", []))
        moving = None
        for b in reversed(self.breadcrumbs[:-1]):
            q = (b["lat"], b["lon"])
            if haversine(q, p) >= 40:
                moving = bearing(q, p)
                break
        best = None
        for nid, n in T.nodes.items():
            if "landmark" not in n or nid in self.told_landmarks:
                continue
            d = haversine(p, T.pos(nid))
            if d > self.landmark_m:
                continue
            deg = bearing(p, T.pos(nid))
            ahead = moving is not None and abs((deg - moving + 180) % 360 - 180) <= 70
            if d <= 40 or n["name"] in on_route or ahead:
                if best is None or d < best[1]:
                    best = (nid, d, deg)
        if best is None:
            return None
        nid, d, deg = best
        pace = self.recent_pace(t)
        return {"id": nid, "landmark": T.name(nid), "about": T.nodes[nid]["landmark"].get("blurb"),
                "distance_m": round_m(d), "heading": COMPASS_WORDS[compass(deg)], "bearing_deg": int(round(deg)),
                "eta_min": int(round(d / pace / 60.0)) if pace and pace > 0.05 and d > 40 else None,
                "here_now": d <= 40, "story": T.nodes[nid]["landmark"].get("story", [])}

    # ---- one iteration of the always-on loop
    def observe(self, t, p, source):
        t0 = time.perf_counter()
        with self.lock:
            rec = {"t": now_iso(), "kind": "iteration", "iteration": self.iteration, "errors": []}
            self.iteration += 1
            try:
                rec["observation"] = {"source": source, "time_local": t.isoformat(), "lat": round(p[0], 6),
                                      "lon": round(p[1], 6), "destination": self.trails.name(self.destination)}
                decision = self.decide(t, p)
                self.history.append((t, p))
                while self.history and (t - self.history[0][0]).total_seconds() > 1800:
                    self.history.popleft()
                self.position, self.sim_t = p, t
                self.breadcrumbs.append({"t": t.isoformat(), "lat": round(p[0], 6), "lon": round(p[1], 6),
                                         "source": source})
                del self.breadcrumbs[:-3000]
                jid = decision.pop("_junction_id")
                if jid:
                    self.junctions_passed.append(jid)
                for nid in self.itinerary:  # mark itinerary stops reached
                    if nid not in self.reached and haversine(p, self.trails.pos(nid)) <= self.junction_m:
                        self.reached.add(nid)
                        decision["itinerary_reached"] = self.trails.name(nid)
                rec["decision"] = decision
                proposals = []
                if decision["new_alerts"]:
                    proposals += ["log_alert", "nearest_bailout"]
                if jid:
                    proposals.append("route_at_junction")
                # proactive guide: a landmark tip, never in the same iteration as a safety alert
                tip = None if decision["new_alerts"] else self.landmark_ahead(t, p)
                if tip:
                    self.told_landmarks.add(tip["id"])
                    decision["approaching_landmark"] = tip["landmark"]
                    proposals.append("landmark_tip")
                    rec["landmark_tip"] = tip["landmark"]
                rec["proposals"] = proposals
                actions, refused = self.act(proposals, p, decision)
                rec["actions"], rec["refused"] = actions, refused
            except Exception as e:  # noqa: BLE001  errors are data; the loop keeps running
                rec["errors"].append("%s: %s" % (type(e).__name__, e))
                self.write(rec)
                return rec
        rec["llm"] = []
        if proposals:
            facts = {}
            for k in decision["new_alerts"]:
                r = decision["rules"][k]
                facts[k] = {kk: vv for kk, vv in r.items() if kk not in ("fired",)}
                if k == "no_movement":
                    facts[k] = {"minutes_without_moving": self.still_s // 60}
                if k == "sunset" and "to_trailhead_m" in r:
                    facts[k]["to_trailhead_km"] = round(r["to_trailhead_m"] / 1000.0, 1)
                    del facts[k]["to_trailhead_m"]
            for a in actions:
                if a["name"] == "nearest_bailout" and a["result"]:
                    b = a["result"]
                    facts["nearest_bailout"] = {k: b.get(k) for k in ("name", "remaining_km", "take_trail",
                                                                      "heading", "straight_line_m",
                                                                      "straight_line_heading")}
                if a["name"] == "route_at_junction" and a["result"]:
                    facts["at_junction"] = decision["new_junction"]
                    facts["route"] = self.route_facts(p, self.target())[1]
                if a["name"] == "landmark_tip":
                    a["result"] = {k: v for k, v in tip.items() if k not in ("id", "story")}
                    facts["approaching_landmark"] = {k: v for k, v in tip.items()
                                                     if k not in ("id", "story", "bearing_deg")}
            if tip:  # the landmark's own story articles, trimmed in code
                titles = set(tip["story"])
                chosen = [a for a in self.area if a.get("title") in titles][:2]
                per = KNOWLEDGE_CHARS // max(1, len(chosen))
                know = [{"title": a["title"], "text": trim_sentences(a.get("extract", ""), per)} for a in chosen]
                rec["knowledge"] = [k["title"] for k in know]
            else:
                know = self.knowledge_for(rec, None, self.trails.name(self.target()),
                                          decision.get("new_junction") or "",
                                          self.trails.name(self.trails.nearest_node(p)))
            call = self.omni.call("landmark" if tip and len(proposals) == 1 else "explain",
                                  say_prompt(facts, knowledge=know))
            rec["llm"].append(call)
            rec["explanation"] = call["text"] if call["ok"] else None
            if not call["ok"]:
                rec["errors"].append("explanation failed: %s" % "; ".join(call["errors"])[:300])
            self.maybe_speak(rec, rec["explanation"])
        rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.write(rec)
        return rec

    def step(self):
        pt = self.next_track_point()
        if pt is None:
            return None
        return self.observe(datetime.fromisoformat(pt["t"]), (pt["lat"], pt["lon"]), "track")

    def spoof(self, lat, lon):
        if self.sim_t is None:
            self.sim_t = datetime.fromisoformat(self.track[0]["t"])
        return self.observe(self.sim_t + timedelta(seconds=60), (lat, lon), "spoofed")

    # ---- hiker question: voice (ASR) or text -> intent in code -> facts -> Omni phrasing
    def intent(self, q):
        s = q.lower()
        T = self.trails
        aliases = {"cathedral": "cathedral_grove", "camp": "camp_eastwood", "eastwood": "camp_eastwood",
                   "pantoll": "pantoll", "ranger": "pantoll", "bohemian": "bohemian_grove",
                   "founders": "founders_grove", "visitor": "mw_entrance", "entrance": "mw_entrance",
                   "car": "mw_entrance", "parking": "mw_entrance", "trailhead": "mw_entrance",
                   "van wyck": "van_wyck", "meadow": "van_wyck", "highway": "panoramic_hwy"}
        for nid, n in T.nodes.items():
            aliases.setdefault(n["name"].lower(), nid)
        # every place named, in the order spoken (longest alias wins on overlap)
        found, taken = [], set()
        for k, nid in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
            i = s.find(k)
            if i >= 0 and not taken.intersection(range(i, i + len(k))):
                taken.update(range(i, i + len(k)))
                found.append((i, nid))
        targets = []
        for _, nid in sorted(found):
            if nid not in targets:
                targets.append(nid)
        target = targets[0] if targets else None
        if targets and any(w in s for w in (" first", "then", "after that", "afterwards", "want to go",
                                            "plan", "stop at", "on the way")):
            if " first" in s:
                rest = [n for n in self.itinerary if n not in targets and n not in self.reached]
                cur = [] if self.destination in targets or self.destination in rest else [self.destination]
                plan = targets + rest + cur
            elif ("then" in s or "after" in s) and self.itinerary:
                plan = [n for n in self.itinerary if n not in self.reached] + \
                    [n for n in targets if n not in self.itinerary]
            else:
                plan = targets
            return "plan", plan
        if "next" in s and not targets:
            return "route", self.target()
        if any(w in s for w in ("bail", "emergency", "help", "get out", "hurt", "injur", "exit")):
            return "bailout", None
        if "water" in s or "thirst" in s or "drink" in s:
            return "water", None
        if any(w in s for w in ("sunset", "dark", "daylight", "time")):
            return "sunset", None
        if target:
            return "route", target
        if any(w in s for w in ("which way", "where", "direction", "path", "go", "how far", "distance", "turn")):
            return "route", self.target()
        return "general", None

    def ask(self, text=None, wav=None):
        t0 = time.perf_counter()
        rec = {"t": now_iso(), "kind": "ask", "errors": [], "llm": []}
        if wav:
            call = self.omni.call("asr", ASR_PROMPT, audio=wav, max_tokens=ASR_MAX_TOKENS)
            rec["llm"].append(call)
            if not call["ok"]:
                rec["errors"].append("transcription failed: %s" % "; ".join(call["errors"])[:300])
                rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                self.write(rec)
                return rec
            text = strip_echo(call["text"])
            if not text:
                rec["errors"].append("transcription was empty after removing the echoed prompt")
                rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                self.write(rec)
                return rec
        rec["question"] = text
        with self.lock:
            p = self.position
            if p is None and self.track:
                p = (self.track[0]["lat"], self.track[0]["lon"])
            t = self.sim_t or datetime.fromisoformat(self.track[0]["t"])
            kind, target = self.intent(text or "")
            T = self.trails
            if kind == "plan":
                self.itinerary = list(target)
                self.reached -= set(target)
                target = self.target()
            elif kind == "route" and target and target in T.nodes and "destination" in T.nodes[target]["kinds"] \
                    and target not in self.itinerary:
                self.destination = target
            rec["intent"] = {"kind": kind, "target": T.name(target) if target else None,
                             "itinerary": [v["name"] for v in self.itinerary_view()]}
            facts = {"you_are_near": T.name(T.nearest_node(p)), "on_trail": T.nearest_trail(p)[2]["trail"]}
            if kind in ("route", "plan"):
                rec["proposals"] = ["route_at_junction"]
                r, f = self.route_facts(p, target)
                rec["actions"] = [{"name": "route_at_junction", "result": r}]
                facts["route"] = f
            elif kind in ("bailout", "water"):
                name = "nearest_bailout" if kind == "bailout" else "nearest_water"
                rec["proposals"] = [name]
                a = self.run_action(name, p, {"new_alerts": []})
                rec["actions"] = [a]
                facts[name] = a["result"]
            elif kind == "sunset":
                saved = (set(self.active), self.last_junction)  # a question must not change alert state
                d = self.decide(t, p)
                self.active, self.last_junction = saved
                facts["sunset"] = {k: v for k, v in d["rules"]["sunset"].items() if k != "fired"}
                facts["sunset"]["minutes_to_sunset"] = int((self.sunset(t) - t).total_seconds() // 60)
                facts["local_time"] = t.strftime("%H:%M")
            else:
                facts["active_alerts"] = sorted(self.active)
                facts["area"] = T.config["area"]
            rec["facts"] = facts
        know = self.knowledge_for(rec, text or "", self.trails.name(self.target()), facts.get("you_are_near", ""),
                                  (rec.get("intent") or {}).get("target") or "")
        call = self.omni.call("answer", say_prompt(facts, text, list(self.turns), know))
        rec["llm"].append(call)
        rec["answer"] = call["text"] if call["ok"] else None
        if not call["ok"]:
            rec["errors"].append("answer failed: %s" % "; ".join(call["errors"])[:300])
        with self.lock:
            self.turns.append({"q": text or "", "a": rec["answer"] or ""})
        self.maybe_speak(rec, rec["answer"])
        rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.write(rec)
        return rec

    def look(self, image):
        """Camera question ("what is this?"): Omni describes a 512x512 photo on the NPU, code picks local
        knowledge from that description, and one capped answer call phrases it for the hiker."""
        t0 = time.perf_counter()
        rec = {"t": now_iso(), "kind": "look", "errors": [], "llm": [], "question": "What is this?",
               "proposals": ["describe_photo"], "actions": []}
        call = self.omni.call("vision", VISION_PROMPT, image=image, max_tokens=VISION_MAX_TOKENS)
        rec["llm"].append(call)
        if not call["ok"]:
            rec["errors"].append("photo description failed: %s" % "; ".join(call["errors"])[:300])
            rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            self.write(rec)
            return rec
        seen = call["text"].strip()
        rec["actions"].append({"name": "describe_photo", "result": {"camera_sees": seen}})
        with self.lock:
            p = self.position
            if p is None and self.track:
                p = (self.track[0]["lat"], self.track[0]["lon"])
            T = self.trails
            facts = {"you_are_near": T.name(T.nearest_node(p)), "camera_sees": seen}
            rec["facts"] = facts
        know = self.knowledge_for(rec, seen, facts["you_are_near"])
        call = self.omni.call("answer", say_prompt(facts, "What is this I'm looking at?", list(self.turns), know))
        rec["llm"].append(call)
        rec["answer"] = call["text"] if call["ok"] else None
        if not call["ok"]:
            rec["errors"].append("answer failed: %s" % "; ".join(call["errors"])[:300])
        with self.lock:
            self.turns.append({"q": "(photo) what is this?", "a": rec["answer"] or ""})
        self.maybe_speak(rec, rec["answer"])
        rec["loop_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.write(rec)
        return rec

# ---------------------------------------------------------------- CLI: replay

def cmd_replay(args):
    agent = Cairn(args)
    print("cairn replay: %d track points, log %s, model %s" % (
        len(agent.track), os.path.abspath(args.log), "MOCK" if args.mock_llm else args.model), flush=True)
    counts = collections.Counter()
    n = 0
    while args.iterations == 0 or n < args.iterations:
        rec = agent.step()
        if rec is None:
            break
        n += 1
        d = rec.get("decision", {})
        tag = []
        if d.get("new_alerts"):
            tag.append("ALERT " + ",".join(d["new_alerts"]))
            counts.update(d["new_alerts"])
        if d.get("new_junction"):
            tag.append("JUNCTION " + d["new_junction"])
            counts["junction"] += 1
        if d.get("approaching_landmark"):
            tag.append("LANDMARK " + d["approaching_landmark"])
            counts["landmark"] += 1
        for c in rec.get("llm", []):
            counts["llm_ok" if c["ok"] else "llm_error"] += 1
        if rec.get("errors"):
            counts["errors"] += 1
        obs = rec.get("observation", {})
        print("[%3d] %s active=%s %s" % (rec["iteration"], obs.get("time_local", "")[11:16],
                                         ",".join(d.get("active", [])) or "-", " | ".join(tag)), flush=True)
        if rec.get("explanation"):
            print("      -> %s" % rec["explanation"], flush=True)
        for e in rec.get("errors", []):
            print("      !! %s" % e, flush=True)
        if args.interval:
            time.sleep(args.interval)
    print("summary: iterations=%d %s" % (n, json.dumps(dict(counts))), flush=True)
    return 0


# ---------------------------------------------------------------- CLI: serve (web UI)

def cmd_serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    agent = Cairn(args)

    def _warm():  # load the resident text model now so the first question is fast too
        with agent.omni.lock:
            try:
                if agent.omni.resident is not None and agent.omni.resident.p is None:
                    agent.omni.resident.start()
            except Exception as e:  # noqa: BLE001  (a failed warm-up just means the first call loads it)
                print("resident warm-up failed: %s" % e, flush=True)
    threading.Thread(target=_warm, daemon=True).start()
    import re
    rec_dir = agent.rec_dir
    with open(os.path.join(HERE, "ui.html"), "rb") as f:
        page = f.read()

    def graph():
        T = agent.trails
        return {"nodes": list(T.nodes.values()),
                "edges": [{"trail": e["trail"], "line": e["line"]} for e in T.edges],
                "track": [[q["lat"], q["lon"]] for q in agent.track],
                "destinations": [{"id": n, "name": T.name(n)} for n in T.with_kind("destination")],
                "config": T.config}

    def state():
        return {"position": agent.position, "time_local": agent.sim_t.isoformat() if agent.sim_t else None,
                "track_i": agent.track_i, "track_len": len(agent.track), "destination": agent.destination,
                "active": sorted(agent.active), "busy": agent.omni.busy, "mock": args.mock_llm,
                "target": agent.target(), "itinerary": agent.itinerary_view(),
                "breadcrumbs": [[b["lat"], b["lon"]] for b in agent.breadcrumbs[-800:]],
                "turns": list(agent.turns), "speak": agent.speak_enabled,
                "volume": VOLUME["percent"], "route": route_via()}

    def route_via():
        """Node names on the route to the next stop, for the UI's route highlight (computed in code)."""
        try:
            if agent.position is None:
                return []
            r = agent.trails.route(agent.position, agent.target(), agent.junction_m)
            return (r or {}).get("via", [])
        except Exception:  # noqa: BLE001
            return []

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 5_000_000:
                raise ValueError("request too large")
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                return self.send(200, page, "text/html; charset=utf-8")
            if path == "/api/samples":
                try:
                    with open(os.path.join(DATA, "samples", "samples.json")) as f:
                        return self.send(200, json.load(f))
                except (OSError, ValueError):
                    return self.send(200, [])
            if path.startswith("/api/sample/"):
                name = path[len("/api/sample/"):]
                if re.fullmatch(r"[a-z_]{1,40}\.ppm", name):
                    fp = os.path.join(DATA, "samples", name)
                    if os.path.isfile(fp):
                        with open(fp, "rb") as f:
                            return self.send(200, f.read(), "image/x-portable-pixmap")
                return self.send(404, {"error": "not found"})
            if path == "/api/graph":
                return self.send(200, graph())
            if path == "/api/state":
                return self.send(200, state())
            if path.startswith("/api/audio/"):
                name = path[len("/api/audio/"):]
                if not re.fullmatch(r"reply-[0-9a-f]{32}\.wav", name):
                    return self.send(404, {"error": "not found"})
                try:
                    with open(os.path.join(rec_dir, name), "rb") as f:
                        return self.send(200, f.read(), "audio/wav")
                except OSError:
                    return self.send(404, {"error": "not found"})
            if path == "/api/log":
                since = 0
                for kv in query.split("&"):
                    if kv.startswith("since="):
                        try:
                            since = int(kv[6:])
                        except ValueError:
                            pass
                with agent.lock:
                    recs = [r for r in agent.records if r["seq"] >= since]
                return self.send(200, {"records": recs[-200:], "state": state()})
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            try:
                raw = self.body()
                if self.path == "/api/ask" and (self.headers.get("Content-Type") or "").startswith("audio/"):
                    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
                        return self.send(400, {"error": "expected a WAV file"})
                    wav = os.path.join(rec_dir, "ask-%s.wav" % uuid.uuid4().hex)
                    with open(wav, "wb") as f:
                        f.write(raw)
                    return self.send(200, agent.ask(wav=wav))
                if self.path == "/api/look":
                    if not raw.startswith(PPM_HEADER) or len(raw) != len(PPM_HEADER) + 512 * 512 * 3:
                        return self.send(400, {"error": "expected a 512x512 binary PPM (P6)"})
                    img = os.path.join(rec_dir, "look-%s.ppm" % uuid.uuid4().hex)
                    with open(img, "wb") as f:
                        f.write(raw)
                    return self.send(200, agent.look(img))
                data = json.loads(raw or b"{}")
                if self.path == "/api/ask":
                    q = str(data.get("text", "")).strip()[:500]
                    if not q:
                        return self.send(400, {"error": "empty question"})
                    return self.send(200, agent.ask(text=q))
                if self.path == "/api/step":
                    rec = agent.step()
                    return self.send(200, rec or {"done": True})
                if self.path == "/api/position":
                    return self.send(200, agent.spoof(float(data["lat"]), float(data["lon"])))
                if self.path == "/api/destination":
                    if data.get("id") not in agent.trails.nodes:
                        return self.send(400, {"error": "unknown destination"})
                    agent.destination = data["id"]
                    return self.send(200, state())
                if self.path == "/api/speak":
                    agent.speak_enabled = bool(data.get("on"))
                    return self.send(200, state())
                if self.path == "/api/volume":
                    agent.volume = set_volume(int(data.get("percent", 8)), mock=args.mock_llm)
                    return self.send(200, state())
                if self.path == "/api/reset":
                    agent.reset()
                    return self.send(200, state())
                return self.send(404, {"error": "not found"})
            except Exception as e:  # noqa: BLE001
                return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    print("cairn UI on http://127.0.0.1:%d (loopback only; from the laptop use an SSH tunnel: "
          "ssh -L %d:127.0.0.1:%d <board>)  model=%s  log=%s" % (
              args.port, args.port, args.port, "MOCK" if args.mock_llm else args.model,
              os.path.abspath(args.log)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sdk", default="~/zetic-demo/sdk", help="ZETIC Ixian SDK 0.2.1 directory (bin/, lib/)")
    common.add_argument("--model", default="~/zetic-demo/models/lfm3-omni-2b",
                        help="ZETIC LFM3-Omni-2B package parent (contains v73/manifest.json)")
    common.add_argument("--mock-llm", action="store_true",
                        help="canned text instead of the model, for laptop smoke tests (marked 'mock')")
    common.add_argument("--off-route-m", type=int, default=75, help="alert beyond this distance from any trail")
    common.add_argument("--still-s", type=int, default=600, help="no-movement window in seconds")
    common.add_argument("--still-m", type=float, default=15.0, help="no-movement: moved less than this in the window")
    common.add_argument("--junction-m", type=float, default=60.0, help="radius that counts as 'at a junction'")
    common.add_argument("--landmark-m", type=float, default=150.0,
                        help="proactive tip when an untold landmark ahead or on the route is within this distance")
    common.add_argument("--log", default=os.path.join(HERE, "cairn_log.jsonl"), help="flushed JSONL log")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("replay", parents=[common], help="headless always-on run over data/track.json")
    r.add_argument("--iterations", type=int, default=0, help="0 = whole track")
    r.add_argument("--interval", type=float, default=0.0, help="seconds to sleep between points")
    r.add_argument("--speak", action="store_true", help="speak explanations on the board speaker (TTS + aplay)")
    s = sub.add_parser("serve", parents=[common], help="web UI on 127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--no-speak", dest="speak", action="store_false",
                   help="do not speak answers on the board speaker (default: speak; toggle in the UI)")
    args = ap.parse_args()
    if args.cmd == "replay":
        return cmd_replay(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
