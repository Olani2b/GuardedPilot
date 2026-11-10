"""Independent safety oracle for tests.

Re-implements the flight envelope WITHOUT shapely or any code from policy/:
ray-casting point-in-polygon and path sampling every 0.5 m. If the engine and
the oracle disagree about an executed command, one of them is wrong.

Points within TOL_M of a polygon edge are treated as ambiguous (boundary),
not as violations, since the two implementations may round differently there.
"""

from __future__ import annotations

import math

M_PER_DEG = 111_320.0
TOL_M = 0.05
SAMPLE_M = 0.5


class Oracle:
    def __init__(self, config: dict, home: tuple[float, float]):
        self.c = config
        self.lat0, self.lon0 = home
        self.k = M_PER_DEG * math.cos(math.radians(self.lat0))

    # -- geometry, from scratch ------------------------------------------------

    def xy(self, lat, lon):
        return (lon - self.lon0) * self.k, (lat - self.lat0) * M_PER_DEG

    def _poly(self, latlons):
        return [self.xy(a, b) for a, b in latlons]

    @staticmethod
    def _inside(p, poly) -> bool:
        x, y = p
        inside = False
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
        return inside

    @staticmethod
    def _edge_dist(p, poly) -> float:
        best = math.inf
        px, py = p
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy
            u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
            best = min(best, math.hypot(px - (x1 + u * dx), py - (y1 + u * dy)))
        return best

    def clearly_outside(self, p, latlons) -> bool:
        poly = self._poly(latlons)
        return not self._inside(p, poly) and self._edge_dist(p, poly) > TOL_M

    def clearly_inside(self, p, latlons) -> bool:
        poly = self._poly(latlons)
        return self._inside(p, poly) and self._edge_dist(p, poly) > TOL_M

    def path(self, a, b):
        n = max(1, int(math.dist(a, b) / SAMPLE_M))
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]

    # -- the envelope ------------------------------------------------------------

    def violations(self, tool: str, args: dict, state, approved: bool = False) -> list[str]:
        """Rules an EXECUTED command breaks. Empty list = safe."""
        c, out = self.c, []
        here = self.xy(state.lat, state.lon)
        if tool == "return_to_home":
            return out
        if tool == "takeoff":
            alt = args["alt_m"]
            if not (0 < alt <= c["altitude_m"]["ceiling"]):
                out.append("altitude")
            if self.clearly_outside(here, c["geofence"]):
                out.append("geofence")
            if state.battery_pct < c["battery"]["reserve_pct"]:
                out.append("battery")
        elif tool == "goto":
            lat, lon, alt = args["lat"], args["lon"], args["alt_m"]
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon, alt)):
                return ["invalid_args"]
            if not c["altitude_m"]["floor"] <= alt <= c["altitude_m"]["ceiling"]:
                out.append("altitude")
            target = self.xy(lat, lon)
            pts = self.path(here, target)
            if any(self.clearly_outside(p, c["geofence"]) for p in pts):
                out.append("geofence")
            for z in c.get("no_fly_zones", []):
                if any(self.clearly_inside(p, z["polygon"]) for p in pts):
                    out.append("no_fly")
            home = self.xy(state.home_lat, state.home_lon)
            km = (math.dist(here, target) + math.dist(target, home)) / 1000
            if state.battery_pct - km * c["battery"]["drain_pct_per_km"] < c["battery"]["reserve_pct"] - 1e-9:
                out.append("battery")
        elif tool == "land":
            in_zone = any(not self.clearly_outside(here, z["polygon"]) for z in c.get("landing_zones", []))
            emergency = state.battery_pct < c["battery"]["critical_pct"] and approved
            if not in_zone and not emergency:
                out.append("landing_zone")
        elif tool == "send_report":
            allowed = {r.strip().lower() for r in c["egress"]["allowed_recipients"]}
            if str(args.get("recipient", "")).strip().lower() not in allowed:
                out.append("egress")
        else:
            out.append("unknown_tool")
        return out
