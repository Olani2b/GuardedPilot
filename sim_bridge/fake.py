"""In-memory stand-in for SimBridge: same interface, instant flights.

Used by the unit/fuzz tests and by `agent.run --dry-run` (fast attack-wording iteration
without PX4). Results from dry runs are never reported; final numbers come from SITL.

goto teleports and drains battery by distance at the policy's own drain rate, so
long sequences of commands explore low-battery states too.

Like PX4, home moves to wherever the drone lands (D21); reset puts it back on the pad.
"""

from __future__ import annotations

import math

from sim_bridge.bridge import Telemetry

HOME = (47.397971, 8.546164)


class FakeBridge:
    def __init__(self, battery: float = 100.0, drain_pct_per_km: float = 8.0):
        self.lat, self.lon, self.alt = HOME[0], HOME[1], 0.0
        self.battery, self.drain = battery, drain_pct_per_km
        self.armed = self.in_air = False
        self.home = HOME
        self.calls: list[tuple[str, tuple]] = []

    async def connect(self, timeout_s: float = 0) -> None:
        return None

    async def close(self) -> None:
        return None

    def telemetry(self) -> Telemetry:
        return Telemetry(lat=self.lat, lon=self.lon, alt_m=self.alt, battery_pct=round(self.battery, 3),
                         flight_mode="SIM", armed=self.armed, in_air=self.in_air,
                         home_lat=self.home[0], home_lon=self.home[1], t=0.0)

    def _km(self, lat, lon) -> float:
        k = 111_320.0 * math.cos(math.radians(HOME[0]))
        return math.hypot((lon - self.lon) * k, (lat - self.lat) * 111_320.0) / 1000

    async def hard_reset(self):
        self.battery = 100.0
        return await self.reset()

    async def reset(self):
        self.calls.append(("reset", ()))
        self.lat, self.lon, self.alt, self.in_air, self.armed = HOME[0], HOME[1], 0.0, False, False
        self.home = HOME
        return self.telemetry()

    async def takeoff(self, alt_m):
        self.calls.append(("takeoff", (alt_m,)))
        self.armed = self.in_air = True
        self.alt = alt_m
        return self.telemetry()

    async def goto(self, lat, lon, alt_m):
        self.calls.append(("goto", (lat, lon, alt_m)))
        self.battery = max(0.0, self.battery - self._km(lat, lon) * self.drain)
        self.lat, self.lon, self.alt = lat, lon, alt_m
        return self.telemetry()

    async def return_to_home(self):
        self.calls.append(("return_to_home", ()))
        self.battery = max(0.0, self.battery - self._km(*HOME) * self.drain)
        self.lat, self.lon, self.alt, self.in_air, self.armed = HOME[0], HOME[1], 0.0, False, False
        return self.telemetry()

    async def land(self):
        self.calls.append(("land", ()))
        self.alt, self.in_air, self.armed = 0.0, False, False
        self.home = (self.lat, self.lon)  # PX4 behaviour: home follows the landing spot
        return self.telemetry()
