"""Deterministic policy engine.

check(command, state, config) -> Allow | Deny | RequireApproval

Pure function: no I/O, no ML, no LLM calls. Inputs are the command the model
asked for (untrusted), vehicle state from telemetry (trusted) and the policy
config (trusted). The model is never asked whether an action is safe.

Rules (all parameters from config/policy.yaml):
  invalid_args   arguments must be finite numbers / non-empty strings
  geofence       goto target and the straight path to it stay inside the fence
  no_fly         goto target and path stay outside every no-fly polygon
  altitude       floor <= alt <= ceiling (takeoff: 0 < alt <= ceiling)
  battery        after flying to the target and back home, >= reserve remains
  landing_zone   land only inside a landing zone; below critical battery,
                 landing elsewhere is an emergency (RequireApproval if listed)
  egress         send_report recipient must be on the allowlist
  rate_limit     at most N actuating commands per 60 s
return_to_home is always allowed: it is the safe action and must never be blocked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from shapely.geometry import LineString, Point, Polygon

ACTUATING_TOOLS = frozenset({"takeoff", "goto", "return_to_home", "land", "send_report"})
M_PER_DEG = 111_320.0


@dataclass(frozen=True)
class Command:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class State:
    """Vehicle state the decision is made against (from telemetry, not from the model)."""

    lat: float
    lon: float
    alt_m: float
    battery_pct: float
    in_air: bool
    home_lat: float
    home_lon: float
    recent_actuations: tuple[float, ...] = ()  # timestamps (s) of earlier allowed actuating commands
    now: float = 0.0


@dataclass(frozen=True)
class Allow:
    decision: Literal["allow"] = "allow"


@dataclass(frozen=True)
class Deny:
    rule: str
    detail: str
    decision: Literal["deny"] = "deny"


@dataclass(frozen=True)
class RequireApproval:
    reason: str
    decision: Literal["require_approval"] = "require_approval"


Decision = Allow | Deny | RequireApproval


def load_config(path: str | Path = "config/policy.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


# ---- geometry (local equirectangular metres around home) ---------------------

class _Frame:
    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.k = M_PER_DEG * math.cos(math.radians(lat0))

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.k, (lat - self.lat0) * M_PER_DEG

    def poly(self, latlons) -> Polygon:
        return Polygon([self.xy(lat, lon) for lat, lon in latlons])


def _num(args: dict, key: str) -> float | None:
    """Strict number: bools, strings, NaN and inf are all rejected."""
    v = args.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


# ---- rules -------------------------------------------------------------------

def _rate_limit(state: State, config: dict) -> Deny | None:
    limit = config["rate_limit"]["actuating_per_minute"]
    recent = [t for t in state.recent_actuations if state.now - t < 60.0]
    if len(recent) >= limit:
        return Deny("rate_limit", f"{len(recent)} actuating commands in the last 60 s (limit {limit})")
    return None


def _check_takeoff(cmd: Command, state: State, config: dict, f: _Frame) -> Decision:
    alt = _num(cmd.args, "alt_m")
    if alt is None:
        return Deny("invalid_args", "alt_m must be a finite number")
    ceiling = config["altitude_m"]["ceiling"]
    if not 0 < alt <= ceiling:
        return Deny("altitude", f"takeoff altitude {alt} m outside (0, {ceiling}] m")
    if not f.poly(config["geofence"]).covers(Point(f.xy(state.lat, state.lon))):
        return Deny("geofence", "vehicle is outside the geofence")
    reserve = config["battery"]["reserve_pct"]
    if state.battery_pct < reserve:
        return Deny("battery", f"battery {state.battery_pct}% below reserve {reserve}%")
    return Allow()


def _check_goto(cmd: Command, state: State, config: dict, f: _Frame) -> Decision:
    lat, lon, alt = (_num(cmd.args, k) for k in ("lat", "lon", "alt_m"))
    if lat is None or lon is None or alt is None:
        return Deny("invalid_args", "lat, lon and alt_m must be finite numbers")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return Deny("invalid_args", f"({lat}, {lon}) is not a valid coordinate")

    floor, ceiling = config["altitude_m"]["floor"], config["altitude_m"]["ceiling"]
    if not floor <= alt <= ceiling:
        return Deny("altitude", f"altitude {alt} m outside [{floor}, {ceiling}] m")

    here, target = Point(f.xy(state.lat, state.lon)), Point(f.xy(lat, lon))
    path = LineString([here.coords[0], target.coords[0]]) if here != target else target
    fence = f.poly(config["geofence"])
    if not fence.covers(target):
        return Deny("geofence", f"target ({lat:.6f}, {lon:.6f}) is outside the geofence")
    if not fence.covers(path):
        return Deny("geofence", "straight-line path to the target leaves the geofence")

    for zone in config.get("no_fly_zones", []):
        nfz = f.poly(zone["polygon"])
        if nfz.intersects(target):
            return Deny("no_fly", f"target is inside no-fly zone '{zone['name']}'")
        if nfz.intersects(path):
            return Deny("no_fly", f"path crosses no-fly zone '{zone['name']}'")

    b = config["battery"]
    home = Point(f.xy(state.home_lat, state.home_lon))
    km = (here.distance(target) + target.distance(home)) / 1000.0
    after = state.battery_pct - km * b["drain_pct_per_km"]
    if after < b["reserve_pct"]:
        return Deny("battery", f"estimated {after:.1f}% left after reaching the target and returning "
                               f"home; reserve is {b['reserve_pct']}%")
    return Allow()


def _check_land(cmd: Command, state: State, config: dict, f: _Frame) -> Decision:
    here = Point(f.xy(state.lat, state.lon))
    for zone in config.get("landing_zones", []):
        if f.poly(zone["polygon"]).covers(here):
            return Allow()
    critical = config["battery"]["critical_pct"]
    if state.battery_pct < critical:
        if "land_outside_zone_emergency" in config.get("approval", {}).get("require_for", []):
            return RequireApproval(f"emergency landing outside a landing zone at {state.battery_pct}% battery")
        return Allow()
    return Deny("landing_zone", "current position is not inside a landing zone")


def _check_send_report(cmd: Command, state: State, config: dict, f: _Frame) -> Decision:
    rcpt, text = cmd.args.get("recipient"), cmd.args.get("text")
    if not isinstance(rcpt, str) or not isinstance(text, str):
        return Deny("invalid_args", "recipient and text must be strings")
    allowed = {r.strip().lower() for r in config["egress"]["allowed_recipients"]}
    if rcpt.strip().lower() not in allowed:
        return Deny("egress", f"recipient {rcpt!r} is not on the allowlist")
    return Allow()


_RULES = {"takeoff": _check_takeoff, "goto": _check_goto, "land": _check_land,
          "send_report": _check_send_report}


def check(command: Command, state: State, config: dict) -> Decision:
    if command.tool not in ACTUATING_TOOLS:
        return Deny("unknown_tool", f"{command.tool!r} is not an actuating tool")
    if command.tool == "return_to_home":
        return Allow()  # never block the safe action, not even by rate limit
    if not isinstance(command.args, dict):
        return Deny("invalid_args", "arguments must be an object")
    if (d := _rate_limit(state, config)) is not None:
        return d
    return _RULES[command.tool](command, state, config, _Frame(state.home_lat, state.home_lon))


def to_dict(d: Decision) -> dict:
    out: dict[str, Any] = {"decision": d.decision}
    if isinstance(d, Deny):
        out |= {"rule": d.rule, "detail": d.detail}
    elif isinstance(d, RequireApproval):
        out |= {"reason": d.reason}
    return out
