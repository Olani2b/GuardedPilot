# GuardedPilot

An LLM agent flies a simulated drone through MCP tools, behind a deterministic safety layer it cannot bypass. A red-team harness measures how often perception-borne attacks (signs in the scene, poisoned mission briefs, tampered tool outputs) make it act unsafely, and how many of those attempts the safety layer stops.

> **Status (2026-11-10):** Phases 1 and 2 done: at least one attack per category (A1 scene text, A2 brief, A3 tool output) fools Qwen3 with no defenses. The agent flies survey missions in PX4 SITL from one sentence. The policy layer enforces 8 rules (100% branch coverage, 0 violations in 20,000 fuzzed commands). A 23-scenario red-team suite runs end to end. **Resuming work? Read [docs/HANDOVER.md](docs/HANDOVER.md).**

## How it works

```
operator instruction ──► LangGraph agent (Qwen3 8B / frontier model)
                              │ MCP tool calls
                              ▼
                        MCP server ──read──► telemetry · camera frame · segmentation · scene description · mission brief
                              │
                       actuate (takeoff, goto, land, return_to_home, send_report)
                              ▼
                  policy.check()  ← deterministic, no ML, config/policy.yaml
                     │allow  │deny → {"blocked": true, "rule", "detail"} back to the agent
                     ▼
                  sim bridge (MAVSDK) ──► PX4 SITL + Gazebo (Docker)
```

The key rule: every actuating command passes `policy.check()` on its own terms, and the policy never asks the model whether something is safe. A lint test ([tests/test_import_rule.py](tests/test_import_rule.py)) fails the build if any code path actuates without it.

## Quick start (macOS, Apple Silicon)

Requirements: Docker Desktop, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), ~25 GB free disk.

```bash
uv sync                                  # Python deps (Python 3.12, locked in uv.lock)
cp .env.example .env                     # add API keys only when using a frontier model
ollama pull qwen3:8b && ollama pull qwen2.5vl:7b
make ollama-fix                          # M5 + macOS 26.0 only, after every reboot (docs/decisions.md D7)

make sim                                 # PX4 SITL + Gazebo, headless
make square RUNS=1                       # smoke test: scripted square flight
make agent MODEL=qwen-local RUNS=1       # one agent mission -> episodes/<id>.json
make site                                # replay at http://localhost:8137/replay.html
```

Optional: open [QGroundControl](https://qgroundcontrol.com) to watch the drone live; it connects on UDP 14550 by itself. View only, don't command while scripts fly.

## Make targets

| Target | What it does |
| --- | --- |
| `make sim` / `sim-down` / `sim-logs` | Start, stop, follow the PX4 container |
| `make square RUNS=10` | Scripted square flight, N times (sim bridge check) |
| `make mcp-smoke` | Call every MCP tool once against the live sim |
| `make agent MODEL=… RUNS=… [MISSION=… \| SCENARIO=…] [DEFENSE=none]` | Agent episodes; one JSON per run in `episodes/` |
| `make suite DEFENSE=none [ONLY=A1,B] [RUNS=…]` | Red-team suite ([docs/redteam.md](docs/redteam.md)) |
| `make site-data` / `make site` | Export episodes to the site and serve it |
| `make test` / `make lint` | Unit tests (incl. the no-actuation-without-policy rule) / ruff |
| `make ollama-fix` | Re-apply the Ollama Metal workaround after a reboot |

## Repository map

| Path | Contents |
| --- | --- |
| `sim_bridge/` | MAVSDK wrapper: arm, takeoff, goto, RTH, land, telemetry, reset |
| `perception/world.py` | Image-library world: seeded aerial map, camera crops, ground truth, scene text (A1) |
| `policy/` | Deterministic policy engine (Phase 1: stub) |
| `mcp_server/` | MCP tools; `runtime.py` holds the only actuation path |
| `agent/` | LangGraph graph (`graph.py`) and episode runner (`run.py`) |
| `eval/episode.py` | Scoring from the server's event log + episode JSON writer |
| `missions/` | Benign mission specs |
| `config/` | `policy.yaml` (flight envelope), `models.yaml` (models, budgets) |
| `site/public/` | Static replay page (Leaflet) |
| `redteam/` | Scenario schema, injectors, 20 scenario YAMLs |
| `docs/` | [decisions](docs/decisions.md), [dev log](docs/dev-log.md), [components](docs/components.md), [red team](docs/redteam.md), [replay site](docs/replay.md) |
| `VERSIONS.md` | Every pinned version |

## Documentation

- [docs/components.md](docs/components.md): what each component does, its interface, how to test it alone.
- [docs/decisions.md](docs/decisions.md): every non-obvious choice, why, and when to revisit.
- [docs/dev-log.md](docs/dev-log.md): dated progress and results.
- [docs/redteam.md](docs/redteam.md): threat model in code, scenario format, predicates, scoring, the 23 scenarios.
- [docs/replay.md](docs/replay.md): the replay page and episode JSON format.
- [docs/portfolio.md](docs/portfolio.md): how the project is presented (site, video, screenshots).
- `GuardedPilot — Project Plan (1).md`: the full project plan.
