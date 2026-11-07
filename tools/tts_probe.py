"""Compares the two ways LiveKit can fetch Deepgram TTS audio, from THIS machine.

  WebSocket : what your agent uses today (text sent word by word, then Flush)
  HTTP      : one request per sentence, audio streamed back

For each it prints: time to first audio, total time, seconds of audio produced,
and a real-time factor (total time / audio seconds). Below 1.0 means audio is
produced faster than it plays; above 1.0 the caller hears stutter and gaps.

Run from the repo root:   python tools/tts_probe.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import settings  # noqa: E402

BASE = os.environ.get("DEEPGRAM_SPEAK_BASE", "https://api.deepgram.com/v1/speak")
MODEL = getattr(settings, "DEEPGRAM_TTS_MODEL", "aura-2-luna-en")
RATE = 24000
BYTES_PER_SEC = RATE * 2  # linear16 mono
TEXT = ("I don't have a Dr. Aymer Khan listed at our clinic, but we do have Dr. Ahmed Khan, "
        "who is a general practitioner. Would you like to see Dr. Ahmed Khan, "
        "or perhaps one of our other specialists?")
RUNS = 2
HEADERS = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}


async def via_websocket(session):
    url = f"{BASE.replace('http', 'ws', 1)}?" + urlencode(
        {"encoding": "linear16", "model": MODEL, "sample_rate": RATE, "mip_opt_out": "false"})
    t0 = time.perf_counter()
    first, size = None, 0
    async with session.ws_connect(url, headers=HEADERS) as ws:
        for word in TEXT.split():                       # same as the LiveKit plugin
            await ws.send_str(json.dumps({"type": "Speak", "text": f"{word} "}))
        await ws.send_str(json.dumps({"type": "Flush"}))
        while True:
            msg = await ws.receive(timeout=30)
            if msg.type == aiohttp.WSMsgType.BINARY:
                first = first or time.perf_counter() - t0
                size += len(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                if json.loads(msg.data).get("type") == "Flushed":
                    break
            else:
                raise RuntimeError(f"socket closed early: {msg.type}")
    return first, time.perf_counter() - t0, size


async def via_http(session):
    url = f"{BASE}?" + urlencode(
        {"encoding": "linear16", "container": "none", "model": MODEL,
         "sample_rate": RATE, "mip_opt_out": "false"})
    t0 = time.perf_counter()
    first, size = None, 0
    async with session.post(url, headers={**HEADERS, "Content-Type": "application/json"},
                            json={"text": TEXT}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        async for chunk, _ in resp.content.iter_chunks():
            if chunk:
                first = first or time.perf_counter() - t0
                size += len(chunk)
    return first, time.perf_counter() - t0, size


async def main():
    if not settings.DEEPGRAM_API_KEY and "api.deepgram.com" in BASE:
        sys.exit("DEEPGRAM_API_KEY is not set")
    print(f"Voice {MODEL}, {RUNS} runs each\n")
    print(f"{'path':<10}{'first audio':>13}{'total':>9}{'audio':>9}{'real-time factor':>19}")
    async with aiohttp.ClientSession() as session:
        for name, fn in (("WebSocket", via_websocket), ("HTTP", via_http)):
            for _ in range(RUNS):
                try:
                    first, total, size = await fn(session)
                except Exception as exc:
                    print(f"{name:<10}FAILED: {type(exc).__name__}: {exc}")
                    continue
                audio = size / BYTES_PER_SEC
                print(f"{name:<10}{(first or 0) * 1000:>10.0f} ms{total:>7.1f} s{audio:>7.1f} s"
                      f"{total / audio if audio else 0:>19.2f}")


if __name__ == "__main__":
    asyncio.run(main())