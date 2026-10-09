"""Local test env defaults. The project has no `.env` autoloader (no
`python-dotenv` dependency) — `docker-compose.yml`'s services define these
values for containers; a local `pytest` run needs them in `os.environ`
directly. Only fills gaps — never overrides a value the environment (CI,
a developer's shell) already set.
"""

from __future__ import annotations

import asyncio
import os
import sys

_DEFAULTS = {
    "POSTGRES_URL": "postgresql://resortflow:resortflow@localhost:5432/resortflow",
    "LANGGRAPH_AES_KEY": "dGVzdC1rZXktbm90LWEtc2VjcmV0LTAxMjM0NTY3ODk=",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)

# psycopg's async connection mode refuses Windows' default ProactorEventLoop
# (it needs select()). pytest-asyncio on Windows otherwise inherits
# Proactor, which breaks every AsyncPostgresSaver-backed test — switch the
# default policy for this test session only.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
