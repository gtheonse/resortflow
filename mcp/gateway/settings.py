"""Gateway configuration — sim base URLs, vendor credentials, and the dev signing key.

All read from the environment with `.env.example`-documented defaults matching the
values Phase 1's sims hardcode for local dev (`sims/*/main.py`). No real-looking
hostnames or secrets — every value here is a local-dev fixture (research.md D2).
"""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Upstream simulator base URLs (research.md C2) --------------------------------

DINING_SIM_URL: str = _env("DINING_SIM_URL", "http://localhost:9002")
TICKETING_SIM_URL: str = _env("TICKETING_SIM_URL", "http://localhost:9003")
PMS_SIM_URL: str = _env("PMS_SIM_URL", "http://localhost:9001")
AMENITIES_SIM_URL: str = _env("AMENITIES_SIM_URL", "http://localhost:9005")
CASINO_SIM_URL: str = _env("CASINO_SIM_URL", "http://localhost:9004")

# --- Vendor credentials — mirror each sim's hardcoded local-dev values exactly ------

DINING_API_KEY: str = _env("DINING_API_KEY", "dining-sim-static-key-MRI")

TICKETING_CLIENT_ID: str = _env("TICKETING_CLIENT_ID", "ticketing-sim-client")
TICKETING_CLIENT_SECRET: str = _env("TICKETING_CLIENT_SECRET", "ticketing-sim-client-secret")
TICKETING_HMAC_SECRET: str = _env("TICKETING_HMAC_SECRET", "ticketing-sim-hmac-shared-secret")

PMS_CLIENT_ID: str = _env("PMS_CLIENT_ID", "pms-sim-client")
PMS_CLIENT_SECRET: str = _env("PMS_CLIENT_SECRET", "pms-sim-client-secret")

AMENITIES_USERNAME: str = _env("AMENITIES_USERNAME", "amenities-mcp")
AMENITIES_PASSWORD: str = _env("AMENITIES_PASSWORD", "amenities-sim-secret")

CASINO_BEARER_TOKEN: str = _env("CASINO_BEARER_TOKEN", "casino-sim-static-bearer-token")

# --- Identity issuer -----------------------------------------------------------------

MCP_DEV_SIGNING_KEY: str = _env("MCP_DEV_SIGNING_KEY", "resortflow-mcp-dev-signing-key-local-only")
