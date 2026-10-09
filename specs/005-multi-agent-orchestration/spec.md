# Feature Specification: Multi-Agent Orchestration

**Feature Branch**: `005-multi-agent-orchestration`

**Created**: 2026-09-11

**Status**: Draft

**Input**: User description: "Feature: multi-agent-orchestration. Deliver the supervisor/intent-router/planner orchestration layer, the four remaining domain capabilities (entertainment, stay, wellness, gaming read-only), correct parallel execution across domains, a deterministic scheduling conflict checker, saga-style hold/verify/commit, semantic recovery on partial failure, and a bounded replan-to-human-escalation cycle — building on Phase 4's single-domain dining vertical slice. Entertainment's backing integration, currently incomplete, is finished as part of this feature so the entertainment capability is fully usable end-to-end."

## Clarifications

### Session 2026-09-11

- Q: When a guest's request is ambiguous or missing information the system needs to build a plan, should the system ask a clarifying follow-up question before planning, or make a best-effort assumption and proceed? → A: Ask the guest a clarifying follow-up question when required information is missing or ambiguous, before attempting to plan.
- Q: When an entire domain has no availability for the guest's request at all (not just a scheduling conflict with another domain), should the system propose a partial itinerary for the rest, or treat the whole request as unfulfillable and run it through the alternative-finding cycle first? → A: Propose a partial itinerary covering the domains that do have availability, and clearly tell the guest which requested domain could not be included.
- Q: If a guest cancels or goes silent after some holds are already placed but before approval/commitment, should the system proactively release those holds, or let them expire naturally? → A: Proactively release all outstanding holds as soon as the guest explicitly cancels; silent/no-response abandonment still relies on natural hold expiry.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Guest books a coordinated multi-domain itinerary (Priority: P1)

A guest describes an evening or stay that spans more than one resort domain in a single
request — for example, dinner, a show, and a spa slot — and receives back one coherent,
confirmed itinerary rather than having to book each piece separately.

**Why this priority**: This is the platform's central value proposition and the reason
this phase exists. Without it, the system is just several independent single-domain
bookers; nothing distinguishes it from Phase 4.

**Independent Test**: Can be fully tested by submitting one request spanning three domains
(e.g., dining, entertainment, wellness) and confirming all three are held, approved, and
committed together as one itinerary, with a schedule that is actually consistent (no
overlaps once travel time between venues is accounted for).

**Acceptance Scenarios**:

1. **Given** a guest has no existing holds, **When** they request dinner, a show, and a spa
   treatment for the same evening in one message, **Then** the system proposes a single
   itinerary with non-conflicting times and, once approved, confirms all three bookings.
2. **Given** a proposed itinerary has two time slots that would require impossible travel
   between venues, **When** the itinerary is assembled, **Then** the conflict is caught and
   an adjusted or alternative itinerary is proposed before the guest is ever asked to
   approve anything.
3. **Given** a guest approves a multi-domain itinerary, **When** commitment begins,
   **Then** each booking is committed in a safe order and the guest sees a single
   confirmation covering everything that was actually confirmed.

---

### User Story 2 - A partial failure produces a real alternative, not a cancellation (Priority: P1)

Partway through committing a multi-domain itinerary, one piece (for example, the dining
reservation) fails after other pieces are already committed or held. Instead of
cancelling the whole itinerary, the guest is offered a sensible alternative that keeps as
much of their original request intact as possible.

**Why this priority**: This is the phase's other defining behavior and one of its explicit
exit criteria. A system that rolls back everything on any single failure is not
meaningfully better than booking each domain separately by hand, and it actively harms
guest trust by discarding commitments that already succeeded.

**Independent Test**: Can be fully tested by forcing one domain in an in-progress
multi-domain booking to fail after other domains have already committed, and confirming
the guest is offered a specific, usable alternative for the failed piece rather than
having their entire itinerary cancelled.

**Acceptance Scenarios**:

1. **Given** a multi-domain itinerary where entertainment and wellness are already
   committed, **When** the dining commitment fails, **Then** the system proposes an
   alternative dining option (different time, venue, or comparable substitute) rather than
   releasing the entertainment and wellness commitments.
