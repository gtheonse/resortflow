"""FastAPI backend for the verification console (feature 008).

`python -m uvicorn ui.app:app --reload --port 8420` (see ui/README.md).

Endpoints (all under /api):
  POST /api/turns                         -- start/continue a turn, SSE-streamed
  GET  /api/threads/{thread_id}/state      -- reconnect: current checkpointed state
  POST /api/threads/{thread_id}/approve    -- 202 + receipt token, resumes async
  GET  /api/receipts/{token}               -- poll an approval's outcome
  PUT  /api/fault                          -- arm a next-call-only X-Sim-Fault
  GET  /api/faults                         -- the real fault value list, for the toolbar
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()
# No code in this repo calls load_dotenv() anywhere else (confirmed by a
# repo-wide grep) -- every other module reads os.environ directly, relying
# on the caller (docker-compose's `environment:`/`env_file:`, or a shell
# that sourced .env) to have already populated it. This console is neither
# of those -- it's a plain `python -m uvicorn ui.app:app` process -- so
# without this call, real credentials sitting in .env (Azure/Foundry judge,
# etc.) are invisible to it even when correctly configured, producing a
# misleading "unavailable, not configured" status for things that actually
# are configured. Called before any other import that might read env vars
# at import time.

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from graph.models import Domain
from graph.supervisor import new_session_state
from ui.inspector import summarize_turn
from ui.receipts import receipt_store
from ui.runtime import REAL_FAULT_VALUES, GraphRuntime, build_runtime, fault_registry

_runtime_cm: Any = None
runtime: GraphRuntime | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _runtime_cm, runtime
    _runtime_cm = build_runtime()
    runtime = await _runtime_cm.__aenter__()
    try:
        yield
    finally:
        await _runtime_cm.__aexit__(None, None, None)


app = FastAPI(title="ResortFlow Verification Console", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="ui/static"), name="static")


def _rt() -> GraphRuntime:
    assert runtime is not None, "runtime not initialized (lifespan didn't run)"
    return runtime


class TurnRequest(BaseModel):
    thread_id: str | None = None
    tenant_id: str = "MGV"
    guest_id: str = "guest-mgv-001"
    text: str


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


async def _run_turn_stream(req: TurnRequest) -> AsyncIterator[bytes]:
    is_new = req.thread_id is None
    if is_new:
        state_input: Any = new_session_state(tenant_id=req.tenant_id, guest_id=req.guest_id, text=req.text)
        thread_id = state_input["session_id"]
        cfg = {"configurable": {"thread_id": thread_id}}
        app_graph = _rt().app_for_thread(thread_id)
    else:
        thread_id = req.thread_id
        from langchain_core.messages import HumanMessage

        cfg = {"configurable": {"thread_id": thread_id}}
        app_graph = _rt().app_for_thread(thread_id)

        # Found live: a `thread_id` with no matching checkpoint (a stale
        # browser tab from before a Postgres restart/reseed, a mistyped
        # reconnect id, or -- as here -- a client that never actually
        # started the conversation) used to crash deep inside `intent_router`
        # with a bare `KeyError: 'tenant_id'`, since `state_input` below
        # carries only `messages`, relying entirely on an existing
        # checkpoint to supply `tenant_id`/`guest_id`. Checking first turns
        # that into a clear, actionable error instead of an opaque 500.
        existing = await app_graph.aget_state(cfg)
        if not existing.values or "tenant_id" not in existing.values:
            yield _sse(
                "error",
                {
                    "message": (
                        f"Unknown or expired thread_id {thread_id!r} — this conversation "
                        "was never started or its state is gone. Start a new conversation."
                    )
                },
            )
            return

        state_input = {"messages": [HumanMessage(content=req.text)]}

    yield _sse("thread", {"thread_id": thread_id})

    node_order: list[list[str]] = []
    try:
        async for chunk in app_graph.astream(state_input, config=cfg, stream_mode="updates"):
            if "__interrupt__" in chunk:
                # Also available via `aget_state(cfg).interrupts` below --
                # skipped here only to avoid emitting a misleading "node"
                # event for a pseudo-step that isn't a real graph node.
                continue
            step_nodes = list(chunk.keys())
            node_order.append(step_nodes)
            yield _sse("node", {"nodes": step_nodes})
    except Exception as exc:  # noqa: BLE001 — surface to the console rather than a bare 500 mid-stream
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return

    snapshot = await app_graph.aget_state(cfg)
    result = dict(snapshot.values)
    if snapshot.interrupts:
        result["__interrupt__"] = snapshot.interrupts

    summary = summarize_turn(result, thread_id=thread_id, node_order=node_order)
    yield _sse("final", summary)


@app.post("/api/turns")
async def start_turn(req: TurnRequest) -> StreamingResponse:
    return StreamingResponse(_run_turn_stream(req), media_type="text/event-stream")


@app.get("/api/threads/{thread_id}/state")
async def get_thread_state(thread_id: str) -> dict[str, Any]:
    cfg = {"configurable": {"thread_id": thread_id}}
    app_graph = _rt().app_for_thread(thread_id)
    snapshot = await app_graph.aget_state(cfg)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="unknown thread_id")

    result = dict(snapshot.values)
    if snapshot.interrupts:
        result["__interrupt__"] = snapshot.interrupts
    messages = result.get("messages") or []
    transcript = [
        {"role": getattr(m, "type", "unknown"), "content": getattr(m, "content", "")} for m in messages
    ]
    summary = summarize_turn(result, thread_id=thread_id, node_order=[])
    summary["transcript"] = transcript
    return summary


class ApproveRequest(BaseModel):
    action: str  # "approved" | "declined" | "modified" -- ADR-047: all-or-nothing today
    approved_domains: list[str] = []  # full set or [] -- sent structurally even though
    # the backend routing is currently all-or-nothing (ADR-047), so the console never
    # simulates granularity it can't honor (ADR-047 Option 3, rejected).


@app.post("/api/threads/{thread_id}/approve", status_code=202)
async def approve_thread(thread_id: str, req: ApproveRequest) -> dict[str, str]:
    receipt = receipt_store.create()

    async def _resume() -> None:
        try:
            cfg = {"configurable": {"thread_id": thread_id}}
            app_graph = _rt().app_for_thread(thread_id)
            # Two, different, real resume contracts exist for a `human_approval`
            # interrupt in this codebase: the cross-domain gate
            # (graph/supervisor.py) reads `{"action": ...}` (falling back to
            # `"approved"` if absent), but every single domain's OWN
            # human_approval node (agents/{dining,stay,wellness,entertainment}
            # /edges/conditions.py) only ever reads `decision.get("approved")`
            # -- a bool, never "action". Sending only `{"action": ...}` (as
            # this endpoint used to) is silently misread as declined by every
            # domain-level node (`.get("approved")` on a dict with no
            # "approved" key returns None -> False) -- found live: wellness/
            # stay/entertainment approvals all reported "declined" even when
            # explicitly approved, while dining looked fine only because its
            # single-domain fast path normally never reaches its own
            # human_approval node at all. Sending both keys satisfies whichever
            # node actually raised the interrupt.
            resume_payload = {
                "action": req.action,
                "approved": req.action == "approved",
                "approved_domains": req.approved_domains,
            }
            async for _ in app_graph.astream(Command(resume=resume_payload), config=cfg, stream_mode="updates"):
                pass
            snapshot = await app_graph.aget_state(cfg)
            result = dict(snapshot.values)
            if snapshot.interrupts:
                result["__interrupt__"] = snapshot.interrupts
            summary = summarize_turn(result, thread_id=thread_id, node_order=[])
            receipt_store.mark_succeeded(receipt.token, summary)
        except Exception as exc:  # noqa: BLE001 — captured on the receipt, not raised into a background task
            receipt_store.mark_failed(receipt.token, f"{type(exc).__name__}: {exc}")

    asyncio.create_task(_resume())
    return {"receipt_token": receipt.token}


class ClarifyRequest(BaseModel):
    answer: str


@app.post("/api/threads/{thread_id}/clarify", status_code=202)
async def clarify_thread(thread_id: str, req: ClarifyRequest) -> dict[str, str]:
    """Resumes a `CLARIFY` interrupt (`graph/supervisor.py`'s
    `make_clarify_node`) — a free-text question expecting a free-text
    answer (`Command(resume=<str>)`), a different shape from
    `/approve`'s `{"action": ...}` resume. Conflating the two was the bug
    this endpoint exists to fix: every interrupt used to be treated as an
    approval regardless of which node raised it."""
    receipt = receipt_store.create()

    async def _resume() -> None:
        try:
            cfg = {"configurable": {"thread_id": thread_id}}
            app_graph = _rt().app_for_thread(thread_id)
            async for _ in app_graph.astream(Command(resume=req.answer), config=cfg, stream_mode="updates"):
                pass
            snapshot = await app_graph.aget_state(cfg)
            result = dict(snapshot.values)
            if snapshot.interrupts:
                result["__interrupt__"] = snapshot.interrupts
            summary = summarize_turn(result, thread_id=thread_id, node_order=[])
            receipt_store.mark_succeeded(receipt.token, summary)
        except Exception as exc:  # noqa: BLE001 — captured on the receipt, not raised into a background task
            receipt_store.mark_failed(receipt.token, f"{type(exc).__name__}: {exc}")

    asyncio.create_task(_resume())
    return {"receipt_token": receipt.token}


@app.get("/api/receipts/{token}")
async def get_receipt(token: str) -> dict[str, Any]:
    receipt = receipt_store.get(token)
    if receipt is None:
        raise HTTPException(status_code=404, detail="unknown receipt token")
    return {"status": receipt.status, "result": receipt.result, "error": receipt.error}


class FaultRequest(BaseModel):
    thread_id: str
    domain: str
    value: str | None  # None clears the pending fault for that domain


@app.put("/api/fault")
async def set_fault(req: FaultRequest) -> dict[str, Any]:
    if req.domain not in {d.value for d in Domain}:
        raise HTTPException(status_code=400, detail=f"unknown domain {req.domain!r}")
    if req.value is None:
        fault_registry.clear(req.thread_id, req.domain)
    else:
        if req.value not in REAL_FAULT_VALUES:
            raise HTTPException(status_code=400, detail=f"unknown fault value {req.value!r}")
        fault_registry.set(req.thread_id, req.domain, req.value)
    return {"pending": fault_registry.peek(req.thread_id)}


@app.get("/api/faults")
async def list_faults() -> dict[str, Any]:
    return {"values": list(REAL_FAULT_VALUES), "domains": [d.value for d in Domain]}


@app.get("/")
async def index() -> Any:
    from fastapi.responses import FileResponse

    return FileResponse("ui/static/index.html")
