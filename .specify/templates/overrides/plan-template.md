# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

[Gates determined based on constitution file]

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |

## Architecture Decision Records *(mandatory — ResortFlow project override)*

> This section is **not optional**. The constitution's EVERY DECISION IS DOCUMENTED
> standard requires it. ADRs skipped under delivery pressure are exactly the ones most needed later.

Every **significant** technical decision made in this plan MUST produce an ADR at
`docs/adr/NNN-slug.md`. A decision is significant when at least one holds:

- It constrains a later feature (an interface, a schema, a naming rule, a state key).
- A competent engineer could reasonably have chosen differently.
- Reversing it later would be a breaking change rather than a refactor.
- It trades a stated project value against another (cost vs. durability, latency vs.
  correctness, simplicity vs. auditability).

Routine application of an already-recorded decision is **not** significant and MUST NOT
generate a new ADR — reference the existing one instead.

### Required ADR structure

Each ADR contains exactly these sections, in this order:

1. **Context** — the forces in play. What made a decision necessary now?
2. **Options Considered** — at least two genuine alternatives, each with its real
   advantages stated fairly. A strawman option is a defect.
3. **Decision** — what was chosen, stated in one sentence, in the active voice.
4. **Consequences** — what this makes easy, what it makes hard, what it forecloses, and
   **what would change the decision**. Negative consequences MUST be stated; an ADR with
   only benefits has not been thought through.

### Plan completion gate

Before this plan is considered complete, list every ADR it obligates:

| ADR | Decision | Status |
|-----|----------|--------|
| [NNN] | [one-line decision] | [ ] drafted / [ ] complete |

A plan whose ADR table is empty MUST state explicitly why no decision in it met the
significance bar above. Silence is not an acceptable answer.