2. **Given** an alternative is proposed after a partial failure, **When** the guest
   approves it, **Then** the full itinerary — original commitments plus the new
   alternative — ends in a single confirmed state.
3. **Given** an alternative cannot be found for the failed piece, **When** the guest is
   informed, **Then** they are told clearly which parts of their itinerary remain
   confirmed and which part could not be fulfilled.

---

### User Story 3 - Guest books entertainment as its own request (Priority: P2)

A guest asks to book or reserve an entertainment experience (a show, an event, a ticketed
activity) on its own, without any other domain involved, and it works end-to-end the same
way dining already does.

**Why this priority**: Entertainment's backing integration is presently incomplete, so
today this request cannot succeed even as a single-domain booking. It must work
standalone before it can be trusted as one leg of a multi-domain itinerary in User Story
1, and it is independently valuable on its own.

**Independent Test**: Can be fully tested by requesting an entertainment booking alone,
with no other domain in the request, and confirming discovery, availability, holding, and
confirmation all complete successfully.

**Acceptance Scenarios**:

1. **Given** a guest asks what entertainment options are available for a given evening,
   **When** they ask, **Then** they receive real, current options (not a placeholder or
   error).
2. **Given** a guest chooses an entertainment option, **When** they ask to book it,
   **Then** the system places a hold, seeks approval, and confirms the booking the same
   way dining does today.

---

### User Story 4 - Guest asks about stay, wellness, or gaming on its own (Priority: P2)

A guest makes a single-domain request for a lodging change, a wellness/spa booking, or
information about gaming (hours, offerings, promotions) without touching any other
domain.

**Why this priority**: These are the remaining domains the platform must cover to make
the multi-domain scenario in User Story 1 meaningful across the full guest experience, and
each is independently useful on its own even before combined bookings are attempted.

**Independent Test**: Can be fully tested by making one single-domain request in each of
stay, wellness, and gaming, and confirming each behaves correctly on its own — stay and
wellness following the same hold-then-confirm pattern as dining, and gaming responding
with information only, never a transaction.

**Acceptance Scenarios**:

1. **Given** a guest requests a wellness/spa booking, **When** they complete the request,
   **Then** it is held, approved, and confirmed the same way dining is today.
2. **Given** a guest requests a change related to their stay, **When** they complete the
   request, **Then** it is held, approved, and confirmed the same way dining is today.
3. **Given** a guest asks a gaming-related question, **When** they ask, **Then** they
   receive accurate information and the system never offers to place, or places, any
   wager or gaming transaction on their behalf.

---

### User Story 5 - Repeated failure ends in human handoff, not an endless loop (Priority: P3)

A request keeps failing to find a workable plan even after the system tries reasonable
alternatives. Rather than retrying indefinitely, the system stops after a bounded number
of attempts and hands the guest off to a person.

**Why this priority**: This is a safety and trust boundary rather than a core value
driver — it matters most under unusual or adversarial input, which is common enough to be
an explicit exit criterion for this phase but is not what most guests will experience day
to day.

**Independent Test**: Can be fully tested by submitting a request that can never succeed
(for example, asking for something with no availability anywhere, repeatedly) and
confirming the system stops retrying after a fixed number of attempts and clearly hands
the conversation to a human rather than continuing to try or looping silently.

**Acceptance Scenarios**:

1. **Given** a request that fails on every attempted plan, **When** the system has
   retried a bounded number of times, **Then** it stops retrying and clearly indicates the
   conversation is being handed to a human.
2. **Given** a conversation has been handed to a human, **When** the guest sends another
   message, **Then** the system does not silently resume automated retrying on its own.

---

### Edge Cases

- What happens when two domains in the same itinerary are placing holds at the same time
  and one fails partway through — does the successful hold get lost, duplicated, or left
  dangling?
- When a guest explicitly cancels after some holds are placed but before anything is
  committed, the system proactively releases all outstanding holds right away; if the
  guest simply goes silent instead, the holds still expire naturally on their own.
- What happens if the conversation is interrupted (e.g., the guest goes silent or the
  system restarts) partway through a multi-domain saga, and is later resumed — are the
  previously placed holds re-verified rather than trusted as-is?
