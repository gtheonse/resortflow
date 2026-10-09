# ADR-053 — Prompt registry client boundary: SDK confined to one adapter, our own compiler, canonical hash

**Status**: Proposed
**Date**: 2026-09-27
**Feature**: 009-prompt-management
**Extends**: ADR-052 (Git-first prompt registry), ADR-051 (Langfuse OTLP exporter wiring), ADR-041 (shared telemetry module)

## Context

ADR-052 makes Git the canonical home of every prompt and Langfuse an optional mirror and runtime registry. That
leaves one engineering question the ADR deliberately does not answer: *how* the codebase talks to Langfuse without
undermining decisions already made.

Three existing decisions constrain it. Principle II says no agent holds a vendor SDK; the `langfuse` package is
installed but has never been imported (ADR-051 wired tracing as raw OpenTelemetry to Langfuse's OTLP endpoint). The
constitution says span attribute names live in one module. And the eval gate compares scores across runs, so
whatever identifies "the same prompt" must be stable and portable.

Research found four facts that shape the design. The Langfuse Python SDK 4.15.x, constructed normally, starts its own
tracer provider and attaches a span processor (`resource_manager.py`), which would double-export the spans ADR-051's
exporter already sends. The SDK's `prompt.compile()` does not report which placeholders a template uses, so it cannot
support fail-closed variable checks. A registry prompt version carries `name, version, config, labels, tags,
commit_message` but **no hash and no author**. And Langfuse "folder" names (`a/b`) have a known bug in prompt-to-trace
association (langfuse issue #9354), while "prompt composability" resolves references on the server, which would make
the served text differ from anything we hash.

## Options Considered

**1. Raw REST calls with `httpx`, no SDK.** Advantage: no vendor package in the runtime path at all, so Principle II
is trivially satisfied and there is no tracer-provider risk. Disadvantage: we would reimplement the SDK's prompt
cache (60 s, stale-while-revalidate), its retry and timeout handling, and the cache invalidation `update_prompt`
performs, for no functional gain — and the sync tooling (create version, move label) is exactly what the SDK already
does well.

**2. The SDK with its own tracing enabled, sharing our tracer provider, using SDK-native generation spans with
`prompt=`.** Advantage: the officially documented way to link a generation to a prompt version. Disadvantage: it
makes the Langfuse package part of every model call's tracing path, contradicts ADR-051's choice of a vendor-neutral
OTel exporter (Azure Monitor must get the same lineage), and depends on UI-linkage behaviour that ADR-052 D5 decided
not to rely on.

**3. The SDK for the prompt API only, constructed with `tracing_enabled=False`, confined to a single adapter module.**
Advantage: we keep the SDK's cache, retries and label API; no span processor is attached and the global tracer
provider is untouched, so ADR-051's exporter stays the only exporter; the whole vendor surface is one file that a test
can police. Disadvantage: the Langfuse client is a process-level singleton per public key, so any future code that
also constructs a client with tracing enabled in the same process would inherit the disabled state — which is why
"only one file constructs a client" is a tested rule, not a convention.

## Decision

**Adopt Option 3.** `prompts/langfuse_adapter.py` is the only file in the repository that imports `langfuse`; it
constructs the client with `tracing_enabled=False`, requires an explicit `LANGFUSE_HOST` (the SDK's default is Langfuse
Cloud, which must never be reached by accident), and never raises to its caller. The package is pinned to
`langfuse>=4.15,<5` in `pyproject.toml` and in the root `Dockerfile`, and the compose image is pinned to
`langfuse/langfuse:3.225.11` and `langfuse/langfuse-worker:3.225.11` (the floating `3` tag resolved to exactly that
version, digest-identical for both images, when this was decided; the Docker daemon was offline so the digests were
compared through Docker Hub's API rather than `docker pull`).

Four supporting rules complete the boundary:

- **Our own compiler renders both sources.** Templates are rendered by `prompts/compiler.py`, never by the SDK's
  `compile()`: a single regex pass over the template's own placeholders, values inserted verbatim and never rescanned.
  That gives identical output from Git and registry, lets us detect a placeholder with no supplied value or one the
  template was never declared to use, and means guest or retrieved text containing `{{…}}` is a value, not a template.
- **The content hash is defined by us.** SHA-256 over the UTF-8 canonical JSON of `{type, messages[{role,content}],
  config}` with sorted keys, compact separators, `ensure_ascii=False` and line endings normalised to LF (universal newlines: CRLF and a lone CR; the
  normalisation is idempotent, which a single-pass CRLF replacement is not). It covers messages
  and config and never name, version, labels, tags or `commit_message`, so identical content hashes identically
  whichever source it came from.
- **`exact`-policy prompts (the judges) always resolve from Git.** Registry version numbers differ between registries,
  so only the hash is portable; no registry lookup is made for them.
- **Names are snake_case; no folders, no composability.** Both are excluded for the reasons in Context.

## Consequences

**Made easier.** The vendor surface is one file, so replacing or removing Langfuse is a one-module change; the boundary
is enforced by a test rather than review; Git and registry paths cannot render differently; a hash is comparable
across processes, registries and time.

**Made harder.** We maintain a small compiler and a canonical-hash definition instead of using the SDK's; the
"one client per process" property must be defended against later contributors; the SDK's cache is opaque to us, so
tests assert what we pass to it rather than its behaviour.

**Foreclosed.** SDK-native prompt-linked tracing; Langfuse folders and prompt composability; using the SDK's
`compile()`; any second module that talks to Langfuse. Revisiting any of these needs its own ADR.
