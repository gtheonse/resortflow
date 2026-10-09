# Contract: Coverage Exemption File

**Location**: `eval/data/exemptions.yaml`, committed to the repository.
**Authored by**: engineers, as part of the same pull request that introduces the
uncovered capability (FR-009a). **Read by**: `eval/coverage.py` at PR- and
merge-queue-tier runtime.

An exemption is a normal file edit in a normal pull request — it goes through
code review like any other change, which is what makes it "individually
reviewable" (FR-009a/SC-009) without any new tooling.

## Shape (one entry per exemption)

```yaml
- exemption_id: "dining-hold-table-2026-09-20"
  surface_id: "dining.hold_table"          # matches Coverage Requirement surface_id
  urgency_tier: "tier_1"                    # tier_1 | tier_2 | other — sets the expiry window
  reason: "New hold-capable tool ships behind a flag; saga case follows in #412"
  tracking_issue: "https://github.com/<org>/<repo>/issues/412"
  created_at: "2026-09-20T00:00:00Z"
  expires_at: "2026-09-27T00:00:00Z"        # created_at + tier window (FR-025) — computed, not hand-edited
  status: "active"                          # active | cleared | void — see data-model.md lifecycle
  request_sequence: 1                       # 1st/2nd/3rd+ request against this surface_id (FR-029)
  adr_ref: null                             # REQUIRED non-null when request_sequence >= 3
```

## Validation rules enforced by `eval/coverage.py`

- `expires_at` MUST equal `created_at` plus the window for `urgency_tier`
  (7d / 14d / 30d) — a hand-edited mismatch fails the PR-tier gate immediately,
  since it would otherwise be a way to manufacture a longer exemption than the
  policy allows.
- An entry with `status: active` past its `expires_at` is treated as `void` by
  the gate regardless of what the file says (FR-027) — the runner recomputes
  status at read time; it does not trust a stale `active` value left in the
  file.
- `request_sequence >= 3` with `adr_ref: null` fails the gate (FR-029) — the
  file cannot represent a 3rd-request exemption without a linked ADR.
- Closing `tracking_issue` has **no effect** on `status` — only the coverage
  gate actually passing for `surface_id` clears an exemption (FR-026). The
  runner does not check issue-tracker state at all.

## Compatibility

Adding an optional field is non-breaking. Removing `surface_id`, `expires_at`,
`status`, or `request_sequence`, or changing the expiry-window values, requires
an ADR (these are exactly the fields FR-025–029's guarantees depend on).
