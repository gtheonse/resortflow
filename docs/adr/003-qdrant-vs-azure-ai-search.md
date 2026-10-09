# ADR-003 — Qdrant for build, Azure AI Search as the documented production target

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

ResortFlow's retrieval track (feature 003, Track D) needs hybrid dense + BM25 + RRF fusion with
cross-encoder reranking, plus per-tenant payload filtering so MGV content never leaks into
an MHB query. This feature's `docker-compose.yml` (T003) has to declare the vector store in
the local topology now, and this feature's `Chunk`/`Citation` models (`graph/models.py`)
and the `dedupe_by_id` reducer already assume a chunk-identity and rank-order contract that
whichever store is chosen must support. `AGENTS.md`'s stated rule — "everything runs
offline except Azure OpenAI" — is a hard constraint on this decision, not a preference.

## Options Considered

**1. Azure AI Search for both build and production.** Single technology end to end, native
Azure integration, managed hybrid search and semantic ranking built in. Azure AI Search
Basic runs approximately $75/month with no free local emulator — for a project
built and iterated on locally, that cost recurs for every day of development, not just
production.

**2. Qdrant for both build and production.** Free, runs in Docker, excellent hybrid +
payload filtering, and avoids a technology switch between environments entirely. Forfeits
the "managed service, integrated Azure security" argument that a production system in this
domain (health data via dietary requirements, PII via guest contact info) should be able to
make.

**3. Qdrant locally, Azure AI Search as the documented production target.** Zero-cost local
iteration; the production architecture is fully specified (index schema, hybrid query
shape, per-tenant filter) without actually running the paid service until deployment
demands it.

## Decision

**Qdrant in Docker for build; Azure AI Search documented as the production target, not
implemented in this repository.** The rule this feature's plan.md applies consistently
across every component in the local-first topology: **open source in Docker wherever the
managed service is only buying convenience; managed Azure wherever it's buying compliance,
durability, or something genuinely hard.** Vector search at this scale is convenience —
Qdrant's hybrid + payload filtering is not meaningfully weaker than Azure AI Search Basic
for a two-tenant, single-property-pair system, and Basic's ~$75/month is not justified
against $0 for a project with no funded production traffic.

## Consequences

**Made easier.** Local development costs nothing and requires no Azure subscription to
iterate on retrieval quality. `docker compose up` gives every contributor an identical
vector store with no shared-tenant cost.

**Made harder.** Two search engines' query DSLs and index-schema idioms both have to be
understood, even though only one ever runs. The production migration is real work deferred,
not eliminated — someone eventually pays the cost of standing up and validating Azure AI
Search against the same hybrid + per-tenant-filter contract Qdrant already proves locally.

**Foreclosed.** A Qdrant-specific feature with no Azure AI Search equivalent cannot be
relied on in the documented production design without an explicit follow-up decision —
config parity (plan.md's stated principle: "identical code paths local and cloud; only
connection strings and the model provider differ") means the retrieval track's abstraction
layer must not assume Qdrant-only capabilities.
