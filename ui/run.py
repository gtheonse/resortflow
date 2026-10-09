"""Windows-compatible launcher for the verification console.

`python -m ui.run` (equivalent to `python -m uvicorn ui.app:app --port 8420`
everywhere else). psycopg's async mode (`AsyncPostgresSaver`, used by
`ui/runtime.py` whenever `POSTGRES_URL` is set) requires a selector-based
event loop; Windows' default since Python 3.8 is `ProactorEventLoop`, which
raises `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`
the first time a real connection is attempted.

Setting `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` at
module import time does *not* fix this: `python -m uvicorn`'s own CLI entry
point creates its loop via `asyncio.run()` internally, and on Python 3.12+
that no longer consults the process-wide event loop policy the way it used
to (confirmed by testing both — the plain `python -m uvicorn` CLI still
crashed with the policy set at import time). The actual fix, per the
exception's own suggested remedy, is `asyncio.run(..., loop_factory=...)` —
only available by running uvicorn programmatically, hence this module
instead of the CLI.

Found live: this was never caught before because no code in this repo calls
`load_dotenv()` (see `ui/app.py`), so `POSTGRES_URL` was always empty in the
console's process and `ui/runtime.py` silently ran on the in-memory
`MemorySaver` fallback for every turn run so far this session, despite
`.env` having a working `POSTGRES_URL` the whole time. Adding `load_dotenv()`
made the Postgres path reachable for the first time, which is what surfaced
this.
"""
from __future__ import annotations

import asyncio
import selectors
import sys


def main() -> None:
    import uvicorn

    config = uvicorn.Config("ui.app:app", host="127.0.0.1", port=8420, log_level="info")
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        asyncio.run(
            server.serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
