"""Production FastAPI entrypoint for the ResortFlow orchestrator (feature 008.5
"Phase 7.5 Gap Closure", ADR-050; closes the root-`Dockerfile`/no-entrypoint
blocker tracked as `specs/006-policy-guardrails-hitl/tasks.md` T056). `python -m uvicorn graph.api:app --port 8100`,
or the root `Dockerfile`'s `CMD`.

Replicates `ui/app.py`'s (the dev-only verification console's) turn/approve/
clarify/reconnect logic against the same `compile_supervisor_graph`
entrypoint, including two already-debugged resume-payload bugs its own
comments document:

1. The cross-domain `HumanApproval` gate (`graph/supervisor.py`) and each
   domain's own `human_approval` node read *different* keys from the resume
   payload -- the gate reads `action` (falling back to `"approved"` if
   absent), every domain's own node reads only `decision.get("approved")` (a
   bool). A resume that sends only one of the two is silently misread by
   whichever node did NOT raise the interrupt. `/v1/threads/{id}/approve`
   sends both keys together for exactly this reason.
2. Clarify's resume is a bare string (`Command(resume=<str>)`) -- a
   different shape entirely from approval's dict resume. Conflating the two
   used to render every interrupt as an approval card regardless of which
   node raised it.

No SSE (deliberate design decision: streaming is a console-only concern --
every endpoint here returns one JSON document). No `load_dotenv()` call:
container env comes from docker-compose's `environment:` block (`ui/app.py`
calls `load_dotenv()` specifically because it is launched directly via
`uvicorn ui.app:app`, outside compose; this module IS launched via compose,
so populating `os.environ` here would be redundant at best and could mask a
real missing-env-var misconfiguration at worst).

SECURITY DISCLOSURE: `tenant_id` arrives in the request body with no edge
authentication in front of this service -- same as the existing `ui/app.py`
console. Real end-user auth (Entra ID / Azure APIM) is Phase 8 scope; see
ADR-049/ADR-050. Not fixed here -- a deliberate, disclosed gap, not an
oversight.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from graph.runtime import GraphRuntime, build_runtime
from graph.supervisor import new_session_state
from graph.turn_summary import summarize_turn
from prompts.registry import validate_all

_runtime_cm: Any = None
runtime: GraphRuntime | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _runtime_cm, runtime
    # feature 009 (FR-010, FR-014b): an invalid or revoked bundled prompt template must fail the container at
    # startup, not the first guest turn. Imports the call-site modules so their variable declarations are seen.
    validate_all(import_call_sites=True)
    _runtime_cm = build_runtime()
    runtime = await _runtime_cm.__aenter__()
    try:
        yield
    finally:
        await _runtime_cm.__aexit__(None, None, None)


app = FastAPI(title="ResortFlow Orchestrator", lifespan=lifespan)


def _rt() -> GraphRuntime:
    assert runtime is not None, "runtime not initialized (lifespan didn't run)"
    return runtime


def _cfg(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _outcome(result: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
    summary = summarize_turn(result, thread_id=thread_id, node_order=[])
    summary["thread_id"] = thread_id
    return summary


class TurnRequest(BaseModel):
    thread_id: str | None = None
    tenant_id: str
    guest_id: str
    text: str


@app.post("/v1/turns")
async def start_turn(req: TurnRequest) -> dict[str, Any]:
    if req.thread_id is None:
        state_input: Any = new_session_state(tenant_id=req.tenant_id, guest_id=req.guest_id, text=req.text)
        thread_id = state_input["session_id"]
        app_graph = _rt().app_for_thread(thread_id)
    else:
        thread_id = req.thread_id
        app_graph = _rt().app_for_thread(thread_id)

        # Same gap `ui/app.py::_run_turn_stream` found and fixed: a
        # `thread_id` with no matching checkpoint used to reach the graph
        # with only `messages` in state, crashing deep inside
        # `intent_router` with a bare `KeyError: 'tenant_id'` instead of a
        # clear client error.
        existing = await app_graph.aget_state(_cfg(thread_id))
        if not existing.values or "tenant_id" not in existing.values:
            raise HTTPException(
                status_code=404,
                detail=f"unknown or expired thread_id {thread_id!r} — start a new conversation",
            )

        state_input = {"messages": [HumanMessage(content=req.text)]}

    result = await app_graph.ainvoke(state_input, config=_cfg(thread_id))
    return _outcome(result, thread_id=thread_id)


@app.get("/v1/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict[str, Any]:
    app_graph = _rt().app_for_thread(thread_id)
    snapshot = await app_graph.aget_state(_cfg(thread_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="unknown thread_id")

    result = dict(snapshot.values)
    if snapshot.interrupts:
        result["__interrupt__"] = snapshot.interrupts
    messages = result.get("messages") or []
    transcript = [
        {"role": getattr(m, "type", "unknown"), "content": getattr(m, "content", "")} for m in messages
    ]
    outcome = _outcome(result, thread_id=thread_id)
    outcome["transcript"] = transcript
    return outcome


class ApproveRequest(BaseModel):
    action: str  # "approved" | "declined" | "modified" -- ADR-047: all-or-nothing today
    approved: bool | None = None
    approved_domains: list[str] = []  # full set or [] -- sent structurally even though
    # the backend routing is currently all-or-nothing (ADR-047), so callers never
    # simulate granularity the graph can't honor (ADR-047 Option 3, rejected).


@app.post("/v1/threads/{thread_id}/approve")
async def approve_thread(thread_id: str, req: ApproveRequest) -> dict[str, Any]:
    app_graph = _rt().app_for_thread(thread_id)
    # See module docstring point 1: both keys, always, regardless of which
    # human_approval node (the cross-domain gate, or a single domain's own)
    # actually raised this interrupt.
    resume_payload = {
        "action": req.action,
        "approved": req.approved if req.approved is not None else req.action == "approved",
        "approved_domains": req.approved_domains,
    }
    result = await app_graph.ainvoke(Command(resume=resume_payload), config=_cfg(thread_id))
    return _outcome(result, thread_id=thread_id)


class ClarifyRequest(BaseModel):
    answer: str


@app.post("/v1/threads/{thread_id}/clarify")
async def clarify_thread(thread_id: str, req: ClarifyRequest) -> dict[str, Any]:
    """Resumes a `CLARIFY` interrupt (`graph/supervisor.py`'s
    `make_clarify_node`) — a free-text question expecting a free-text
    answer (`Command(resume=<str>)`), a different shape from `/approve`'s
    dict resume. See module docstring point 2."""
    app_graph = _rt().app_for_thread(thread_id)
    result = await app_graph.ainvoke(Command(resume=req.answer), config=_cfg(thread_id))
    return _outcome(result, thread_id=thread_id)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
