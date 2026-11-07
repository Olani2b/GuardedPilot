# Decisions

One entry per decision: what, why, and what would make us revisit it.

## D1 — Camera: image library, not Gazebo camera (2026-11-07)

**Decision:** `get_camera_frame` returns pre-rendered aerial images from a library indexed by grid cell over the sim world. Scene-text attacks (A1) composite text onto those images. Gazebo/PX4 SITL is still used for flight physics and telemetry.

**Why:** Development machine is an Apple Silicon Mac (arm64, 16 GB, no NVIDIA GPU). Headless Gazebo camera rendering inside Docker without a GPU is the highest-risk item in the plan, and the image library makes A1 attacks deterministic and easy to author.

**Revisit if:** we move experiments to a Linux GPU box and want camera realism for the video.

**Consequence:** the demo video is a screen recording of the episode replay page plus the agent trace, not a Gazebo GUI capture.

## D2 — Models: local model via Ollama, running natively on the host (2026-11-07)

**Decision:** Ollama runs natively on macOS (not in Docker) so it can use the Metal GPU. Containers reach it at `host.docker.internal:11434`. Phase 1 tests 2–3 ~8B models for tool calling and keeps the best; a vision model backs `describe_scene`. A frontier API model is added before Phase 3.

**Why:** Docker on macOS has no GPU passthrough; an 8B model on CPU is too slow for 240 episodes.

## D3 — PX4 image: `px4io/px4-sitl-gazebo:v1.18.0-rc1` (2026-11-07)

**Decision:** use the official multi-arch image (native arm64), pinned to the only release tag available (`v1.18.0-rc1`; no stable 1.18 yet). Run headless (`HEADLESS=1`).

**Why:** native arm64 avoids x86 emulation; the older `px4-dev-*` images are amd64-only.

**Revisit if:** a stable v1.18.0 tag ships before Dec 15 (upgrade only after Dec 15 per plan).

## D4 — Tracing: episode JSON only, no LangSmith (2026-11-07)

**Decision:** the Phase 1 episode logger (one JSON per run) is the trace. No LangSmith account.

**Why:** the demo site reads the episode JSON anyway; one fewer account and API key.

## D5 — MAVSDK: `mavsdk-grpc` package, not `mavsdk` v4 (2026-11-07)

**Decision:** depend on `mavsdk-grpc` and `import mavsdk_grpc`. Since v4 the `mavsdk` PyPI name is a native binding with a different API; the classic async gRPC API (the one the plan specifies) continues under `mavsdk-grpc`, still maintained.

**Gotcha:** `SimBridge.close()` must kill the `mavsdk_server` subprocess, or the Python process never exits.

## D6 — Docker networking: `extra_hosts: host-gateway` (2026-11-07)

**Decision:** the PX4 container maps `host.docker.internal` to `host-gateway`. The image entrypoint then points MAVLink at the host, so the bridge on macOS receives telemetry on `udpin://0.0.0.0:14540`. Without the mapping the name doesn't resolve and PX4 sends to its own localhost.

## Open: simulated battery

PX4 SITL defaults drain the battery in ~1 min and clamp at 50% (`SIM_BAT_DRAIN`, `SIM_BAT_MIN_PCT`). Must be tuned before the battery-reserve rule means anything (Phase 2).

## D7 — Ollama on M5 + macOS 26.0: disable Metal tensor ops (2026-11-07)

**Problem:** Ollama 0.40.15's Metal tensor kernels fail to compile on Apple M5 with macOS 26.0 (`MetalPerformancePrimitives ... "Unsupported type"`), so no model loads.

**Workaround:** `launchctl setenv GGML_METAL_TENSOR_DISABLE 1 && brew services restart ollama`. Models then run 100% on GPU. Not persistent: re-run after every reboot (owner chose this over a login item).

**Revisit:** after a macOS or Ollama update; test by unsetting the variable.