- When an entire domain is unavailable or erroring for the whole request (not just one
  time slot), the system proposes a partial itinerary covering the domains that do have
  availability and clearly tells the guest which requested domain could not be included.
- What happens when a guest's dietary restriction or other structured preference applies
  to one domain (dining) inside a multi-domain itinerary — is it still captured and
  enforced with the same rigor as a dining-only request?
- What happens when a guest asks for a gaming transaction (a wager, a deposit, an
  account-specific action) instead of gaming information — is it refused clearly rather
  than silently ignored or attempted?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST determine, from a single guest request, which resort
  domain(s) — dining, entertainment, stay, wellness, gaming-information — it involves,
  including requests that touch more than one domain at once.
- **FR-002**: The system MUST coordinate the domains involved in a single request as one
  itinerary rather than treating each domain independently once more than one is
  identified.
- **FR-003**: The system MUST decompose a multi-domain request into an ordered set of
  steps that respects dependencies and constraints between domains (for example, a
  schedule constraint in one domain affecting the acceptable timing in another).
- **FR-004**: The system MUST support entertainment, stay, and wellness bookings using the
  same discover → check availability → hold → approve → confirm lifecycle already
  established for dining, including entertainment, which today cannot complete this
  lifecycle end-to-end.
- **FR-005**: The system MUST support gaming-related requests as information-only. It
  MUST NOT place, offer to place, or execute any wager or gaming account transaction under
  any circumstance.
- **FR-006**: When a request involves multiple domains, the system MUST work on them
  concurrently rather than strictly one after another, wherever the domains are
  independent of each other.
- **FR-007**: A failure in one domain during concurrent processing MUST NOT cause the loss
  or unintended duplication of work already completed in another domain in the same
  request.
- **FR-008**: The system MUST detect scheduling conflicts across an itinerary's steps —
  including the time realistically required to travel between venues or activities —
  before presenting the itinerary to the guest for approval.
- **FR-009**: Scheduling conflict detection MUST be deterministic (the same inputs always
  produce the same conflict result) and MUST NOT depend on a language model's judgment.
- **FR-010**: The system MUST place holds provisionally for every domain in an itinerary
  before seeking guest approval, and MUST require guest approval before committing any of
  them.
- **FR-011**: The system MUST commit the domains in a multi-domain itinerary in a
  deliberate order — committing the scarcest or hardest-to-reverse commitments first —
  rather than committing them all at once or in an arbitrary order.
- **FR-012**: The system MUST independently verify each commitment after it is made,
  rather than assuming a commitment succeeded because the request to make it was sent.
- **FR-013**: When part of an in-progress multi-domain commitment fails, the system MUST
  attempt to find and propose a real alternative for the failed piece that preserves the
  parts of the itinerary already committed, rather than cancelling the entire itinerary by
  default.
- **FR-014**: If no reasonable alternative can be found for a failed piece, the system MUST
  clearly communicate to the guest which parts of the itinerary remain confirmed and which
  part could not be completed, rather than leaving the outcome ambiguous.
- **FR-015**: The system MUST limit how many times it will automatically retry or replan a
  failing request, and MUST hand the conversation to a human once that limit is reached
  rather than continuing indefinitely.
- **FR-016**: Once a conversation has been handed to a human, the system MUST NOT silently
  resume fully automated retrying on that same failing request without a person's
  involvement.
- **FR-017**: If a conversation involving in-progress or already-placed holds is resumed
  after any interruption, the system MUST re-verify the true state of every hold with the
  owning domain before proceeding, rather than trusting previously recorded state as-is.
- **FR-018**: Every domain-mutating action the system takes on a guest's behalf MUST be
  tied to the specific resort property (tenant) the guest is interacting with, and the
  system MUST NOT allow one property's request to affect another property's data or
  inventory.
- **FR-019**: When a dietary restriction is part of a multi-domain request that includes
  dining, it MUST be captured and enforced with the same rigor (structured, not
  free-text-only) as it is for a dining-only request today.
- **FR-020**: When a guest's request lacks information the system needs to build a plan
  (for example, no date, no party size, or which domains are actually wanted is unclear),
  the system MUST ask the guest a clarifying follow-up question before attempting to plan,
  rather than guessing and proceeding.
