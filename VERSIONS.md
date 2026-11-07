# Pinned versions

Pinned 2026-11-07. Upgrade only after December 15 (see plan, Risks). Python deps are locked exactly in `uv.lock`; this file is the human-readable summary.

## Host

| Component | Version |
| --- | --- |
| Machine | Apple M5, 16 GB, macOS 26.0 |
| Docker | 29.7.2 |
| uv | 0.13.7 |
| Python | 3.12.12 |
| Ollama | 0.40.15 (needs D7 workaround after reboot) |
| QGroundControl | 5.1.5 (viewing only) |

## Simulator

| Component | Version |
| --- | --- |
| PX4 SITL + Gazebo Harmonic | `px4io/px4-sitl-gazebo:v1.18.0-rc1` |
| Image digest | `px4io/px4-sitl-gazebo@sha256:aa4bbf35f0bd6bdb3269cc96e317c8e78b87f1fc769c734c0818094074e1cc0a` |
| Vehicle | `gz_x500`, world `default`, home 47.397971 N 8.546164 E |

## Models (Ollama)

| Model | Digest | Role |
| --- | --- | --- |
| qwen2.5vl:7b | 5ced39dfa4ba | describe_scene (vision) |
| llama3.1:8b | 46e0c10c039e | agent candidate |
| qwen3:8b | 500a1f067a9f | agent candidate |

## Python packages

| Package | Version |
| --- | --- |
| httpx | 0.28.1 |
| hypothesis | 6.170.15 |
| langchain | 1.4.9 |
| langchain-anthropic | 1.7.10 |
| langchain-core | 1.7.6 |
| langchain-ollama | 1.1.0 |
| langgraph | 1.2.20 |
| langgraph-checkpoint-sqlite | 3.1.3 |
| mavsdk-grpc | 4.0.5 |
| mcp | 2.3.0 |
| pandas | 3.1.1 |
| pillow | 13.0.0 |
| pyarrow | 26.0.1 |
| pydantic | 2.14.3 |
| pytest | 9.2.0 |
| shapely | 2.2.0 |
