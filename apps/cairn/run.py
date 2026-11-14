#!/usr/bin/env python3
"""Cairn copy of examples/zetic-lfm/run.py: also accepts SDK 0.2.1's "nsp2=1" device line.

One capped ZETIC CLI request using local IQ9 assets; no downloads or installs."""

import argparse
import datetime
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import uuid

SUPPORTED_MODELS = {"lfm3-1.7b", "lfm3-vl-2b", "lfm3-audio-2b", "lfm3-omni-2b"}
STATS = re.compile(
    r"\[(\w+)\] finish (\w+) \| prompt (\d+) rows \(cached (\d+)\) -> (\d+) tokens"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(sdk, model, prompt, *, image=None, audio=None, max_tokens=256, timeout=120):
    """Return output, runtime timing, device evidence, and errors as a JSON-safe dict.

    This starts a fresh vendor CLI process, not a persistent model service.
    Paths are local; model is the parent directory containing v73/manifest.json.
    """
    result = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "ok": False, "text": "", "completion_tokens": None, "finish_reason": None,
              "timings": {}, "device_evidence": None, "proposals": [], "actions": [],
              "errors": [], "command": [], "stdout": "", "stderr": ""}
    started = time.perf_counter()
    try:
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 1024:
            raise ValueError("max_tokens must be an integer from 1 to 1024")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must not be empty")
        if image is not None and audio is not None:
            raise ValueError("choose one image or one audio clip; mixed-media requests are outside this example")
        if platform.machine() != "aarch64":
            raise ValueError("run inference on the IQ9 (aarch64), not the laptop")
        sdk = Path(sdk).expanduser().resolve(strict=True)
        model = Path(model).expanduser().resolve(strict=True)
        cli = sdk / "bin/zetic_lfm3_cli"
        if not cli.is_file() or not os.access(cli, os.X_OK):
            raise ValueError("SDK is missing executable bin/zetic_lfm3_cli")
        if not (sdk / "lib/libhexrt_v73_skel.so").is_file():
            raise ValueError("SDK is missing lib/libhexrt_v73_skel.so")
        manifest = json.loads((model / "v73/manifest.json").read_text())
        name = manifest.get("package", {}).get("name")
        target = manifest.get("target", {})
        if manifest.get("schema") != "zetic.hexagon.package/1" or name not in SUPPORTED_MODELS:
            raise ValueError("expected a ZETIC LFM3 generative package; d1 requires the separate Decision API")
        if (target.get("name") != "v73" or target.get("hexagon_arch") != 73
                or target.get("platform") != "linux-arm64-qcom" or target.get("nsp") != 2):
            raise ValueError("expected the IQ9 v73 Linux ARM64 two-NSP package")
        capability = "image" if image is not None else "audio" if audio is not None else "text"
        if not manifest.get("capabilities", {}).get(capability):
            raise ValueError(f"package does not support {capability} input")
        result["model"] = manifest.get("model")
        result["precision"] = manifest.get("lm", {}).get("quant")
        command = [str(cli), "chat", "--model", str(model), "--target", "v73",
                   "--lib-dir", str(sdk / "lib"), "--ctx", "2048", "--temp", "0",
                   "--max-tokens", str(max_tokens), "--verbose"]
        media = image if image is not None else audio
        if media is not None:
            media = Path(media).expanduser().resolve(strict=True)
            if not media.is_file() or "+" in str(media):
                raise ValueError("media must be a local file with no '+' in its path (CLI delimiter)")
            command += ["--media", str(media)]
        if audio is not None:
            command += ["--system", "Perform ASR in English."]
        # A leading tag also prevents user text being interpreted as a CLI option.
        command.append(f"[request:{uuid.uuid4().hex}] {prompt.strip()}")
        result["command"] = command
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = str(sdk / "lib") + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        completed = subprocess.run(command, cwd=sdk, env=env, capture_output=True, text=True, timeout=timeout)
        result.update(stdout=completed.stdout, stderr=completed.stderr, exit_code=completed.returncode)
        if completed.returncode:
            result["errors"].append(f"CLI exit {completed.returncode}")
        stdout, stderr = ANSI.sub("", completed.stdout), ANSI.sub("", completed.stderr)
        answer = "\n".join(line for line in stdout.splitlines() if not line.startswith(("model ", "caps ")))
        result["text"] = re.sub(r"<think>.*?(?:</think>|$)", "", answer, flags=re.S | re.I).strip()
        stats = STATS.search(stderr)
        if stats is None:
            result["errors"].append("missing completion status/usage in CLI output")
        else:
            status, finish, rows, cached, tokens = stats.groups()
            result.update(completion_tokens=int(tokens), finish_reason=finish.lower(),
                          prompt_rows=int(rows), cached_rows=int(cached))
            if status.lower() != "ok":
                result["errors"].append(f"generation status {status}")
            if not 0 < int(tokens) <= max_tokens:
                result["errors"].append("zero completion tokens or token cap exceeded")
            if finish.lower() != "stop":
                result["errors"].append(f"generation did not finish naturally: {finish}")
        if not result["text"]:
            result["errors"].append("empty or reasoning-only answer")
        device = next((line for line in stderr.splitlines()
                       if "hexrt open" in line and "v73" in line
                       and ("HEXRT_NSP2=1" in line or re.search(r"\bnsp2=1\b", line))), None)
        if device is None or f"loaded {name} v73" not in stderr:
            result["errors"].append("missing current-request Hexagon v73/two-NSP evidence")
        result["device_evidence"] = device
        for key, pattern, source in (
            ("runtime_ttft_ms", r"\| ttft ([\d.]+) ms", stderr),
            ("runtime_decode_tok_s", r"\| decode \d+ steps ([\d.]+) t/s", stderr),
            ("model_load_ms", r"\| load ([\d.]+) ms", stdout),
        ):
            match = re.search(pattern, source)
            result["timings"][key] = float(match.group(1)) if match else None
    except subprocess.TimeoutExpired as exc:
        result["errors"].append(f"CLI timed out after {timeout} seconds")
        for field, value in (("stdout", exc.stdout), ("stderr", exc.stderr)):
            result[field] = value.decode(errors="replace") if isinstance(value, bytes) else value or ""
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["timings"]["wall_s"] = time.perf_counter() - started
    result["ok"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, help="local SDK directory containing bin/ and lib/")
    parser.add_argument("--model", required=True, help="local model parent containing v73/manifest.json")
    parser.add_argument("--prompt", default="Say hello in one short sentence.")
    media = parser.add_mutually_exclusive_group()
    media.add_argument("--image", help="local 512x512 image; the SDK does not resize")
    media.add_argument("--audio", help="local WAV, up to 30 seconds; English ASR system prompt")
    parser.add_argument("--max-tokens", type=int, default=256, help="safety cap, 1..1024; not a target length")
    parser.add_argument("--timeout", type=float, default=120, help="total CLI timeout, including model loading")
    parser.add_argument("--log", type=Path, help="append a flushed JSONL result to this file")
    args = parser.parse_args()
    result = run(args.sdk, args.model, args.prompt, image=args.image, audio=args.audio,
                 max_tokens=args.max_tokens, timeout=args.timeout)
    if args.log:
        try:
            with args.log.expanduser().open("a") as log:
                log.write(json.dumps(result) + "\n")
                log.flush()
        except OSError as exc:
            result["errors"].append(f"could not write log: {exc}")
            result["ok"] = False
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
