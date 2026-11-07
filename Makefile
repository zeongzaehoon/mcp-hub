.PHONY: up down logs ps test test-backend test-mcp e2e e2e-ui lint rust-parity

# Everything docker lives in deploy/docker; run through these targets from the repo root.
COMPOSE := docker compose -f deploy/docker/docker-compose.yml

# host-side tests reach the compose Redis (127.0.0.1:6379) with its password: env/secrets/redis.env wins over env/redis.env
REDIS_PASSWORD ?= $(shell cat env/redis.env env/secrets/redis.env 2>/dev/null | sed -n 's/^REDIS_PASSWORD=//p' | tail -1)
export REDIS_PASSWORD
export TEST_REDIS_URL ?= redis://:$(REDIS_PASSWORD)@localhost:6379

up:            ## build & start the whole stack (env: tracked env/*.env + optional env/secrets/*.env)
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down -v

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail 100 gateway backend mcp-gateway mcp-postgres mcp-clickhouse mcp-mongo mcp-hacker

test: test-backend test-mcp   ## unit + integration (needs aws-local dynamodb + `make up` redis)

test-backend:
	cd backend && uv run pytest -q -p no:warnings

MCP_SERVERS := mcp-postgres mcp-clickhouse mcp-mongo mcp-hacker mcp-gateway

test-mcp:
	@for s in $(MCP_SERVERS); do echo "== $$s"; (cd $$s && uv run pytest -q -p no:warnings --ignore tests/test_e2e.py --ignore tests/test_generic.py --ignore tests/test_client_guide.py --ignore tests/test_inspector_compat.py --ignore tests/test_oauth.py) || exit 1; done

e2e:           ## isolated throwaway stack (deploy/docker/docker-compose.e2e.yml), removed afterwards: MCP servers + gateway
	scripts/e2e.sh mcp

e2e-ui:        ## isolated throwaway stack, removed afterwards: admin console (Playwright)
	scripts/e2e.sh ui

rust-parity:   ## prove the Rust wheel matches the Python fallback (inside the image)
	$(COMPOSE) run --rm --no-deps -T -e EXPECT_RUST=1 mcp-postgres sh -c "uv pip install -q pytest pytest-asyncio >/dev/null 2>&1; python -m pytest -q -p no:warnings tests/test_postprocess.py"

lint:
	cd backend && uv run ruff check app tests
