# MCP Gateway (`mcp/gateway/`)

The governed boundary between every agent in this repo and the five simulated vendors.
No agent holds a vendor SDK or a vendor credential — every tool call passes through this
one process. See `specs/003-mcp-tool-layer/plan.md` and AGENTS.md's architectural
commitments #2 ("all tool access through MCP") and #3 ("every external mutation is
two-phase hold-then-confirm").

Note the two-root package layout (F1 in `specs/003-mcp-tool-layer/tasks.md`): `mcp/` is a
plain directory (no `__init__.py`) because a top-level package literally named `mcp`
collides with the `mcp` SDK on PyPI. The importable package lives one level down, at
`mcp/gateway/`, and is on `sys.path` via `pyproject.toml`'s
`[tool.setuptools.packages.find]` (`where = [".", "mcp"]`).

## Mount paths

One process, one `uvicorn` app, port `9100` (ADR-011). Five `FastMCP` instances are
mounted as ASGI sub-apps behind one shared authentication + tracing middleware chain
(`mcp/gateway/main.py`):

| Mount path | Domain | Tool count |
|---|---|---|
| `/dining` | Dining (`dining-sim`) | 8 |
| `/entertainment` | Entertainment / ticketing (`ticketing-sim`) | 7 |
| `/stay` | Stay / PMS (`pms-sim`) | 7 |
| `/wellness` | Wellness / amenities (`amenities-sim`) | 6 |
| `/gaming` | Gaming / casino (`casino-sim`) — **read-only** | 3 |

Every tool name follows `resortflow.<domain>.<verb>_<noun>` (enforced by
`_shared/registry.py`'s `TOOL_NAME_RE`); the exact roster is frozen in
`specs/003-mcp-tool-layer/contracts/tool-registry.md`.

## Authentication and the token issuer CLI

Every request must carry `Authorization: Bearer <token>`. The token is a signed JWT
(`HS256`, signed with `MCP_DEV_SIGNING_KEY`) verified once by the shared middleware
(`_shared/middleware.py`) ahead of all five sub-apps; the resulting `CallerIdentity`
(`_shared/identity.py`) is the *only* source of `tenant_id` for the rest of the request —
never a tool argument, never model output (FR-008/FR-009).

There is no networked identity provider in local dev (see `docker-compose.yml`'s comment
by the `qdrant`/`postgres` block, ADR-049). `_shared/identity.py` doubles as a dev-only
issuer with a CLI:

```bash
python -m gateway._shared.identity issue \
  --tenant MGV \
  --scopes dining:read,dining:hold,stay:read \
  [--sub test-harness] [--ttl-seconds 3600]
```

Prints a signed token to stdout. `--tenant` must be `MGV` or `MHB`; `--scopes` is a
comma-separated list of `<domain>:<action>` strings (`domain` one of
`dining|entertainment|stay|wellness|gaming`, `action` one of `read|hold|commit`).

A malformed, expired, unsigned, tampered, or unknown-tenant token all produce the exact
same refusal (`IdentityError`) — deliberately indistinguishable, so a probing caller can't
fingerprint which check failed.

## Running the gateway locally

Prerequisites: the five sims from `specs/002-simulation-ecosystem` running and seeded, and
`MCP_DEV_SIGNING_KEY` set (see `.env.example`; `make seed` generates one if absent).

**Via Docker Compose** (matches `specs/003-mcp-tool-layer/quickstart.md`):

```bash
docker compose --profile sims up -d          # the five vendor sims
docker compose --profile app up -d mcp-gateway
```

The gateway is then reachable at `http://localhost:9100` (`/dining`, `/entertainment`,
`/stay`, `/wellness`, `/gaming`).

**In-process, without Docker** (for local iteration):

```bash
uvicorn gateway.main:app --host 0.0.0.0 --port 9100 --reload
```

Requires `PYTHONPATH` to include `mcp/` (or running from inside `mcp/`) so `import
gateway...` resolves, and the five `*_SIM_URL` environment variables pointing at running
sims (defaults in `_shared`/`settings.py` assume `localhost:9001-9005`).

## What every tool call gets, uniformly

Applied by the single `register_tool` decorator in `_shared/registry.py` — a tool cannot
be registered while skipping any of these:

- Name-regex validation against the frozen `resortflow.<domain>.<verb>_<noun>` contract.
- Entitlement check (`_shared/entitlements.py`) against the caller's scopes — `read`,
  `hold`, `commit` are a structural grant, not a naming convention.
- Idempotency-key validation on every mutating tool (`_shared/idempotency.py`).
- One OpenTelemetry span per call (`_shared/tracing.py`), attribute names imported only
  from `observability/attributes.py`.
- Total error classification into one of four classes — `RETRYABLE`, `TERMINAL`,
  `REQUIRES_HUMAN`, `POLICY_BLOCKED` (`_shared/errors.py`) — with no vendor envelope shape
  ever reaching the caller.
- Composite fan-out support (`SubCallSpec`, `required_for_success`,
  `completed_sub_calls`) for tools that call more than one vendor operation.

See `mcp/tests/unit/test_composite_failure.py` for the composite partial-failure proof, and
`specs/003-mcp-tool-layer/` for what shipped and what's deferred.
