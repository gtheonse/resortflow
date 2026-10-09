"""Registers the shared per-sim TestClient fixtures (sims/tests/fixtures/clients.py)
for every test under sims/tests/."""

from sims.tests.fixtures.clients import (  # noqa: F401
    amenities_client,
    casino_client,
    dining_client,
    pms_client,
    ticketing_client,
)
