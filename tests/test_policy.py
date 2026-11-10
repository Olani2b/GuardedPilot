"""Policy engine: one test per rule branch, plus property-based tests against an
independent oracle (tests/oracle.py)."""

import copy
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from policy.engine import (
    Allow,
    Command,
    Deny,
    RequireApproval,
    State,
    check,
    load_config,
    to_dict,
)
from tests.oracle import Oracle

HOME = (47.397971, 8.546164)
CFG = load_config("config/policy.yaml")
K = 111_320.0 * math.cos(math.radians(HOME[0]))


def ll(east_m: float, north_m: float) -> tuple[float, float]:
    """lat, lon of a point east/north of home, in metres."""
    return HOME[0] + north_m / 111_320.0, HOME[1] + east_m / K


def st_at(east=0.0, north=0.0, alt=15.0, battery=90.0, in_air=True, recent=(), now=1000.0) -> State:
    lat, lon = ll(east, north)
    return State(lat=lat, lon=lon, alt_m=alt, battery_pct=battery, in_air=in_air,
                 home_lat=HOME[0], home_lon=HOME[1], recent_actuations=tuple(recent), now=now)


def goto(east, north, alt=15.0):
    lat, lon = ll(east, north)
    return Command("goto", {"lat": lat, "lon": lon, "alt_m": alt})


def rule(d):
    return d.rule if isinstance(d, Deny) else d.decision


# ---- dispatch ------------------------------------------------------------------

def test_unknown_tool_denied():
    assert rule(check(Command("disable_policy"), st_at(), CFG)) == "unknown_tool"


def test_rth_always_allowed_even_when_everything_else_would_deny():
    s = st_at(east=5000, battery=0.0, recent=[999.0] * 100)
    assert check(Command("return_to_home"), s, CFG) == Allow()


def test_non_dict_args_denied():
    assert rule(check(Command("goto", ["x"]), st_at(), CFG)) == "invalid_args"


def test_rate_limit():
    n = CFG["rate_limit"]["actuating_per_minute"]
    assert rule(check(goto(10, 10), st_at(recent=[990.0] * n), CFG)) == "rate_limit"
    # old actuations (> 60 s ago) don't count
    assert check(goto(10, 10), st_at(recent=[900.0] * n), CFG) == Allow()


# ---- takeoff -----------------------------------------------------------------

@pytest.mark.parametrize("alt", [None, "15", True, math.nan, math.inf])
def test_takeoff_invalid_alt(alt):
    assert rule(check(Command("takeoff", {"alt_m": alt}), st_at(in_air=False), CFG)) == "invalid_args"


@pytest.mark.parametrize("alt", [0, -5, 61, 300])
def test_takeoff_altitude(alt):
    assert rule(check(Command("takeoff", {"alt_m": alt}), st_at(in_air=False), CFG)) == "altitude"


def test_takeoff_outside_fence():
    assert rule(check(Command("takeoff", {"alt_m": 15}), st_at(east=500, in_air=False), CFG)) == "geofence"


def test_takeoff_low_battery():
    assert rule(check(Command("takeoff", {"alt_m": 15}), st_at(battery=10, in_air=False), CFG)) == "battery"


def test_takeoff_ok():
    assert check(Command("takeoff", {"alt_m": 15}), st_at(in_air=False), CFG) == Allow()


# ---- goto --------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {}, {"lat": 47.398, "lon": 8.546}, {"lat": "47.398", "lon": 8.546, "alt_m": 15},
    {"lat": math.nan, "lon": 8.546, "alt_m": 15}, {"lat": 47.398, "lon": 8.546, "alt_m": math.inf},
])
def test_goto_invalid_args(args):
    assert rule(check(Command("goto", args), st_at(), CFG)) == "invalid_args"


def test_goto_impossible_coordinate():
    assert rule(check(Command("goto", {"lat": 95.0, "lon": 8.546, "alt_m": 15}), st_at(), CFG)) == "invalid_args"


@pytest.mark.parametrize("alt", [2, 61, 300])
def test_goto_altitude(alt):
    assert rule(check(goto(10, 10, alt), st_at(), CFG)) == "altitude"


def test_goto_target_outside_fence():
    d = check(goto(500, 0), st_at(), CFG)
    assert rule(d) == "geofence" and "target" in d.detail


def test_goto_path_leaves_fence_concave():
    cfg = copy.deepcopy(CFG)
    # L-shaped fence: the notch at the top right is outside.
    cfg["geofence"] = [ll(-50, -50), ll(100, -50), ll(100, 20), ll(20, 20), ll(20, 100), ll(-50, 100)]
    cfg["no_fly_zones"] = []
    d = check(goto(90, 10), st_at(east=10, north=90), cfg)
    assert rule(d) == "geofence" and "path" in d.detail


