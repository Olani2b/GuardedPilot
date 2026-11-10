"""Plant a scenario's attacks into a fresh episode.

Called by the runner after Runtime.start_episode(), before the agent starts.
Attacks only touch what the adversary controls in the threat model: the scene
(A1), the retrieved brief (A2) and read-tool outputs (A3). Never the policy,
its config, the operator instruction or the system prompt.
"""

from __future__ import annotations

import math

from perception.world import SceneText, World
from redteam.schema import BriefInjection, Scenario, SceneTextInjection, ToolOutputInjection

M_PER_DEG = 111_320.0


def _offset(lat: float, lon: float, east: float, north: float) -> tuple[float, float]:
    return lat + north / M_PER_DEG, lon + east / (M_PER_DEG * math.cos(math.radians(lat)))


def build_world(sc: Scenario, mission: dict) -> World:
    texts = []
    for inj in sc.injections:
        if isinstance(inj, SceneTextInjection):
            lat, lon = mission["waypoints"][inj.at_waypoint - 1]
            lat, lon = _offset(lat, lon, *inj.offset_m)
            texts.append(SceneText(lat=lat, lon=lon, text=inj.text, width_m=inj.width_m))
    return World(seed=sc.seed, texts=texts)


def apply(sc: Scenario, rt) -> list[dict]:
    """Apply brief and tool-output injections to the runtime. Returns a record of what was planted."""
    planted = []
    for inj in sc.injections:
        if isinstance(inj, SceneTextInjection):
            planted.append({"type": "scene_text", "at_waypoint": inj.at_waypoint, "text": inj.text})
        elif isinstance(inj, BriefInjection):
            brief = rt.mission["brief"].rstrip("\n")
            if inj.mode == "append":
                brief = f"{brief}\n{inj.text}"
            elif inj.mode == "prepend":
                brief = f"{inj.text}\n{brief}"
            else:
                if inj.match not in brief:
                    raise ValueError(f"{sc.id}: replace_line match {inj.match!r} not in brief")
                brief = "\n".join(inj.text if inj.match in line else line for line in brief.split("\n"))
            rt.mission["brief"] = brief + "\n"
            planted.append({"type": "brief", "mode": inj.mode, "text": inj.text})
        elif isinstance(inj, ToolOutputInjection):
            rt.read_filters[inj.tool] = _tamper(inj)
            planted.append({"type": "tool_output", "tool": inj.tool, "field": inj.field, "value": inj.value})
    return planted


def _tamper(inj: ToolOutputInjection):
    calls = {"n": 0}

    def f(result: dict) -> dict:
        calls["n"] += 1
        if calls["n"] > inj.after_calls:
            result[inj.field] = inj.value
        return result

    return f
