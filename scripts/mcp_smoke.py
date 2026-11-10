"""Smoke test: every MCP tool once, in-process, against the live simulator."""

import asyncio
import json
import os
import sys

from mcp import Client

from mcp_server.runtime import Runtime
from mcp_server.server import build_server
from sim_bridge.bridge import SimBridge


def show(name, res):
    body = res.structured_content if res.structured_content is not None else [c.text for c in res.content]
    print(f"{name:18} -> {json.dumps(body)[:300]}", flush=True)


async def main():
    bridge = SimBridge()
    await bridge.connect()
    rt = Runtime(bridge)
    await rt.start_episode("missions/survey_basic.yaml")
    async with Client(build_server(rt), read_timeout_seconds=600) as c:
        tools = await c.list_tools()
        print("tools:", [(t.name, "ro" if t.annotations.read_only_hint else "ACT") for t in tools.tools])
        show("get_telemetry", await c.call_tool("get_telemetry"))
        show("get_mission_brief", await c.call_tool("get_mission_brief", {"mission_id": "survey_basic"}))
        show("takeoff", await c.call_tool("takeoff", {"alt_m": 15}))
        wp = rt.mission["waypoints"][0]
        show("goto", await c.call_tool("goto", {"lat": wp[0], "lon": wp[1], "alt_m": 15}))
        show("get_camera_frame", await c.call_tool("get_camera_frame"))
        show("segment_scene", await c.call_tool("segment_scene", {"frame_id": "f001"}))
        show("describe_scene", await c.call_tool("describe_scene", {"frame_id": "f001"}))
        show("send_report", await c.call_tool("send_report", {"text": "ok", "recipient": "ops@guardedpilot.local"}))
        show("return_to_home", await c.call_tool("return_to_home"))
    await rt.end_episode()
    print(f"events: {len(rt.events)}, track samples: {len(rt.track)}")
    for e in rt.events:
        if e["kind"] == "actuate":
            print("  ", e["t"], e["tool"], e["policy"]["decision"], "executed" if e["executed"] else "NOT executed")
    await bridge.close()


asyncio.run(main())
sys.stdout.flush()
os._exit(0)
