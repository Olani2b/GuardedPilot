"""Smoke test: take off, fly a 40 m square at 20 m, return home. Repeat N times.

Usage: uv run python scripts/fly_square.py [runs]
"""

import asyncio
import math
import sys
import time

from sim_bridge.bridge import SimBridge, haversine_m

SIDE_M = 40.0
ALT_M = 20.0


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


async def one_run(b: SimBridge, i: int) -> bool:
    t0 = time.monotonic()
    await b.reset()
    home = b.telemetry()
    await b.takeoff(ALT_M)
    for n, e in [(SIDE_M, 0), (SIDE_M, SIDE_M), (0, SIDE_M), (0, 0)]:
        lat, lon = offset(home.home_lat, home.home_lon, n, e)
        await b.goto(lat, lon, ALT_M)
    end = await b.return_to_home()
    err = haversine_m(end.lat, end.lon, end.home_lat, end.home_lon)
    ok = not end.in_air and err < 3.0
    print(f"run {i}: {'OK' if ok else 'FAIL'}  landed {err:.1f} m from home, "
          f"battery {end.battery_pct}%, {time.monotonic() - t0:.0f}s")
    return ok


async def main(runs: int) -> int:
    b = SimBridge()
    print("connecting...")
    await b.connect()
    print("connected:", b.telemetry().to_dict())
    passed = 0
    for i in range(1, runs + 1):
        try:
            passed += await one_run(b, i)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"run {i}: FAIL {type(e).__name__}: {e}")
    print(f"{passed}/{runs} passed")
    await b.close()
    return 0 if passed == runs else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)))
