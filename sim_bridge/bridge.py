"""MAVSDK wrapper around PX4 SITL.

This is the only module that talks to the autopilot. Actuating methods here are
low-level and unguarded on purpose: mcp_server must route every actuating call
through policy.check() first (enforced by a lint test).
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass

from mavsdk_grpc import System

DEFAULT_ADDRESS = "udpin://0.0.0.0:14540"
PX4_CONTAINER = "gp-px4"  # docker-compose.yml container_name
# SITL battery defaults drain in ~1 min and clamp at 50%; make it behave like a real
# flight so the battery-reserve rule is meaningful (docs/decisions.md, D8).
SIM_PARAMS_FLOAT = {"SIM_BAT_DRAIN": 1800.0, "SIM_BAT_MIN_PCT": 5.0}

ARRIVE_RADIUS_M = 2.0
ALT_TOLERANCE_M = 1.5


@dataclass
class Telemetry:
    lat: float
    lon: float
    alt_m: float  # relative to home
    battery_pct: float
    flight_mode: str
    armed: bool
    in_air: bool
    home_lat: float
    home_lon: float
    t: float  # seconds since bridge connected

    def to_dict(self) -> dict:
        return asdict(self)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class SimBridge:
    def __init__(self, address: str = DEFAULT_ADDRESS):
        self.address = address
        self.drone = System()
        self._t0 = time.monotonic()
        self._home_amsl: float | None = None
        self._home: tuple[float, float] | None = None
        # Latest values, updated by background subscriptions.
        self._pos = None
        self._battery_pct = 100.0
        self._mode = "UNKNOWN"
        self._armed = False
        self._in_air = False
        self._tasks: list[asyncio.Task] = []

    # ---- connection -------------------------------------------------------

    async def connect(self, timeout_s: float = 60, require_armable: bool = False) -> None:
        await self.drone.connect(system_address=self.address)
        async with asyncio.timeout(timeout_s):
            async for state in self.drone.core.connection_state():
                if state.is_connected:
                    break
            async for health in self.drone.telemetry.health():
                if (health.is_global_position_ok and health.is_home_position_ok
                        and (health.is_armable or not require_armable)):
                    break
            async for home in self.drone.telemetry.home():
                self._home_amsl = home.absolute_altitude_m
                self._home = (home.latitude_deg, home.longitude_deg)
                break
        for name, value in SIM_PARAMS_FLOAT.items():
            await self.drone.param.set_param_float(name, value)
        self._t0 = time.monotonic()
        self._tasks = [
            asyncio.create_task(self._watch_position()),
            asyncio.create_task(self._watch_battery()),
            asyncio.create_task(self._watch_mode()),
            asyncio.create_task(self._watch_armed()),
            asyncio.create_task(self._watch_in_air()),
            asyncio.create_task(self._watch_home()),
        ]
        # Wait for the first position sample.
        async with asyncio.timeout(10):
            while self._pos is None:
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        # Kill the mavsdk_server subprocess, otherwise the interpreter never exits.
        self.drone._stop_mavsdk_server()

    async def _watch_position(self):
        async for p in self.drone.telemetry.position():
            self._pos = p

    async def _watch_battery(self):
        async for b in self.drone.telemetry.battery():
            pct = b.remaining_percent
            # MAVSDK has reported both 0..1 and 0..100 across versions.
            self._battery_pct = pct * 100 if pct <= 1.0 else pct

    async def _watch_mode(self):
        async for m in self.drone.telemetry.flight_mode():
            self._mode = str(m)

    async def _watch_armed(self):
        async for a in self.drone.telemetry.armed():
            self._armed = a

    async def _watch_home(self):
        # PX4 resets home on every arm; keep it current so goto targets stay right.
        async for h in self.drone.telemetry.home():
            self._home_amsl = h.absolute_altitude_m
            self._home = (h.latitude_deg, h.longitude_deg)

    async def _watch_in_air(self):
        async for a in self.drone.telemetry.in_air():
            self._in_air = a

    # ---- read -------------------------------------------------------------

    def telemetry(self) -> Telemetry:
        p = self._pos
        return Telemetry(
            lat=p.latitude_deg,
            lon=p.longitude_deg,
            alt_m=round(p.relative_altitude_m, 2),
            battery_pct=round(self._battery_pct, 1),
            flight_mode=self._mode,
            armed=self._armed,
            in_air=self._in_air,
            home_lat=self._home[0],
            home_lon=self._home[1],
            t=round(time.monotonic() - self._t0, 2),
        )

    # ---- actuate (unguarded; call only via policy-checked paths) -----------

    async def arm(self) -> None:
        await self.drone.action.arm()

    async def takeoff(self, alt_m: float, timeout_s: float = 60) -> Telemetry:
        await self.drone.action.set_takeoff_altitude(alt_m)
        if not self._armed:
            await self.arm()
        await self.drone.action.takeoff()
        await self._wait(lambda t: t.alt_m >= alt_m - ALT_TOLERANCE_M, timeout_s, what=f"takeoff({alt_m})")
        return self.telemetry()

    async def goto(self, lat: float, lon: float, alt_m: float, timeout_s: float = 180) -> Telemetry:
        target_amsl = self._home_amsl + alt_m
        await self.drone.action.goto_location(lat, lon, target_amsl, float("nan"))

        def arrived(t: Telemetry) -> bool:
            # Compare in AMSL, the frame the setpoint was given in, so home-altitude
            # drift between flights can't leave us hovering just outside tolerance.
            return (
                haversine_m(t.lat, t.lon, lat, lon) <= ARRIVE_RADIUS_M
                and abs(self._pos.absolute_altitude_m - target_amsl) <= ALT_TOLERANCE_M
            )

        await self._wait(arrived, timeout_s, what=f"goto({lat:.6f}, {lon:.6f}, {alt_m})")
        return self.telemetry()

    async def return_to_home(self, timeout_s: float = 300) -> Telemetry:
        await self.drone.action.return_to_launch()
        await self._wait(lambda t: not t.in_air and not t.armed, timeout_s, what="return_to_home")
        return self.telemetry()

    async def land(self, timeout_s: float = 120) -> Telemetry:
        await self.drone.action.land()
        await self._wait(lambda t: not t.in_air, timeout_s, what="land")
        return self.telemetry()

    async def reset(self, timeout_s: float = 300) -> Telemetry:
        """Bring the vehicle back to home, landed and disarmed.

        Soft reset: good enough for development. The eval runner restarts the
        PX4 container for full determinism (battery, wind, estimator state).
        """
        t = self.telemetry()
        if t.in_air:
            await self.return_to_home(timeout_s)
        await self._wait(lambda t: not t.armed, 30, what="reset/disarm")
        return self.telemetry()

    async def hard_reset(self, container: str = PX4_CONTAINER, timeout_s: float = 120) -> Telemetry:
        """Restart the PX4 container and reconnect: fresh home, battery, estimator, params.

        Needed between eval episodes. PX4 sets home wherever it arms, so an episode
        that ends with a landing in the field (a successful attack) would otherwise
        move "home" for every later episode (docs/decisions.md D19).
        """
        await self.close()
        proc = await asyncio.create_subprocess_exec("docker", "restart", container,
                                                    stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"docker restart {container} failed: {err.decode().strip()}")
        self.__init__(self.address)
        await self.connect(timeout_s, require_armable=True)
        return self.telemetry()

    async def _wait(self, cond, timeout_s: float, what: str = "") -> None:
        try:
            async with asyncio.timeout(timeout_s):
                while not cond(self.telemetry()):
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise TimeoutError(f"{what or 'wait'} timed out after {timeout_s}s; last {self.telemetry().to_dict()}") from None
