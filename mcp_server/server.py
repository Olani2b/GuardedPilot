"""GuardedPilot MCP server.

Read tools go straight to the runtime. Actuating tools go through
Runtime.actuate(), which calls policy.check() before the bridge. This module must
never call a SimBridge actuating method directly (tests/test_import_rule.py).

Run over HTTP for the MCP Inspector:  uv run python -m mcp_server.server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from mcp_server.runtime import Runtime

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ACT = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
ACT_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
EGRESS = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def build_server(rt: Runtime) -> MCPServer:
    mcp = MCPServer(
        "guardedpilot",
        instructions="Drone survey tools. Actuating tools are checked by a safety policy; "
                     "a blocked call returns {blocked, rule, detail}.",
    )

    def _check_mission(mission_id: str) -> str | None:
        if rt.mission is None:
            return "no mission loaded"
        if mission_id != rt.mission["id"]:
            return f"unknown mission_id {mission_id!r}"
        return None

    # ---- read ---------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def get_telemetry() -> dict:
        """Current position (lat, lon), altitude above home in metres, battery %, armed and in_air flags."""
        t = rt.bridge.telemetry()
        out = {"lat": round(t.lat, 7), "lon": round(t.lon, 7), "alt_m": t.alt_m,
               "battery_pct": t.battery_pct, "armed": t.armed, "in_air": t.in_air,
               "home": [round(t.home_lat, 7), round(t.home_lon, 7)]}
        rt.log(kind="read", tool="get_telemetry")
        return rt.filter_read("get_telemetry", out)

    @mcp.tool(annotations=READ)
    def get_mission_brief(mission_id: str) -> dict:
        """The operator's mission brief: purpose, altitude, survey area and waypoints."""
        if err := _check_mission(mission_id):
            return {"error": err}
        m = rt.mission
        rt.log(kind="read", tool="get_mission_brief", args={"mission_id": mission_id})
        return rt.filter_read("get_mission_brief", {
            "mission_id": m["id"], "brief": m["brief"], "altitude_m": m["altitude_m"],
            "area": m["area"], "waypoints": m["waypoints"], "report_recipient": m.get("report_recipient")})

    @mcp.tool(annotations=READ)
    def get_weather() -> dict:
        """Current weather at the survey site: wind, gusts, visibility, precipitation."""
        rt.log(kind="read", tool="get_weather")
        return rt.filter_read("get_weather", dict(rt.weather))

    @mcp.tool(annotations=READ)
    def get_camera_frame() -> dict:
        """Capture a downward camera frame at the current pose. Returns a frame_id for segment_scene/describe_scene."""
        fr = rt.capture()
        rt.log(kind="read", tool="get_camera_frame", frame_id=fr.frame_id, footprint_m=fr.footprint_m)
        return {"frame_id": fr.frame_id, "pose": fr.pose, "footprint_m": fr.footprint_m}

    @mcp.tool(annotations=READ)
    def segment_scene(frame_id: str) -> dict:
        """Semantic segmentation of a frame: per-class pixel fractions, vehicle count, landing pad visibility."""
        fr = rt.frames.get(frame_id)
        if fr is None:
            return {"error": f"unknown frame_id {frame_id!r}"}
        rt.log(kind="read", tool="segment_scene", args={"frame_id": frame_id})
        # Phase 1 stub: ground truth from the drawn world. The UAVid model replaces this in the buffer.
        return rt.filter_read("segment_scene", {"frame_id": frame_id, **fr.truth})

    @mcp.tool(annotations=READ)
    async def describe_scene(frame_id: str) -> dict:
        """Natural-language description of a frame from a vision model, including any visible text."""
        fr = rt.frames.get(frame_id)
        if fr is None:
            return {"error": f"unknown frame_id {frame_id!r}"}
        text = await rt.describe(fr)
        signs = rt.world.texts_in_view(fr.pose["lat"], fr.pose["lon"], fr.pose["alt"])
        rt.log(kind="read", tool="describe_scene", args={"frame_id": frame_id}, output=text,
               scene_text_in_view=[t.text for t in signs])
        return rt.filter_read("describe_scene", {"frame_id": frame_id, "description": text})

    # ---- actuate (all via rt.actuate -> policy.check) -------------------------

    @mcp.tool(annotations=ACT)
    async def takeoff(alt_m: float) -> dict:
        """Arm and climb to alt_m metres above home. Blocks until the altitude is reached."""
        return await rt.actuate("takeoff", {"alt_m": alt_m})

    @mcp.tool(annotations=ACT)
    async def goto(lat: float, lon: float, alt_m: float) -> dict:
        """Fly to (lat, lon) at alt_m metres above home. Blocks until arrival."""
        return await rt.actuate("goto", {"lat": lat, "lon": lon, "alt_m": alt_m})

    @mcp.tool(annotations=ACT_SAFE)
    async def return_to_home() -> dict:
        """Fly back to home and land. Blocks until landed and disarmed."""
        return await rt.actuate("return_to_home", {})

    @mcp.tool(annotations=ACT)
    async def land() -> dict:
        """Land at the current position. Blocks until on the ground."""
        return await rt.actuate("land", {})

    @mcp.tool(annotations=EGRESS)
    async def send_report(text: str, recipient: str) -> dict:
        """Send a mission report to a recipient."""
        return await rt.actuate("send_report", {"text": text, "recipient": recipient})

    return mcp


if __name__ == "__main__":
    import asyncio

    from sim_bridge.bridge import SimBridge

    async def main():
        bridge = SimBridge()
        await bridge.connect()
        rt = Runtime(bridge)
        await rt.start_episode("missions/survey_basic.yaml")
        server = build_server(rt)
        await server.run_streamable_http_async(host="127.0.0.1", port=8765, stateless_http=True)

    asyncio.run(main())
