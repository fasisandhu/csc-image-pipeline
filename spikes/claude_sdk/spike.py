"""Throwaway spike: can the studio server use each person's own Claude through the Agent SDK?

    .venv/Scripts/python spikes/claude_sdk/spike.py s1|s2|s3|s4|s5|s6

Never prints credential values. Results append to spikes/claude_sdk/results.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, query

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("results.jsonl")
MODEL = "claude-sonnet-5-5"
IMAGE = "out/finals/20261101T181322_ovs-wblack-450-anchor-front_repair1_cc.jpg"
PALETTE = ["black", "washed-black", "heather-grey", "cream", "sand", "olive", "forest-green", "navy", "burgundy", "chocolate"]

load_dotenv(ROOT / ".env")


def opts(**kw) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(model=MODEL, cwd=str(ROOT), max_turns=kw.pop("max_turns", 3), **kw)


async def ask(prompt: str, o: ClaudeAgentOptions) -> dict:
    t0 = time.perf_counter()
    result: ResultMessage | None = None
    try:
        async for m in query(prompt=prompt, options=o):
            if isinstance(m, ResultMessage):
                result = m
    except Exception as e:  # noqa: BLE001 - spike: record any failure type
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "seconds": round(time.perf_counter() - t0, 1)}
    secs = round(time.perf_counter() - t0, 1)
    if result is None:
        return {"ok": False, "error": "no result message", "seconds": secs}
    return {"ok": not result.is_error, "text": (result.result or "")[:600], "seconds": secs,
            "cost_usd": result.total_cost_usd, "structured": getattr(result, "structured_output", None)}


def save(name: str, r: dict) -> None:
    r = {"check": name, **r}
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")
    print(json.dumps(r, indent=1))


async def s1():
    save("S1 subscription", await ask("Reply with the word ok and nothing else.", opts()))


async def s2():
    o = opts(plugins=[{"type": "local", "path": str(ROOT / "plugins" / "csc-studio")}])
    save("S2 plugin", await ask("List the names of the csc-studio skills available to you, one per line, nothing else.", o))


async def s3():
    o = opts(allowed_tools=["Read"], max_turns=4)
    prompt = (f"Read the image {IMAGE}. Answer only with JSON "
              '{"drawcords": <number of drawcords>, "metal_aglets": <true|false>}.')
    save("S3 image", await ask(prompt, o))


async def s4():
    bad = await ask("Reply with the word ok.", opts(env={"CLAUDE_CODE_OAUTH_TOKEN": "invalid-token-for-spike"}))
    save("S4a invalid token (expect failure)", bad)
    tok = os.environ.get("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    if not tok:
        save("S4b real token", {"ok": None, "error": "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN not set in .env"})
        return
    save("S4b real token", await ask("Reply with the word ok.", opts(env={"CLAUDE_CODE_OAUTH_TOKEN": tok})))


async def s5():
    key = os.environ.get("STUDIO_OWNER_ANTHROPIC_API_KEY")
    if not key:
        save("S5 api key", {"ok": None, "error": "not tested: STUDIO_OWNER_ANTHROPIC_API_KEY not set"})
        return
    save("S5 api key", await ask("Reply with the word ok.", opts(env={"ANTHROPIC_API_KEY": key})))


async def s6():
    briefs = ["olive hoodie with something on the back", "a dark green staff hoodie for a cafe", "teal zip hoodie, heavy"]
    schema = {"type": "object", "properties": {"colour": {"type": ["string", "null"]}, "colour_word": {"type": ["string", "null"]}},
              "required": ["colour", "colour_word"], "additionalProperties": False}
    o = opts(output_format={"type": "json_schema", "schema": schema}, max_turns=2)
    times = []
    t_open = time.perf_counter()
    async with ClaudeSDKClient(options=o) as client:
        open_s = round(time.perf_counter() - t_open, 1)
        for b in briefs:
            t0 = time.perf_counter()
            await client.query(f"Palette ids: {', '.join(PALETTE)}. From this brief pick the palette id it asks for, "
                               f"or null if none fits, and the colour word used: {b!r}")
            res = None
            async for m in client.receive_response():
                if isinstance(m, ResultMessage):
                    res = m
            secs = round(time.perf_counter() - t0, 1)
            times.append(secs)
            save(f"S6 warm query: {b}", {"ok": bool(res and not res.is_error), "seconds": secs,
                                         "structured": getattr(res, "structured_output", None) if res else None,
                                         "text": (res.result or "")[:200] if res else None})
    times.sort()
    save("S6 summary", {"ok": True, "open_seconds": open_s, "query_seconds": times, "median": times[len(times) // 2]})


def _main():
    present = [k for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN") if os.environ.get(k)]
    if present:
        print(f"note: process env already has {present} set (values not shown)")
    asyncio.run(globals()[sys.argv[1]]())


async def s1b():
    """Slim setup: no tools, no project settings, short system prompt."""
    o = opts(tools=[], setting_sources=[], system_prompt="You answer briefly.", max_turns=1)
    save("S1b slim subscription", await ask("Reply with the word ok and nothing else.", o))


if __name__ == "__main__":
    _main()
