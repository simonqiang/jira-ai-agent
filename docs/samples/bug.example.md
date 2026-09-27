# Bug example (anonymized)

> Derived from a real pilot-project ticket (fetched 2026-09-27), paraphrased and
> anonymized: keys, people and identifying details replaced. Original mapping kept
> out of the repository.

- Issue type: Bug · Status when captured: New · Estimate: **none recorded**
- Reported by: Requestor A (product side)

## Content

Title: Database connection pool exhausted during peak hours

Observable problem:
During peak usage windows (morning and early-afternoon peaks), the application's
database connection pool becomes exhausted. Symptoms: slow response times, application
errors, and potential service unavailability for end users.

Environment / version: production, peak-load periods (specific windows recorded in
the original).

Reproduction steps: **not present in the source ticket** (noted as a gap — inferred
trigger: high concurrent request volume during peak windows).

Expected: pool serves peak load without exhaustion.
Actual: pool exhausts, responses degrade, errors surface to users.

Impact: user-facing degradation and potential unavailability during business peaks;
root cause not yet established.

Candidate directions (from the original): query optimization, connection-management
tuning, or database scaling — pending root-cause investigation.

Verification criteria: **none present in the source ticket** (noted as a gap).

## Observations (for Week 7 templates)

- Good: clear symptom + impact + time-window framing; honest about root cause being
  unknown; lists candidate remediation directions rather than presuming one fix.
- Gaps: **no reproduction steps, no verification criteria, no environment specifics,
  no estimate** — the drafting assistant must ask for repro + verification before a
  Bug draft is considered ready (spec §3 separates schema validity, team rules and
  advisory quality; repro/verification are team-mandatory for Bugs).
- Structure worth keeping as the Bug default: Problem → Environment → Repro →
  Expected/Actual → Impact → Evidence → Verification criteria.