- **FR-021**: When a requested domain has no availability at all for the request (as
  opposed to a schedule conflict with another domain), the system MUST propose a partial
  itinerary covering the domains that do have availability and MUST clearly tell the
  guest which requested domain could not be included, rather than treating the entire
  request as failed.
- **FR-022**: When a guest explicitly cancels an in-progress itinerary after holds have
  been placed but before approval or commitment, the system MUST proactively release
  every outstanding hold for that itinerary immediately, rather than leaving them to
  expire on their own.

### Key Entities *(include if feature involves data)*

- **Itinerary**: The set of one or more domain bookings a guest requests together in a
  single conversation; carries its overall status (planning, awaiting approval,
  partially committed, fully committed, failed) and the guest and property it belongs to.
- **Domain Step**: One domain's portion of an itinerary (e.g., "the dining piece," "the
  entertainment piece"); tracks its own lifecycle stage independently of the other steps
  in the same itinerary.
- **Hold**: A provisional, time-limited claim on a specific domain resource (a table, a
  seat, a room, a treatment slot) made before guest approval and before commitment.
- **Conflict**: A detected incompatibility between two or more domain steps in the same
  itinerary, such as an overlapping or unreachable schedule.
- **Recovery Alternative**: A proposed substitute for a domain step that failed after
  other steps in the same itinerary were already committed.
- **Replan Attempt**: One cycle of the system trying to find a workable plan after a prior
  attempt failed; counted toward the bound that triggers human handoff.
- **Escalation**: The state of a conversation once automated replanning has reached its
  limit and the conversation has been handed to a human.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A guest can request and receive a single confirmed itinerary spanning at
  least three resort domains (for example, dining, entertainment, and wellness) in one
  conversation, without booking each domain separately.
- **SC-002**: When a domain fails after other domains in the same itinerary are already
  committed, the guest receives a specific, usable alternative in the same conversation
  rather than a cancellation of the entire itinerary, in at least 90% of such cases in
  testing.
- **SC-003**: Across repeated concurrent multi-domain testing, zero holds are ever
  duplicated or left orphaned (placed but never resolved to confirmed, released, or
  expired) as a result of a partial failure.
- **SC-004**: A request that cannot be fulfilled after automated replanning reaches human
  handoff within a fixed, small number of attempts every time, never continuing
  indefinitely.
- **SC-005**: Guests can complete a single-domain entertainment, stay, or wellness request
  successfully at a rate matching the existing dining capability's success rate.
- **SC-006**: Scheduling conflicts within a proposed itinerary — including travel time
  between venues — are caught before the guest is asked to approve, in 100% of tested
  conflicting scenarios.
- **SC-007**: No gaming-related request ever results in a wager, deposit, or other gaming
  account transaction being placed or offered by the system.

## Assumptions

- Entertainment, stay, and wellness follow the same hold → approve → confirm lifecycle
  already proven for dining; gaming remains information-only, with no booking or
  transaction lifecycle at all, consistent with project-wide policy.
- The bounded number of automated replan attempts before human handoff reuses the limit
  already established elsewhere in the platform's durable state design, rather than this
  feature introducing a separate one.
- Within this feature, "human handoff" means the conversation reaches a distinct,
  clearly-communicated escalation state that stops automated retrying; building the
  full live-staff-facing console or notification workflow around that state is a
  separate, later concern.
- A guest's preferences or restrictions from a previous, separate visit are not
  automatically carried into a new conversation as part of this feature — each
  conversation still captures relevant preferences (such as dietary restrictions) fresh,
  consistent with how dining works today.
- At least one domain's backend integration in this feature is exercised through the
  same kind of real, governed integration boundary the platform uses for all external
  systems, rather than a shortcut that only works because everything happens to run in
  one process — since this is the first point in the project where more than one domain
  runs at once.
- The actual compliance/policy rules referenced by this feature (responsible gaming
  limits, jurisdiction-specific messaging, data handling) are represented only as
  points in the flow where such a check will run; the rules themselves are delivered by a
  later feature and are not evaluated by this one.
