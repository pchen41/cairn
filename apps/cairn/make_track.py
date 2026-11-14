#!/usr/bin/env python3
"""Regenerate data/track.json: a synthetic hike from the Muir Woods entrance to Pantoll Ranger Station.

Main Trail -> long stop at Cathedral Grove -> Ben Johnson Trail with an off-route wander -> slow last climb.
One point per minute. Deterministic (fixed seed). Standard library only.
"""
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cairn import Trails, haversine  # noqa: E402

rng = random.Random(7)
T = Trails(os.path.join(HERE, "data", "trails.json"))
path = ["mw_entrance", "bridge1", "founders_grove", "bridge2", "bohemian_grove", "cathedral_grove",
        "bridge3", "bridge4", "ben_johnson_jct", "pantoll"]
speed = {"bridge4": 0.9, "ben_johnson_jct": 0.45}  # speed on the leg that starts at this node
legs = [(a, b, speed.get(a, 1.1)) for a, b in zip(path, path[1:])]
WANDER_LEG, STOP_AT = "bridge4", "cathedral_grove"
t = datetime.fromisoformat("2026-11-13T15:48:00-08:00")
pts = []


def add(p):
    pts.append({"t": t.isoformat(), "lat": round(p[0], 6), "lon": round(p[1], 6)})


def line_for(a, b):
    for v, edge, line in T.adj[a]:
        if v == b:
            return line
    raise KeyError((a, b))


def walk(line, speed, wander=None):
    global t
    seg = [(line[i], line[i + 1], haversine(line[i], line[i + 1])) for i in range(len(line) - 1)]
    total = sum(s[2] for s in seg)
    d = 0.0
    while d < total:
        acc, rem = 0.0, d
        for a, b, L in seg:
            if rem <= L:
                f = rem / L if L else 0
                p = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                break
            rem -= L
        frac = d / total
        if wander and wander[0] <= frac <= wander[1]:
            mid = (wander[0] + wander[1]) / 2
            off = wander[2] * (1 - abs(frac - mid) / (mid - wander[0]))
            p = (p[0] + off / 111320.0, p[1])  # step north, off the trail
        jit = 2.0 / 111320.0
        add((p[0] + rng.uniform(-jit, jit), p[1] + rng.uniform(-jit, jit)))
        t += timedelta(seconds=60)
        d += speed * 60 * rng.uniform(0.9, 1.1)


for a, b, speed in legs:
    line = line_for(a, b)
    walk(line, speed, wander=(0.3, 0.75, 170.0) if a == WANDER_LEG else None)
    if b == STOP_AT:
        p = line[-1]
        for _ in range(13):  # water and snack stop
            jit = 3.0 / 111320.0
            add((p[0] + rng.uniform(-jit, jit), p[1] + rng.uniform(-jit, jit)))
            t += timedelta(seconds=60)
add(T.pos(path[-1]))

with open(os.path.join(HERE, "data", "track.json"), "w") as f:
    json.dump(pts, f, indent=0)
print("wrote %d points, %s to %s" % (len(pts), pts[0]["t"], pts[-1]["t"]))
