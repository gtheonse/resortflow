"""Venue-menu retrieval — the allergen checker's only dependency on Track D
(`content/`, `rag/`). Per the original brief: "if your allergen-check node
needs the RAG retriever and it isn't ready yet, build against a
stub/interface... rather than blocking or reimplementing retrieval
yourself."

`query_venue_menu` is that interface, still kept here as the deterministic
fallback. Track D's retrieval has since landed
(`rag.menu.get_venue_chunks` — an exhaustive tenant+venue scroll, not a
ranked semantic search, since a policy-critical allergen check must see
every chunk, not the top-k most "relevant" ones). `default_menu_query`
below is what `allergen_check.py` actually uses by default now: try the
real Qdrant-backed lookup, and fall back to this stub only if Qdrant is
unreachable (mirrors the same infra-optional pattern already used for
Azure OpenAI credentials and Langfuse elsewhere in this feature, so the
graph stays demoable without `docker compose up`).

Both paths return `graph.models.Chunk` — the frozen retrieval model — so
`allergen_check.py` never changes shape regardless of which one answers.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

from graph.models import Chunk

# --- Stub corpus -------------------------------------------------------------
# Deliberately tiny and deterministic: enough menu text per seeded venue
# (`sims/dining/seed.py`) to exercise every `DietaryRestriction` keyword at
# least once. Replace wholesale once Track D's retriever exists — nothing
# downstream depends on this dict's shape, only on `query_venue_menu`'s
# return type.

_STUB_MENU_TEXT: dict[str, list[str]] = {
    "ven-mgv-01": [  # Meridian Steakhouse
        "Dry-aged ribeye finished in brown butter (contains dairy).",
        "Charred octopus with romesco (contains tree nut almond romesco).",
        "Classic Caesar salad (contains egg, dairy, gluten croutons).",
    ],
    "ven-mgv-02": [  # Sakura Sushi Bar
        "Spicy tuna roll (contains fish, sesame seeds, soy sauce).",
        "Shrimp tempura roll (contains shellfish, gluten batter).",
        "Peanut ponzu dipping sauce served with edamame (contains peanut, soy).",
    ],
    "ven-mgv-03": [  # Cactus Flower Cantina
        "Vegan black bean tacos (no animal products).",
        "Queso fundido (contains dairy).",
    ],
    "ven-mgv-04": [  # The Green Table
        "Fully vegetarian and vegan-friendly tasting menu.",
        "Kosher-certified dessert cart.",
    ],
    "ven-mgv-05": [  # Neon Noodle House
        "Pad thai with crushed peanut garnish (contains peanut, egg, fish sauce).",
        "Halal-certified beef noodle soup.",
    ],
    "ven-mhb-01": [  # Harbor Chophouse
        "Lobster bisque (contains shellfish, dairy).",
        "Gluten-free bunless burger available on request.",
    ],
    "ven-mhb-02": [  # North End Trattoria
        "Fresh pasta made in-house (contains gluten, egg).",
        "Low-sodium marinara available on request.",
    ],
    "ven-mhb-03": [  # Bayside Raw Bar
        "Raw oyster and clam platter (contains shellfish).",
        "Grilled salmon (contains fish).",
    ],
}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def query_venue_menu(tenant_id: str, venue_id: str) -> list[Chunk]:
    """Return the menu-item chunks for one venue. Stub implementation:
    in-memory dict, deterministic, no network/vector-store dependency —
    exactly the shape `rag.retrieve` will eventually have (`tenant_id` +
    `venue_id` in, `list[Chunk]` out), so `allergen_check.py` requires no
    change when the real retriever replaces this function.
    """
    items = _STUB_MENU_TEXT.get(venue_id, [])
    now = datetime.now(UTC)
    return [
        Chunk(
            chunk_id=f"{venue_id}-menu-{i:02d}",
            tenant_id=tenant_id,
            content=text,
            source_uri=f"stub://menu/{tenant_id}/{venue_id}#{i}",
            valid_from=now,
            valid_to=None,
            content_hash=_content_hash(text),
        )
        for i, text in enumerate(items)
    ]


def default_menu_query(tenant_id: str, venue_id: str) -> list[Chunk]:
    """Real Qdrant lookup, stub fallback. This is what `allergen_check.py`
    wires in by default; tests inject `query_venue_menu` or a fake directly
    when they want the deterministic stub without a live Qdrant."""
    try:
        from rag.menu import get_venue_chunks

        chunks = get_venue_chunks(tenant_id, venue_id)
    except Exception:  # noqa: BLE001 — any retrieval failure falls back to the stub menu
        logger.warning(
            "rag.menu.get_venue_chunks unavailable (Qdrant unreachable or "
            "not indexed) — falling back to the deterministic stub menu "
            "for venue_id=%s. Fine for local dev/tests; not the production path.",
            venue_id,
        )
        return _screen(query_venue_menu(tenant_id, venue_id))

    if chunks:
        return _screen(chunks)
    # Real index reachable but has nothing for this venue (e.g. Track D's
    # indexer hasn't been run yet) — fall back rather than silently
    # skipping the allergen check.
    return _screen(query_venue_menu(tenant_id, venue_id))


def _screen(chunks: list[Chunk]) -> list[Chunk]:
    """Feature 006 (ADR-032): screen retrieved menu content for embedded
    instructions before allergen_check's keyword matching ever sees it.

    Disclosed limitation: this is the *only* retrieval call site that exists
    anywhere in `agents/`/`graph/` today — there is no general
    property-knowledge-document Q&A node yet (the scenario spec.md's User
    Story 3 describes end-to-end). Menu content here is only ever used for
    keyword matching, never interpolated into an LLM generation context, so
    the practical risk this screen defends against is lower here than the
    scenario that motivated it — but it is real content from a retrieval
    source, and screening it before any use is still a reasonable
    defense-in-depth measure with the infrastructure this feature builds."""
    from policy.injection import load_injection_corpus, screen_retrieved_content

    results = screen_retrieved_content(chunks, corpus=load_injection_corpus())
    suspect_ids = {r.chunk_id for r in results if r.verdict == "suspect"}
    if suspect_ids:
        logger.warning("Excluded %d suspect chunk(s) from menu content: %s", len(suspect_ids), suspect_ids)
    return [c for c in chunks if c.chunk_id not in suspect_ids]
