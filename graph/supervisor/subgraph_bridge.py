"""Runtime bridge between the supervisor and a domain's standalone subgraph.

`DETERMINISTIC` invokes a *separate* compiled graph rather than mounting it as a
LangGraph subgraph node, so three pieces of engine plumbing live here instead of
in orchestration (ADR-061):

- the sub-thread identity (`{session_id}:deterministic:{domain}` — a persistence
  contract: changing it orphans in-flight conversations' inner checkpoints);
- explicit forwarding of a nested `interrupt()` and its later resume;
- the append-only-channel deduplication (feature 010, ADR-058) that stops a
  booking being recorded twice when the outer and inner histories both carry it.

Behaviour is intentionally identical to the code this was extracted from;
mounting the subgraph instead would change the checkpoint identity and needs a
`GRAPH_VERSION` major bump and its own ADR.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from graph.models import Domain
from graph.state import ResortFlowState


async def run_in_subthread(compiled: Any, state: ResortFlowState, domain: Domain) -> dict[str, Any]:
    """Run `compiled` in its own sub-thread for this session and return the
    `ResortFlowState` delta to merge back into the outer graph."""
    # A distinct sub-thread namespace, not the supervisor's own
    # thread_id — this subgraph has a different node/channel shape than
    # the supervisor's, and giving it its own thread_id (derived
    # deterministically from the session) avoids conflating the two
    # graphs' checkpoint histories under one identity while still
    # surviving resume within the same conversation.
    sub_thread_id = f"{state['session_id']}:deterministic:{domain.value}"
    inner_cfg = {"configurable": {"thread_id": sub_thread_id}}

    # This node is a plain async function manually invoking a *separate*
    # compiled graph — not a mounted LangGraph subgraph node — so a
    # nested interrupt does not propagate to the outer graph for free;
    # it must be forwarded explicitly, or the guest's pending approval
    # silently vanishes the moment DETERMINISTIC returns a normal dict.
    # `aget_state` distinguishes "first call" from "resuming a call that
    # already interrupted": a non-empty `.next` means the inner graph is
    # sitting at an unresolved interrupt from a previous pass through
    # this node.
    from langgraph.types import Command

    snapshot = await compiled.aget_state(inner_cfg)
    if snapshot.next:
        # Re-entering after the OUTER graph itself was resumed — forward
        # whatever the human sent straight into the inner subgraph's own
        # pending interrupt (they are answering that same question).
        outer_resume_value = interrupt({"forward": True})
        result = await compiled.ainvoke(Command(resume=outer_resume_value), config=inner_cfg)
    else:
        # Feature 006 convergence (T055): `dict(state)` used to forward the
        # OUTER graph's entire, ever-growing `messages` list as fresh input
        # on every single-domain turn. `add_messages` merges by id, so nothing
        # was logically duplicated, but the sub-thread's checkpointer still
        # re-diffed and re-persisted that whole list at every internal
        # super-step of the inner subgraph, on every turn — cost that grows
        # with total conversation length, not with this domain's own turn
        # count, which is what made the 40-turn adversarial script's per-turn
        # latency climb from 0.05s past 186s.
        # This sub-thread already retains every message it has ever been
        # given, in its own persisted checkpoint — so only the messages it
        # has not yet seen need to cross into this call; `add_messages`
        # appends them onto what it already has, exactly as if the full list
        # had been resent. First call to a fresh sub-thread has no prior
        # checkpoint (`snapshot.values` empty), so the entire history to date
        # is genuinely new to it — e.g. an allergy disclosed on an earlier
        # turn must still reach `allergen_check`'s conversation-wide scan the
        # first time this domain's subgraph runs.
        known_ids = {
            getattr(m, "id", None) for m in (snapshot.values or {}).get("messages") or []
        }
        new_messages = [
            m for m in (state.get("messages") or []) if getattr(m, "id", None) not in known_ids
        ]
        # Feature 010 (ADR-058, research R-12): when the sub-thread ALREADY
        # has a checkpoint, its own append-only channels
        # (confirmations/failures/saga_log/policy_flags) are already
        # exactly what they should be — forwarding the OUTER state's
        # cumulative copies of those same channels as "input" makes the
        # sub-thread's reducer append them a second time on top of what it
        # already has, which is the writer bug behind this feature's
        # reported defect (a guest's booking recorded twice). Only
        # `messages` needs the new-since-last-seen treatment above; the
        # other append-only channels need no forwarding at all once the
        # sub-thread has its own history. A fresh sub-thread (no prior
        # checkpoint) still gets the full state, exactly as before — it
        # has nothing of its own yet to duplicate against.
        _APPEND_ONLY_KEYS = ("confirmations", "failures", "saga_log", "policy_flags")
        has_prior_checkpoint = bool(snapshot.values)
        if has_prior_checkpoint:
            sub_state = {k: v for k, v in state.items() if k not in _APPEND_ONLY_KEYS}
            sub_state["messages"] = new_messages
        else:
            sub_state = {**state, "messages": new_messages}
        result = await compiled.ainvoke(sub_state, config=inner_cfg)
        if result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            # Pauses THIS (outer) graph too, forwarding the inner
            # subgraph's own interrupt payload verbatim. `interrupt()`
            # raises here — nothing below runs until the outer graph
            # itself resumes and this node function re-runs from the
            # top, landing in the `if snapshot.next:` branch above.
            interrupt(payload)

    keys = (
        "proposals", "holds", "confirmations", "failures", "messages", "saga_log",
        "awaiting_approval", "approval_scope", "policy_flags",
    )
    # Feature 006: policy_flags added — this allowlist previously
    # silently dropped every PolicyFlag either domain's standalone
    # subgraph produced (gaming's self-exclusion suppression, dining's
    # allergen-fidelity block) the moment a single-domain request
    # routed through DETERMINISTIC, which is most gaming/dining
    # traffic (Constitution Principle I: ~60% of requests). Confirmed
    # by this feature's own multi-turn integration test
    # (test_allergen_and_self_exclusion_flags_both_survive_in_one_
    # conversation) failing before this fix.
    update = {k: result[k] for k in keys if k in result}

    # Feature 010 (ADR-058, research R-12): the OTHER half of the
    # duplication bug — `result["confirmations"]` is the sub-thread's
    # FULL, ever-growing list, and copying it back verbatim into the
    # outer append-only `confirmations` channel re-appends every earlier
    # confirmation on every subsequent single-domain turn. Only genuinely
    # new confirmations (by `idempotency_key`, dedup rather than a length
    # slice — robust even if the outer and sub-thread histories don't
    # align 1:1) are copied back. `failures`/`saga_log`/`policy_flags`
    # are append-only and strictly ordered, so the new TAIL since this
    # call's own pre-invoke snapshot is exactly what's new.
    if "confirmations" in update:
        outer_keys = {c.idempotency_key for c in (state.get("confirmations") or [])}
        update["confirmations"] = [
            c for c in update["confirmations"] if c.idempotency_key not in outer_keys
        ]
    pre_invoke = snapshot.values or {}
    for key in ("failures", "saga_log", "policy_flags"):
        if key in update:
            pre_len = len(pre_invoke.get(key) or [])
            update[key] = update[key][pre_len:]
    return update
