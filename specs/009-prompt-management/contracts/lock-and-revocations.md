# Contract: Prompt lock and revocation list

Two append-only JSON files, shipped in the application image. The runtime reads them; CI
protects them. Rationale for two files: revocation must be approvable by an incident responder
without loosening approval on the lock (research R-12, R-13).

## `prompts/prompts.lock.json` — approved hashes

```json
{
  "schema": 1,
  "prompts": {
    "dining_intent_extraction": {
      "approved": [
        { "sha256": "<64 hex>", "added": "2026-09-25", "commit": "<git sha>" }
      ]
    }
  }
}
```

Written by `python -m prompts.sync lock`, which sets `approved = base_lock ∪ {current template hashes}` (seeded from the merge-base lock, never from the file being edited). It never removes an entry. Rolling `production` back to an older approved version works
precisely because old hashes are retained.

## `prompts/revocations.json` — tombstones

```json
{
  "schema": 1,
  "revoked": [
    {
      "sha256": "<64 hex>",
      "prompt_id": "dining_intent_extraction",
      "reason": "wording under-extracted sesame allergy",
      "date": "2026-10-02",
      "ref": "https://…/incident-or-pr",
      "break_glass": true,
      "owner_ack": null
    }
  ]
}
```

## CI invariants (deterministic, no registry access)

| # | Invariant | Spec |
|---|---|---|
| L1 | Every template's current hash ∈ `approved` of its prompt | FR-024 |
| L2 | `approved(head) ⊇ approved(base)` — nothing removed | FR-013 |
| L2b | `approved(head) ⊆ approved(base) ∪ {hashes of head templates and pinned judge templates}` — a hash may be approved only together with the template that has it; with no base lock (bootstrap), exactly the head template and pinned-judge hashes are allowed; a rollback target already in `approved(base)` is unaffected | FR-013 |
| L3 | `revoked(head) ⊇ revoked(base)`; existing entries unchanged except `owner_ack` being set | FR-013a |
| L4 | `revoked ∩ {current template hashes} = ∅` — the bundled fallback is never revoked | FR-014b, S-6 |
| L5 | `revoked ∩ {pinned judge hashes} = ∅` | FR-016a |
| L6 | PR marked break-glass ⇒ `approved(head) == approved(base)` (only already-approved content may replace) | FR-040 |
| L7 | Every tombstone has `reason`, `date`, `ref` | FR-013a |

`base` = `git merge-base HEAD <target>` (the PR's base branch, or the merge group's `base_sha`), **not** `HEAD~1`, so a multi-commit branch behaves like a single commit and pull requests and the merge queue agree.

## Runtime use

`registry.resolve` loads both files once per process. A hash is **servable** iff
`hash ∈ approved ∧ hash ∉ revoked`. Revocation
propagates only by deploying a build with the updated files (FR-014c); there is no runtime
channel. Propagation target: 60 minutes from merge (assumption to confirm at review); stated in
ADR-052 and the runbook as **not instantaneous**.
