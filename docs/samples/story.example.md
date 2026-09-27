# Story example (anonymized)

> Derived from a real pilot-project ticket (fetched 2026-09-27), paraphrased and
> anonymized: keys, people and identifying details replaced. Original mapping kept
> out of the repository.

- Issue type: Story · Status when captured: New · Estimate: **none recorded**
- Reported by: Requestor A (product side)

## Content

Title: Add automatic retry handling for failed shipment-status API calls

As a **logistics operations analyst**, I want **automatic retries for failed
shipment-status API calls** so that **transient failures do not block daily tracking
updates**.

Current state:
Failed shipment-status calls currently block the tracking-update pipeline. Each
incident delays updates by several minutes; the team spends roughly 2–3 hours per day
on manual reconciliation, and customers lose visibility of shipment status meanwhile.

Proposed solution:
Intelligent retry with exponential backoff, circuit-breaker behavior and alerting
when retries are exhausted. (Original also sketched configuration options.)

Scope: retry logic for the shipment-status integration only.
Out of scope: other integrations; changes to the upstream API.

Acceptance criteria: **none present in the source ticket** (noted as a gap).

Dependencies: none recorded.
Open questions: retry budget/limits; behaviour when the circuit breaker opens.

## Observations (for Week 7 templates)

- Good: role/goal/benefit framing present; quantified impact (delay, hours lost);
  clear current-state vs proposed-solution structure.
- Gaps: **no acceptance criteria**, no estimate, no assignee — the drafting assistant
  should ask for these rather than invent them (spec §3: unknowns become questions).
- Structure worth keeping as the Story default: Context → Current state → Proposed
  solution → Scope/Out of scope → Acceptance criteria → Dependencies → Open questions.
