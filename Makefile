.PHONY: up mcp seed eval eval-adversarial test-docker demo

up:
	docker compose --profile sims --profile app up -d

# mcp: bring up just the MCP gateway (Track B) plus the five sims it depends on,
# without the rest of the `app` profile (resortflow-orchestrator, hold-ledger-reaper) — useful
# when only the tool layer is under development, and for the
# quickstart.md scenario set (specs/003-mcp-tool-layer/quickstart.md).
mcp:
	docker compose --profile sims up -d
	docker compose --profile app up -d mcp-gateway

seed:
	@if [ -z "$$MCP_DEV_SIGNING_KEY" ] && ! grep -q '^MCP_DEV_SIGNING_KEY=.\+' .env 2>/dev/null; then \
		echo "Generating MCP_DEV_SIGNING_KEY (dev-only, mcp/gateway/_shared/identity.py)..."; \
		KEY=$$(python -c "import secrets; print(secrets.token_urlsafe(32))"); \
		if [ -f .env ]; then \
			if grep -q '^MCP_DEV_SIGNING_KEY=' .env; then \
				sed -i "s#^MCP_DEV_SIGNING_KEY=.*#MCP_DEV_SIGNING_KEY=$$KEY#" .env; \
			else \
				echo "MCP_DEV_SIGNING_KEY=$$KEY" >> .env; \
			fi; \
		else \
			echo "MCP_DEV_SIGNING_KEY=$$KEY" >> .env; \
		fi; \
	fi
	python -m sims.seed_all

eval:
	python -m agents.dining.eval.runner && \
	python -m agents.entertainment.eval.runner && \
	python -m agents.stay.eval.runner && \
	python -m agents.wellness.eval.runner

eval-adversarial:
	# Not wired into the default `eval` target: a known, pre-existing
	# checkpointing-performance characteristic (feature 005's
	# make_deterministic_node re-checkpoints the whole, ever-growing
	# conversation state into each domain's own sub-thread lineage every
	# turn) makes this script's runtime grow steeply with turn count —
	# discovered by this script itself, not something feature 006 introduced.
	# Run separately, with patience, until that
	# is addressed.
	python -m agents.gaming.eval.adversarial.runner

# test-docker: runs the full pytest suite inside Linux, bypassing Windows Smart App Control's
# blocking of unsigned native wheels (spaCy/psycopg_binary/jsonschema_rs) on the host. See
# docker/test.Dockerfile.
test-docker:
	docker compose --profile test run --rm tests

demo:
	python -m graph.demo \
	  --tenant MGV --guest guest-mgv-001 \
	  --text "Dinner, a show, and a spa treatment tomorrow evening" \
	  --inject-failure dining:confirm_reservation \
	  --auto-approve
