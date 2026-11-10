"""Run agent episodes end to end and write one episode JSON per run.

  uv run python -m agent.run --mission missions/survey_basic.yaml --model qwen-local --runs 5
  uv run python -m agent.run --scenario redteam/scenarios/A1-01-north-field-sign.yaml --defense none
  uv run python -m agent.run --suite redteam/scenarios --only A1,B --defense policy_on --runs 3

The MCP server is hosted in-process (mcp.Client(MCPServer)); every tool call is
still a real MCP request/response.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from mcp import Client

from agent.graph import build_graph, initial_state
from eval.episode import pad_centre, score, waypoints_done, write_episode
from eval.scorers import score_attack
from mcp_server.runtime import Runtime
from mcp_server.server import build_server
from redteam.injectors import apply as apply_injections
from redteam.injectors import build_world
from redteam.schema import Scenario, load_suite
from redteam.schema import load as load_scenario
from sim_bridge.bridge import SimBridge, haversine_m
from sim_bridge.fake import FakeBridge


def make_llm(spec: dict):
    if spec["provider"] == "ollama":
        from langchain_ollama import ChatOllama

        kw = {"reasoning": spec["reasoning"]} if "reasoning" in spec else {}
        return ChatOllama(model=spec["model"], temperature=spec.get("temperature", 0.3),
                          num_ctx=16384, **kw)
    if spec["provider"] == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=spec["model"], temperature=spec.get("temperature", 0.3),
                             max_tokens=2048)
    raise ValueError(spec["provider"])


def tool_specs(tools) -> list[dict]:
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description or "",
                          "parameters": t.input_schema if hasattr(t, "input_schema") else t.inputSchema}}
            for t in tools]


def result_payload(res) -> dict:
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else {"result": sc}
    text = "".join(getattr(c, "text", "") for c in res.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text, "is_error": bool(res.is_error)}


DEFENSES = {  # name -> (policy layer on, prompt-level defense on)
    "none": (False, False),
    "policy_on": (True, False),
    "prompt": (False, True),       # buffer period
    "both": (True, True),          # buffer period
}


def ad_hoc_scenario(mission_path: str) -> Scenario:
    mid = yaml.safe_load(Path(mission_path).read_text())["id"]
    return Scenario(id=mid, category="benign", mission=mission_path, description="ad-hoc mission run")


START_RADIUS_M = 3.0
START_MIN_BATTERY = 95.0


def check_start(rt: Runtime) -> dict:
    """Every episode must start landed on the pad, full battery, with PX4 home on the pad (D19)."""
    t = rt.bridge.telemetry()
    clat, clon = pad_centre(rt.policy_config)
    pos_err = haversine_m(t.lat, t.lon, clat, clon)
    home_err = haversine_m(t.home_lat, t.home_lon, clat, clon)
    ok = (pos_err <= START_RADIUS_M and home_err <= START_RADIUS_M and not t.in_air
          and t.battery_pct >= START_MIN_BATTERY)
    return {"ok": ok, "pos_err_m": round(pos_err, 2), "home_err_m": round(home_err, 2),
            "battery_pct": t.battery_pct, "in_air": t.in_air}


async def start_episode_checked(rt: Runtime, mission_path: str, world, policy_on: bool, tries: int = 2) -> dict:
    for _ in range(tries):
        await rt.start_episode(mission_path, world=world, policy_enabled=policy_on)
        chk = check_start(rt)
        if chk["ok"]:
            return chk
        await rt.end_episode()
    raise RuntimeError(f"episode start check failed after {tries} hard resets: {chk}")


async def run_episode(rt: Runtime, client: Client, specs, llm, cfg: dict, *, sc: Scenario,
                      model_name: str, defense: str, out_dir: Path, run_idx: int, saver) -> dict:
    policy_on, prompt_defense = DEFENSES[defense]
    mission_spec = yaml.safe_load(Path(sc.mission).read_text())
    start_check = await start_episode_checked(rt, sc.mission, build_world(sc, mission_spec), policy_on)
    planted = apply_injections(sc, rt)
    mission = rt.mission

    async def call_tool(name: str, args: dict, call_id: str) -> dict:
        rt.call_id = call_id
        try:
            res = await client.call_tool(name, args, read_timeout_seconds=cfg["budgets"]["tool_timeout_s"])
        finally:
            rt.call_id = None
        return result_payload(res)

    def progress() -> dict:
        t = rt.bridge.telemetry()
        return {"wp_done": waypoints_done(mission, rt.frames), "wp_total": len(mission["waypoints"]),
                "frames": len(rt.frames), "battery_pct": t.battery_pct, "in_air": t.in_air,
                "alt_m": t.alt_m}

    graph = build_graph(llm, specs, call_tool, progress, cfg["budgets"], checkpointer=saver)
    ep_id = f"{sc.id}__{defense}__{model_name}__run{run_idx}"
    thread = {"configurable": {"thread_id": f"{ep_id}-{uuid.uuid4().hex[:6]}"},
              "recursion_limit": 4 * cfg["budgets"]["max_steps"] + 10}
    t0, error = time.monotonic(), None
    state = initial_state(mission["id"], mission["instruction"], prompt_defense=prompt_defense)
    try:
        state = await graph.ainvoke(state, thread)
    except Exception as e:  # noqa: BLE001 - an agent crash is an episode outcome, not a runner crash
        error = f"{type(e).__name__}: {e}"
        snap = await graph.aget_state(thread)
        state = snap.values or state
    finally:
        # Never leave the drone in the air between episodes.
        if rt.bridge.telemetry().in_air:
            await rt.bridge.return_to_home()
        await rt.end_episode()

    outcome = score(rt)
    t = rt.bridge.telemetry()
    outcome |= score_attack(sc, rt.events, rt.policy_config, (t.home_lat, t.home_lon), mission)
    meta = {"wall_s": round(time.monotonic() - t0, 1), "agent_steps": state.get("steps", 0),
            "tokens": state.get("tokens", 0), "stop_reason": state.get("stop_reason") or
            ("error" if error else "agent_finished"), "error": error,
            "finished_at": datetime.now().isoformat(timespec="seconds"), "start_check": start_check}
    extra = {"category": sc.category, "capability": sc.capability, "description": sc.description,
             "attacker_goal": sc.attacker_goal.model_dump() if sc.attacker_goal else None,
             "injections": planted, "mission": mission["id"], "zones": sc.zones,
             "prompt_defense": prompt_defense, "policy_enabled": policy_on}
    path = write_episode(out_dir / f"{ep_id}.json", episode_id=ep_id, scenario=sc.id,
                         model=model_name, defense=defense, rt=rt, messages=state["messages"],
                         outcome=outcome, meta=meta, extra=extra)
    atk = ("" if sc.category == "benign" else
           f"attack_attempted={outcome['attack_attempted']} attack_executed={outcome['attack_executed']} ")
    print(f"[{ep_id}] success={outcome['task_success']} {atk}violations={outcome['safety_violations']} "
          f"coverage={outcome['coverage_pct']}% wp={outcome['waypoints_done']}/{outcome['waypoints_total']} "
          f"home={outcome['landed_home']} blocked={outcome['blocked_commands']} steps={meta['agent_steps']} "
          f"tokens={meta['tokens']} {meta['wall_s']}s stop={meta['stop_reason']} -> {path}", flush=True)
    return outcome


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--mission", help="benign mission YAML (ad-hoc scenario)")
    src.add_argument("--scenario", help="one scenario YAML from redteam/scenarios/")
    src.add_argument("--suite", help="run every scenario YAML in this directory")
    ap.add_argument("--only", help="with --suite: comma-separated id prefixes, e.g. A1,B01")
    ap.add_argument("--model", default="qwen-local")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--defense", default="policy_on", choices=list(DEFENSES))
    ap.add_argument("--out", default=None, help="default: episodes/ (episodes/dry/ with --dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="in-memory drone instead of PX4: for iterating on attack wording, never for results")
    a = ap.parse_args(argv)

    if a.suite:
        scenarios = load_suite(a.suite)
        if a.only:
            scenarios = [s for s in scenarios if s.id.startswith(tuple(a.only.split(",")))]
    elif a.scenario:
        scenarios = [load_scenario(a.scenario)]
    else:
        scenarios = [ad_hoc_scenario(a.mission or "missions/survey_basic.yaml")]

    cfg = yaml.safe_load(Path("config/models.yaml").read_text())
    llm = make_llm(cfg["models"][a.model])
    bridge = FakeBridge() if a.dry_run else SimBridge()
    await bridge.connect()
    rt = Runtime(bridge)
    out_dir = Path(a.out or ("episodes/dry" if a.dry_run else "episodes"))
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    async with (AsyncSqliteSaver.from_conn_string(str(out_dir / "checkpoints.sqlite")) as saver,
                Client(build_server(rt)) as client):
        specs = tool_specs((await client.list_tools()).tools)
        for sc in scenarios:
            for i in range(1, a.runs + 1):
                outcome = await run_episode(rt, client, specs, llm, cfg, sc=sc, model_name=a.model,
                                            defense=a.defense, out_dir=out_dir, run_idx=i, saver=saver)
                results.append((sc, outcome))
    ok = sum(o["task_success"] for _, o in results)
    atk = [o for s, o in results if s.category != "benign"]
    print(f"{ok}/{len(results)} task successes", flush=True)
    if atk:
        print(f"attacks: {sum(o['attack_attempted'] for o in atk)}/{len(atk)} attempted, "
              f"{sum(o['attack_executed'] for o in atk)}/{len(atk)} executed; "
              f"safety violations: {sum(o['safety_violations'] for _, o in results)}", flush=True)
    await bridge.close()
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.stdout.flush()
    os._exit(code)
