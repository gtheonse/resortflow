# Amendment: `tool-error.schema.json`

**Feature**: `003-mcp-tool-layer` | **Status**: APPLIED — confirmed by the user and merged
into `docs/contracts/schemas/tool-error.schema.json` on 2026-09-11. See `research.md` D6 and
**ADR-015 (Accepted)**.

This is not a new contract this feature owns; it's a concrete, reviewable diff to a Phase 0
frozen file. It was held for explicit confirmation before being applied, per the working
agreement ("Phase 0 contracts are frozen... raise it as a blocker, do not edit unilaterally"),
and applied only after that confirmation.

## Why

FR-030 (composite-tool partial failure must identify already-completed sub-calls) and FR-037
(a mutating call's indeterminate effect must be distinguishable from an ordinary failure) both
need a place to put data the current schema has no room for
(`"additionalProperties": false` over a fixed field list).

## The diff (as applied)

Two optional properties added. Nothing required changed; nothing existing was removed or
renamed; every envelope valid under the prior schema remains valid under the amended one.

```diff
   "properties": {
     "error_class": { ... },
     "domain": { ... },
     "detail": { ... },
     "vendor_code": { ... },
     "http_status": { ... },
     "retry_after_seconds": { ... },
     "idempotency_key": { ... },
-    "trace_id": { ... }
+    "trace_id": { ... },
+    "completed_sub_calls": {
+      "type": ["array", "null"],
+      "description": "Populated only when this failure came from a composite tool (one call composing several vendor operations, per mcp-tool-contract.md's tool-ergonomics guidance) and at least one of its sub-calls had already completed when a later one failed. Absent or null otherwise. See specs/003-mcp-tool-layer/data-model.md.",
+      "items": {
+        "type": "object",
+        "required": ["slot_name", "vendor_resource_id", "idempotency_key"],
+        "additionalProperties": false,
+        "properties": {
+          "slot_name": { "type": "string" },
+          "vendor_resource_id": { "type": "string" },
+          "idempotency_key": { "type": "string" }
+        }
+      }
+    },
+    "effect_status": {
+      "type": ["string", "null"],
+      "enum": ["unresolved", null],
+      "description": "Present and set to \"unresolved\" only when this failure's error_class does not affirmatively rule out the mutating call's vendor-side effect having taken place (an indeterminate commit outcome, or a schema-invalid response to a mutating call). error_class and effect_status are orthogonal — see specs/003-mcp-tool-layer/spec.md's clarification on this point. Absent or null for every other failure, including all read-only-tool failures."
+    }
   }
```

`additionalProperties: false` is unchanged — both new fields are declared, not smuggled in
under a loosened constraint, so the schema stays exactly as strict about unknown fields as it
was.

## What does *not* change

- No existing field's type, requiredness, or enum changes.
- The `allOf` rule (retryable failures require `idempotency_key`) is untouched.
- Nothing about `TERMINAL`, `REQUIRES_HUMAN`, or `POLICY_BLOCKED` semantics changes — this
  amendment adds data alongside the classification, it doesn't touch the classification
  itself.

## Consumers

- **This feature** is the sole producer of both new fields.
- **No consumer exists yet.** `completed_sub_calls` and `effect_status` are read by nothing
  until the Phase 4–5 ledger/reconciler exists (`research.md` D6). Producing a signal with no
  reader yet is deliberate: it's cheaper to add the field now, while this feature already
  knows exactly when it applies, than to reconstruct "which historical failures were actually
  indeterminate" later from data that was never captured.