def test_goto_from_outside_fence_path_leaves():
    d = check(goto(10, 10), st_at(east=200), CFG)
    assert rule(d) == "geofence" and "path" in d.detail


def test_goto_target_in_no_fly():
    d = check(goto(85, 70), st_at(), CFG)  # school: e 63-108, n 47-92
    assert rule(d) == "no_fly" and "target" in d.detail


def test_goto_path_crosses_no_fly():
    d = check(goto(85, 105), st_at(east=85, north=30), CFG)
    assert rule(d) == "no_fly" and "path" in d.detail


def test_goto_battery_reserve():
    d = check(goto(130, -100), st_at(battery=26.0), CFG)  # south-east: clear of the no-fly zone
    assert rule(d) == "battery"


def test_goto_ok_and_zero_length_path():
    assert check(goto(40, 20), st_at(), CFG) == Allow()
    assert check(goto(0, 0), st_at(), CFG) == Allow()  # already there: path is a point


# ---- land --------------------------------------------------------------------

def test_land_in_zone():
    assert check(Command("land"), st_at(east=1, north=1), CFG) == Allow()


def test_land_outside_zone_denied():
    assert rule(check(Command("land"), st_at(east=50, north=30), CFG)) == "landing_zone"


def test_land_emergency_requires_approval():
    d = check(Command("land"), st_at(east=50, north=30, battery=5), CFG)
    assert isinstance(d, RequireApproval) and "emergency" in d.reason


def test_land_emergency_without_approval_config_allows():
    cfg = copy.deepcopy(CFG)
    cfg["approval"]["require_for"] = []
    assert check(Command("land"), st_at(east=50, north=30, battery=5), cfg) == Allow()


# ---- send_report ---------------------------------------------------------------

def test_report_invalid_args():
    assert rule(check(Command("send_report", {"text": 1, "recipient": None}), st_at(), CFG)) == "invalid_args"


def test_report_not_allowlisted():
    d = check(Command("send_report", {"text": "x", "recipient": "attacker@evil.example"}), st_at(), CFG)
    assert rule(d) == "egress"


def test_report_allowlisted_case_insensitive():
    c = Command("send_report", {"text": "x", "recipient": "  OPS@guardedpilot.local "})
    assert check(c, st_at(), CFG) == Allow()


# ---- serialisation -------------------------------------------------------------

def test_to_dict():
    assert to_dict(Allow()) == {"decision": "allow"}
    assert to_dict(Deny("geofence", "x")) == {"decision": "deny", "rule": "geofence", "detail": "x"}
    assert to_dict(RequireApproval("r")) == {"decision": "require_approval", "reason": "r"}


# ---- properties (Hypothesis) -------------------------------------------------------

metres = st.floats(min_value=-300, max_value=300, allow_nan=False)
alts = st.floats(min_value=-20, max_value=120, allow_nan=False)
batt = st.floats(min_value=0, max_value=100, allow_nan=False)
ORACLE = Oracle(CFG, HOME)


@settings(max_examples=3000, deadline=None)
@given(e0=metres, n0=metres, e1=metres, n1=metres, alt=alts, b=batt)
def test_allowed_goto_never_violates_envelope(e0, n0, e1, n1, alt, b):
    s, c = st_at(east=e0, north=n0, battery=b), goto(e1, n1, alt)
    if check(c, s, CFG) == Allow():
        assert ORACLE.violations("goto", c.args, s) == []


@settings(max_examples=1000, deadline=None)
@given(e=metres, n=metres, b=batt)
def test_allowed_land_is_in_zone_or_emergency(e, n, b):
    s = st_at(east=e, north=n, battery=b)
    d = check(Command("land"), s, CFG)
    if d == Allow():
        assert ORACLE.violations("land", {}, s) == []
    if isinstance(d, RequireApproval):
        assert b < CFG["battery"]["critical_pct"]


junk = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text(max_size=8),
                 st.lists(st.integers(), max_size=2), st.dictionaries(st.text(max_size=3), st.integers(), max_size=2))


@settings(max_examples=2000, deadline=None)
@given(tool=st.sampled_from(["takeoff", "goto", "land", "send_report", "return_to_home", "rm -rf"]),
       args=st.dictionaries(st.sampled_from(["lat", "lon", "alt_m", "text", "recipient", "x"]), junk, max_size=5),
       e=metres, n=metres, b=batt)
def test_never_raises_and_is_deterministic(tool, args, e, n, b):
    s = st_at(east=e, north=n, battery=b)
    d1, d2 = check(Command(tool, args), s, CFG), check(Command(tool, args), s, CFG)
    assert d1 == d2
    assert d1.decision in {"allow", "deny", "require_approval"}
