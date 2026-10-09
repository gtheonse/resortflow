"""Standalone `make seed` runner (T042): resets and reseeds all five sims' SQLite
state to the deterministic baseline in `contracts/seed-data-spec.md`, without requiring
the sims' HTTP servers to be running.

Invoked by the root `Makefile`'s `seed` target. Filling in the full docker-compose
service topology this target eventually runs inside is flagged as an integration-commit
concern per `plan.md`'s Structure Decision (the Makefile is a shared, cross-track
artifact); this script itself is entirely Track A's own concern and needs nothing
outside `sims/`.
"""

from __future__ import annotations

import asyncio


async def main() -> None:
    from sims.amenities.main import DB_PATH as AMENITIES_DB
    from sims.amenities.main import reset as amenities_reset
    from sims.casino.main import DB_PATH as CASINO_DB
    from sims.casino.main import reset as casino_reset
    from sims.dining.main import DB_PATH as DINING_DB
    from sims.dining.main import reset as dining_reset
    from sims.pms.main import DB_PATH as PMS_DB
    from sims.pms.main import reset as pms_reset
    from sims.ticketing.main import DB_PATH as TICKETING_DB
    from sims.ticketing.main import reset as ticketing_reset

    sims = [
        ("amenities", AMENITIES_DB, amenities_reset),
        ("casino", CASINO_DB, casino_reset),
        ("dining", DINING_DB, dining_reset),
        ("pms", PMS_DB, pms_reset),
        ("ticketing", TICKETING_DB, ticketing_reset),
    ]
    for name, db_path, reset_fn in sims:
        await reset_fn(db_path)
        print(f"seeded {name}-sim at {db_path}")

    print("seed: 2 tenants (MGV, MHB) — amenities, casino, dining, pms, ticketing all reset to baseline")


if __name__ == "__main__":
    asyncio.run(main())
