"""Times each external service on its own, outside LiveKit.

Run from the repo root:   python tools/latency_probe.py
Makes tiny requests only (a few tokens of LLM, one short TTS sentence).
"""

import json
import socket
import sys
import time
from pathlib import Path
from statistics import median
from urllib import request as urlrequest
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import settings  # noqa: E402

RUNS = 3


def timed(fn):
    t0 = time.perf_counter()
    try:
        fn()
    except Exception as exc:  # report, never crash the probe
        return None, f"{type(exc).__name__}: {exc}"
    return (time.perf_counter() - t0) * 1000, None


def report(name, fn):
    samples, err = [], None
    for _ in range(RUNS):
        ms, err = timed(fn)
        if ms is not None:
            samples.append(ms)
    if samples:
        print(f"{name:<34} median {median(samples):7.0f} ms   all {[round(s) for s in samples]}")
    else:
        print(f"{name:<34} FAILED  {err}")


def tcp_connect(host, port=443):
    def run():
        socket.create_connection((host, port), timeout=10).close()
    return run


def gemini_first_token():
    model = settings.GEMINI_MODEL
    if model == "gemini-3.8-flash":
        model = "gemini-3.5-flash-lite"
    body = json.dumps({
        "model": model, "stream": True, "max_tokens": 20,
        "messages": [{"role": "user", "content": "Say hello in five words."}],
    }).encode()
    req = urlrequest.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {settings.GEMINI_API_KEY}"})

    def run():
        with urlrequest.urlopen(req, timeout=30) as resp:
            resp.readline()  # first streamed chunk = time to first token
    return run


def deepgram_tts_first_byte():
    model = getattr(settings, "DEEPGRAM_TTS_MODEL", "aura-2-luna-en")
    body = json.dumps({"text": "Thank you for calling the clinic."}).encode()
    req = urlrequest.Request(
        f"https://api.deepgram.com/v1/speak?model={model}",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"})

    def run():
        with urlrequest.urlopen(req, timeout=30) as resp:
            resp.read(1024)  # first bytes of audio
    return run


if __name__ == "__main__":
    print(f"Probe from this machine, {RUNS} runs each (lower is better)\n")
    lk_host = urlparse(settings.LIVEKIT_URL.replace("wss://", "https://")).hostname
    if lk_host:
        report("TCP connect to LiveKit", tcp_connect(lk_host))
    report("TCP connect to Deepgram", tcp_connect("api.deepgram.com"))
    report("TCP connect to Gemini", tcp_connect("generativelanguage.googleapis.com"))
    if settings.GEMINI_API_KEY:
        report("Gemini: time to first token", gemini_first_token())
    if settings.DEEPGRAM_API_KEY:
        report("Deepgram TTS: time to first audio", deepgram_tts_first_byte())