.PHONY: sim sim-down sim-logs square test lint ollama-fix

sim:            ## Start PX4 SITL + Gazebo headless
	docker compose up -d px4

sim-down:
	docker compose down

sim-logs:
	docker logs -f gp-px4

square:         ## Smoke test: fly a square N times (make square RUNS=10)
	uv run python -W ignore scripts/fly_square.py $(or $(RUNS),1)

test:
	uv run pytest

lint:
	uv run ruff check .

ollama-fix:     ## Re-apply after reboot on M5/macOS 26.0 (docs/decisions.md D7)
	launchctl setenv GGML_METAL_TENSOR_DISABLE 1 && brew services restart ollama
