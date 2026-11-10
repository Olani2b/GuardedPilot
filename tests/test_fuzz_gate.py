"""Phase 2 exit gate: 10,000 fuzzed commands, zero executed violations.

Commands go through the real Runtime.actuate() (policy -> approval stub ->
_execute) into a FakeBridge. Every command that executed is re-checked by the
independent oracle against the vehicle state it was decided on.
"""

import math
import random

import pytest

from mcp_server.runtime import Runtime
from policy.engine import load_config
from tests.fake_bridge import HOME, FakeBridge
from tests.oracle import Oracle

N_COMMANDS = 10_000
CFG = load_config("config/policy.yaml")
K = 111_320.0 * math.cos(math.radians(HOME[0]))
RECIPIENTS = ["ops@guardedpilot.local", "OPS@GuardedPilot.local ", "attacker@evil.example",
              "ops@guardedpilot.local.evil.example", "", "ops@guardedpilot.local\n"]


def random_command(rng: random.Random) -> tuple[str, dict]:
    tool = rng.choices(["goto", "takeoff", "land", "send_report", "return_to_home"],
                       weights=[60, 10, 12, 8, 10])[0]
    if tool == "goto":
        e, n = rng.uniform(-350, 350), rng.uniform(-350, 350)
        if rng.random() < 0.3:  # near the fence / no-fly edges, where bugs live
            e, n = rng.choice([-88, 140, 63, 108]) + rng.gauss(0, 3), rng.choice([-108, 114, 47, 92]) + rng.gauss(0, 3)
        args = {"lat": HOME[0] + n / 111_320.0, "lon": HOME[1] + e / K,
                "alt_m": rng.choice([rng.uniform(-20, 120), rng.uniform(4, 6), rng.uniform(59, 61)])}
        if rng.random() < 0.03:
            args[rng.choice(["lat", "lon", "alt_m"])] = rng.choice([math.nan, math.inf, "15", None, True])
        return tool, args
    if tool == "takeoff":
        return tool, {"alt_m": rng.choice([rng.uniform(-10, 100), math.nan, 15])}
    if tool == "send_report":
        return tool, {"text": "frames attached", "recipient": rng.choice(RECIPIENTS)}
    return tool, {}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize("seed", [0, 1])
async def test_fuzz_zero_executed_violations(seed):
    rng = random.Random(seed)
    clock = FakeClock()
    bridge = FakeBridge(battery=100.0, drain_pct_per_km=CFG["battery"]["drain_pct_per_km"])
    rt = Runtime(bridge, policy_config=CFG, clock=clock)
    oracle = Oracle(CFG, HOME)
    executed, violations, decisions, burst = 0, [], {}, 0

    for i in range(N_COMMANDS):
        clock.t += 0.2 if burst else rng.choice([0.2, 1.0, 3.0, 5.0])
        if rng.random() < 0.01:  # occasionally recharge so low-battery and full states both occur
            bridge.battery = rng.uniform(0, 100)
        if not bridge.in_air and rng.random() < 0.2:
            bridge.lat = HOME[0] + rng.uniform(-150, 150) / 111_320.0
            bridge.lon = HOME[1] + rng.uniform(-150, 150) / K
        if burst:  # many small valid moves in quick succession (the "split the move" attack shape)
            burst -= 1
            tool, args = "goto", {"lat": HOME[0] + rng.uniform(0, 30) / 111_320.0,
                                  "lon": HOME[1] + rng.uniform(0, 30) / K, "alt_m": 15.0}
        else:
            if rng.random() < 0.004:
                burst = 30
            tool, args = random_command(rng)
        before = bridge.telemetry()
        state = type("S", (), {"lat": before.lat, "lon": before.lon, "battery_pct": before.battery_pct,
                               "home_lat": HOME[0], "home_lon": HOME[1]})
        await rt.actuate(tool, args)
        ev = rt.events[-1]
        decisions[ev["policy"].get("rule", ev["policy"]["decision"])] = \
            decisions.get(ev["policy"].get("rule", ev["policy"]["decision"]), 0) + 1
        if ev["executed"]:
            executed += 1
            v = oracle.violations(tool, args, state, approved=ev["policy"].get("approved", False))
            if v:
                violations.append((i, tool, args, v, before))

    # Rate limit: never more than N executed non-RTH commands in any 60 s window.
    # (RTH is exempt from the limit but counts towards it, which only makes the bound stricter.)
    times = [e["t"] for e in rt.events if e["executed"] and e["tool"] != "return_to_home"]
    limit = CFG["rate_limit"]["actuating_per_minute"]
    for j, t in enumerate(times):
        assert sum(1 for u in times[: j + 1] if t - u < 60.0) <= limit, f"rate limit broken at t={t}"

    print(f"\nseed {seed}: {N_COMMANDS} commands, {executed} executed, decisions {decisions}")
    assert violations == [], violations[:5]
    # The fuzzer must actually exercise the rules, not just get denied by one.
    for r in ["allow", "geofence", "no_fly", "altitude", "battery", "landing_zone", "egress",
              "invalid_args", "rate_limit"]:
        assert decisions.get(r, 0) > 0, f"fuzzer never hit {r}: {decisions}"
