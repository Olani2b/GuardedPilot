.PHONY: sim sim-down sim-logs square mcp-smoke agent suite site-data site test lint ollama-fix

sim:            ## Start PX4 SITL + Gazebo headless
	docker compose up -d px4

sim-down:
	docker compose down

sim-logs:
	docker logs -f gp-px4

square:         ## Smoke test: fly a square N times (make square RUNS=10)
	uv run python -W ignore scripts/fly_square.py $(or $(RUNS),1)

mcp-smoke:      ## Call every MCP tool once against the live sim
	uv run python -W ignore scripts/mcp_smoke.py

agent:          ## Agent episodes: make agent [MISSION=... | SCENARIO=...] MODEL=qwen-local RUNS=1 DEFENSE=policy_on
	uv run python -u -W ignore -m agent.run --model $(or $(MODEL),qwen-local) --runs $(or $(RUNS),1) \
	  $(if $(SCENARIO),--scenario $(SCENARIO),--mission $(or $(MISSION),missions/survey_basic.yaml)) \
	  --defense $(or $(DEFENSE),policy_on)

suite:          ## Red-team suite: make suite DEFENSE=none [ONLY=A1,B] [RUNS=1] [MODEL=qwen-local]
	uv run python -u -W ignore -m agent.run --suite redteam/scenarios $(if $(ONLY),--only $(ONLY)) \
	  --model $(or $(MODEL),qwen-local) --runs $(or $(RUNS),1) --defense $(or $(DEFENSE),policy_on)

site-data:      ## Export episodes/ into site/public/data for the replay page
	uv run python scripts/export_site_data.py

site: site-data ## Serve the static site at http://localhost:8137/replay.html
	python3 -m http.server 8137 --directory site/public

test:           ## Unit + property + fuzz tests; fails if policy/ branch coverage < 100%
	uv run pytest --cov=policy --cov-branch --cov-fail-under=100 --cov-report=term-missing:skip-covered

lint:
	uv run ruff check .

ollama-fix:     ## Re-apply after reboot on M5/macOS 26.0 (docs/decisions.md D7)
	launchctl setenv GGML_METAL_TENSOR_DISABLE 1 && brew services restart ollama
